"""Config and options flows."""

from unittest.mock import patch

import pytest
import voluptuous_serialize
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_validation as cv
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarsplit import get_settings, load_settings
from custom_components.solarsplit.const import (
    CONF_AUTO_MANAGE,
    CONF_AUTO_STATE,
    CONF_EXPORT_SENSOR,
    CONF_GLOBAL_CONDITION,
    CONF_GRID_SENSOR,
    CONF_GRID_SIGN,
    CONF_HOURS_SENSOR,
    CONF_IMPORT_SENSOR,
    CONF_LIMIT_ENTITY,
    CONF_LIMIT_MAX,
    CONF_LIMIT_MIN,
    CONF_LIMIT_THRESHOLD,
    CONF_LOAD_ENTITY,
    CONF_LOAD_TYPE,
    CONF_MANAGE_AT,
    CONF_MODE_ENTITY,
    CONF_NOMINAL_POWER,
    CONF_ON_DEMAND,
    CONF_POWER_SENSOR,
    CONF_PRIORITY,
    CONF_RESPECT_MANUAL,
    CONF_SAFETY_MARGIN,
    CONF_SEASON_ENTITY,
    CONF_SEASONS,
    CONF_STALE_TIMEOUT,
    CONF_TARGET_ENTITY,
    CONF_TARGET_HOURS,
    DEFAULT_AUTO_STATE,
    DEFAULT_GRID_SIGN,
    DEFAULT_LIMIT_THRESHOLD,
    DEFAULT_PRIORITY,
    DEFAULT_SAFETY_MARGIN,
    DEFAULT_STALE_TIMEOUT,
    DOMAIN,
    GRID_SIGN_EXPORT_POSITIVE,
    LOAD_TYPE_REGULABLE,
    LOAD_TYPE_RELAY,
    SECTION_CONTROL,
    SECTION_DAILY_TARGET,
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


def _initial(fields: list[dict]) -> dict:
    """The data the frontend starts a form with (computeInitialHaFormData)."""
    data: dict = {}
    for field in fields:
        suggested = (field.get("description") or {}).get("suggested_value")
        if suggested is not None:
            data[field["name"]] = suggested
        elif "default" in field:
            data[field["name"]] = field["default"]
        elif field.get("type") == "expandable":
            inner = _initial(field["schema"])
            if field.get("required") or inner:
                data[field["name"]] = inner
    return data


def _submit(form: dict, changes: dict) -> dict:
    """What the frontend sends: the form's initial data with the user's changes.

    A section in `changes` updates that section; None clears a field.
    """
    data = _initial(voluptuous_serialize.convert(form["data_schema"], custom_serializer=cv.custom_serializer))
    for key, value in changes.items():
        if isinstance(value, dict):
            data[key] = {**data.get(key, {}), **value}
        else:
            data[key] = value
    return _clean(data)


def _clean(data: dict) -> dict:
    return {k: _clean(v) if isinstance(v, dict) else v for k, v in data.items() if v is not None}


async def _add_load(hass: HomeAssistant, entry: MockConfigEntry, data: dict) -> dict:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_LOAD), context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.subentries.async_configure(result["flow_id"], _submit(result, data))


async def _reconfigure(hass: HomeAssistant, entry: MockConfigEntry, subentry_id: str, changes: dict) -> dict:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_LOAD),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": subentry_id},
    )
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.subentries.async_configure(result["flow_id"], _submit(result, changes))


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
    result = await _reconfigure(
        hass, entry, subentry.subentry_id, {CONF_NAME: "Depuradora", CONF_PRIORITY: 1, CONF_POWER_SENSOR: None}
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


async def test_add_load_with_daily_target(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass)
    target = {CONF_HOURS_SENSOR: "sensor.piscina_on_hoy", CONF_TARGET_ENTITY: "input_number.horas_piscina"}
    result = await _add_load(hass, entry, {**LOAD, SECTION_DAILY_TARGET: target})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (subentry,) = entry.subentries.values()
    assert SECTION_DAILY_TARGET not in subentry.data
    settings = load_settings(subentry)
    assert settings[CONF_HOURS_SENSOR] == "sensor.piscina_on_hoy"
    assert settings[CONF_TARGET_ENTITY] == "input_number.horas_piscina"
    assert settings[CONF_TARGET_HOURS] == 0


async def test_user_flow_with_signed_grid_sensor(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_GRID_SENSOR: "sensor.grid", CONF_GRID_SIGN: GRID_SIGN_EXPORT_POSITIVE}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    settings = get_settings(result["result"])
    assert settings[CONF_GRID_SENSOR] == "sensor.grid"
    assert settings[CONF_GRID_SIGN] == GRID_SIGN_EXPORT_POSITIVE
    assert settings[CONF_EXPORT_SENSOR] is None


@pytest.mark.parametrize(
    "sensors",
    [
        {},  # none
        {CONF_EXPORT_SENSOR: "sensor.export"},  # only one of the split sensors
        {**SENSORS, CONF_GRID_SENSOR: "sensor.grid"},  # both modes at once
    ],
)
async def test_user_flow_needs_one_grid_mode(hass: HomeAssistant, sensors: dict) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], sensors)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "grid_sensors"}


