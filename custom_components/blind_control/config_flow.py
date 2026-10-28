"""Config and options flow for Blind Control."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME, DEGREE, PERCENTAGE, UnitOfLength, UnitOfTime
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
    TextSelector,
    TimeSelector,
)
import voluptuous as vol

from .const import (
    ACTIONS,
    BLIND_TYPES,
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
    GLAZINGS,
    RULE_ACTION,
    RULE_CONDITION,
    RULE_DAYS,
    RULE_END_OFFSET,
    RULE_END_TIME,
    RULE_END_TYPE,
    RULE_ID,
    RULE_NAME,
    RULE_POSITION,
    RULE_PRIORITY,
    RULE_START_OFFSET,
    RULE_START_TIME,
    RULE_START_TYPE,
    RULE_TILT,
    THERMOSTAT_MODES,
    TILT_MODES,
    TIME_KINDS,
    TYPE_DEFAULTS,
)
from .schedule import DAYS, default_rules

TILT_TYPES = ("venetian", "tilt_only")

# --------------------------------------------------------------------------- #
# Selector helpers
# --------------------------------------------------------------------------- #


def _num(lo: float, hi: float, step: float = 1, unit: str | None = None, slider: bool = False) -> NumberSelector:
    config = NumberSelectorConfig(
        min=lo, max=hi, step=step, mode=NumberSelectorMode.SLIDER if slider else NumberSelectorMode.BOX
    )
    if unit:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


def _pct() -> NumberSelector:
    return _num(0, 100, 1, PERCENTAGE, slider=True)


def _entity(*domains: str) -> EntitySelector:
    return EntitySelector(EntitySelectorConfig(domain=list(domains)))


def _select(options: tuple[str, ...], key: str, multiple: bool = False) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=list(options),
            translation_key=key,
            multiple=multiple,
            mode=SelectSelectorMode.LIST if multiple else SelectSelectorMode.DROPDOWN,
        )
    )


def _req(key: str, values: dict[str, Any]) -> vol.Required:
    return vol.Required(key, default=values.get(key, DEFAULTS.get(key)))


def _opt(key: str, values: dict[str, Any]) -> vol.Optional:
    """Optional field that can be cleared (suggested value instead of default)."""
    return vol.Optional(key, description={"suggested_value": values.get(key)})


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


def base_schema(values: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=values.get(CONF_NAME, "")): TextSelector(),
            vol.Required(CONF_COVER, default=values.get(CONF_COVER)): _entity("cover"),
            vol.Required(CONF_BLIND_TYPE, default=values.get(CONF_BLIND_TYPE, "roller_blind")): _select(
                BLIND_TYPES, "blind_type"
            ),
        }
    )


def geometry_schema(blind_type: str, v: dict[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {
        _req(CONF_AZIMUTH, v): _num(0, 359, 1, DEGREE),
        _req(CONF_FOV_LEFT, v): _num(0, 90, 1, DEGREE),
        _req(CONF_FOV_RIGHT, v): _num(0, 90, 1, DEGREE),
        _req(CONF_MIN_ELEVATION, v): _num(0, 90, 1, DEGREE),
        _req(CONF_MAX_ELEVATION, v): _num(0, 90, 1, DEGREE),
        _opt(CONF_HORIZON, v): TextSelector(),
        _req(CONF_WINDOW_WIDTH, v): _num(0.1, 10, 0.05, UnitOfLength.METERS),
        _req(CONF_WINDOW_HEIGHT, v): _num(0.1, 6, 0.05, UnitOfLength.METERS),
        _req(CONF_SILL_HEIGHT, v): _num(0, 5, 0.05, UnitOfLength.METERS),
        _req(CONF_SHADE_DEPTH, v): _num(0, 5, 0.05, UnitOfLength.METERS),
        _req(CONF_GLAZING, v): _select(GLAZINGS, "glazing"),
        _opt(CONF_G_VALUE, v): _num(0.1, 0.9, 0.01),
        _opt(CONF_U_VALUE, v): _num(0.3, 6, 0.1, "W/m²K"),
        _req(CONF_EXTERIOR, v): BooleanSelector(),
    }
    if blind_type in TILT_TYPES:
        fields |= {
            _req(CONF_TILT_MODE, v): _select(TILT_MODES, "tilt_mode"),
            _req(CONF_TILT_INVERT, v): BooleanSelector(),
            _req(CONF_SLAT_WIDTH, v): _num(5, 300, 1, UnitOfLength.MILLIMETERS),
            _req(CONF_SLAT_SPACING, v): _num(5, 300, 1, UnitOfLength.MILLIMETERS),
            _req(CONF_TILT_MARGIN, v): _num(0, 30, 1, DEGREE),
        }
    return vol.Schema(fields)


def climate_schema(v: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            _opt(CONF_INDOOR_TEMP, v): _entity("sensor", "climate"),
            _opt(CONF_OUTDOOR_TEMP, v): _entity("sensor", "weather"),
            _opt(CONF_WEATHER, v): _entity("weather"),
            _opt(CONF_LUX, v): _entity("sensor"),
            _req(CONF_LUX_THRESHOLD, v): _num(0, 200000, 100, "lx"),
            _opt(CONF_IRRADIANCE, v): _entity("sensor"),
            _opt(CONF_PRESENCE, v): _entity(
                "person", "zone", "group", "binary_sensor", "input_boolean", "device_tracker"
            ),
            _req(CONF_HEAT_SETPOINT, v): _num(5, 35, 0.5, "°C"),
            _req(CONF_COOL_SETPOINT, v): _num(5, 40, 0.5, "°C"),
            _req(CONF_OUTDOOR_HEAT_BELOW, v): _num(-30, 45, 0.5, "°C"),
            _req(CONF_OUTDOOR_COOL_ABOVE, v): _num(-30, 50, 0.5, "°C"),
            _req(CONF_HYSTERESIS, v): _num(0, 3, 0.1, "°C"),
            _req(CONF_GLARE, v): BooleanSelector(),
        }
    )


def positions_schema(blind_type: str, v: dict[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {}
    if blind_type != "tilt_only":
        fields |= {
            _req(CONF_DEFAULT_POSITION, v): _pct(),
            _req(CONF_HEAT_POSITION, v): _pct(),
            _req(CONF_SHADE_POSITION, v): _pct(),
            _req(CONF_SHADE_MIN_POSITION, v): _pct(),
            _req(CONF_AWAY_POSITION, v): _pct(),
            _req(CONF_POSITION_STEP, v): _num(1, 25, 1, PERCENTAGE),
        }
    if blind_type in TILT_TYPES:
        fields[_opt(CONF_DEFAULT_TILT, v)] = _pct()
    return vol.Schema(fields)


def energy_schema(v: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            _req(CONF_HEAT_PRICE, v): _num(0, 2, 0.01, "/kWh"),
            _req(CONF_PREDICTIVE, v): BooleanSelector(),
            _req(CONF_PREDICT_HOURS, v): _num(1, 8, 0.5, UnitOfTime.HOURS),
            _req(CONF_INSULATE_AWAY, v): BooleanSelector(),
            _opt(CONF_THERMOSTAT, v): _entity("climate"),
            _req(CONF_THERMOSTAT_MODE, v): _select(THERMOSTAT_MODES, "thermostat_mode"),
            _req(CONF_MAX_SETBACK, v): _num(0, 5, 0.5, "°C"),
            _req(CONF_VENT_DELTA, v): _num(0.5, 10, 0.5, "°C"),
        }
    )


def protection_schema(v: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            _opt(CONF_WIND, v): _entity("sensor"),
            _req(CONF_WIND_THRESHOLD, v): _num(0, 200, 1),
            _opt(CONF_RAIN, v): _entity("binary_sensor", "sensor"),
            _opt(CONF_SAFETY_POSITION, v): _pct(),
            _req(CONF_SAFETY_RELEASE, v): _num(0, 240, 1, UnitOfTime.MINUTES),
            _opt(CONF_CONTACT, v): _entity("binary_sensor"),
            _req(CONF_LOCKOUT_POSITION, v): _pct(),
        }
    )


def behaviour_schema(v: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            _req(CONF_OVERRIDE_MINUTES, v): _num(1, 1440, 1, UnitOfTime.MINUTES),
            _req(CONF_OVERRIDE_UNTIL_RULE, v): BooleanSelector(),
            _req(CONF_OVERRIDE_TOLERANCE, v): _num(1, 50, 1, PERCENTAGE),
            _req(CONF_MIN_INTERVAL, v): _num(0, 240, 1, UnitOfTime.MINUTES),
            _req(CONF_MIN_DELTA, v): _num(1, 50, 1, PERCENTAGE),
            _req(CONF_UPDATE_INTERVAL, v): _num(1, 30, 1, UnitOfTime.MINUTES),
            _req(CONF_SETTLE_TIME, v): _num(10, 600, 5, UnitOfTime.SECONDS),
            _req(CONF_INVERT_POSITION, v): BooleanSelector(),
            _req(CONF_DRY_RUN, v): BooleanSelector(),
        }
    )


RULE_DEFAULTS: dict[str, Any] = {
    RULE_NAME: "",
    RULE_DAYS: list(DAYS),
    RULE_START_TYPE: "time",
    RULE_START_TIME: "08:00:00",
    RULE_START_OFFSET: 0,
    RULE_END_TYPE: "time",
    RULE_END_TIME: "20:00:00",
    RULE_END_OFFSET: 0,
    RULE_ACTION: "auto",
    RULE_PRIORITY: 50,
}


def rule_schema(v: dict[str, Any]) -> vol.Schema:
    v = {**RULE_DEFAULTS, **v}

    def req(key: str) -> vol.Required:
        return vol.Required(key, default=v[key])

    return vol.Schema(
        {
            req(RULE_NAME): TextSelector(),
            req(RULE_DAYS): _select(DAYS, "weekday", multiple=True),
            req(RULE_START_TYPE): _select(TIME_KINDS, "time_kind"),
            req(RULE_START_TIME): TimeSelector(),
            req(RULE_START_OFFSET): _num(-240, 240, 1, UnitOfTime.MINUTES),
            req(RULE_END_TYPE): _select(TIME_KINDS, "time_kind"),
            req(RULE_END_TIME): TimeSelector(),
            req(RULE_END_OFFSET): _num(-240, 240, 1, UnitOfTime.MINUTES),
            req(RULE_ACTION): _select(ACTIONS, "action"),
            _opt(RULE_POSITION, v): _pct(),
            _opt(RULE_TILT, v): _pct(),
            req(RULE_PRIORITY): _num(0, 100, 1),
            _opt(RULE_CONDITION, v): TemplateSelector(),
        }
    )


def _validate_rule(data: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not data.get(RULE_DAYS):
        errors[RULE_DAYS] = "no_days"
    if (
        data[RULE_START_TYPE] == data[RULE_END_TYPE]
        and (data[RULE_START_TYPE] != "time" or data[RULE_START_TIME] == data[RULE_END_TIME])
        and int(data[RULE_START_OFFSET]) == int(data[RULE_END_OFFSET])
    ):
        errors["base"] = "empty_interval"
    if data[RULE_ACTION] == "position" and data.get(RULE_POSITION) is None:
        errors[RULE_POSITION] = "position_required"
    return errors


def _merge(target: dict[str, Any], schema: vol.Schema, user_input: dict[str, Any]) -> None:
    """Store a form result; optional fields left empty are removed."""
    for marker in schema.schema:
        key = str(marker)
        if key in user_input and user_input[key] not in (None, ""):
            target[key] = user_input[key]
        else:
            target.pop(key, None)


# --------------------------------------------------------------------------- #
# Config flow
# --------------------------------------------------------------------------- #


class BlindControlConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a blind: cover -> window geometry -> climate inputs."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._options: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> BlindControlOptionsFlow:
        return BlindControlOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_COVER])
            self._abort_if_unique_id_configured()
            self._data = user_input
            self._options = {
                **DEFAULTS,
                **TYPE_DEFAULTS.get(user_input[CONF_BLIND_TYPE], {}),
                CONF_RULES: default_rules(),
            }
            return await self.async_step_window()
        return self.async_show_form(step_id="user", data_schema=base_schema({}))

    async def async_step_window(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        schema = geometry_schema(self._data[CONF_BLIND_TYPE], self._options)
        if user_input is not None:
            _merge(self._options, schema, user_input)
            return await self.async_step_climate()
        return self.async_show_form(step_id="window", data_schema=schema)

    async def async_step_climate(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        schema = climate_schema(self._options)
        if user_input is not None:
            _merge(self._options, schema, user_input)
            data = {CONF_COVER: self._data[CONF_COVER], CONF_BLIND_TYPE: self._data[CONF_BLIND_TYPE]}
            return self.async_create_entry(title=self._data[CONF_NAME], data=data, options=self._options)
        return self.async_show_form(step_id="climate", data_schema=schema)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the controlled cover, its type or the name."""
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_COVER])
            if user_input[CONF_COVER] != entry.unique_id:
                self._abort_if_unique_id_configured()
            return self.async_update_reload_and_abort(
                entry,
                unique_id=user_input[CONF_COVER],
                title=user_input[CONF_NAME],
                data={CONF_COVER: user_input[CONF_COVER], CONF_BLIND_TYPE: user_input[CONF_BLIND_TYPE]},
            )
        return self.async_show_form(
            step_id="reconfigure", data_schema=base_schema({**entry.data, CONF_NAME: entry.title})
        )


