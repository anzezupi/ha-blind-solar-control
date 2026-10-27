"""Runtime controller for one blind."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
import time as monotonic_time
from typing import Any

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_CURRENT_TILT_POSITION,
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    DOMAIN as COVER_DOMAIN,
    CoverEntityFeature,
    CoverState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_SET_COVER_POSITION,
    SERVICE_SET_COVER_TILT_POSITION,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    SUN_EVENT_SUNRISE,
    SUN_EVENT_SUNSET,
)
from homeassistant.core import CALLBACK_TYPE, Context, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError, TemplateError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_track_point_in_time, async_track_state_change_event
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.sun import get_astral_event_date, get_astral_location
from homeassistant.helpers.template import Template, result_as_boolean
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import engine as eng
from .const import (
    CONF_AWAY_POSITION,
    CONF_AZIMUTH,
    CONF_BLIND_TYPE,
    CONF_CONTACT,
    CONF_COOL_SETPOINT,
    CONF_COVER,
    CONF_DEFAULT_POSITION,
    CONF_DEFAULT_TILT,
    CONF_DRY_RUN,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_GLARE,
    CONF_HEAT_POSITION,
    CONF_HEAT_SETPOINT,
    CONF_HORIZON,
    CONF_HYSTERESIS,
    CONF_INDOOR_TEMP,
    CONF_INVERT_POSITION,
    CONF_LOCKOUT_POSITION,
    CONF_LUX,
    CONF_LUX_THRESHOLD,
    CONF_MAX_ELEVATION,
    CONF_MIN_DELTA,
    CONF_MIN_ELEVATION,
    CONF_MIN_INTERVAL,
    CONF_OUTDOOR_COOL_ABOVE,
    CONF_OUTDOOR_HEAT_BELOW,
    CONF_OUTDOOR_TEMP,
    CONF_OVERRIDE_MINUTES,
    CONF_OVERRIDE_TOLERANCE,
    CONF_OVERRIDE_UNTIL_RULE,
    CONF_POSITION_STEP,
    CONF_PRESENCE,
    CONF_RAIN,
    CONF_RULES,
    CONF_SAFETY_POSITION,
    CONF_SAFETY_RELEASE,
    CONF_SETTLE_TIME,
    CONF_SHADE_DEPTH,
    CONF_SHADE_MIN_POSITION,
    CONF_SHADE_POSITION,
    CONF_SILL_HEIGHT,
    CONF_SLAT_SPACING,
    CONF_SLAT_WIDTH,
    CONF_TILT_INVERT,
    CONF_TILT_MARGIN,
    CONF_TILT_MODE,
    CONF_UPDATE_INTERVAL,
    CONF_WEATHER,
    CONF_WIND,
    CONF_WIND_THRESHOLD,
    CONF_WINDOW_HEIGHT,
    DEFAULTS,
    DOMAIN,
)
from .schedule import Rule, active_rule, next_boundary

_LOGGER = logging.getLogger(__name__)

MOVING = (CoverState.OPENING, CoverState.CLOSING)
INVALID = (STATE_UNAVAILABLE, STATE_UNKNOWN, None)
SUNNY_WEATHER = frozenset({"sunny", "partlycloudy", "exceptional"})
FORECAST_STEP = timedelta(minutes=5)
FORECAST_TTL = timedelta(minutes=30)

type BlindConfigEntry = ConfigEntry[BlindCoordinator]


@dataclass(slots=True)
class BlindState:
    """Snapshot exposed to entities."""

    decision: eng.Decision
    azimuth: float
    elevation: float
    climate: eng.Climate
    rule: str | None
    override_until: datetime | None
    safety: bool
    next_change: datetime | None
    sun_enters: datetime | None
    sun_leaves: datetime | None


def _opt_int(value: Any) -> int | None:
    return None if value in (None, "") else int(value)


def build_engine_config(cfg: dict[str, Any]) -> eng.EngineConfig:
    """Translate the flat entry options into the engine's config objects."""
    return eng.EngineConfig(
        blind_type=eng.BlindType(cfg[CONF_BLIND_TYPE]),
        geometry=eng.Geometry(
            azimuth=float(cfg[CONF_AZIMUTH]),
            fov_left=float(cfg[CONF_FOV_LEFT]),
            fov_right=float(cfg[CONF_FOV_RIGHT]),
            min_elevation=float(cfg[CONF_MIN_ELEVATION]),
            max_elevation=float(cfg[CONF_MAX_ELEVATION]),
            horizon=eng.parse_horizon(cfg.get(CONF_HORIZON)),
            window_height=float(cfg[CONF_WINDOW_HEIGHT]),
            sill_height=float(cfg[CONF_SILL_HEIGHT]),
            shade_depth=float(cfg[CONF_SHADE_DEPTH]),
            slat_width=float(cfg[CONF_SLAT_WIDTH]),
            slat_spacing=float(cfg[CONF_SLAT_SPACING]),
        ),
        positions=eng.Positions(
            default_position=int(cfg[CONF_DEFAULT_POSITION]),
            default_tilt=_opt_int(cfg.get(CONF_DEFAULT_TILT)),
            heat_position=int(cfg[CONF_HEAT_POSITION]),
            shade_position=int(cfg[CONF_SHADE_POSITION]),
            shade_min_position=int(cfg[CONF_SHADE_MIN_POSITION]),
            away_position=int(cfg[CONF_AWAY_POSITION]),
            lockout_position=int(cfg[CONF_LOCKOUT_POSITION]),
            safety_position=_opt_int(cfg.get(CONF_SAFETY_POSITION)),
            step=int(cfg[CONF_POSITION_STEP]),
        ),
        tilt_mode=eng.TiltMode(cfg[CONF_TILT_MODE]),
        tilt_invert=bool(cfg[CONF_TILT_INVERT]),
        tilt_margin=float(cfg[CONF_TILT_MARGIN]),
        glare_protection=bool(cfg[CONF_GLARE]),
    )


