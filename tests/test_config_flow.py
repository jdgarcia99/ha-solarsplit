"""Config and options flows."""

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarsplit import get_settings
from custom_components.solarsplit.const import (
    CONF_EXPORT_SENSOR,
    CONF_GLOBAL_CONDITION,
    CONF_IMPORT_SENSOR,
    CONF_SAFETY_MARGIN,
    DEFAULT_SAFETY_MARGIN,
    DOMAIN,
)

SENSORS = {CONF_EXPORT_SENSOR: "sensor.export", CONF_IMPORT_SENSOR: "sensor.import"}


async def test_user_flow_creates_single_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SENSORS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert get_settings(entry)[CONF_SAFETY_MARGIN] == DEFAULT_SAFETY_MARGIN

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_updates_and_clears(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN,
        data={**SENSORS, CONF_GLOBAL_CONDITION: "input_boolean.casa_activada"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**SENSORS, CONF_SAFETY_MARGIN: 200}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    settings = get_settings(entry)
    assert settings[CONF_SAFETY_MARGIN] == 200
    assert settings[CONF_GLOBAL_CONDITION] is None
