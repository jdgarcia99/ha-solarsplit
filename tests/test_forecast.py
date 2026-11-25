"""Expected surplus from the solar forecast inside Home Assistant."""

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarsplit.const import (
    CONF_BUY_PRICE_SENSOR,
    CONF_DEADLINE,
    CONF_FORECAST_ENTRIES,
    CONF_MIN_HOURS,
    CONF_PV_SENSOR,
)
from custom_components.solarsplit.forecast import (
    async_forecast_entries,
    async_read_forecast,
)

from .conftest import EXPORT
from .test_coordinator import ENTRY_ID, POOL, _cycle, _entity, _setup, _sub_id
from .test_objectives import BUY, _at, _objective, _plan_hours, _pump, _set_prices

PV = "sensor.pv_power"
READ = "custom_components.solarsplit.coordinator.async_read_forecast"


def _local_hour(hour: int, days: int = 0) -> str:
    """ISO time of a local hour of today (or a later day)."""
    return (dt_util.start_of_local_day() + timedelta(days=days, hours=hour)).isoformat()


def _wh(hours: dict[int, float], days: int = 0) -> dict[float, float]:
    """A summed forecast: Wh per local hour start."""
    start = dt_util.start_of_local_day() + timedelta(days=days)
    return {(start + timedelta(hours=h)).timestamp(): wh for h, wh in hours.items()}


async def _forecast_setup(
    hass: HomeAssistant, memory: dict | None, hass_storage: dict[str, Any], pool: str = "off",
    load: dict | None = None, **settings: Any,
) -> MockConfigEntry:
    """The pump needs 1 h before 17:00; the cheapest hour is 08:00, then 14:00."""
    _set_prices(hass, {8: 0.04, 14: 0.05})
    if memory is not None:
        hass_storage[f"solarsplit.{ENTRY_ID}"] = {
            "version": 1, "key": f"solarsplit.{ENTRY_ID}",
            "data": {"saved_at": dt_util.utcnow().timestamp(), "loads": {}, **memory},
        }
    return await _setup(
        hass, pool=pool, loads=[_pump(**{CONF_MIN_HOURS: 1, CONF_DEADLINE: "17:00:00", **(load or {})})],
        **{CONF_BUY_PRICE_SENSOR: BUY, **settings},
    )


def _profile(hour: int, watts: float) -> dict[str, list[list[float]]]:
    """A day of history with one reading at an hour."""
    hours = [[0.0, 0] for _ in range(24)]
    hours[hour] = [watts, 1]
    return {"2026-01-01": hours}


async def test_forecast_covers_the_minimum(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage: dict[str, Any]
) -> None:
    # 3000 Wh forecast at 12:00 and a house that uses 900 W then: 2100 W of surplus for the 530 W pump.
    _at(freezer, 8)
    with patch(READ, return_value=_wh({12: 3000})):
        entry = await _forecast_setup(
            hass, {"base": _profile(12, 900)}, hass_storage, **{CONF_FORECAST_ENTRIES: ["forecast_1"]}
        )
        await _cycle(hass, entry, export=0)
    assert _plan_hours(hass, entry) == [] and _plan_hours(hass, entry, grid=False) == [(0, 12)]
    cost = _objective(hass, entry, "objective_cost").attributes
    assert cost["surplus_source"] == "forecast" and cost["plan"][0]["source"] == "forecast"


async def test_forecast_less_the_learned_base_load_is_not_enough(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage: dict[str, Any]
) -> None:
    # The house uses 2800 W at 12:00: 200 W left, the pump needs the grid (08:00, the cheapest).
    _at(freezer, 8)
    with patch(READ, return_value=_wh({12: 3000})):
        entry = await _forecast_setup(
            hass, {"base": _profile(12, 2800)}, hass_storage, **{CONF_FORECAST_ENTRIES: ["forecast_1"]}
        )
        await _cycle(hass, entry, export=0)
    assert _plan_hours(hass, entry) == [(0, 8)]
    assert _objective(hass, entry, "objective_cost").attributes["surplus_source"] == "forecast"