async def test_options_flow_switches_to_signed_grid_sensor(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data=SENSORS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_GRID_SENSOR: "sensor.grid"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    settings = get_settings(entry)
    assert settings[CONF_GRID_SENSOR] == "sensor.grid"
    assert settings[CONF_GRID_SIGN] == DEFAULT_GRID_SIGN
    assert settings[CONF_EXPORT_SENSOR] is None and settings[CONF_IMPORT_SENSOR] is None  # cleared

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "grid_sensors"}


# ---------------------------------------------------------------------------
# Sections filled by the frontend, manual control and season
# ---------------------------------------------------------------------------

SEASON = "input_select.temporada"


async def test_sections_have_no_default(hass: HomeAssistant) -> None:
    """With a default the frontend sends that default and ignores the section's fields."""
    entry = await _setup_entry(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_LOAD), context={"source": config_entries.SOURCE_USER}
    )
    fields = voluptuous_serialize.convert(result["data_schema"], custom_serializer=cv.custom_serializer)
    sections = [field for field in fields if field.get("type") == "expandable"]
    assert {field["name"] for field in sections} == {SECTION_CONTROL, SECTION_REGULABLE, SECTION_DAILY_TARGET}
    assert all(field["required"] and "default" not in field for field in sections)
    regulable = _initial(fields)[SECTION_REGULABLE]
    assert regulable[CONF_AUTO_STATE] == DEFAULT_AUTO_STATE
    assert regulable[CONF_LIMIT_THRESHOLD] == DEFAULT_LIMIT_THRESHOLD


async def test_editing_a_regulable_load_keeps_its_section(hass: HomeAssistant) -> None:
    """The bug: the section came back empty and its entities were cleared on save."""
    entry = await _setup_entry(hass)
    await _add_load(hass, entry, {**TERMO, SECTION_REGULABLE: REGULABLE})
    (subentry,) = entry.subentries.values()
    result = await _reconfigure(hass, entry, subentry.subentry_id, {CONF_PRIORITY: 2})
    assert result["reason"] == "reconfigure_successful"
    settings = load_settings(entry.subentries[subentry.subentry_id])
    assert settings[CONF_PRIORITY] == 2
    assert settings[CONF_LIMIT_ENTITY] == REGULABLE[CONF_LIMIT_ENTITY]
    assert settings[CONF_MODE_ENTITY] == REGULABLE[CONF_MODE_ENTITY]
    assert settings[CONF_LIMIT_MAX] == 1600


async def test_load_with_manual_control_options(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass)
    control = {CONF_ON_DEMAND: True, CONF_AUTO_MANAGE: True, CONF_MANAGE_AT: "03:30:00"}
    result = await _add_load(hass, entry, {**LOAD, SECTION_CONTROL: control})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (subentry,) = entry.subentries.values()
    assert SECTION_CONTROL not in subentry.data  # stored flat
    settings = load_settings(subentry)
    assert settings[CONF_ON_DEMAND] is True
    assert settings[CONF_AUTO_MANAGE] is True
    assert settings[CONF_MANAGE_AT] == "03:30:00"
    assert settings[CONF_SEASONS] == []


async def _setup_with_season(hass: HomeAssistant) -> MockConfigEntry:
    hass.states.async_set(SEASON, "Verano", {"options": ["Verano", "Invierno", "Entretiempo"]})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={**SENSORS, CONF_SEASON_ENTITY: SEASON})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    return entry


async def test_seasons_offer_the_options_of_the_season_entity(hass: HomeAssistant) -> None:
    entry = await _setup_with_season(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_LOAD), context={"source": config_entries.SOURCE_USER}
    )
    control = result["data_schema"].schema[SECTION_CONTROL].schema.schema
    seasons = next(value for key, value in control.items() if key == CONF_SEASONS)
    assert seasons.config["options"] == ["Verano", "Invierno", "Entretiempo"]
    assert seasons.config["multiple"] is True
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], _submit(result, {**LOAD, SECTION_CONTROL: {CONF_SEASONS: ["Verano"]}})
    )
    (subentry,) = entry.subentries.values()
    assert load_settings(subentry)[CONF_SEASONS] == ["Verano"]


