"""Without solar production surplus management does not apply."""

from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.solarsplit.const import (
    CONF_BUY_PRICE_SENSOR,
    CONF_DEADLINE,
    CONF_HVAC_MODE,
    CONF_MIN_HOURS,
    CONF_ON_DEMAND,
    CONF_PV_SENSOR,
    CONF_SOLAR_ENTITY,
)

from .test_coordinator import (
    AC,
    ENTRY_ID,
    LIMIT,
    POOL,
    TERMO,
    TERMO_POWER,
    _cycle,
    _limits,
    _load,
    _setup,
    _setup_termo,
    _sid,
    _state,
    _termo,
)
from .test_objectives import BUY, _at, _set_prices

SUN = "binary_sensor.produccion_solar"
PV = "sensor.pv_power"


def _ac_on_demand() -> dict[str, Any]:
    return _load("Salon", AC, 900, 2, **{CONF_ON_DEMAND: True, CONF_HVAC_MODE: "cool"})


async def test_night_leaves_an_on_demand_load_on(hass: HomeAssistant) -> None:
    hass.states.async_set(SUN, "off")
    entry = await _setup(hass, shadow=False, loads=[_ac_on_demand()], **{CONF_SOLAR_ENTITY: SUN})
    hvac = async_mock_service(hass, "climate", "set_hvac_mode")
    hass.states.async_set(AC, "cool")  # someone asks for it at night
    for _ in range(3):
        await _cycle(hass, entry, imp=900)
    assert hvac == []
    assert _state(hass, entry, "mode") == "no_solar"
    assert _state(hass, entry, "load_reason", AC) == "no_solar"
    assert _state(hass, entry, "load_state", AC) == "on"


async def test_load_switched_on_by_surplus_is_switched_off_at_dusk(hass: HomeAssistant) -> None:
    hass.states.async_set(SUN, "on")
    entry = await _setup(hass, shadow=False, loads=[_load("Pool", POOL, 750, 1)], **{CONF_SOLAR_ENTITY: SUN})
    switch_on = async_mock_service(hass, "switch", "turn_on")
    switch_off = async_mock_service(hass, "switch", "turn_off")
    await _cycle(hass, entry, export=1500)
    assert [c.data["entity_id"] for c in switch_on] == [POOL]
    hass.states.async_set(POOL, "on")
    await _cycle(hass, entry, export=700)

    hass.states.async_set(SUN, "off")  # dusk: the surplus is gone
    entry.runtime_data.last_action = None  # past the settle time
    await _cycle(hass, entry, imp=500)
    assert [c.data["entity_id"] for c in switch_off] == [POOL]
    assert _state(hass, entry, "load_reason", POOL) == "no_budget"