async def test_forecast_without_consumption_history_uses_the_fixed_base_load(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage: dict[str, Any]
) -> None:
    _at(freezer, 8)
    # 900 Wh at 12:00 less the default 400 W: 500 W, short of the pump's 530 W. 12:00 needs
    # the grid for 30 W only, so it is still the cheapest hour (about 0.02 €/kWh).
    with patch(READ, return_value=_wh({12: 900})):
        entry = await _forecast_setup(hass, None, hass_storage, **{CONF_FORECAST_ENTRIES: ["forecast_1"]})
        await _cycle(hass, entry, export=0)
    assert _plan_hours(hass, entry) == [(0, 12)]
    [slot] = _objective(hass, entry, "objective_cost").attributes["plan"]
    covered = 500 / 530
    assert slot["price"] == pytest.approx((1 - covered) * 0.30, abs=1e-4)  # no sell price: the sun is free


async def test_fixed_base_load_setting(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage: dict[str, Any]
) -> None:
    _at(freezer, 8)
    with patch(READ, return_value=_wh({12: 900})):
        entry = await _forecast_setup(
            hass, None, hass_storage, **{CONF_FORECAST_ENTRIES: ["forecast_1"], "base_load": 300}
        )
        await _cycle(hass, entry, export=0)
    assert _plan_hours(hass, entry) == [] and _plan_hours(hass, entry, grid=False) == [(0, 12)]


async def test_without_forecast_the_learned_surplus(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage: dict[str, Any]
) -> None:
    _at(freezer, 8)
    entry = await _forecast_setup(hass, {"surplus": _profile(13, 1500)}, hass_storage)
    await _cycle(hass, entry, export=0)
    assert _plan_hours(hass, entry, grid=False) == [(0, 13)]
    assert _objective(hass, entry, "objective_cost").attributes["surplus_source"] == "learned"


async def test_without_forecast_or_history_no_surplus(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage: dict[str, Any]
) -> None:
    _at(freezer, 8)
    entry = await _forecast_setup(hass, None, hass_storage)
    await _cycle(hass, entry, export=0)
    assert _plan_hours(hass, entry) == [(0, 8)]
    assert _objective(hass, entry, "objective_cost").attributes["surplus_source"] == "none"


async def test_forecast_without_data_falls_back_to_the_learned_surplus(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage: dict[str, Any]
) -> None:
    _at(freezer, 8)
    with patch(READ, return_value=None):  # the entries give nothing (not loaded, removed)
        entry = await _forecast_setup(
            hass, {"surplus": _profile(13, 1500)}, hass_storage, **{CONF_FORECAST_ENTRIES: ["forecast_1"]}
        )
        await _cycle(hass, entry, export=0)
    assert _plan_hours(hass, entry, grid=False) == [(0, 13)]
    assert _objective(hass, entry, "objective_cost").attributes["surplus_source"] == "learned"


async def test_partial_forecast(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage: dict[str, Any]
) -> None:
    # From 18:00 to 16:00 tomorrow. The forecast covers only today: tonight's hours (listed by
    # nobody) produce nothing; tomorrow, not covered, uses the surplus learned at 13:00.
    _at(freezer, 18 + 10 / 60)
    with patch(READ, return_value=_wh({12: 3000})):
        entry = await _forecast_setup(
            hass, {"surplus": _profile(13, 1500)}, hass_storage, load={"day_start": "18:00:00", CONF_DEADLINE: "16:00:00"},
            **{CONF_FORECAST_ENTRIES: ["forecast_1"]},
        )
        await _cycle(hass, entry, export=0)
    assert _plan_hours(hass, entry) == [] and _plan_hours(hass, entry, grid=False) == [(1, 13)]
    cost = _objective(hass, entry, "objective_cost").attributes
    assert cost["plan"][0]["source"] == "learned"
    assert cost["surplus_source"] == "forecast"  # tonight's hours came from the forecast


