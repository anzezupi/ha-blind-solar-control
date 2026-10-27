"""Sensors: target position, decision reason, sun data."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.const import DEGREE, PERCENTAGE, EntityCategory
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
    attrs_fn: Callable[[BlindState], dict[str, Any]] | None = None


SENSORS: tuple[BlindSensorDescription, ...] = (
    BlindSensorDescription(
        key="target_position",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda s: s.decision.position,
        attrs_fn=lambda s: {
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
    BlindSensorDescription(
        key="profile_angle",
        native_unit_of_measurement=DEGREE,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
        value_fn=lambda s: None if s.decision.profile_angle is None else round(s.decision.profile_angle, 1),
        attrs_fn=lambda s: {"sun_azimuth": s.azimuth, "sun_elevation": s.elevation},
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
    # Changing every few minutes; keep the database small.
    _unrecorded_attributes = frozenset({"sun_azimuth", "sun_elevation", "next_change"})

    def __init__(self, coordinator: BlindCoordinator, description: BlindSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

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
            for k, v in self.entity_description.attrs_fn(data).items()
        }
