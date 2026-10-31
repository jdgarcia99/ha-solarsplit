"""The control loop inside Home Assistant: shadow mode, acting, entities."""

from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import CONF_NAME, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.solarsplit.const import (
    CONF_EXPORT_SENSOR,
    CONF_GLOBAL_CONDITION,
    CONF_HVAC_MODE,
    CONF_IMPORT_SENSOR,
    CONF_LOAD_ENTITY,
    CONF_MIN_OFF,
    CONF_MIN_ON,
    CONF_NOMINAL_POWER,
    CONF_OFF_DELAY,
    CONF_ON_DELAY,
    CONF_PRIORITY,
    CONF_RESPECT_GLOBAL,
    CONF_SETTLE_TIME,
    CONF_SHADOW_MODE,
    CONF_SMOOTHING_WINDOW,
    DOMAIN,
    SUBENTRY_LOAD,
)

from .conftest import EXPORT, IMPORT

POOL = "switch.depuradora_piscina"
AC = "climate.ac_salon"


def _load(name: str, entity: str, power: int, priority: int, **extra: Any) -> dict[str, Any]:
    data = {
        CONF_NAME: name, CONF_LOAD_ENTITY: entity, CONF_NOMINAL_POWER: power, CONF_PRIORITY: priority,
        CONF_ON_DELAY: 0, CONF_OFF_DELAY: 0, CONF_MIN_ON: 0, CONF_MIN_OFF: 0, **extra,
    }
    return {"data": data, "subentry_type": SUBENTRY_LOAD, "title": name, "unique_id": entity}


