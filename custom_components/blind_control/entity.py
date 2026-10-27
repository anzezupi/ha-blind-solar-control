"""Base entity for Blind Control."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_BLIND_TYPE, DOMAIN
from .coordinator import BlindCoordinator


class BlindEntity(CoordinatorEntity[BlindCoordinator]):
    """Entity bound to one blind's coordinator and device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BlindCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Blind Control",
            model=coordinator.cfg[CONF_BLIND_TYPE].replace("_", " ").title(),
            entry_type=DeviceEntryType.SERVICE,
        )
