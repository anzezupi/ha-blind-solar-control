"""Buttons: resume automation after a manual change, force recalculation."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BlindConfigEntry, BlindCoordinator
from .entity import BlindEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: BlindConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([ResumeButton(coordinator), RecalculateButton(coordinator)])


class ResumeButton(BlindEntity, ButtonEntity):
    """Clears a manual override."""

    def __init__(self, coordinator: BlindCoordinator) -> None:
        super().__init__(coordinator, "resume")

    async def async_press(self) -> None:
        await self.coordinator.async_resume()


class RecalculateButton(BlindEntity, ButtonEntity):
    """Re-applies the current target even if within tolerance."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: BlindCoordinator) -> None:
        super().__init__(coordinator, "recalculate")

    async def async_press(self) -> None:
        await self.coordinator.async_recalculate()
