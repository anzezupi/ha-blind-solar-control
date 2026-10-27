"""Select for the climate strategy (auto / winter / summer / glare / off)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import STRATEGIES
from .coordinator import BlindConfigEntry, BlindCoordinator
from .engine import Strategy
from .entity import BlindEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: BlindConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([StrategySelect(entry.runtime_data)])


class StrategySelect(BlindEntity, SelectEntity, RestoreEntity):
    """Forces heating / cooling / glare behaviour or lets the engine decide."""

    _attr_options = list(STRATEGIES)

    def __init__(self, coordinator: BlindCoordinator) -> None:
        super().__init__(coordinator, "strategy")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None and last.state in STRATEGIES:
            self.coordinator.strategy = Strategy(last.state)

    @property
    def current_option(self) -> str:
        return self.coordinator.strategy.value

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_strategy(option)
        self.async_write_ha_state()