async def test_grid_fill_ends_at_night(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    # 1 h before 07:00 at 05:00, the cheapest hour: switched on from the grid, off when it is done.
    _at(freezer, 5)
    hass.states.async_set(SUN, "off")
    _set_prices(hass, {5: 0.04})
    entry = await _setup(
        hass, shadow=False, loads=[_load("Pool", POOL, 530, 3, **{CONF_MIN_HOURS: 1, CONF_DEADLINE: "07:00:00"})],
        **{CONF_SOLAR_ENTITY: SUN, CONF_BUY_PRICE_SENSOR: BUY},
    )
    switch_on = async_mock_service(hass, "switch", "turn_on")
    switch_off = async_mock_service(hass, "switch", "turn_off")
    await _cycle(hass, entry, imp=300)
    assert [c.data["entity_id"] for c in switch_on] == [POOL]
    hass.states.async_set(POOL, "on")
    await _cycle(hass, entry, imp=830)
    assert _state(hass, entry, "load_reason", POOL) == "grid_fill"

    _at(freezer, 6 + 1 / 60)  # the minimum is met, a little light but no production yet
    await _cycle(hass, entry, imp=830)
    assert [c.data["entity_id"] for c in switch_off] == [POOL]


async def test_regulable_at_night_gets_its_maximum_and_nothing_else(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    # SolarSplit had lowered the heater's limit to 800 W in the afternoon.
    hass_storage[f"solarsplit.{ENTRY_ID}"] = {
        "version": 1, "key": f"solarsplit.{ENTRY_ID}",
        "data": {"saved_at": dt_util.utcnow().timestamp(), "loads": {_sid(TERMO): {"lowered": True}}},
    }
    hass.states.async_set(SUN, "off")
    entry, calls = await _setup_termo(hass, power=0, limit=800, loads=[_termo(1)], **{CONF_SOLAR_ENTITY: SUN})
    await _cycle(hass, entry, imp=300)
    assert _limits(calls) == [(LIMIT, 1600)]
    assert _state(hass, entry, "load_reason", TERMO) == "no_solar"
    # The maximum is back: TankWise heats from the grid and nothing more is written
    hass.states.async_set(LIMIT, "1600", {"min": 0, "max": 3500, "step": 50})
    hass.states.async_set(TERMO_POWER, "1500", {"unit_of_measurement": "W"})
    for _ in range(3):
        await _cycle(hass, entry, imp=1800)
    assert _limits(calls) == [(LIMIT, 1600)]


@pytest.mark.parametrize("sun", ["unavailable", "unknown"])
async def test_unknown_solar_entity_is_no_production(hass: HomeAssistant, sun: str) -> None:
    hass.states.async_set(SUN, sun)
    entry = await _setup(hass, shadow=False, loads=[_ac_on_demand()], **{CONF_SOLAR_ENTITY: SUN})
    hvac = async_mock_service(hass, "climate", "set_hvac_mode")
    hass.states.async_set(AC, "cool")
    await _cycle(hass, entry, imp=900)
    assert _state(hass, entry, "mode") == "no_solar" and hvac == []
    # Nor is anything switched on with a surplus reading
    hass.states.async_set(AC, "off")
    await _cycle(hass, entry, export=3000)
    assert hvac == [] and _state(hass, entry, "load_reason", AC) == "no_solar"


async def test_load_switched_on_by_hand_at_night_is_manual_at_dawn(hass: HomeAssistant) -> None:
    hass.states.async_set(SUN, "off")
    entry = await _setup(hass, shadow=False, loads=[_load("Pool", POOL, 750, 1)], **{CONF_SOLAR_ENTITY: SUN})
    switch_off = async_mock_service(hass, "switch", "turn_off")
    hass.states.async_set(POOL, "on")  # by hand, at night
    await _cycle(hass, entry, imp=750)
    assert _state(hass, entry, "load_reason", POOL) == "no_solar"

    hass.states.async_set(SUN, "on")  # dawn, still no surplus
    for _ in range(3):
        await _cycle(hass, entry, imp=500)
    assert switch_off == []
    assert _state(hass, entry, "load_state", POOL) == "manual"
    # Switched off by that person: like any load switched off from outside, never switched
    # back on by SolarSplit until it is on again or Manage is turned off and on.
    hass.states.async_set(POOL, "off")
    await _cycle(hass, entry, export=1500)
    assert _state(hass, entry, "load_reason", POOL) == "switched_off"


async def test_pv_threshold_with_hysteresis(hass: HomeAssistant) -> None:
    hass.states.async_set(PV, "120", {"unit_of_measurement": "W"})
    entry = await _setup(hass, loads=[_load("Pool", POOL, 750, 1)], **{CONF_PV_SENSOR: PV})
    modes = []
    for pv in ("80", "40", "80", "120", "unavailable"):
        hass.states.async_set(PV, pv, {"unit_of_measurement": "W"})
        await _cycle(hass, entry)
        modes.append(_state(hass, entry, "mode"))
    # On above 100 W, off below 50 W, unchanged in between; unknown is no production.
    assert modes == ["normal", "no_solar", "no_solar", "normal", "no_solar"]


async def test_shadow_mode_does_not_own_a_load_switched_on_at_night(hass: HomeAssistant) -> None:
    # Shadow mode cannot know who switched a load on: only a load that went on with sun is
    # assumed to be SolarSplit's, so it would be switched off at dusk.
    hass.states.async_set(SUN, "off")
    entry = await _setup(hass, loads=[_load("Pool", POOL, 750, 1)], **{CONF_SOLAR_ENTITY: SUN})
    hass.states.async_set(POOL, "on")  # an automation, at night
    await _cycle(hass, entry, imp=750)
    assert _state(hass, entry, "load_reason", POOL) == "no_solar"
    assert _state(hass, entry, "last_decision") == "none"

    hass.states.async_set(POOL, "off")
    hass.states.async_set(SUN, "on")
    await _cycle(hass, entry)
    hass.states.async_set(POOL, "on")  # with sun
    await _cycle(hass, entry, export=500)
    hass.states.async_set(SUN, "off")  # dusk
    entry.runtime_data.last_action = None
    await _cycle(hass, entry, imp=750)
    assert _state(hass, entry, "load_reason", POOL) == "no_budget"
    assert _state(hass, entry, "last_decision") == "turn_off"