async def test_base_load_is_learned_without_solarsplit_loads(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage: dict[str, Any]
) -> None:
    _at(freezer, 12)
    hass.states.async_set(PV, "3000", {"unit_of_measurement": "W"})
    hass.states.async_set(EXPORT, "1000", {"unit_of_measurement": "W"})  # from the first cycle on
    entry = await _forecast_setup(hass, None, hass_storage, pool="on", **{CONF_PV_SENSOR: PV})
    await _cycle(hass, entry, export=1000, imp=0)
    # The house: 3000 W of PV - 1000 W exported = 2000 W, of which 530 W are the pump.
    base = entry.runtime_data.base
    assert base.known(12) and base.expected(12) == pytest.approx(2000 - 530)


async def test_forecast_entries_are_read_and_summed(hass: HomeAssistant) -> None:
    strings = [MockConfigEntry(domain="forecast_solar", title=f"String {i}") for i in (1, 2)]
    for entry in strings:
        entry.add_to_hass(hass)

    async def forecast(hass: HomeAssistant, entry_id: str) -> dict:
        return {"wh_hours": {_local_hour(13): 1000 if entry_id == strings[0].entry_id else 500}}

    async def platform(hass: HomeAssistant, domain: str) -> Any:
        # The energy platform of Forecast.Solar (its library is not installed in the tests)
        return forecast if domain == "forecast_solar" else None

    with patch("custom_components.solarsplit.forecast._forecast_function", side_effect=platform):
        assert [label for _, label in await async_forecast_entries(hass)] == [
            "String 1 (forecast_solar)", "String 2 (forecast_solar)",
        ]
        # Not loaded: no forecast
        assert await async_read_forecast(hass, [e.entry_id for e in strings]) is None
        for entry in strings:
            entry.mock_state(hass, ConfigEntryState.LOADED)
        total = await async_read_forecast(hass, [e.entry_id for e in strings])
        for entry in strings:  # so the teardown does not unload (and import) Forecast.Solar
            entry.mock_state(hass, ConfigEntryState.NOT_LOADED)
    # Forecast.Solar ends its periods: 13:00 is what is produced from 12:00 to 13:00
    start = dt_util.start_of_local_day() + timedelta(hours=12)
    assert total == {start.timestamp(): 1500}


async def test_plan_with_todays_prices_and_sun_at_every_hour(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage: dict[str, Any]
) -> None:
    # The real case: minimum 3 h, window 10:00-17:00, deadline 17:00, sun forecast all day.
    _at(freezer, 9 + 5 / 60)
    sell = {9: 0.12495, 10: 0.0527, 11: 0.00503, 12: 0.00512, 13: 0.00516, 14: 0.00669, 15: 0.04645, 16: 0.10332}
    hass.states.async_set(BUY, "0.19", {f"price_{h:02d}h": 0.2 for h in range(24)})
    hass.states.async_set("sensor.sell", "0.12", {f"price_{h:02d}h": sell.get(h, 0.15) for h in range(24)})
    with patch(READ, return_value=_wh({h: 3000 for h in range(9, 17)})):
        entry = await _setup(
            hass, loads=[_pump(**{
                CONF_MIN_HOURS: 3, CONF_DEADLINE: "17:00:00", "min_priority": 1,
                "window_start": "10:00:00", "window_end": "17:00:00",
            })],
            **{CONF_BUY_PRICE_SENSOR: BUY, "sell_price_sensor": "sensor.sell", CONF_FORECAST_ENTRIES: ["f"]},
        )
        await _cycle(hass, entry, export=2000)
    assert _plan_hours(hass, entry, grid=False) == [(0, 11), (0, 12), (0, 13)]
    assert float(_objective(hass, entry, "objective_cost").state) == pytest.approx(0.0081, abs=1e-4)
    # At 09:00 the pump keeps its normal priority (3): its minimum is planned for later
    attr = hass.states.get(_entity(hass, f"{_sub_id(entry, POOL)}_load_state")).attributes
    assert attr["effective_priority"] == 3
