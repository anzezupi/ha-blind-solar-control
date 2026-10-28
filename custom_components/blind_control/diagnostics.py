"""Diagnostics download."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import BlindConfigEntry


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: BlindConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    return {
        "config": coordinator.cfg,
        "enabled": coordinator.enabled,
        "strategy": coordinator.strategy,
        "room_model": coordinator.energy.model.as_dict(),
        "state": asdict(coordinator.data) if coordinator.data else None,
    }
