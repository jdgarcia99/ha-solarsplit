"""Every user-facing text is translated, the same way in the three files."""

import json
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import section
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarsplit.config_flow import load_schema, settings_schema
from custom_components.solarsplit.const import (
    CONF_CIRCUIT_SENSOR,
    CONF_EXPORT_SENSOR,
    CONF_IMPORT_SENSOR,
    CONF_ON_DEMAND,
    DOMAIN,
    SUBENTRY_LOAD,
)
from custom_components.solarsplit.coordinator import LOAD_STATES, MODES
from custom_components.solarsplit.engine import Action, Reason
from custom_components.solarsplit.settings import DEFAULTS, LOAD_DEFAULTS

FOLDER = Path(__file__).parents[1] / "custom_components" / DOMAIN
FILES = {
    "strings": FOLDER / "strings.json",
    "en": FOLDER / "translations" / "en.json",
    "es": FOLDER / "translations" / "es.json",
}
TEXTS = {name: json.loads(path.read_text(encoding="utf-8")) for name, path in FILES.items()}


def _keys(data: Any, prefix: str = "") -> set[str]:
    if not isinstance(data, dict):
        return {prefix}
    keys = set()
    for key, value in data.items():
        keys |= _keys(value, f"{prefix}/{key}")
    return keys


def test_the_three_files_have_the_same_keys() -> None:
    strings = _keys(TEXTS["strings"])
    for name in ("en", "es"):
        assert _keys(TEXTS[name]) == strings, f"{name}.json: {_keys(TEXTS[name]) ^ strings}"


def test_strings_json_is_the_english_translation() -> None:
    assert TEXTS["strings"] == TEXTS["en"]


def test_no_text_is_empty() -> None:
    for name, data in TEXTS.items():
        empty = [key for key in _keys(data) if not _value(data, key).strip()]
        assert not empty, f"{name}.json: {empty}"


def _value(data: Any, path: str) -> str:
    for key in path.strip("/").split("/"):
        data = data[key]
    return data


def _states(entity: str) -> set[str]:
    return set(TEXTS["strings"]["entity"]["sensor"][entity]["state"])


def test_enum_sensors_and_reason_codes_are_translated() -> None:
    reasons = {reason.value for reason in Reason}
    assert _states("mode") == set(MODES)
    assert _states("load_state") == set(LOAD_STATES)
    assert _states("last_decision") == {action.value for action in Action}
    assert _states("load_reason") == reasons
    attributes = TEXTS["strings"]["entity"]["sensor"]["last_decision"]["state_attributes"]
    assert set(attributes["reason"]["state"]) == reasons


def _fields(schema: vol.Schema) -> list[tuple[str | None, str, Any]]:
    """(section, key, selector) of every field of a form, inside sections too."""
    fields = []
    for marker, value in schema.schema.items():
        if isinstance(value, section):
            fields += [(str(marker), key, inner) for _, key, inner in _fields(value.schema)]
        else:
            fields.append((None, str(marker), value))
    return fields


def _check_form(step: dict, schema: vol.Schema) -> None:
    for section_key, key, field in _fields(schema):
        block = step["sections"][section_key] if section_key else step
        assert key in block["data"], f"label of {key}"
        assert key in block["data_description"], f"description of {key}"
        if section_key:
            assert block.get("name"), f"name of section {section_key}"
        if isinstance(field, selector.SelectSelector) and (key_ := field.config.get("translation_key")):
            options = TEXTS["strings"]["selector"][key_]["options"]
            assert set(field.config["options"]) <= set(options), f"options of {key}"


def test_every_form_field_is_translated() -> None:
    config = TEXTS["strings"]["config"]["step"]["user"]
    options = TEXTS["strings"]["options"]["step"]["init"]
    for step in (config, options):
        _check_form(step, settings_schema(DEFAULTS))
    for step_id in ("user", "reconfigure"):
        step = TEXTS["strings"]["config_subentries"][SUBENTRY_LOAD]["step"][step_id]
        # With a season entity the seasons field is shown too.
        _check_form(step, load_schema({**LOAD_DEFAULTS, "name": "x"}, ["Verano"]))


async def test_every_entity_has_a_translated_name(hass: HomeAssistant) -> None:
    hass.states.async_set("switch.pool", "off")
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN,
        data={CONF_EXPORT_SENSOR: "sensor.export", CONF_IMPORT_SENSOR: "sensor.import"},
        subentries_data=[{
            "data": {
                "name": "Pool", "entity_id": "switch.pool", "nominal_power": 750,
                CONF_ON_DEMAND: True, CONF_CIRCUIT_SENSOR: "sensor.circuit",
            },
            "subentry_type": SUBENTRY_LOAD, "title": "Pool", "unique_id": "switch.pool",
        }],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert {entity.domain for entity in entities} == {"binary_sensor", "number", "sensor", "switch"}
    assert "learned_power" in {entity.translation_key for entity in entities}
    for entity in entities:
        assert entity.translation_key in TEXTS["strings"]["entity"][entity.domain], entity.entity_id
