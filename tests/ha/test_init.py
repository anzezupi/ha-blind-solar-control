"""Runtime tests: entities, commands, forecast, thermostat and manual override."""

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.blind_control.const import CONF_RULES, DOMAIN

from .conftest import COVER, INDOOR, OUTDOOR

TZ = ZoneInfo("Europe/Ljubljana")
WINTER_NOON = datetime(2026, 1, 15, 12, 0, tzinfo=TZ)


def _entry(**options: Any) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Living room",
        unique_id=COVER,
        data={"cover_entity": COVER, "blind_type": "roller_shutter"},
        options={
            "azimuth": 180,
            "indoor_temperature": INDOOR,
            "outdoor_temperature": OUTDOOR,
            "exterior_mount": True,
            CONF_RULES: [],
            **options,
        },
    )


def _state(hass: HomeAssistant, key: str) -> Any:
    entity_id = er.async_get(hass).async_get_entity_id(
        key.split(".")[0], DOMAIN, f"{hass.config_entries.async_entries(DOMAIN)[0].entry_id}_{key.split('.')[1]}"
    )
    assert entity_id, key
    return hass.states.get(entity_id)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture
def cover_calls(hass_ljubljana: HomeAssistant) -> list[ServiceCall]:
    return async_mock_service(hass_ljubljana, "cover", "set_cover_position")


async def test_winter_sun_opens_cold_room(
    hass_ljubljana: HomeAssistant, freezer: FrozenDateTimeFactory, cover_calls: list[ServiceCall]
) -> None:
    hass = hass_ljubljana
    freezer.move_to(WINTER_NOON)
    await _setup(hass, _entry())

    assert _state(hass, "sensor.reason").state == "heat_gain"
    assert _state(hass, "sensor.climate").state == "heat"
    assert _state(hass, "binary_sensor.sun_in_window").state == "on"
    assert cover_calls and cover_calls[-1].data["position"] == 100

    gain = _state(hass, "sensor.solar_potential")
    assert 300 < float(gain.state) < 1500  # clear winter noon on a south window
    card = _state(hass, "sensor.solar_gain").attributes
    assert card["window"]["azimuth"] == 180
    assert len(card["sun_path"]) > 20 and len(card["hourly"]) == 24


async def test_energy_is_integrated_and_persisted(
    hass_ljubljana: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    hass = hass_ljubljana
    freezer.move_to(WINTER_NOON)
    hass.states.async_set(COVER, "open", {"current_position": 100, "supported_features": 15})
    entry = _entry()
    await _setup(hass, entry)

    for _ in range(6):  # one hour in 10-minute steps
        freezer.tick(timedelta(minutes=10))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    energy = float(_state(hass, "sensor.solar_energy_today").state)
    assert 0.2 < energy < 2.0
    assert float(_state(hass, "sensor.useful_heat_today").state) == pytest.approx(energy, rel=0.01)
    assert float(_state(hass, "sensor.savings_today").state) > 0

    # Reload keeps today's counters (saved on unload).
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert float(_state(hass, "sensor.solar_energy_today").state) == pytest.approx(energy, rel=0.05)


async def test_hot_forecast_shades_preemptively(
    hass_ljubljana: HomeAssistant, freezer: FrozenDateTimeFactory, cover_calls: list[ServiceCall]
) -> None:
    hass = hass_ljubljana
    freezer.move_to(datetime(2026, 7, 10, 10, 0, tzinfo=TZ))
    hass.states.async_set(INDOOR, "22.5")
    hass.states.async_set(OUTDOOR, "21.0")
    hass.states.async_set(COVER, "open", {"current_position": 100, "supported_features": 15})
    hass.states.async_set("weather.home", "sunny", {"temperature": 21, "cloud_coverage": 0})

    async def forecasts(call: ServiceCall) -> dict[str, Any]:
        start = datetime(2026, 7, 10, 10, 0, tzinfo=TZ)
        return {
            "weather.home": {
                "forecast": [
                    {"datetime": (start + timedelta(hours=h)).isoformat(), "temperature": 22 + h, "cloud_coverage": 0}
                    for h in range(12)
                ]
            }
        }

    hass.services.async_register("weather", "get_forecasts", forecasts, supports_response=SupportsResponse.ONLY)
    await _setup(hass, _entry(weather_entity="weather.home", azimuth=120))

    assert _state(hass, "sensor.climate").state == "precool"
    assert _state(hass, "sensor.reason").state == "predictive_shade"
    assert cover_calls and cover_calls[-1].data["position"] < 100


async def test_thermostat_control(hass_ljubljana: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    hass = hass_ljubljana
    freezer.move_to(datetime(2026, 1, 15, 10, 0, tzinfo=TZ))
    hass.states.async_set(INDOOR, "21.2")
    hass.states.async_set("climate.living_room", "heat", {"temperature": 21})
    calls = async_mock_service(hass, "climate", "set_temperature")
    await _setup(hass, _entry(thermostat_entity="climate.living_room", thermostat_mode="control"))

    suggested = float(_state(hass, "sensor.suggested_setpoint").state)
    assert suggested < 21  # the sun is expected to do part of the heating
    assert calls and calls[-1].data["temperature"] == suggested


async def test_manual_override_pauses_automation(
    hass_ljubljana: HomeAssistant, freezer: FrozenDateTimeFactory, cover_calls: list[ServiceCall]
) -> None:
    hass = hass_ljubljana
    freezer.move_to(WINTER_NOON)
    await _setup(hass, _entry())
    assert cover_calls[-1].data["position"] == 100
    hass.states.async_set(COVER, "open", {"current_position": 100, "supported_features": 15})
    await hass.async_block_till_done()

    # Somebody closes the shutter by hand after the settle window.
    freezer.tick(timedelta(minutes=5))
    hass.states.async_set(COVER, "closed", {"current_position": 0, "supported_features": 15})
    await hass.async_block_till_done()
    assert _state(hass, "binary_sensor.manual_override").state == "on"
    assert _state(hass, "sensor.reason").state == "manual_override"

    count = len(cover_calls)
    freezer.tick(timedelta(minutes=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(cover_calls) == count  # automation leaves it alone

    await hass.services.async_call(DOMAIN, "resume", {}, blocking=True)
    await hass.async_block_till_done()
    assert _state(hass, "binary_sensor.manual_override").state == "off"
    assert cover_calls[-1].data["position"] == 100


async def test_night_rule_closes(
    hass_ljubljana: HomeAssistant, freezer: FrozenDateTimeFactory, cover_calls: list[ServiceCall]
) -> None:
    hass = hass_ljubljana
    freezer.move_to(datetime(2026, 1, 15, 22, 0, tzinfo=TZ))
    hass.states.async_set(COVER, "open", {"current_position": 100, "supported_features": 15})
    from custom_components.blind_control.schedule import default_rules

    await _setup(hass, _entry(**{CONF_RULES: default_rules()}))
    assert _state(hass, "sensor.reason").state == "rule_close"
    assert cover_calls[-1].data["position"] == 0
    # A closed exterior shutter on a cold night saves heat.
    for _ in range(3):
        freezer.tick(timedelta(minutes=10))
        hass.states.async_set(COVER, "closed", {"current_position": 0, "supported_features": 15})
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert float(_state(hass, "sensor.insulation_saved_today").state) > 0
