"""Pure decision engine for Blind Control.

No Home Assistant imports here on purpose: the whole decision logic can be
unit-tested in isolation and stays cheap to evaluate.

Conventions
-----------
* Positions use Home Assistant semantics: 0 = closed, 100 = open
  (for awnings: 0 = retracted, 100 = extended).
* Slat angle ``beta`` (degrees): 0 = horizontal (open), +90 = closed with the
  outer edge down (blocks sun from above), -90 = closed with the outer edge up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import math


class BlindType(StrEnum):
    """Supported kinds of window coverings."""

    ROLLER_BLIND = "roller_blind"  # interior roller blind
    ROLLER_SHUTTER = "roller_shutter"  # exterior roller shutter
    VENETIAN = "venetian"  # position + slat tilt
    TILT_ONLY = "tilt_only"  # louvred shutters / pergola, tilt only
    CURTAIN = "curtain"
    AWNING = "awning"


class Climate(StrEnum):
    """What the room currently needs from the sun."""

    HEAT = "heat"
    COOL = "cool"
    NEUTRAL = "neutral"


class Strategy(StrEnum):
    """User-selected strategy (select entity)."""

    AUTO = "auto"
    HEATING = "heating"
    COOLING = "cooling"
    GLARE = "glare"
    OFF = "off"


class TiltMode(StrEnum):
    """How the device maps tilt percentage to slat angle."""

    NONE = "none"
    QUARTER = "90"  # 0 % = closed, 100 % = horizontal
    HALF = "180"  # 0 % = closed (outer edge down), 50 % = horizontal, 100 % = closed other way


class Action(StrEnum):
    """Actions a schedule rule can request."""

    AUTO = "auto"
    OPEN = "open"
    CLOSE = "close"
    POSITION = "position"
    TILT = "tilt"
    TILT_AUTO = "tilt_auto"
    SHADE_ONLY = "shade_only"
    SKIP = "skip"


class Reason(StrEnum):
    """Why the engine chose a target (exposed as an enum sensor)."""

    DISABLED = "disabled"
    SAFETY = "safety"
    MANUAL_OVERRIDE = "manual_override"
    RULE_SKIP = "rule_skip"
    RULE_OPEN = "rule_open"
    RULE_CLOSE = "rule_close"
    RULE_POSITION = "rule_position"
    RULE_TILT = "rule_tilt"
    HEAT_GAIN = "heat_gain"
    COOLING_SHADE = "cooling_shade"
    GLARE_SHADE = "glare_shade"
    AWAY_SHADE = "away_shade"
    NO_SUN = "no_sun"
    STRATEGY_OFF = "strategy_off"


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #


def angle_diff(a: float, b: float) -> float:
    """Signed smallest difference a - b in degrees, range [-180, 180)."""
    return (a - b + 180.0) % 360.0 - 180.0


def parse_horizon(text: str | None) -> tuple[tuple[float, float, float], ...]:
    """Parse ``"90-120:15, 200-240:25"`` into ((from_az, to_az, elevation), ...).

    Invalid chunks are ignored so a typo never breaks the integration.
    """
    if not text:
        return ()
    result: list[tuple[float, float, float]] = []
    for chunk in text.replace(";", ",").split(","):
        try:
            span, elev = chunk.split(":")
            start, end = span.split("-")
            result.append((float(start) % 360, float(end) % 360, float(elev)))
        except ValueError:
            continue
    return tuple(result)


def horizon_blocked(
    horizon: tuple[tuple[float, float, float], ...], azimuth: float, elevation: float
) -> bool:
    """Return True when an obstacle (building, tree, hill) hides the sun."""
    for start, end, elev in horizon:
        inside = start <= azimuth <= end if start <= end else azimuth >= start or azimuth <= end
        if inside and elevation <= elev:
            return True
    return False


@dataclass(frozen=True, slots=True)
class Geometry:
    """Window orientation and dimensions."""

    azimuth: float = 180.0  # direction the window faces (0 = N, 90 = E, 180 = S)
    fov_left: float = 80.0  # degrees counter-clockwise from the normal
    fov_right: float = 80.0  # degrees clockwise from the normal
    min_elevation: float = 0.0
    max_elevation: float = 90.0
    horizon: tuple[tuple[float, float, float], ...] = ()
    window_height: float = 1.5  # m
    sill_height: float = 0.9  # m, bottom of the glass above the floor
    shade_depth: float = 0.5  # m, how deep direct sun may reach into the room (0 = off)
    slat_width: float = 80.0  # mm
    slat_spacing: float = 70.0  # mm


def sun_in_window(geo: Geometry, azimuth: float, elevation: float) -> bool:
    """Is the sun shining onto this window?"""
    if elevation <= max(geo.min_elevation, 0.0) or elevation > geo.max_elevation:
        return False
    diff = angle_diff(azimuth, geo.azimuth)
    if not -geo.fov_left <= diff <= geo.fov_right:
        return False
    return not horizon_blocked(geo.horizon, azimuth, elevation)


def profile_angle(geo: Geometry, azimuth: float, elevation: float) -> float:
    """Vertical shadow angle in the plane perpendicular to the window (degrees)."""
    cos_diff = math.cos(math.radians(angle_diff(azimuth, geo.azimuth)))
    if cos_diff <= 1e-6:
        return 90.0
    return math.degrees(math.atan(math.tan(math.radians(elevation)) / cos_diff))


def roller_shade_position(geo: Geometry, gamma: float) -> float:
    """Highest position that keeps direct sun within ``shade_depth`` of the window."""
    if geo.window_height <= 0:
        return 0.0
    edge = geo.shade_depth * math.tan(math.radians(gamma))  # bottom edge above floor
    pos = (edge - geo.sill_height) / geo.window_height * 100.0
    return min(100.0, max(0.0, pos))


def cutoff_beta(geo: Geometry, gamma: float) -> float:
    """Smallest slat angle that blocks direct sun (cut-off angle).

    Derived from sin(beta + gamma) = (spacing / width) * cos(gamma).
    """
    if geo.slat_width <= 0:
        return 90.0
    x = geo.slat_spacing / geo.slat_width * math.cos(math.radians(gamma))
    if x >= 1.0:
        return 90.0
    return math.degrees(math.asin(x)) - gamma


def beta_to_percent(beta: float, mode: TiltMode, invert: bool = False) -> int:
    """Convert slat angle to the device's tilt percentage."""
    if mode is TiltMode.QUARTER:
        pct = 100.0 - min(90.0, max(0.0, beta)) / 90.0 * 100.0
    else:
        pct = 50.0 - min(90.0, max(-90.0, beta)) / 90.0 * 50.0
    if invert:
        pct = 100.0 - pct
    return round(pct)