# --------------------------------------------------------------------------- #
# Options flow
# --------------------------------------------------------------------------- #


class BlindControlOptionsFlow(OptionsFlow):
    """Menu-driven editor; changes are kept in memory until 'Save'."""

    def __init__(self) -> None:
        self._opts: dict[str, Any] | None = None
        self._rule_id: str | None = None

    @property
    def opts(self) -> dict[str, Any]:
        if self._opts is None:
            self._opts = {**DEFAULTS, **deepcopy(dict(self.config_entry.options))}
        return self._opts

    @property
    def blind_type(self) -> str:
        return self.config_entry.data[CONF_BLIND_TYPE]

    @property
    def rules(self) -> list[dict[str, Any]]:
        return self.opts.setdefault(CONF_RULES, [])

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["geometry", "climate", "energy", "positions", "protection", "behaviour", "rules", "save"],
        )

    async def _form(self, step_id: str, schema: vol.Schema, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        if user_input is not None:
            _merge(self.opts, schema, user_input)
            return await self.async_step_init()
        return self.async_show_form(step_id=step_id, data_schema=schema)

    async def async_step_geometry(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._form("geometry", geometry_schema(self.blind_type, self.opts), user_input)

    async def async_step_climate(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._form("climate", climate_schema(self.opts), user_input)

    async def async_step_energy(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._form("energy", energy_schema(self.opts), user_input)

    async def async_step_positions(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._form("positions", positions_schema(self.blind_type, self.opts), user_input)

    async def async_step_protection(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._form("protection", protection_schema(self.opts), user_input)

    async def async_step_behaviour(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._form("behaviour", behaviour_schema(self.opts), user_input)

    async def async_step_save(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_create_entry(data=self.opts)

    # -- rules ------------------------------------------------------------ #

    async def async_step_rules(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        options = ["rule_add", "init"]
        if self.rules:
            options[1:1] = ["rule_edit", "rule_delete"]
        summary = "\n".join(
            f"- **{r[RULE_NAME]}** — {r[RULE_ACTION]} (prio {r.get(RULE_PRIORITY, 50)})" for r in self.rules
        )
        return self.async_show_menu(
            step_id="rules", menu_options=options, description_placeholders={"rules": summary or "—"}
        )

    def _rule_choice(self) -> SelectSelector:
        return SelectSelector(
            SelectSelectorConfig(options=[{"value": r[RULE_ID], "label": r[RULE_NAME]} for r in self.rules])
        )

    async def async_step_rule_add(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._rule_id = None
        return await self.async_step_rule_form()

    async def async_step_rule_edit(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._rule_id = user_input[RULE_ID]
            return await self.async_step_rule_form()
        return self.async_show_form(
            step_id="rule_edit", data_schema=vol.Schema({vol.Required(RULE_ID): self._rule_choice()})
        )

    async def async_step_rule_delete(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self.opts[CONF_RULES] = [r for r in self.rules if r[RULE_ID] != user_input[RULE_ID]]
            return await self.async_step_rules()
        return self.async_show_form(
            step_id="rule_delete", data_schema=vol.Schema({vol.Required(RULE_ID): self._rule_choice()})
        )

    async def async_step_rule_form(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = next((r for r in self.rules if r[RULE_ID] == self._rule_id), {})
        errors: dict[str, str] = {}
        if user_input is not None:
            if not (errors := _validate_rule(user_input)):
                rule: dict[str, Any] = {RULE_ID: self._rule_id or uuid4().hex[:8]}
                _merge(rule, rule_schema({}), user_input)
                if self._rule_id:
                    self.opts[CONF_RULES] = [rule if r[RULE_ID] == self._rule_id else r for r in self.rules]
                else:
                    self.rules.append(rule)
                return await self.async_step_rules()
            current = user_input
        return self.async_show_form(step_id="rule_form", data_schema=rule_schema(current), errors=errors)