class BlindCoordinator(DataUpdateCoordinator[BlindState]):
    """Evaluates the engine periodically and on sensor changes, then moves the cover."""

    config_entry: BlindConfigEntry

    def __init__(self, hass: HomeAssistant, entry: BlindConfigEntry) -> None:
        cfg: dict[str, Any] = {**DEFAULTS, **entry.data, **entry.options}
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=entry.title,
            update_interval=timedelta(minutes=float(cfg[CONF_UPDATE_INTERVAL])),
            request_refresh_debouncer=Debouncer(hass, _LOGGER, cooldown=3, immediate=True),
        )
        self.cfg = cfg
        self.cover: str = cfg[CONF_COVER]
        self.engine = eng.Engine(build_engine_config(cfg))
        self.climate_cfg = eng.ClimateConfig(
            heat_setpoint=float(cfg[CONF_HEAT_SETPOINT]),
            cool_setpoint=float(cfg[CONF_COOL_SETPOINT]),
            outdoor_heat_below=float(cfg[CONF_OUTDOOR_HEAT_BELOW]),
            outdoor_cool_above=float(cfg[CONF_OUTDOOR_COOL_ABOVE]),
            hysteresis=float(cfg[CONF_HYSTERESIS]),
        )
        self.rules: list[Rule] = [Rule.from_dict(r) for r in cfg.get(CONF_RULES, [])]
        self._templates: dict[str, Template] = {
            r.id: Template(r.condition, hass) for r in self.rules if r.condition
        }

        # Runtime state (switch/select entities restore and set these).
        self.enabled = True
        self.strategy = eng.Strategy.AUTO
        self.override_until: datetime | None = None
        self._climate = eng.Climate.NEUTRAL
        self._safety_until: datetime | None = None
        self._started = False
        self._last_key: tuple[str, str | None] | None = None
        self._last_move: datetime | None = None
        self._target: tuple[int | None, int | None] | None = None
        self._contexts: deque[str] = deque(maxlen=16)
        self._settle_until = 0.0
        self._move_task: asyncio.Task[None] | None = None
        self._unsub_wakeup: CALLBACK_TYPE | None = None
        self._wakeup_at: datetime | None = None
        self._sun_cache: dict[date, tuple[datetime | None, datetime | None]] = {}
        self._forecast: tuple[datetime, datetime | None, datetime | None] | None = None

    # ------------------------------------------------------------------ #
    # Setup / teardown
    # ------------------------------------------------------------------ #

    @callback
    def async_setup_listeners(self) -> None:
        """Subscribe to the cover and all input sensors."""
        entry = self.config_entry
        entry.async_on_unload(
            async_track_state_change_event(self.hass, [self.cover], self._on_cover_change)
        )
        inputs = [
            self.cfg.get(key)
            for key in (
                CONF_INDOOR_TEMP,
                CONF_OUTDOOR_TEMP,
                CONF_WEATHER,
                CONF_LUX,
                CONF_PRESENCE,
                CONF_WIND,
                CONF_RAIN,
                CONF_CONTACT,
            )
        ]
        if watched := [e for e in inputs if e]:
            entry.async_on_unload(
                async_track_state_change_event(self.hass, watched, self._on_input_change)
            )
        entry.async_on_unload(async_at_started(self.hass, self._on_started))
        entry.async_on_unload(self._cancel_tasks)

    @callback
    def _on_started(self, _hass: HomeAssistant) -> None:
        self._started = True
        self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _cancel_tasks(self) -> None:
        if self._unsub_wakeup:
            self._unsub_wakeup()
            self._unsub_wakeup = None
        if self._move_task and not self._move_task.done():
            self._move_task.cancel()

    # ------------------------------------------------------------------ #
    # Public controls (entities / services)
    # ------------------------------------------------------------------ #

    async def async_set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.override_until = None
        self._last_key = None  # re-apply immediately
        await self.async_request_refresh()

    async def async_set_strategy(self, strategy: str) -> None:
        self.strategy = eng.Strategy(strategy)
        await self.async_request_refresh()

    async def async_resume(self) -> None:
        self.override_until = None
        self._last_key = None
        await self.async_request_refresh()

    async def async_pause(self, minutes: float) -> None:
        self.override_until = dt_util.now() + timedelta(minutes=minutes)
        await self.async_request_refresh()

    async def async_recalculate(self) -> None:
        self._last_key = None
        self._forecast = None
        await self.async_request_refresh()

    # ------------------------------------------------------------------ #
    # Inputs
    # ------------------------------------------------------------------ #

    def _state(self, key: str) -> State | None:
        entity_id = self.cfg.get(key)
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        return None if state is None or state.state in INVALID else state

    def _number(self, key: str) -> float | None:
        if (state := self._state(key)) is None:
            return None
        if state.domain == "weather":
            raw = state.attributes.get("temperature")
        elif state.domain == "climate":
            raw = state.attributes.get("current_temperature")
        else:
            raw = state.state
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def _is_on(self, key: str) -> bool:
        if (state := self._state(key)) is None:
            return False
        if state.state == STATE_ON:
            return True
        try:
            return float(state.state) > 0
        except ValueError:
            return False

    def _bright(self) -> bool:
        if self.cfg.get(CONF_LUX):
            lux = self._number(CONF_LUX)
            return lux is None or lux >= float(self.cfg[CONF_LUX_THRESHOLD])
        if (weather := self._state(CONF_WEATHER)) is not None:
            return weather.state in SUNNY_WEATHER
        return True

    def _present(self) -> bool | None:
        if (state := self._state(CONF_PRESENCE)) is None:
            return None
        if state.domain == "zone":
            try:
                return int(state.state) > 0
            except ValueError:
                return None
        return state.state in ("home", STATE_ON)

    def _safety(self, now: datetime) -> bool:
        wind = self._number(CONF_WIND)
        alarm = (wind is not None and wind >= float(self.cfg[CONF_WIND_THRESHOLD])) or self._is_on(
            CONF_RAIN
        )
        if alarm:
            self._safety_until = now + timedelta(minutes=float(self.cfg[CONF_SAFETY_RELEASE]))
        return self._safety_until is not None and now < self._safety_until

    def _sun_times(self, day: date) -> tuple[datetime | None, datetime | None]:
        if (cached := self._sun_cache.get(day)) is None:
            if len(self._sun_cache) > 8:
                self._sun_cache.clear()
            rise = get_astral_event_date(self.hass, SUN_EVENT_SUNRISE, day)
            sett = get_astral_event_date(self.hass, SUN_EVENT_SUNSET, day)
            cached = (
                dt_util.as_local(rise) if rise else None,
                dt_util.as_local(sett) if sett else None,
            )
            self._sun_cache[day] = cached
        return cached

    def _sun_position(self, when: datetime) -> tuple[float, float]:
        location, elevation = get_astral_location(self.hass)  # cached by HA
        return (
            location.solar_azimuth(when, elevation),
            location.solar_elevation(when, elevation),
        )

    def _condition_ok(self, rule: Rule) -> bool:
        try:
            return result_as_boolean(self._templates[rule.id].async_render(parse_result=False))
        except TemplateError as err:
            _LOGGER.warning("%s: condition of rule '%s' failed: %s", self.name, rule.name, err)
            return False

    def _sun_forecast(self, now: datetime) -> tuple[datetime | None, datetime | None]:
        """When does the sun next enter / leave this window (scan 24 h, cached)."""
        if self._forecast and now - self._forecast[0] < FORECAST_TTL:
            return self._forecast[1], self._forecast[2]
        geo = self.engine.cfg.geometry
        enters = leaves = None
        inside = eng.sun_in_window(geo, *self._sun_position(now))
        t = now
        for _ in range(int(timedelta(days=1) / FORECAST_STEP)):
            t += FORECAST_STEP
            now_inside = eng.sun_in_window(geo, *self._sun_position(t))
            if inside and not now_inside:
                leaves = t
                break
            if not inside and now_inside:
                enters, inside = t, True
        self._forecast = (now, enters, leaves)
        return enters, leaves

    # ------------------------------------------------------------------ #
    # Evaluation
    # ------------------------------------------------------------------ #

    async def _async_update_data(self) -> BlindState:
        now = dt_util.now()
        if self.override_until and now >= self.override_until:
            self.override_until = None
            self._last_key = None  # return to the automatic target right away

        azimuth, elevation = self._sun_position(now)
        self._climate = eng.climate_state(
            self._climate, self._number(CONF_INDOOR_TEMP), self._number(CONF_OUTDOOR_TEMP), self.climate_cfg
        )
        rule = active_rule(self.rules, now, self._sun_times, self._condition_ok)
        safety = self._safety(now)

        decision = self.engine.decide(
            eng.Inputs(
                azimuth=azimuth,
                elevation=elevation,
                climate=self._climate,
                strategy=self.strategy,
                bright=self._bright(),
                present=self._present(),
                safety=safety,
                lockout=self._is_on(CONF_CONTACT),
                override=self.override_until is not None,
                rule=eng.RuleTarget(eng.Action(rule.action), rule.position, rule.tilt, rule.name)
                if rule
                else None,
            )
        )
        if not self.enabled:
            decision = eng.Decision(
                None, None, eng.Reason.DISABLED, decision.rule, False, decision.sun_in_window, decision.profile_angle
            )

        nxt = next_boundary(self.rules, now, self._sun_times)
        self._schedule_wakeup(now, nxt)
        enters, leaves = self._sun_forecast(now)
        await self._async_apply(decision, now)

        return BlindState(
            decision=decision,
            azimuth=round(azimuth, 1),
            elevation=round(elevation, 1),
            climate=self._climate,
            rule=rule.name if rule else None,
            override_until=self.override_until,
            safety=safety,
            next_change=nxt,
            sun_enters=enters,
            sun_leaves=leaves,
        )

    def _schedule_wakeup(self, now: datetime, boundary: datetime | None) -> None:
        """Wake up exactly at the next rule boundary / override or safety expiry."""
        candidates = [t for t in (boundary, self.override_until, self._safety_until) if t and t > now]
        wake = min(candidates, default=None)
        if wake == self._wakeup_at:
            return
        if self._unsub_wakeup:
            self._unsub_wakeup()
            self._unsub_wakeup = None
        self._wakeup_at = wake
        if wake is not None:
            self._unsub_wakeup = async_track_point_in_time(
                self.hass, self._on_wakeup, wake + timedelta(seconds=1)
            )

    @callback
    def _on_wakeup(self, _now: datetime) -> None:
        self._unsub_wakeup = None
        self._wakeup_at = None
        self.hass.async_create_task(self.async_request_refresh())

    # ------------------------------------------------------------------ #
    # Actuation
    # ------------------------------------------------------------------ #

    def _read_cover(self, state: State) -> tuple[int | None, int | None]:
        pos = state.attributes.get(ATTR_CURRENT_POSITION)
        if pos is None:
            pos = 100 if state.state == CoverState.OPEN else 0
        if self.cfg[CONF_INVERT_POSITION]:
            pos = 100 - pos
        return pos, state.attributes.get(ATTR_CURRENT_TILT_POSITION)

    async def _async_apply(self, decision: eng.Decision, now: datetime) -> None:
        if decision.position is None and decision.tilt is None:
            if decision.reason is not eng.Reason.MANUAL_OVERRIDE:
                self._target = None  # nothing to defend while skipped / disabled
            return
        key =(decision.reason.value, decision.rule)
        forced = decision.forced or key != self._last_key
        if not forced and self._last_move and now - self._last_move < timedelta(
            minutes=float(self.cfg[CONF_MIN_INTERVAL])
        ):
            return

        state = self.hass.states.get(self.cover)
        if state is None or state.state in INVALID or state.state in MOVING:
            return
        cur_pos, cur_tilt = self._read_cover(state)
        delta = 1 if forced else int(self.cfg[CONF_MIN_DELTA])
        set_pos = decision.position is not None and (cur_pos is None or abs(cur_pos - decision.position) >= delta)
        set_tilt = decision.tilt is not None and (cur_tilt is None or abs(cur_tilt - decision.tilt) >= delta)

        self._last_key = key
        self._target = (decision.position, decision.tilt)
        if not (set_pos or set_tilt):
            return
        if self.cfg[CONF_DRY_RUN] or not self._started:
            _LOGGER.debug("%s: would move to %s (%s)", self.name, self._target, decision.reason)
            return

        self._last_move = now
        if self._move_task and not self._move_task.done():
            self._move_task.cancel()
        self._move_task = self.config_entry.async_create_background_task(
            self.hass,
            self._async_move(state, decision.position if set_pos else None, decision.tilt if set_tilt else None),
            f"{DOMAIN} move {self.cover}",
        )

    async def _async_call(self, service: str, data: dict[str, Any] | None = None) -> None:
        context = Context()
        self._contexts.append(context.id)
        self._settle_until = monotonic_time.monotonic() + float(self.cfg[CONF_SETTLE_TIME])
        try:
            await self.hass.services.async_call(
                COVER_DOMAIN,
                service,
                {ATTR_ENTITY_ID: self.cover, **(data or {})},
                blocking=True,
                context=context,
            )
        except HomeAssistantError as err:
            _LOGGER.warning("%s: %s failed: %s", self.name, service, err)

    async def _async_move(self, state: State, position: int | None, tilt: int | None) -> None:
        features = state.attributes.get(ATTR_SUPPORTED_FEATURES, 0)
        _LOGGER.debug("%s: moving to position=%s tilt=%s", self.name, position, tilt)
        if position is not None:
            if self.cfg[CONF_INVERT_POSITION]:
                position = 100 - position
            if features & CoverEntityFeature.SET_POSITION:
                await self._async_call(SERVICE_SET_COVER_POSITION, {ATTR_POSITION: position})
            else:
                await self._async_call(SERVICE_OPEN_COVER if position >= 50 else SERVICE_CLOSE_COVER)
            if tilt is not None:
                await self._async_wait_stopped()
        if tilt is not None and features & CoverEntityFeature.SET_TILT_POSITION:
            await self._async_call(SERVICE_SET_COVER_TILT_POSITION, {ATTR_TILT_POSITION: tilt})

    async def _async_wait_stopped(self, timeout: float = 180) -> None:
        """Venetians reset their slats while moving: wait for the travel to finish."""
        await asyncio.sleep(3)
        stopped = asyncio.Event()

        @callback
        def _changed(event: Event[EventStateChangedData]) -> None:
            new = event.data["new_state"]
            if new is None or new.state not in MOVING:
                stopped.set()

        unsub = async_track_state_change_event(self.hass, [self.cover], _changed)
        try:
            current = self.hass.states.get(self.cover)
            if current is not None and current.state in MOVING:
                async with asyncio.timeout(timeout):
                    await stopped.wait()
            await asyncio.sleep(1)
        except TimeoutError:
            _LOGGER.debug("%s: cover still moving after %ss", self.name, timeout)
        finally:
            unsub()

    # ------------------------------------------------------------------ #
    # Event handlers
    # ------------------------------------------------------------------ #

    @callback
    def _on_input_change(self, _event: Event[EventStateChangedData]) -> None:
        self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _on_cover_change(self, event: Event[EventStateChangedData]) -> None:
        """Detect manual operation (wall switch, app, remote) and pause automation."""
        new = event.data["new_state"]
        if new is None or new.state in INVALID or not self.enabled:
            return
        ctx = new.context
        ours = ctx.id in self._contexts or (ctx.parent_id is not None and ctx.parent_id in self._contexts)
        settling = monotonic_time.monotonic() < self._settle_until
        if new.state in MOVING:
            if settling:  # slow covers: keep the grace window open while moving
                self._settle_until = max(self._settle_until, monotonic_time.monotonic() + 30)
            return
        if ours or settling or self._target is None or self.override_until is not None:
            return

        pos, tilt = self._read_cover(new)
        tol = int(self.cfg[CONF_OVERRIDE_TOLERANCE])
        want_pos, want_tilt = self._target
        moved = (want_pos is not None and pos is not None and abs(pos - want_pos) > tol) or (
            want_tilt is not None and tilt is not None and abs(tilt - want_tilt) > tol
        )
        if not moved:
            return

        now = dt_util.now()
        until = now + timedelta(minutes=float(self.cfg[CONF_OVERRIDE_MINUTES]))
        if self.cfg[CONF_OVERRIDE_UNTIL_RULE] and (
            boundary := next_boundary(self.rules, now, self._sun_times)
        ):
            until = min(until, boundary)
        _LOGGER.info("%s: manual change detected, pausing until %s", self.name, until)
        self.override_until = until
        self.hass.async_create_task(self.async_request_refresh())
