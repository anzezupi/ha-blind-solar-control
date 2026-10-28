"""Runtime controller for one blind."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
import logging
from typing import Any, NamedTuple

from homeassistant.components.climate.const import (
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
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
    ATTR_TEMPERATURE,
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

from . import engine as eng, thermal
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
    CONF_EXTERIOR,
    CONF_FOV_LEFT,
    CONF_FOV_RIGHT,
    CONF_G_VALUE,
    CONF_GLARE,
    CONF_GLAZING,
    CONF_HEAT_POSITION,
    CONF_HEAT_PRICE,
    CONF_HEAT_SETPOINT,
    CONF_HORIZON,
    CONF_HYSTERESIS,
    CONF_INDOOR_TEMP,
    CONF_INSULATE_AWAY,
    CONF_INVERT_POSITION,
    CONF_IRRADIANCE,
    CONF_LOCKOUT_POSITION,
    CONF_LUX,
    CONF_LUX_THRESHOLD,
    CONF_MAX_ELEVATION,
    CONF_MAX_SETBACK,
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
    CONF_PREDICT_HOURS,
    CONF_PREDICTIVE,
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
    CONF_THERMOSTAT,
    CONF_THERMOSTAT_MODE,
    CONF_TILT_INVERT,
    CONF_TILT_MARGIN,
    CONF_TILT_MODE,
    CONF_U_VALUE,
    CONF_UPDATE_INTERVAL,
    CONF_VENT_DELTA,
    CONF_WEATHER,
    CONF_WIND,
    CONF_WIND_THRESHOLD,
    CONF_WINDOW_HEIGHT,
    CONF_WINDOW_WIDTH,
    DEFAULTS,
    DOMAIN,
)
from .energy import Counters, EnergyTracker
from .forecast import Forecast
from .schedule import Rule, active_rule, next_boundary

_LOGGER = logging.getLogger(__name__)

MOVING = (CoverState.OPENING, CoverState.CLOSING)
INVALID = (STATE_UNAVAILABLE, STATE_UNKNOWN, None)
SUNNY_WEATHER = frozenset({"sunny", "partlycloudy", "exceptional"})
WINDOW_SCAN_STEP = timedelta(minutes=5)
CACHE_TTL = timedelta(minutes=30)
QUARTER = timedelta(minutes=15)

type BlindConfigEntry = ConfigEntry[BlindCoordinator]


class _SolarInputs(NamedTuple):
    cloud: float  # %
    gain: float  # W entering now
    potential: float  # W if fully open
    saving: float  # W of heat loss avoided by the closed blind
    expected: float  # kWh in the look-ahead window (fully open)
    t_max: float | None  # forecast high today
    t_pred: float | None  # predicted room temperature


@dataclass(slots=True)
class SolarState:
    """Energy view of the window."""

    cloud: float
    gain: float  # W entering the room now
    potential: float  # W if the blind were fully open
    today: Counters
    savings: float  # money saved today
    expected: float  # kWh expected in the next hours (fully open)
    forecast_today: float  # kWh still to come today (fully open)
    predicted_temp: float | None
    suggested_setpoint: float | None
    ventilate: bool | None
    sensitivity: float  # learned °C per kWh
    loss_rate: float  # learned 1/h
    samples: int
    trained: bool
    hourly: list[dict[str, Any]]
    sun_path: list[tuple[float, float]]


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
    solar: SolarState


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
        insulate_when_away=bool(cfg[CONF_INSULATE_AWAY]),
    )


def build_window(cfg: dict[str, Any]) -> thermal.Window:
    return thermal.Window.build(
        width=float(cfg[CONF_WINDOW_WIDTH]),
        height=float(cfg[CONF_WINDOW_HEIGHT]),
        glazing=cfg[CONF_GLAZING],
        blind_type=eng.BlindType(cfg[CONF_BLIND_TYPE]),
        exterior=bool(cfg[CONF_EXTERIOR]),
        g_value=None if cfg.get(CONF_G_VALUE) is None else float(cfg[CONF_G_VALUE]),
        u_value=None if cfg.get(CONF_U_VALUE) is None else float(cfg[CONF_U_VALUE]),
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
            request_refresh_debouncer=Debouncer(hass, _LOGGER, cooldown=10, immediate=True),
        )
        self.cfg = cfg
        self.cover: str = cfg[CONF_COVER]
        self.engine = eng.Engine(build_engine_config(cfg))
        self.window = build_window(cfg)
        self.energy = EnergyTracker(hass, entry.entry_id)
        self.forecast = Forecast(hass, cfg.get(CONF_WEATHER))
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
        self._settle_until = dt_util.utcnow()
        self._move_task: asyncio.Task[None] | None = None
        self._unsub_wakeup: CALLBACK_TYPE | None = None
        self._wakeup_at: datetime | None = None
        self._sun_cache: dict[date, tuple[datetime | None, datetime | None]] = {}
        self._window_cache: tuple[datetime, datetime | None, datetime | None] | None = None
        self._hourly_cache: tuple[datetime, list[dict[str, Any]]] | None = None
        self._path_cache: tuple[date, list[tuple[float, float]]] | None = None
        self._sent_setpoint: float | None = None

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
    # User actions refresh immediately; async_refresh cancels any debounced refresh.

    async def async_set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.override_until = None
        self._last_key = None  # re-apply immediately
        await self.async_refresh()

    async def async_set_strategy(self, strategy: str) -> None:
        self.strategy = eng.Strategy(strategy)
        await self.async_refresh()

    async def async_resume(self) -> None:
        self.override_until = None
        self._last_key = None
        await self.async_refresh()

    async def async_pause(self, minutes: float) -> None:
        self.override_until = dt_util.now() + timedelta(minutes=minutes)
        await self.async_refresh()

    async def async_recalculate(self) -> None:
        self._last_key = None
        self._window_cache = self._hourly_cache = None
        self.forecast.fetched = None
        await self.async_refresh()

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
        if self._window_cache and now - self._window_cache[0] < CACHE_TTL:
            return self._window_cache[1], self._window_cache[2]
        geo = self.engine.cfg.geometry
        enters = leaves = None
        inside = eng.sun_in_window(geo, *self._sun_position(now))
        t = now
        for _ in range(int(timedelta(days=1) / WINDOW_SCAN_STEP)):
            t += WINDOW_SCAN_STEP
            now_inside = eng.sun_in_window(geo, *self._sun_position(t))
            if inside and not now_inside:
                leaves = t
                break
            if not inside and now_inside:
                enters, inside = t, True
        self._window_cache = (now, enters, leaves)
        return enters, leaves

    # ------------------------------------------------------------------ #
    # Solar energy
    # ------------------------------------------------------------------ #

    def _cloud_now(self, elevation: float) -> float:
        """Current cloud cover in %, from the best source available."""
        if (weather := self._state(CONF_WEATHER)) is not None:
            if (cloud := weather.attributes.get("cloud_coverage")) is not None:
                return float(cloud)
            return thermal.CONDITION_CLOUDS.get(weather.state, 50.0)
        measured, clear = self._number(CONF_IRRADIANCE), thermal.ghi_clear(elevation)
        if measured is not None and clear > 50:
            # Invert Kasten-Czeplak to estimate clouds from a pyranometer.
            ratio = min(1.0, max(0.0, (1 - measured / clear) / 0.75))
            return 100.0 * ratio ** (1 / 3.4)
        if self.cfg.get(CONF_LUX):
            return 0.0 if self._bright() else 80.0
        return 0.0

    def _open_fraction(self) -> float:
        state = self.hass.states.get(self.cover)
        if state is None or state.state in INVALID:
            return 1.0
        pos, tilt = self._read_cover(state)
        cfg = self.engine.cfg
        return thermal.open_fraction(cfg.blind_type, pos, tilt, cfg.tilt_mode, cfg.tilt_invert)

    def _potential_kwh(self, start: datetime, hours: float, cloud_now: float) -> float:
        """Solar heat a fully open window would collect, using the forecast clouds."""
        location, observer = get_astral_location(self.hass)
        geo = self.engine.cfg.geometry
        total = 0.0
        for i in range(max(1, round(hours * 4))):
            t = start + QUARTER * (i + 0.5)
            elevation = location.solar_elevation(t, observer)
            if elevation <= 0:
                continue
            ghi = thermal.ghi_cloudy(elevation, self.forecast.cloud_at(t, cloud_now))
            irr = thermal.window_irradiance(geo, location.solar_azimuth(t, observer), elevation, ghi)
            total += self.window.gain(irr) / 4000  # W over 15 min -> kWh
        return total

    def _hourly(self, now: datetime, cloud_now: float) -> list[dict[str, Any]]:
        """Expected kWh per hour for the next 24 h (cached, feeds the card)."""
        if self._hourly_cache and now - self._hourly_cache[0] < CACHE_TTL:
            return self._hourly_cache[1]
        first = now.replace(minute=0, second=0, microsecond=0)
        hourly = []
        for h in range(24):
            hour = first + timedelta(hours=h)
            hourly.append({"time": hour.isoformat(), "kwh": round(self._potential_kwh(hour, 1, cloud_now), 3)})
        self._hourly_cache = (now, hourly)
        return hourly

    def _sun_path(self, now: datetime) -> list[tuple[float, float]]:
        """Today's sun path as (azimuth, elevation) every 15 min (for the card)."""
        if self._path_cache and self._path_cache[0] == now.date():
            return self._path_cache[1]
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        path = []
        for i in range(96):
            az, el = self._sun_position(start + QUARTER * i)
            if el > -1:
                path.append((round(az, 1), round(el, 1)))
        self._path_cache = (now.date(), path)
        return path

    def _solar_inputs(
        self, now: datetime, azimuth: float, elevation: float, t_in: float | None, t_out: float | None
    ) -> _SolarInputs:
        """Current solar powers plus what the forecast and room model expect."""
        cfg = self.cfg
        cloud = self._cloud_now(elevation)
        measured = self._number(CONF_IRRADIANCE)
        ghi = max(0.0, measured) if measured is not None else thermal.ghi_cloudy(elevation, cloud)
        irr = thermal.window_irradiance(self.engine.cfg.geometry, azimuth, elevation, ghi)
        open_frac = self._open_fraction()
        potential = self.window.gain(irr)
        gain = self.window.gain(irr, open_frac)
        saving = 0.0
        if t_in is not None and t_out is not None:
            saving = self.window.insulation_saving(t_in, t_out, 1.0 - open_frac)

        hours = float(cfg[CONF_PREDICT_HOURS])
        expected = self._potential_kwh(now, hours, cloud)
        t_max = t_pred = None
        if cfg[CONF_PREDICTIVE]:
            t_max = self.forecast.max_temp_today(now)
            t_avg = self.forecast.avg_temp(now, hours)
            if t_avg is None:
                t_avg = t_out
            if t_in is not None and t_avg is not None:
                t_pred = self.energy.model.predict(t_in, t_avg, expected, hours)
        return _SolarInputs(cloud, gain, potential, saving, expected, t_max, t_pred)

    def _solar_state(self, now: datetime, sol: _SolarInputs, t_in: float | None, t_out: float | None) -> SolarState:
        """Book the energy, feed the room model and assemble the entity view."""
        cfg, climate_cfg = self.cfg, self.climate_cfg
        heating_season = self._climate is eng.Climate.HEAT or (
            t_out is not None and t_out < climate_cfg.outdoor_heat_below
        )
        cooling = self._climate in (eng.Climate.COOL, eng.Climate.PRECOOL)
        kwh = self.energy.add(now, sol.gain, sol.potential - sol.gain, sol.saving, heating_season, cooling)
        self.energy.learn(now, t_in, t_out, kwh)

        hourly = self._hourly(now, sol.cloud)
        remaining = 1 - now.minute / 60  # the first bucket is the current, partly elapsed hour
        forecast_today = sum(
            h["kwh"] * (remaining if i == 0 else 1)
            for i, h in enumerate(hourly)
            if datetime.fromisoformat(h["time"]).date() == now.date()
        )
        model = self.energy.model
        suggested = None
        if cfg.get(CONF_THERMOSTAT) and cfg[CONF_THERMOSTAT_MODE] != "off":
            suggested = climate_cfg.heat_setpoint
            if heating_season:
                suggested = thermal.setpoint_suggestion(
                    suggested, sol.expected, model.sensitivity, float(cfg[CONF_MAX_SETBACK])
                )
        ventilate = None
        if t_in is not None and t_out is not None:
            ventilate = t_in >= climate_cfg.cool_setpoint - 1 and t_out <= t_in - float(cfg[CONF_VENT_DELTA])

        today = self.energy.today
        return SolarState(
            cloud=round(sol.cloud),
            gain=round(sol.gain),
            potential=round(sol.potential),
            today=replace(today),
            savings=round((today.useful + today.insulation) * float(cfg[CONF_HEAT_PRICE]), 2),
            expected=round(sol.expected, 2),
            forecast_today=round(forecast_today, 2),
            predicted_temp=None if sol.t_pred is None else round(sol.t_pred, 1),
            suggested_setpoint=suggested,
            ventilate=ventilate,
            sensitivity=round(model.sensitivity, 2),
            loss_rate=round(model.loss_rate, 3),
            samples=model.samples,
            trained=model.trained,
            hourly=hourly,
            sun_path=self._sun_path(now),
        )

    async def _async_thermostat(self, setpoint: float | None) -> None:
        """Lower the heating setpoint while the sun is expected to do the work."""
        entity_id = self.cfg.get(CONF_THERMOSTAT)
        if setpoint is None or not entity_id or setpoint == self._sent_setpoint:
            return
        state = self.hass.states.get(entity_id)
        if state is None or state.state != HVACMode.HEAT:  # never touch cooling / off
            return
        self._sent_setpoint = setpoint
        if state.attributes.get(ATTR_TEMPERATURE) == setpoint:
            return
        try:
            await self.hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_TEMPERATURE,
                {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: setpoint},
                blocking=True,
            )
        except HomeAssistantError as err:
            _LOGGER.warning("%s: setting thermostat failed: %s", self.name, err)

    # ------------------------------------------------------------------ #
    # Evaluation
    # ------------------------------------------------------------------ #

    async def _async_update_data(self) -> BlindState:
        now = dt_util.now()
        if self.override_until and now >= self.override_until:
            self.override_until = None
            self._last_key = None  # return to the automatic target right away

        cfg = self.cfg
        azimuth, elevation = self._sun_position(now)
        t_in, t_out = self._number(CONF_INDOOR_TEMP), self._number(CONF_OUTDOOR_TEMP)
        if await self.forecast.async_update(now):
            self._hourly_cache = None
        sol = self._solar_inputs(now, azimuth, elevation, t_in, t_out)
        self._climate = eng.climate_state(self._climate, t_in, t_out, self.climate_cfg, sol.t_max, sol.t_pred)
        solar = self._solar_state(now, sol, t_in, t_out)

        rule =active_rule(self.rules, now, self._sun_times, self._condition_ok)
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
        if self.enabled and self._started and not cfg[CONF_DRY_RUN] and cfg[CONF_THERMOSTAT_MODE] == "control":
            await self._async_thermostat(solar.suggested_setpoint)

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
            solar=solar,
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
        self._settle_until = dt_util.utcnow() + timedelta(seconds=float(self.cfg[CONF_SETTLE_TIME]))
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
        settling = dt_util.utcnow() < self._settle_until
        if new.state in MOVING:
            if settling:  # slow covers: keep the grace window open while moving
                self._settle_until = max(self._settle_until, dt_util.utcnow() + timedelta(seconds=30))
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
        self.hass.async_create_task(self.async_refresh())
