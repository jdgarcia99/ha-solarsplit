"""Config and options flows."""

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarsplit import get_settings, load_settings
from custom_components.solarsplit.const import (
    CONF_AUTO_STATE,
    CONF_EXPORT_SENSOR,
    CONF_GLOBAL_CONDITION,
    CONF_IMPORT_SENSOR,
    CONF_LIMIT_ENTITY,
    CONF_LIMIT_MAX,
    CONF_LIMIT_MIN,
    CONF_LIMIT_THRESHOLD,
    CONF_LOAD_ENTITY,
    CONF_LOAD_TYPE,
    CONF_MODE_ENTITY,
    CONF_NOMINAL_POWER,
    CONF_POWER_SENSOR,
    CONF_PRIORITY,
    CONF_RESPECT_MANUAL,
    CONF_SAFETY_MARGIN,
    CONF_STALE_TIMEOUT,
    DEFAULT_AUTO_STATE,
    DEFAULT_LIMIT_THRESHOLD,
    DEFAULT_PRIORITY,
    DEFAULT_SAFETY_MARGIN,
    DEFAULT_STALE_TIMEOUT,
    DOMAIN,
    LOAD_TYPE_REGULABLE,
    LOAD_TYPE_RELAY,
    SECTION_REGULABLE,
    SUBENTRY_LOAD,
)

SENSORS = {CONF_EXPORT_SENSOR: "sensor.export", CONF_IMPORT_SENSOR: "sensor.import"}


async def test_user_flow_creates_single_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SENSORS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert get_settings(entry)[CONF_SAFETY_MARGIN] == DEFAULT_SAFETY_MARGIN
    assert get_settings(entry)[CONF_STALE_TIMEOUT] == DEFAULT_STALE_TIMEOUT

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


LOAD = {
    CONF_NAME: "Pool pump",
    CONF_LOAD_ENTITY: "switch.depuradora_piscina",
    CONF_NOMINAL_POWER: 750,
}


async def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data=SENSORS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    return entry


async def _add_load(hass: HomeAssistant, entry: MockConfigEntry, data: dict) -> dict:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_LOAD), context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.subentries.async_configure(result["flow_id"], data)


async def test_add_load_with_defaults(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass)
    result = await _add_load(hass, entry, LOAD)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (subentry,) = entry.subentries.values()
    assert subentry.title == "Pool pump"
    assert subentry.unique_id == "switch.depuradora_piscina"
    settings = load_settings(subentry)
    assert settings[CONF_PRIORITY] == DEFAULT_PRIORITY
    assert settings[CONF_RESPECT_MANUAL] is True


async def test_entity_belongs_to_one_load(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass)
    await _add_load(hass, entry, LOAD)
    result = await _add_load(hass, entry, {**LOAD, CONF_NAME: "Again"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_LOAD_ENTITY: "entity_in_use"}


async def test_reconfigure_load(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass)
    await _add_load(hass, entry, {**LOAD, CONF_POWER_SENSOR: "sensor.pool_power"})
    (subentry,) = entry.subentries.values()
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_LOAD),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": subentry.subentry_id},
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**LOAD, CONF_NAME: "Depuradora", CONF_PRIORITY: 1}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    subentry = entry.subentries[subentry.subentry_id]
    assert subentry.title == "Depuradora"
    settings = load_settings(subentry)
    assert settings[CONF_PRIORITY] == 1
    assert settings[CONF_POWER_SENSOR] is None  # cleared


TERMO = {
    CONF_NAME: "Termo",
    CONF_LOAD_TYPE: LOAD_TYPE_REGULABLE,
    CONF_LOAD_ENTITY: "switch.termo",
    CONF_NOMINAL_POWER: 1600,
    CONF_POWER_SENSOR: "sensor.termo_power",
}
REGULABLE = {
    CONF_LIMIT_ENTITY: "number.coladuria_x10_derivador_potencia_maxima",
    CONF_MODE_ENTITY: "select.coladuria_x10_derivador_modo",
    CONF_LIMIT_MAX: 1600,
}


async def test_add_regulable_load(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass)
    result = await _add_load(hass, entry, {**TERMO, SECTION_REGULABLE: REGULABLE})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (subentry,) = entry.subentries.values()
    assert SECTION_REGULABLE not in subentry.data  # stored flat
    settings = load_settings(subentry)
    assert settings[CONF_LIMIT_ENTITY] == "number.coladuria_x10_derivador_potencia_maxima"
    assert settings[CONF_LIMIT_MAX] == 1600
    assert settings[CONF_AUTO_STATE] == DEFAULT_AUTO_STATE
    assert settings[CONF_LIMIT_THRESHOLD] == DEFAULT_LIMIT_THRESHOLD


async def test_regulable_load_needs_its_entities(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass)
    result = await _add_load(hass, entry, {**TERMO, SECTION_REGULABLE: {CONF_MODE_ENTITY: "select.modo"}})
    assert result["errors"] == {"base": "regulable_incomplete"}
    no_power = {k: v for k, v in TERMO.items() if k != CONF_POWER_SENSOR}
    result = await _add_load(hass, entry, {**no_power, SECTION_REGULABLE: REGULABLE})
    assert result["errors"] == {"base": "regulable_incomplete"}
    result = await _add_load(hass, entry, {**TERMO, SECTION_REGULABLE: {**REGULABLE, CONF_LIMIT_MIN: 1600}})
    assert result["errors"] == {"base": "limit_range"}


async def test_relay_load_ignores_the_regulable_section(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass)
    result = await _add_load(hass, entry, LOAD)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (subentry,) = entry.subentries.values()
    assert load_settings(subentry)[CONF_LOAD_TYPE] == LOAD_TYPE_RELAY
