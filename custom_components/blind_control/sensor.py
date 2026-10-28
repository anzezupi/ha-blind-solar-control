"""Sensors: decision, solar energy, prediction and learned room data."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    DEGREE,
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BlindConfigEntry, BlindCoordinator, BlindState
from .engine import Climate, Reason
from .entity import BlindEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class BlindSensorDescription(SensorEntityDescription):
    """Describes a Blind Control sensor."""

    value_fn: Callable[[BlindState], Any]
    attrs_fn: Callable[[BlindCoordinator, BlindState], dict[str, Any]] | None = None


def _card_attrs(coordinator: BlindCoordinator, s: BlindState) -> dict[str, Any]:
    """Everything the sun-path card needs."""
    geo = coordinator.engine.cfg.geometry
    return {
        "window": {
            "azimuth": geo.azimuth,
            "fov_left": geo.fov_left,
            "fov_right": geo.fov_right,
            "min_elevation": geo.min_elevation,
            "max_elevation": geo.max_elevation,
            "horizon": [list(h) for h in geo.horizon],
        },
        "sun": [s.azimuth, s.elevation],
        "sun_path": [list(p) for p in s.solar.sun_path],
        "hourly": s.solar.hourly,
        "today_kwh": round(s.solar.today.solar, 2),
        "potential_w": s.solar.potential,
    }


def _energy(key: str, value_fn: Callable[[BlindState], Any]) -> BlindSensorDescription:
    return BlindSensorDescription(
        key=key,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=value_fn,
    )


def _temperature(key: str, value_fn: Callable[[BlindState], Any]) -> BlindSensorDescription:
    return BlindSensorDescription(
        key=key,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=value_fn,
    )


SENSORS: tuple[BlindSensorDescription, ...] = (
    BlindSensorDescription(
        key="target_position",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda s: s.decision.position,
        attrs_fn=lambda _c, s: {
            "target_tilt": s.decision.tilt,
            "rule": s.rule,
            "lockout": s.decision.lockout,
            "override_until": s.override_until,
            "next_change": s.next_change,
        },
    ),
    BlindSensorDescription(
        key="reason",
        device_class=SensorDeviceClass.ENUM,
        options=[r.value for r in Reason],
        value_fn=lambda s: s.decision.reason.value,
    ),
    BlindSensorDescription(
        key="climate",
        device_class=SensorDeviceClass.ENUM,
        options=[c.value for c in Climate],
        value_fn=lambda s: s.climate.value,
    ),
    # --- solar energy ------------------------------------------------------
    BlindSensorDescription(
        key="solar_gain",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.solar.gain,
        attrs_fn=_card_attrs,
    ),
    BlindSensorDescription(
        key="solar_potential",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.solar.potential,
    ),
    _energy("solar_energy_today", lambda s: round(s.solar.today.solar, 3)),
    _energy("useful_heat_today", lambda s: round(s.solar.today.useful, 3)),
    _energy("blocked_heat_today", lambda s: round(s.solar.today.blocked, 3)),
    _energy("insulation_saved_today", lambda s: round(s.solar.today.insulation, 3)),
    BlindSensorDescription(
        key="savings_today",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        value_fn=lambda s: s.solar.savings,
    ),
    BlindSensorDescription(
        key="solar_forecast_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda s: s.solar.forecast_today,
        attrs_fn=lambda _c, s: {"next_hours_kwh": s.solar.expected},
    ),
    # --- prediction & room model --------------------------------------------
    _temperature("predicted_temperature", lambda s: s.solar.predicted_temp),
    _temperature("suggested_setpoint", lambda s: s.solar.suggested_setpoint),
    BlindSensorDescription(
        key="room_sensitivity",
        native_unit_of_measurement="°C/kWh",
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        value_fn=lambda s: s.solar.sensitivity,
        attrs_fn=lambda _c, s: {
            "loss_rate_per_hour": s.solar.loss_rate,
            "samples": s.solar.samples,
            "trained": s.solar.trained,
        },
    ),
    BlindSensorDescription(
        key="cloud_cover",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.solar.cloud,
    ),
    # --- sun ------------------------------------------------------------------
    BlindSensorDescription(
        key="profile_angle",
        native_unit_of_measurement=DEGREE,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
        value_fn=lambda s: None if s.decision.profile_angle is None else round(s.decision.profile_angle, 1),
        attrs_fn=lambda _c, s: {"sun_azimuth": s.azimuth, "sun_elevation": s.elevation},
    ),
    BlindSensorDescription(
        key="sun_enters",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s: s.sun_enters,
    ),
    BlindSensorDescription(
        key="sun_leaves",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s: s.sun_leaves,
    ),
    BlindSensorDescription(
        key="next_change",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.next_change,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: BlindConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(BlindSensor(coordinator, d) for d in SENSORS)


class BlindSensor(BlindEntity, SensorEntity):
    """Generic sensor driven by a value function."""

    entity_description: BlindSensorDescription
    # Volatile or bulky attributes stay out of the recorder database.
    _unrecorded_attributes = frozenset(
        {
            "sun_azimuth",
            "sun_elevation",
            "next_change",
            "window",
            "sun",
            "sun_path",
            "hourly",
            "today_kwh",
            "potential_w",
            "next_hours_kwh",
        }
    )

    def __init__(self, coordinator: BlindCoordinator, description: BlindSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        if description.device_class is SensorDeviceClass.MONETARY:
            self._attr_native_unit_of_measurement = coordinator.hass.config.currency

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data
        return None if data is None else self.entity_description.value_fn(data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if data is None or self.entity_description.attrs_fn is None:
            return None
        return {
            k: v.isoformat() if isinstance(v, datetime) else v
            for k, v in self.entity_description.attrs_fn(self.coordinator, data).items()
        }
