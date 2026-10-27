"""Constants for Blind Control."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "blind_control"

# Base (entry.data)
CONF_COVER: Final = "cover_entity"
CONF_BLIND_TYPE: Final = "blind_type"

# Geometry
CONF_AZIMUTH: Final = "azimuth"
CONF_FOV_LEFT: Final = "fov_left"
CONF_FOV_RIGHT: Final = "fov_right"
CONF_MIN_ELEVATION: Final = "min_elevation"
CONF_MAX_ELEVATION: Final = "max_elevation"
CONF_HORIZON: Final = "horizon_profile"
CONF_WINDOW_HEIGHT: Final = "window_height"
CONF_SILL_HEIGHT: Final = "sill_height"
CONF_SHADE_DEPTH: Final = "shade_depth"
CONF_SLAT_WIDTH: Final = "slat_width"
CONF_SLAT_SPACING: Final = "slat_spacing"
CONF_TILT_MODE: Final = "tilt_mode"
CONF_TILT_INVERT: Final = "tilt_invert"
CONF_TILT_MARGIN: Final = "tilt_margin"

# Climate
CONF_INDOOR_TEMP: Final = "indoor_temperature"
CONF_OUTDOOR_TEMP: Final = "outdoor_temperature"
CONF_WEATHER: Final = "weather_entity"
CONF_LUX: Final = "lux_entity"
CONF_LUX_THRESHOLD: Final = "lux_threshold"
CONF_HEAT_SETPOINT: Final = "heat_setpoint"
CONF_COOL_SETPOINT: Final = "cool_setpoint"
CONF_OUTDOOR_HEAT_BELOW: Final = "outdoor_heat_below"
CONF_OUTDOOR_COOL_ABOVE: Final = "outdoor_cool_above"
CONF_HYSTERESIS: Final = "hysteresis"
CONF_GLARE: Final = "glare_protection"
CONF_PRESENCE: Final = "presence_entity"

# Positions
CONF_DEFAULT_POSITION: Final = "default_position"
CONF_DEFAULT_TILT: Final = "default_tilt"
CONF_HEAT_POSITION: Final = "heat_position"
CONF_SHADE_POSITION: Final = "shade_position"
CONF_SHADE_MIN_POSITION: Final = "shade_min_position"
CONF_AWAY_POSITION: Final = "away_position"
CONF_POSITION_STEP: Final = "position_step"

# Protection
CONF_WIND: Final = "wind_entity"
CONF_WIND_THRESHOLD: Final = "wind_threshold"
CONF_RAIN: Final = "rain_entity"
CONF_SAFETY_POSITION: Final = "safety_position"
CONF_SAFETY_RELEASE: Final = "safety_release"
CONF_CONTACT: Final = "contact_entity"
CONF_LOCKOUT_POSITION: Final = "lockout_position"

# Behaviour
CONF_OVERRIDE_MINUTES: Final = "override_minutes"
CONF_OVERRIDE_UNTIL_RULE: Final = "override_until_rule"
CONF_OVERRIDE_TOLERANCE: Final = "override_tolerance"
CONF_MIN_INTERVAL: Final = "min_interval"
CONF_MIN_DELTA: Final = "min_delta"
CONF_UPDATE_INTERVAL: Final = "update_interval"
CONF_SETTLE_TIME: Final = "settle_time"
CONF_INVERT_POSITION: Final = "invert_position"
CONF_DRY_RUN: Final = "dry_run"

CONF_RULES: Final = "rules"

# Rule fields
RULE_ID: Final = "id"
RULE_NAME: Final = "name"
RULE_DAYS: Final = "days"
RULE_START_TYPE: Final = "start_type"
RULE_START_TIME: Final = "start_time"
RULE_START_OFFSET: Final = "start_offset"
RULE_END_TYPE: Final = "end_type"
RULE_END_TIME: Final = "end_time"
RULE_END_OFFSET: Final = "end_offset"
RULE_ACTION: Final = "action"
RULE_POSITION: Final = "position"
RULE_TILT: Final = "tilt"
RULE_PRIORITY: Final = "priority"
RULE_CONDITION: Final = "condition"

BLIND_TYPES: Final = (
    "roller_blind",
    "roller_shutter",
    "venetian",
    "tilt_only",
    "curtain",
    "awning",
)
TILT_MODES: Final = ("none", "90", "180")
STRATEGIES: Final = ("auto", "heating", "cooling", "glare", "off")
ACTIONS: Final = ("auto", "open", "close", "position", "tilt", "tilt_auto", "shade_only", "skip")
TIME_KINDS: Final = ("time", "sunrise", "sunset")

DEFAULTS: Final[dict[str, object]] = {
    CONF_AZIMUTH: 180,
    CONF_FOV_LEFT: 80,
    CONF_FOV_RIGHT: 80,
    CONF_MIN_ELEVATION: 2,
    CONF_MAX_ELEVATION: 90,
    CONF_HORIZON: "",
    CONF_WINDOW_HEIGHT: 1.5,
    CONF_SILL_HEIGHT: 0.9,
    CONF_SHADE_DEPTH: 0.5,
    CONF_SLAT_WIDTH: 80,
    CONF_SLAT_SPACING: 70,
    CONF_TILT_MODE: "none",
    CONF_TILT_INVERT: False,
    CONF_TILT_MARGIN: 5,
    CONF_LUX_THRESHOLD: 15000,
    CONF_HEAT_SETPOINT: 21.0,
    CONF_COOL_SETPOINT: 24.5,
    CONF_OUTDOOR_HEAT_BELOW: 15.0,
    CONF_OUTDOOR_COOL_ABOVE: 25.0,
    CONF_HYSTERESIS: 0.5,
    CONF_GLARE: False,
    CONF_DEFAULT_POSITION: 100,
    CONF_HEAT_POSITION: 100,
    CONF_SHADE_POSITION: 0,
    CONF_SHADE_MIN_POSITION: 0,
    CONF_AWAY_POSITION: 0,
    CONF_POSITION_STEP: 5,
    CONF_WIND_THRESHOLD: 40,
    CONF_SAFETY_RELEASE: 15,
    CONF_LOCKOUT_POSITION: 100,
    CONF_OVERRIDE_MINUTES: 120,
    CONF_OVERRIDE_UNTIL_RULE: True,
    CONF_OVERRIDE_TOLERANCE: 5,
    CONF_MIN_INTERVAL: 10,
    CONF_MIN_DELTA: 5,
    CONF_UPDATE_INTERVAL: 2,
    CONF_SETTLE_TIME: 90,
    CONF_INVERT_POSITION: False,
    CONF_DRY_RUN: False,
}

# Sensible per-type overrides applied when a blind is created.
TYPE_DEFAULTS: Final[dict[str, dict[str, object]]] = {
    "venetian": {CONF_TILT_MODE: "90", CONF_SAFETY_POSITION: 100},
    "tilt_only": {CONF_TILT_MODE: "180"},
    "curtain": {CONF_SHADE_DEPTH: 0, CONF_SHADE_POSITION: 30},
    "awning": {
        CONF_DEFAULT_POSITION: 0,
        CONF_HEAT_POSITION: 0,
        CONF_SHADE_POSITION: 100,
        CONF_AWAY_POSITION: 100,
        CONF_LOCKOUT_POSITION: 0,
        CONF_SAFETY_POSITION: 0,
        CONF_SHADE_DEPTH: 0,
    },
}