# --------------------------------------------------------------------------- #
# Climate
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ClimateConfig:
    """Thresholds for heating / cooling decisions."""

    heat_setpoint: float = 21.0
    cool_setpoint: float = 24.5
    outdoor_heat_below: float = 15.0
    outdoor_cool_above: float = 24.0
    hysteresis: float = 0.5


def climate_state(
    prev: Climate, t_in: float | None, t_out: float | None, cfg: ClimateConfig
) -> Climate:
    """Decide what the room needs, with hysteresis to avoid flapping."""
    h = cfg.hysteresis
    if t_in is not None:
        if t_in < cfg.heat_setpoint or (prev is Climate.HEAT and t_in < cfg.heat_setpoint + h):
            return Climate.HEAT
        if t_in > cfg.cool_setpoint or (prev is Climate.COOL and t_in > cfg.cool_setpoint - h):
            return Climate.COOL
        # Comfortable inside but a hot day outside: shade pre-emptively.
        if t_out is not None and t_out >= cfg.outdoor_cool_above:
            return Climate.COOL
        return Climate.NEUTRAL
    if t_out is not None:
        if t_out < cfg.outdoor_heat_below or (
            prev is Climate.HEAT and t_out < cfg.outdoor_heat_below + h
        ):
            return Climate.HEAT
        if t_out > cfg.outdoor_cool_above or (
            prev is Climate.COOL and t_out > cfg.outdoor_cool_above - h
        ):
            return Climate.COOL
    return Climate.NEUTRAL


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Positions:
    """Configured target positions."""

    default_position: int = 100
    default_tilt: int | None = None  # None = horizontal slats
    heat_position: int = 100
    shade_position: int = 0  # fixed shade (curtain/awning or when shade_depth = 0)
    shade_min_position: int = 0  # never close further than this while shading
    away_position: int = 0  # hot room, nobody home
    lockout_position: int = 100  # door/window open
    safety_position: int | None = None  # wind / rain
    step: int = 5  # round computed positions to this step


