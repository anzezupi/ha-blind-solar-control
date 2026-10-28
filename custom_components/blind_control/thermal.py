"""Thermal physics: solar gain through a window, insulation and a learned room model.

Pure Python (no Home Assistant imports), like ``engine.py``.

Model chain
-----------
1. Clear-sky global horizontal irradiance (Haurwitz).
2. Cloud attenuation (Kasten-Czeplak) or a measured irradiance sensor.
3. Split into beam and diffuse (Erbs correlation).
4. Projection onto the vertical window (incidence angle, isotropic sky, ground albedo).
5. Transmission: glazing g-value, incidence angle modifier, blind shading factor (Fc).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

try:
    from .engine import BlindType, Geometry, TiltMode, angle_diff, sun_in_window
except ImportError:  # loaded standalone by the unit tests
    from engine import BlindType, Geometry, TiltMode, angle_diff, sun_in_window  # type: ignore[no-redef]

SOLAR_CONSTANT = 1361.0
ALBEDO = 0.2

# glazing: (g-value / SHGC, U-value W/m²K)
GLAZING: dict[str, tuple[float, float]] = {
    "single": (0.85, 5.8),
    "double_old": (0.75, 2.9),
    "double": (0.60, 1.1),
    "triple": (0.50, 0.7),
}

# Shading factor Fc of a fully closed blind and its added thermal resistance (m²K/W).
_EXTERIOR: dict[BlindType, tuple[float, float]] = {
    BlindType.ROLLER_SHUTTER: (0.05, 0.17),
    BlindType.ROLLER_BLIND: (0.15, 0.05),
    BlindType.VENETIAN: (0.10, 0.05),
    BlindType.TILT_ONLY: (0.10, 0.05),
    BlindType.AWNING: (0.30, 0.0),
    BlindType.CURTAIN: (0.30, 0.05),
}
_INTERIOR: dict[BlindType, tuple[float, float]] = {
    BlindType.ROLLER_SHUTTER: (0.60, 0.08),
    BlindType.ROLLER_BLIND: (0.60, 0.08),
    BlindType.VENETIAN: (0.70, 0.03),
    BlindType.TILT_ONLY: (0.70, 0.03),
    BlindType.AWNING: (0.60, 0.0),
    BlindType.CURTAIN: (0.55, 0.08),
}

# Typical cloud cover (%) when a weather entity reports no ``cloud_coverage``.
CONDITION_CLOUDS: dict[str, float] = {
    "sunny": 5,
    "clear-night": 5,
    "windy": 30,
    "partlycloudy": 45,
    "exceptional": 50,
    "cloudy": 85,
    "rainy": 90,
    "snowy": 90,
    "snowy-rainy": 90,
    "lightning": 90,
    "lightning-rainy": 95,
    "hail": 95,
    "pouring": 100,
    "fog": 100,
}


# --------------------------------------------------------------------------- #
# Irradiance
# --------------------------------------------------------------------------- #


def ghi_clear(elevation: float) -> float:
    """Clear-sky global horizontal irradiance (Haurwitz), W/m²."""
    s = math.sin(math.radians(elevation))
    return 0.0 if s <= 0.01 else 1098.0 * s * math.exp(-0.057 / s)


def ghi_cloudy(elevation: float, cloud_pct: float) -> float:
    """Kasten-Czeplak cloud attenuation."""
    c = min(100.0, max(0.0, cloud_pct)) / 100.0
    return ghi_clear(elevation) * (1.0 - 0.75 * c**3.4)


def erbs(ghi: float, elevation: float) -> tuple[float, float]:
    """Split GHI into (DNI, DHI) with the Erbs diffuse-fraction correlation."""
    s = math.sin(math.radians(elevation))
    if ghi <= 0 or s <= 0.01:
        return 0.0, max(0.0, ghi)
    kt = min(1.0, ghi / (SOLAR_CONSTANT * s))
    if kt <= 0.22:
        kd = 1.0 - 0.09 * kt
    elif kt <= 0.80:
        kd = 0.9511 - 0.1604 * kt + 4.388 * kt**2 - 16.638 * kt**3 + 12.336 * kt**4
    else:
        kd = 0.165
    dhi = kd * ghi
    dni = min(1100.0, (ghi - dhi) / max(s, 0.05))
    return dni, dhi


@dataclass(frozen=True, slots=True)
class WindowIrradiance:
    """Irradiance on the outside of the glass (W/m²)."""

    beam: float
    diffuse: float
    cos_incidence: float


def window_irradiance(geo: Geometry, azimuth: float, elevation: float, ghi: float) -> WindowIrradiance:
    """Project horizontal irradiance onto the vertical window plane."""
    if elevation <= 0:
        return WindowIrradiance(0.0, 0.0, 0.0)
    dni, dhi = erbs(ghi, elevation)
    cos_inc = math.cos(math.radians(elevation)) * math.cos(math.radians(angle_diff(azimuth, geo.azimuth)))
    beam = dni * cos_inc if cos_inc > 0 and sun_in_window(geo, azimuth, elevation) else 0.0
    diffuse = 0.5 * dhi + 0.5 * ALBEDO * ghi  # isotropic sky + ground reflection
    return WindowIrradiance(beam, diffuse, max(0.0, cos_inc))


def incidence_modifier(cos_inc: float, b0: float = 0.1) -> float:
    """ASHRAE incidence angle modifier for glass."""
    if cos_inc <= 0.05:
        return 0.0
    return min(1.0, max(0.0, 1.0 - b0 * (1.0 / cos_inc - 1.0)))


# --------------------------------------------------------------------------- #
# Window
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Window:
    """Thermal properties of one window with its blind."""

    area: float = 1.8  # m² of glass
    g_value: float = 0.6
    u_value: float = 1.1
    fc: float = 0.6  # transmitted fraction with the blind fully closed
    r_add: float = 0.05  # extra resistance with the blind closed (m²K/W)

    @classmethod
    def build(
        cls,
        width: float,
        height: float,
        glazing: str,
        blind_type: BlindType,
        exterior: bool,
        g_value: float | None = None,
        u_value: float | None = None,
    ) -> Window:
        g, u = GLAZING.get(glazing, GLAZING["double"])
        fc, r_add = (_EXTERIOR if exterior else _INTERIOR)[blind_type]
        return cls(
            area=max(0.0, width * height * 0.85),  # ~15 % frame
            g_value=g_value if g_value is not None else g,
            u_value=u_value if u_value is not None else u,
            fc=fc,
            r_add=r_add,
        )

    def gain(self, irr: WindowIrradiance, open_fraction: float = 1.0) -> float:
        """Solar heat entering the room (W)."""
        q_open = self.area * self.g_value * (irr.beam * incidence_modifier(irr.cos_incidence) + 0.9 * irr.diffuse)
        f = min(1.0, max(0.0, open_fraction))
        return q_open * (f + (1.0 - f) * self.fc)

    def insulation_saving(self, t_in: float, t_out: float, closed_fraction: float) -> float:
        """Heat loss avoided by the closed blind (W)."""
        if t_in <= t_out or closed_fraction <= 0 or self.r_add <= 0:
            return 0.0
        u_closed = 1.0 / (1.0 / self.u_value + self.r_add)
        return (self.u_value - u_closed) * self.area * (t_in - t_out) * min(1.0, closed_fraction)


def percent_to_beta(pct: float, mode: TiltMode, invert: bool = False) -> float:
    """Inverse of ``engine.beta_to_percent``."""
    if invert:
        pct = 100.0 - pct
    if mode is TiltMode.QUARTER:
        return (100.0 - pct) / 100.0 * 90.0
    return (50.0 - pct) / 50.0 * 90.0


def open_fraction(
    blind_type: BlindType,
    position: float | None,
    tilt: float | None,
    tilt_mode: TiltMode,
    tilt_invert: bool = False,
) -> float:
    """How much of the window the blind leaves uncovered (0 … 1)."""
    pos = 100.0 if position is None else position
    slats = 1.0
    if tilt is not None and tilt_mode is not TiltMode.NONE:
        slats = 1.0 - min(1.0, abs(percent_to_beta(tilt, tilt_mode, tilt_invert)) / 90.0)
    if blind_type is BlindType.TILT_ONLY:
        return slats
    if blind_type is BlindType.AWNING:
        return 1.0 - pos / 100.0
    frac = pos / 100.0
    if blind_type is BlindType.VENETIAN:
        return frac + (1.0 - frac) * slats
    return frac


# --------------------------------------------------------------------------- #
# Learned room model
# --------------------------------------------------------------------------- #

DEFAULT_SENSITIVITY = 0.6  # °C per kWh of solar gain
DEFAULT_LOSS_RATE = 0.05  # 1/h, share of the in/out difference lost per hour
MIN_SAMPLES = 48  # ~1 day of 30-min samples before trusting the model


@dataclass(slots=True)
class RoomModel:
    """Recursive least squares fit of  dT = a*E + b*(T_out - T_in)*dt + c*dt.

    a: temperature rise per kWh of solar gain (°C/kWh)
    b: heat loss rate towards outdoors (1/h)
    c: everything else: heating, people, appliances (°C/h)
    """

    theta: list[float] = field(default_factory=lambda: [DEFAULT_SENSITIVITY, DEFAULT_LOSS_RATE, 0.0])
    p: list[list[float]] = field(default_factory=lambda: [[50.0, 0, 0], [0, 0.05, 0], [0, 0, 1.0]])
    samples: int = 0
    forgetting: float = 0.998

    def update(self, energy_kwh: float, t_in_start: float, t_in_end: float, t_out: float, hours: float) -> None:
        x = (energy_kwh, (t_out - t_in_start) * hours, hours)
        y = t_in_end - t_in_start
        px = [sum(self.p[i][j] * x[j] for j in range(3)) for i in range(3)]
        denom = self.forgetting + sum(x[i] * px[i] for i in range(3))
        if denom <= 1e-9:
            return
        k = [v / denom for v in px]
        err = y - sum(self.theta[i] * x[i] for i in range(3))
        self.theta = [self.theta[i] + k[i] * err for i in range(3)]
        self.p = [[(self.p[i][j] - k[i] * px[j]) / self.forgetting for j in range(3)] for i in range(3)]
        # Keep the physics plausible even with noisy data.
        self.theta[0] = min(10.0, max(0.0, self.theta[0]))
        self.theta[1] = min(1.0, max(0.0, self.theta[1]))
        self.samples += 1

    @property
    def trained(self) -> bool:
        return self.samples >= MIN_SAMPLES

    @property
    def sensitivity(self) -> float:
        return self.theta[0] if self.trained else DEFAULT_SENSITIVITY

    @property
    def loss_rate(self) -> float:
        return self.theta[1] if self.trained else DEFAULT_LOSS_RATE

    def predict(self, t_in: float, t_out: float, energy_kwh: float, hours: float) -> float:
        """Room temperature after ``hours`` with ``energy_kwh`` of solar gain (no heating)."""
        return t_in + self.sensitivity * energy_kwh + self.loss_rate * (t_out - t_in) * hours

    def as_dict(self) -> dict[str, Any]:
        return {"theta": self.theta, "p": self.p, "samples": self.samples}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RoomModel:
        if not data:
            return cls()
        try:
            return cls(theta=list(data["theta"]), p=[list(r) for r in data["p"]], samples=int(data["samples"]))
        except (KeyError, TypeError, ValueError):
            return cls()


def setpoint_suggestion(
    comfort: float, expected_kwh: float, sensitivity: float, max_setback: float, step: float = 0.5
) -> float:
    """Lower the heating setpoint by the warming the sun is expected to deliver."""
    drop = min(max_setback, max(0.0, sensitivity * expected_kwh))
    return comfort - math.floor(drop / step) * step
