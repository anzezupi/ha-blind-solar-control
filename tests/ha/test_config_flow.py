"""Config and options flow tests."""

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.blind_control.const import CONF_RULES, DOMAIN

from .conftest import COVER, INDOOR


async def test_full_user_flow(hass_ljubljana: HomeAssistant) -> None:
    hass = hass_ljubljana
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Living room", "cover_entity": COVER, "blind_type": "venetian"}
    )
    assert result["step_id"] == "window"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"azimuth": 165})
    assert result["step_id"] == "climate"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"indoor_temperature": INDOOR})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.title == "Living room"
    assert entry.data == {"cover_entity": COVER, "blind_type": "venetian"}
    assert entry.options["azimuth"] == 165
    assert entry.options["tilt_mode"] == "90"  # venetian default
    assert entry.options["exterior_mount"] is True
    assert entry.options[CONF_RULES][0]["action"] == "close"

    # Same cover twice is refused.
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Again", "cover_entity": COVER, "blind_type": "venetian"}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_options_flow_rules_and_energy(hass_ljubljana: HomeAssistant) -> None:
    hass = hass_ljubljana
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living room",
        unique_id=COVER,
        data={"cover_entity": COVER, "blind_type": "roller_shutter"},
        options={"indoor_temperature": INDOOR, CONF_RULES: []},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU

    # Add a rule; an empty interval is rejected first.
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "rules"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "rule_add"})
    rule = {
        "name": "Afternoon",
        "days": ["mon", "tue"],
        "start_type": "time",
        "start_time": "14:00:00",
        "start_offset": 0,
        "end_type": "time",
        "end_time": "14:00:00",
        "end_offset": 0,
        "action": "tilt_auto",
        "priority": 60,
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], rule)
    assert result["errors"] == {"base": "empty_interval"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], {**rule, "end_time": "17:00:00"})
    assert result["type"] is FlowResultType.MENU and result["step_id"] == "rules"

    # Energy settings, then save.
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "init"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "energy"})
    assert result["step_id"] == "energy"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "heat_price": 0.2,
            "predictive": True,
            "predict_hours": 2,
            "insulate_when_away": True,
            "thermostat_mode": "suggest",
            "max_setback": 1.0,
            "ventilation_delta": 2,
        },
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "save"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["heat_price"] == 0.2
    assert entry.options["insulate_when_away"] is True
    assert [r["name"] for r in entry.options[CONF_RULES]] == ["Afternoon"]
    assert entry.options["indoor_temperature"] == INDOOR  # untouched steps are kept
