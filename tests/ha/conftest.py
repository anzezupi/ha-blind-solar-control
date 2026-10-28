"""Fixtures for tests that run the integration inside Home Assistant."""

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant

COVER = "cover.living_room"
INDOOR = "sensor.living_room_temperature"
OUTDOOR = "sensor.outdoor_temperature"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ from the repository."""
    return


@pytest.fixture
async def hass_ljubljana(hass: HomeAssistant) -> HomeAssistant:
    """Home Assistant located in Ljubljana with a positionable cover and temperatures."""
    await hass.config.async_update(latitude=46.05, longitude=14.51, elevation=295, time_zone="Europe/Ljubljana")
    hass.states.async_set(COVER, "closed", {"current_position": 0, "supported_features": 15})
    hass.states.async_set(INDOOR, "19.0", {"unit_of_measurement": "°C"})
    hass.states.async_set(OUTDOOR, "5.0", {"unit_of_measurement": "°C"})
    return hass