async def _setup(hass: HomeAssistant, shadow: bool = True, loads: list | None = None, **settings: Any) -> MockConfigEntry:
    hass.states.async_set(POOL, "off")
    hass.states.async_set(AC, "off")
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN,
        data={
            CONF_EXPORT_SENSOR: EXPORT, CONF_IMPORT_SENSOR: IMPORT, CONF_SHADOW_MODE: shadow,
            CONF_SMOOTHING_WINDOW: 0, CONF_SETTLE_TIME: 30, **settings,
        },
        subentries_data=loads if loads is not None else [
            _load("Pool", POOL, 750, 1), _load("Salon", AC, 900, 2, **{CONF_HVAC_MODE: "cool"}),
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity(hass: HomeAssistant, unique_id: str, domain: str = "sensor") -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id, unique_id
    return entity_id


def _sub_id(entry: MockConfigEntry, entity: str) -> str:
    return next(s.subentry_id for s in entry.subentries.values() if s.unique_id == entity)


async def _cycle(hass: HomeAssistant, entry: MockConfigEntry, export: float = 0, imp: float = 0) -> None:
    hass.states.async_set(EXPORT, str(export), {"unit_of_measurement": "W"})
    hass.states.async_set(IMPORT, str(imp), {"unit_of_measurement": "W"})
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


def _state(hass: HomeAssistant, entry: MockConfigEntry, key: str, entity: str | None = None) -> str:
    unique = f"{_sub_id(entry, entity)}_{key}" if entity else f"{entry.entry_id}_{key}"
    return hass.states.get(_entity(hass, unique)).state


async def test_shadow_mode_decides_without_acting(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    calls = async_mock_service(hass, "switch", "turn_on")
    await _cycle(hass, entry, export=1500)

    decision = hass.states.get(_entity(hass, f"{entry.entry_id}_last_decision"))
    assert decision.state == "turn_on"
    assert decision.attributes["load"] == "Pool" and decision.attributes["shadow"] is True
    assert float(_state(hass, entry, "budget")) == 1400
    assert _state(hass, entry, "mode") == "normal"
    assert _state(hass, entry, "load_state", POOL) == "waiting"
    assert _state(hass, entry, "load_reason", POOL) == "fits"
    assert _state(hass, entry, "load_reason", AC) == "no_budget"
    assert calls == []
    assert hass.states.get(POOL).state == "off"


async def test_acts_one_load_at_a_time(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    entry = await _setup(hass, shadow=False)
    switch_on = async_mock_service(hass, "switch", "turn_on")
    climate = async_mock_service(hass, "climate", "set_hvac_mode")

    await _cycle(hass, entry, export=2000)
    assert [c.data["entity_id"] for c in switch_on] == [POOL]
    hass.states.async_set(POOL, "on")

    await _cycle(hass, entry, export=1250)  # within the settle time
    assert climate == []
    assert _state(hass, entry, "load_reason", AC) == "settling"

    freezer.tick(timedelta(seconds=31))
    await _cycle(hass, entry, export=1250)
    assert [(c.data["entity_id"], c.data["hvac_mode"]) for c in climate] == [(AC, "cool")]


async def test_manual_change_is_respected(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    entry = await _setup(hass, shadow=False, loads=[_load("Pool", POOL, 750, 1)])
    async_mock_service(hass, "switch", "turn_on")
    switch_off = async_mock_service(hass, "switch", "turn_off")
    await _cycle(hass, entry, export=1000)
    hass.states.async_set(POOL, "on")

    hass.states.async_set(POOL, "off")  # someone switches it off by hand
    freezer.tick(timedelta(seconds=31))
    await _cycle(hass, entry, export=1000)
    assert _state(hass, entry, "load_state", POOL) == "manual"

    hass.states.async_set(POOL, "on")  # back to the state SolarSplit set: controlled again
    await _cycle(hass, entry, export=0, imp=500)
    assert _state(hass, entry, "load_reason", POOL) == "no_budget"
    assert [c.data["entity_id"] for c in switch_off] == [POOL]


async def test_master_and_managed_switches(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    managed = _entity(hass, f"{_sub_id(entry, POOL)}_managed", "switch")
    await hass.services.async_call("switch", "turn_off", {"entity_id": managed}, blocking=True)
    await _cycle(hass, entry, export=1500)
    assert _state(hass, entry, "load_state", POOL) == "unmanaged"
    assert _state(hass, entry, "load_reason", AC) == "fits"

    active = _entity(hass, f"{entry.entry_id}_active", "switch")
    await hass.services.async_call("switch", "turn_off", {"entity_id": active}, blocking=True)
    await _cycle(hass, entry, export=1500)
    assert _state(hass, entry, "mode") == "paused"
    assert _state(hass, entry, "last_decision") == "none"


async def test_global_condition_holds_loads_that_respect_it(hass: HomeAssistant) -> None:
    hass.states.async_set("input_boolean.casa_activada", "off")
    entry = await _setup(
        hass, loads=[_load("Pool", POOL, 750, 1, **{CONF_RESPECT_GLOBAL: False}), _load("Salon", AC, 900, 2)],
        **{CONF_GLOBAL_CONDITION: "input_boolean.casa_activada"},
    )
    await _cycle(hass, entry, export=3000)
    assert _state(hass, entry, "load_reason", POOL) == "fits"
    assert _state(hass, entry, "load_reason", AC) == "global_condition"


async def test_on_delay_through_the_loop(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    entry = await _setup(hass, shadow=False, loads=[_load("Pool", POOL, 750, 1, **{CONF_ON_DELAY: 60})])
    switch_on = async_mock_service(hass, "switch", "turn_on")
    await _cycle(hass, entry, export=1000)
    assert _state(hass, entry, "load_reason", POOL) == "on_delay"
    freezer.tick(timedelta(seconds=30))
    await _cycle(hass, entry, export=1000)
    assert switch_on == []
    freezer.tick(timedelta(seconds=30))
    await _cycle(hass, entry, export=1000)
    assert [c.data["entity_id"] for c in switch_on] == [POOL]


async def test_priority_number_updates_the_load(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    number = _entity(hass, f"{_sub_id(entry, AC)}_priority", "number")
    await hass.services.async_call("number", "set_value", {"entity_id": number, "value": 1}, blocking=True)
    await hass.async_block_till_done()
    assert entry.subentries[_sub_id(entry, AC)].data[CONF_PRIORITY] == 1


@pytest.mark.parametrize("sensor", [EXPORT, IMPORT])
async def test_unavailable_grid_makes_entities_unavailable(hass: HomeAssistant, sensor: str) -> None:
    entry = await _setup(hass)
    hass.states.async_set(sensor, STATE_UNAVAILABLE)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(_entity(hass, f"{entry.entry_id}_budget")).state == STATE_UNAVAILABLE
