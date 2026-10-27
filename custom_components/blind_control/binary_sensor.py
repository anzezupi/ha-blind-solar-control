"""Binary sensors: sun on window, manual override, safety."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BlindConfigEntry, BlindCoordinator, BlindState
from .entity import BlindEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class BlindBinaryDescription(BinarySensorEntityDescription):
    """Describes a Blind Control binary sensor."""

    is_on_fn: Callable[[BlindState], bool]


BINARY_SENSORS: tuple[BlindBinaryDescription, ...] = (
    BlindBinaryDescription(key="sun_in_window", is_on_fn=lambda s: s.decision.sun_in_window),
    BlindBinaryDescription(key="manual_override", is_on_fn=lambda s: s.override_until is not None),
    BlindBinaryDescription(
        key="safety", device_class=BinarySensorDeviceClass.SAFETY, is_on_fn=lambda s: s.safety
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: BlindConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(BlindBinarySensor(coordinator, d) for d in BINARY_SENSORS)


class BlindBinarySensor(BlindEntity, BinarySensorEntity):
    """Generic binary sensor driven by a predicate."""

    entity_description: BlindBinaryDescription

    def __init__(self, coordinator: BlindCoordinator, description: BlindBinaryDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return None if data is None else self.entity_description.is_on_fn(data)
