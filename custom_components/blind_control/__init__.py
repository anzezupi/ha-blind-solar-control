"""Blind Control: sun- and climate-aware automation for blinds, shutters and awnings."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .const import DOMAIN
from .coordinator import BlindConfigEntry, BlindCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SELECT, Platform.SENSOR, Platform.SWITCH]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

ATTR_ENTRY_ID = "config_entry_id"
ATTR_MINUTES = "minutes"

_ENTRY_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTRY_ID): cv.string})
_PAUSE_SCHEMA = _ENTRY_SCHEMA.extend(
    {vol.Required(ATTR_MINUTES): vol.All(vol.Coerce(float), vol.Range(min=1, max=7 * 24 * 60))}
)


def _coordinators(hass: HomeAssistant, call: ServiceCall) -> list[BlindCoordinator]:
    """Coordinators targeted by a service call (all blinds when no entry given)."""
    entries: list[BlindConfigEntry] = [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED
    ]
    if entry_id := call.data.get(ATTR_ENTRY_ID):
        entries = [e for e in entries if e.entry_id == entry_id]
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_found",
                translation_placeholders={"entry_id": entry_id},
            )
    return [e.runtime_data for e in entries]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register domain services once."""

    async def resume(call: ServiceCall) -> None:
        for coordinator in _coordinators(hass, call):
            await coordinator.async_resume()

    async def pause(call: ServiceCall) -> None:
        for coordinator in _coordinators(hass, call):
            await coordinator.async_pause(call.data[ATTR_MINUTES])

    async def recalculate(call: ServiceCall) -> None:
        for coordinator in _coordinators(hass, call):
            await coordinator.async_recalculate()

    hass.services.async_register(DOMAIN, "resume", resume, schema=_ENTRY_SCHEMA)
    hass.services.async_register(DOMAIN, "pause", pause, schema=_PAUSE_SCHEMA)
    hass.services.async_register(DOMAIN, "recalculate", recalculate, schema=_ENTRY_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BlindConfigEntry) -> bool:
    """Set up one blind."""
    coordinator = BlindCoordinator(hass, entry)
    entry.runtime_data = coordinator
    # Entities restore 'enabled' and 'strategy' before the first evaluation.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.async_config_entry_first_refresh()
    coordinator.async_setup_listeners()
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: BlindConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: BlindConfigEntry) -> bool:
    """Unload a blind."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