async def test_seasons_kept_without_a_season_entity(hass: HomeAssistant) -> None:
    """Without the entity the field is hidden and the stored seasons stay."""
    entry = await _setup_entry(hass)
    await _add_load(hass, entry, LOAD)
    (subentry,) = entry.subentries.values()
    hass.config_entries.async_update_subentry(entry, subentry, data={**subentry.data, CONF_SEASONS: ["Verano"]})
    await hass.async_block_till_done()
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_LOAD),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": subentry.subentry_id},
    )
    assert CONF_SEASONS not in result["data_schema"].schema[SECTION_CONTROL].schema.schema
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], _submit(result, {}))
    assert result["reason"] == "reconfigure_successful"
    assert load_settings(entry.subentries[subentry.subentry_id])[CONF_SEASONS] == ["Verano"]


async def test_options_set_and_clear_the_season_entity(hass: HomeAssistant) -> None:
    entry = await _setup_with_season(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], SENSORS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert get_settings(entry)[CONF_SEASON_ENTITY] is None  # cleared


async def test_add_load_with_a_minimum_and_urgency(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass)
    objective = {
        "min_hours": 4, "day_start": "17:00:00", "deadline": "09:00:00", "min_priority": 1,
        "window_start": "10:00:00", "window_end": "17:00:00",
    }
    control = {"urgency_entity": "binary_sensor.tankwise_plan_at_risk", "urgency_priority": 1}
    result = await _add_load(hass, entry, {**LOAD, SECTION_DAILY_TARGET: objective, "control": control})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (subentry,) = entry.subentries.values()
    settings = load_settings(subentry)
    assert (settings["min_hours"], settings["deadline"], settings["day_start"]) == (4, "09:00:00", "17:00:00")
    assert settings["urgency_entity"] == "binary_sensor.tankwise_plan_at_risk"
    # Editing it keeps everything; clearing the window removes it
    result = await _reconfigure(hass, entry, subentry.subentry_id, {
        SECTION_DAILY_TARGET: {"window_start": None, "window_end": None},
    })
    assert result["reason"] == "reconfigure_successful"
    settings = load_settings(entry.subentries[subentry.subentry_id])
    assert settings["window_start"] is None and settings["min_hours"] == 4
    assert settings["urgency_entity"] == "binary_sensor.tankwise_plan_at_risk"


async def test_minimum_and_window_are_validated(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass)
    result = await _add_load(hass, entry, {**TERMO, SECTION_REGULABLE: REGULABLE, SECTION_DAILY_TARGET: {"min_hours": 2}})
    assert result["errors"] == {"base": "minimum_relay_only"}
    result = await _add_load(hass, entry, {**LOAD, SECTION_DAILY_TARGET: {"window_start": "10:00:00"}})
    assert result["errors"] == {"base": "window_incomplete"}


async def test_options_set_the_prices_and_the_contracted_power(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    changes = {"buy_price_sensor": "sensor.pvpc", "contracted_power": 9200, "grid_margin": 1000}
    result = await hass.config_entries.options.async_configure(result["flow_id"], _submit(result, changes))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    settings = get_settings(entry)
    assert settings["buy_price_sensor"] == "sensor.pvpc" and settings["sell_price_sensor"] is None
    assert entry.runtime_data.grid_cap == 8200


async def test_options_choose_the_solar_forecast_entries(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass)
    entries = [("string_1", "String 1 (forecast_solar)"), ("string_2", "String 2 (forecast_solar)")]
    with patch("custom_components.solarsplit.config_flow.async_forecast_entries", return_value=entries):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _submit(result, {"solar_forecast_entries": ["string_1", "string_2"], "base_load": 350})
        )
    assert get_settings(entry)["solar_forecast_entries"] == ["string_1", "string_2"]
    assert get_settings(entry)["base_load"] == 350
    # No forecast entry to choose from (Forecast.Solar not loaded yet): the field is hidden and kept
    with patch("custom_components.solarsplit.config_flow.async_forecast_entries", return_value=[]):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert "solar_forecast_entries" not in result["data_schema"].schema
        result = await hass.config_entries.options.async_configure(result["flow_id"], _submit(result, {}))
    assert get_settings(entry)["solar_forecast_entries"] == ["string_1", "string_2"]