@dataclass(frozen=True, slots=True)
class EngineConfig:
    """Static configuration of one blind."""

    blind_type: BlindType = BlindType.ROLLER_BLIND
    geometry: Geometry = field(default_factory=Geometry)
    positions: Positions = field(default_factory=Positions)
    tilt_mode: TiltMode = TiltMode.NONE
    tilt_invert: bool = False
    tilt_margin: float = 5.0
    glare_protection: bool = False


@dataclass(frozen=True, slots=True)
class RuleTarget:
    """The part of an active schedule rule the engine cares about."""

    action: Action
    position: int | None = None
    tilt: int | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class Inputs:
    """Live inputs for one evaluation."""

    azimuth: float
    elevation: float
    climate: Climate = Climate.NEUTRAL
    strategy: Strategy = Strategy.AUTO
    bright: bool = True
    present: bool | None = None
    safety: bool = False
    lockout: bool = False
    override: bool = False
    rule: RuleTarget | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    """Result of an evaluation. ``None`` means 'do not touch'."""

    position: int | None
    tilt: int | None
    reason: Reason
    rule: str | None = None
    forced: bool = False  # bypass rate limiting
    sun_in_window: bool = False
    profile_angle: float | None = None
    lockout: bool = False


def _round_down(value: float, step: int) -> int:
    """Round down to ``step`` (more shade rather than less)."""
    if step <= 1:
        return int(value)
    return int(value // step * step)


class Engine:
    """Stateless calculator bound to one blind's configuration."""

    __slots__ = ("cfg",)

    def __init__(self, cfg: EngineConfig) -> None:
        self.cfg = cfg

    # -- helpers ---------------------------------------------------------- #

    @property
    def has_position(self) -> bool:
        return self.cfg.blind_type is not BlindType.TILT_ONLY

    @property
    def has_tilt(self) -> bool:
        return self.cfg.tilt_mode is not TiltMode.NONE and self.cfg.blind_type in (
            BlindType.VENETIAN,
            BlindType.TILT_ONLY,
        )

    def tilt(self, beta: float) -> int | None:
        if not self.has_tilt:
            return None
        return beta_to_percent(beta, self.cfg.tilt_mode, self.cfg.tilt_invert)

    def _pos(self, value: int) -> int | None:
        return value if self.has_position else None

    # -- targets ---------------------------------------------------------- #

    def _closed(self) -> tuple[int | None, int | None]:
        return self._pos(0), self.tilt(90.0)

    def _open(self) -> tuple[int | None, int | None]:
        return self._pos(100), self.tilt(0.0)

    def _default(self) -> tuple[int | None, int | None]:
        p = self.cfg.positions
        tilt = p.default_tilt if p.default_tilt is not None and self.has_tilt else self.tilt(0.0)
        return self._pos(p.default_position), tilt

    def _heat(self, gamma: float) -> tuple[int | None, int | None]:
        # Slats parallel to the sun rays let in as much radiation as possible.
        return self._pos(self.cfg.positions.heat_position), self.tilt(-gamma)

    def _shade(self, gamma: float) -> tuple[int | None, int | None]:
        cfg, p = self.cfg, self.cfg.positions
        bt = cfg.blind_type
        if bt in (BlindType.VENETIAN, BlindType.TILT_ONLY):
            beta = cutoff_beta(cfg.geometry, gamma) + cfg.tilt_margin
            return self._pos(p.shade_position), self.tilt(beta)
        if bt in (BlindType.ROLLER_BLIND, BlindType.ROLLER_SHUTTER) and cfg.geometry.shade_depth > 0:
            pos = _round_down(roller_shade_position(cfg.geometry, gamma), p.step)
            return max(pos, p.shade_min_position), None
        return p.shade_position, None

    def _away(self, gamma: float) -> tuple[int | None, int | None]:
        if self.cfg.blind_type is BlindType.AWNING:
            return self._shade(gamma)
        return self._pos(self.cfg.positions.away_position), self.tilt(90.0)

    # -- main ------------------------------------------------------------- #

    def decide(self, inp: Inputs) -> Decision:
        cfg = self.cfg
        in_window = sun_in_window(cfg.geometry, inp.azimuth, inp.elevation)
        gamma = profile_angle(cfg.geometry, inp.azimuth, inp.elevation) if in_window else None
        rule_name = inp.rule.name if inp.rule else None

        def make(target: tuple[int | None, int | None], reason: Reason, forced: bool = False) -> Decision:
            pos, tilt = target
            lockout = False
            if inp.lockout and pos is not None and pos < cfg.positions.lockout_position:
                pos, lockout = cfg.positions.lockout_position, True
            return Decision(pos, tilt, reason, rule_name, forced or lockout, in_window, gamma, lockout)

        if inp.safety and cfg.positions.safety_position is not None:
            return make((self._pos(cfg.positions.safety_position), self.tilt(0.0)), Reason.SAFETY, True)
        if inp.override:
            return Decision(None, None, Reason.MANUAL_OVERRIDE, rule_name, False, in_window, gamma)

        action = inp.rule.action if inp.rule else Action.AUTO
        if action is Action.SKIP:
            return Decision(None, None, Reason.RULE_SKIP, rule_name, False, in_window, gamma)
        if action is Action.OPEN:
            return make(self._open(), Reason.RULE_OPEN)
        if action is Action.CLOSE:
            return make(self._closed(), Reason.RULE_CLOSE)
        if action is Action.POSITION:
            pos = inp.rule.position if inp.rule and inp.rule.position is not None else 0
            tilt = inp.rule.tilt if inp.rule and self.has_tilt else None
            return make((self._pos(pos), tilt), Reason.RULE_POSITION)
        if action is Action.TILT:
            tilt = inp.rule.tilt if inp.rule and inp.rule.tilt is not None else self.tilt(0.0)
            return make((self._pos(0), tilt), Reason.RULE_TILT)

        target, reason = self._solar(inp, gamma, shade_only=action is Action.SHADE_ONLY)
        if action is Action.TILT_AUTO:
            target = (self._pos(0), target[1] if target[1] is not None else self.tilt(0.0))
        return make(target, reason)

    def _solar(
        self, inp: Inputs, gamma: float | None, shade_only: bool
    ) -> tuple[tuple[int | None, int | None], Reason]:
        if inp.strategy is Strategy.OFF:
            return self._default(), Reason.STRATEGY_OFF
        if gamma is None or not inp.bright:
            return self._default(), Reason.NO_SUN

        climate = {
            Strategy.HEATING: Climate.HEAT,
            Strategy.COOLING: Climate.COOL,
            Strategy.GLARE: Climate.NEUTRAL,
        }.get(inp.strategy, inp.climate)

        if climate is Climate.HEAT and not shade_only:
            return self._heat(gamma), Reason.HEAT_GAIN
        if climate is Climate.COOL:
            if inp.present is False:
                return self._away(gamma), Reason.AWAY_SHADE
            return self._shade(gamma), Reason.COOLING_SHADE
        if shade_only or self.cfg.glare_protection or inp.strategy is Strategy.GLARE:
            return self._shade(gamma), Reason.GLARE_SHADE
        return self._default(), Reason.NO_SUN
