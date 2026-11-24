"""Objectives inside Home Assistant: minimum before a deadline, grid fill plan, day change and urgency."""

from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.solarsplit.const import (
    CONF_BUY_PRICE_SENSOR,
    CONF_DAY_START,
    CONF_DEADLINE,
    CONF_DEADLINE_ENTITY,
    CONF_MIN_HOURS,
    CONF_MIN_PRIORITY,
    CONF_SELL_PRICE_SENSOR,
    CONF_URGENCY_ENTITY,
    CONF_URGENCY_PRIORITY,
    CONF_WINDOW_END,
    CONF_WINDOW_START,
)

from .test_coordinator import (
    POOL,
    TERMO_POWER,
    _cycle,
    _entity,
    _load,
    _setup,
    _setup_termo,
    _state,
    _sub_id,
    _termo,
)

BUY = "sensor.pvpc"
SELL = "sensor.esios_injection_price"
RISK = "binary_sensor.tankwise_plan_at_risk"


def _at(freezer: FrozenDateTimeFactory, hours: float, days: int = 0) -> None:
    """Move to a local time of the current day (or of a later one)."""
    freezer.move_to(dt_util.start_of_local_day() + timedelta(days=days, hours=hours))


def _set_prices(hass: HomeAssistant, cheap: dict[int, float], dear: float = 0.30) -> None:
    """Buy prices of today: `dear` except the hours in `cheap`; selling is worth 0.005 at every hour."""
    hass.states.async_set(BUY, "0.2", {f"price_{h:02d}h": cheap.get(h, dear) for h in range(24)})
    hass.states.async_set(SELL, "0.005", {f"price_{h:02d}h": 0.005 for h in range(24)})


def _pump(**extra: Any) -> dict[str, Any]:
    return _load("Pool", POOL, 530, 3, **extra)


def _objective(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> Any:
    return hass.states.get(_entity(hass, f"{_sub_id(entry, POOL)}_{key}"))


def _local(state: Any) -> Any:
    return dt_util.as_local(dt_util.parse_datetime(state))


def _plan_hours(hass: HomeAssistant, entry: MockConfigEntry, grid: bool = True) -> list[tuple[int, int]]:
    """(day offset, hour) of every planned slot bought from the grid (or covered by surplus)."""
    today = dt_util.now().date()
    plan = _objective(hass, entry, "objective_cost").attributes["plan"]
    return [
        ((_local(slot["start"]).date() - today).days, _local(slot["start"]).hour)
        for slot in plan if slot["grid"] is grid
    ]


async def test_minimum_completed_with_the_grid_in_the_cheapest_hours(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    _at(freezer, 14 + 10 / 60)
    _set_prices(hass, {14: 0.05, 15: 0.06, 3: 0.04})
    entry = await _setup(
        hass, shadow=False, loads=[_pump(**{CONF_MIN_HOURS: 1.5, CONF_DEADLINE: "20:00:00"})],
        **{CONF_BUY_PRICE_SENSOR: BUY, CONF_SELL_PRICE_SENSOR: SELL},
    )
    switch_on = async_mock_service(hass, "switch", "turn_on")
    await _cycle(hass, entry, export=0, imp=300)  # no sun at all

    assert [c.data["entity_id"] for c in switch_on] == [POOL]
    assert _state(hass, entry, "load_reason", POOL) == "grid_fill"
    assert _plan_hours(hass, entry) == [(0, 14), (0, 15)]  # 03:00 is cheaper, but already gone
    assert float(_objective(hass, entry, "objective_pending").state) == pytest.approx(1.5)
    assert _local(_objective(hass, entry, "objective_deadline").state).hour == 20
    cost = _objective(hass, entry, "objective_cost")
    assert float(cost.state) == pytest.approx(0.53 * (50 / 60 * 0.05 + 40 / 60 * 0.06), abs=1e-3)
    assert cost.attributes["priced"] is True and cost.attributes["grid_hours"] == pytest.approx(1.5)


async def test_grid_fill_power_is_not_reassigned(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    _at(freezer, 14 + 10 / 60)
    _set_prices(hass, {14: 0.05})
    entry = await _setup(
        hass, pool="on", loads=[_pump(**{CONF_MIN_HOURS: 1, CONF_DEADLINE: "20:00:00"})],
        **{CONF_BUY_PRICE_SENSOR: BUY},
    )
    await _cycle(hass, entry, export=0, imp=530)
    assert _state(hass, entry, "load_reason", POOL) == "grid_fill"
    assert float(_state(hass, entry, "budget")) == -630  # the pump's 530 W are not surplus
    assert float(_state(hass, entry, "assigned_power")) == 0


async def test_shadow_mode_publishes_the_plan_without_acting(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    _at(freezer, 14 + 10 / 60)
    _set_prices(hass, {14: 0.05})
    entry = await _setup(
        hass, loads=[_pump(**{CONF_MIN_HOURS: 0.5, CONF_DEADLINE: "20:00:00"})], **{CONF_BUY_PRICE_SENSOR: BUY}
    )
    switch_on = async_mock_service(hass, "switch", "turn_on")
    await _cycle(hass, entry, export=0)
    assert switch_on == []
    assert _state(hass, entry, "load_reason", POOL) == "grid_fill"
    assert _state(hass, entry, "load_state", POOL) == "waiting"
    assert _plan_hours(hass, entry) == [(0, 14)]


async def test_preferred_window_comes_first(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    _at(freezer, 8)
    _set_prices(hass, {8: 0.04, 9: 0.04, 12: 0.12, 13: 0.10, 16: 0.11})
    entry = await _setup(
        hass, loads=[_pump(**{
            CONF_MIN_HOURS: 2, CONF_DEADLINE: "17:00:00", CONF_WINDOW_START: "10:00:00", CONF_WINDOW_END: "17:00:00",
        })],
        **{CONF_BUY_PRICE_SENSOR: BUY},
    )
    await _cycle(hass, entry, export=0)
    assert _plan_hours(hass, entry) == [(0, 13), (0, 16)]  # not 08:00 or 09:00, outside the window
    assert _state(hass, entry, "load_reason", POOL) == "no_budget"


async def test_without_prices_the_last_hours_before_the_deadline(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    _at(freezer, 14 + 10 / 60)
    entry = await _setup(hass, loads=[_pump(**{CONF_MIN_HOURS: 1, CONF_DEADLINE: "16:00:00"})])
    await _cycle(hass, entry, export=0)
    assert _plan_hours(hass, entry) == [(0, 15)]
    cost = _objective(hass, entry, "objective_cost")
    assert cost.state == "unknown" and cost.attributes["priced"] is False
    assert _state(hass, entry, "load_reason", POOL) == "no_budget"

    _at(freezer, 15 + 5 / 60)
    await _cycle(hass, entry, export=0)
    assert _state(hass, entry, "load_reason", POOL) == "grid_fill_no_price"


async def test_unusable_price_sensor_is_no_prices(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    _at(freezer, 15 + 5 / 60)
    hass.states.async_set(BUY, "unavailable")
    entry = await _setup(
        hass, loads=[_pump(**{CONF_MIN_HOURS: 1, CONF_DEADLINE: "16:00:00"})], **{CONF_BUY_PRICE_SENSOR: BUY}
    )
    await _cycle(hass, entry, export=0)
    assert _state(hass, entry, "load_reason", POOL) == "grid_fill_no_price"


async def test_objective_across_midnight_and_the_day_change(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    # From 17:00 to 06:30 the next morning: the night's cheap hours.
    _at(freezer, 16)
    _set_prices(hass, {2: 0.04, 3: 0.05, 4: 0.05, 14: 0.03})
    entry = await _setup(
        hass, shadow=False, loads=[_pump(**{CONF_MIN_HOURS: 3, CONF_DAY_START: "17:00:00", CONF_DEADLINE: "06:30:00"})],
        **{CONF_BUY_PRICE_SENSOR: BUY},
    )
    switch_on = async_mock_service(hass, "switch", "turn_on")
    await _cycle(hass, entry, export=0)
    # Today's day started yesterday at 17:00 and its deadline (06:30) has passed: nothing to plan.
    assert _local(_objective(hass, entry, "objective_deadline").state).hour == 6
    assert _plan_hours(hass, entry) == [] and _state(hass, entry, "load_reason", POOL) == "no_budget"

    _at(freezer, 17.5)  # a new day: deadline tomorrow at 06:30, prices of tomorrow estimated
    await _cycle(hass, entry, export=0)
    deadline = _local(_objective(hass, entry, "objective_deadline").state)
    assert (deadline.date() - dt_util.now().date()).days == 1 and (deadline.hour, deadline.minute) == (6, 30)
    assert _plan_hours(hass, entry) == [(1, 2), (1, 3), (1, 4)]
    plan = _objective(hass, entry, "objective_cost").attributes["plan"]
    assert all(slot["estimated"] for slot in plan)
    assert float(_objective(hass, entry, "objective_pending").state) == 3

    _at(freezer, 2 + 5 / 60, days=1)  # 02:05: switched on from the grid
    await _cycle(hass, entry, export=0)
    assert [c.data["entity_id"] for c in switch_on] == [POOL]
    hass.states.async_set(POOL, "on")
    await _cycle(hass, entry, export=0)
    _at(freezer, 5 + 5 / 60)
    await _cycle(hass, entry, export=0)
    pending = _objective(hass, entry, "objective_pending")
    assert float(pending.attributes["done"]) == pytest.approx(3, abs=0.01)  # the hours after midnight count
    assert float(pending.state) == pytest.approx(0, abs=0.01)

    hass.states.async_set(POOL, "off")
    await _cycle(hass, entry, export=0)
    _at(freezer, 17 + 5 / 60)  # the next objective day: back to zero
    await _cycle(hass, entry, export=0)
    assert float(_objective(hass, entry, "objective_pending").attributes["done"]) == 0
    assert float(_objective(hass, entry, "objective_pending").state) == 3


async def test_deadline_from_an_entity(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    _at(freezer, 10)
    hass.states.async_set("input_datetime.piscina_limite", "18:30:00")
    entry = await _setup(
        hass, loads=[_pump(**{CONF_MIN_HOURS: 1, CONF_DEADLINE_ENTITY: "input_datetime.piscina_limite"})]
    )
    await _cycle(hass, entry, export=0)
    deadline = _local(_objective(hass, entry, "objective_deadline").state)
    assert (deadline.hour, deadline.minute) == (18, 30)
    assert _plan_hours(hass, entry) == [(0, 17), (0, 18)]  # no prices: the last hour before 18:30
    hass.states.async_set("input_datetime.piscina_limite", "unavailable")
    await _cycle(hass, entry, export=0)
    assert _objective(hass, entry, "objective_deadline").state == "unknown"


async def test_priority_until_the_minimum(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    _at(freezer, 10)
    entry = await _setup(hass, loads=[
        _pump(**{CONF_MIN_HOURS: 1, CONF_MIN_PRIORITY: 1}), _load("Salon", "climate.ac_salon", 900, 2),
    ])
    await _cycle(hass, entry, export=1100)  # room for one of them
    assert _state(hass, entry, "load_reason", POOL) == "fits"
    attr = hass.states.get(_entity(hass, f"{_sub_id(entry, POOL)}_load_state")).attributes
    assert attr["effective_priority"] == 1


async def test_urgency_puts_the_heater_first_and_then_lets_go(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    _at(freezer, 12)
    hass.states.async_set(RISK, "on")
    entry, _ = await _setup_termo(hass, shadow=True, power=0, loads=[
        _pump(**{CONF_MIN_HOURS: 2, CONF_MIN_PRIORITY: 1}),
        _termo(2, **{CONF_URGENCY_ENTITY: RISK, CONF_URGENCY_PRIORITY: 1}),
    ])
    await _cycle(hass, entry, export=1200)
    termo = hass.states.get(_entity(hass, f"{_sub_id(entry, 'switch.termo')}_load_state"))
    assert termo.attributes["effective_priority"] == 1
    # The heater could take 1100 W: the pump, still short of its minimum, does not start
    assert _state(hass, entry, "load_reason", POOL) == "no_budget"

    hass.states.async_set(RISK, "off")
    hass.states.async_set(TERMO_POWER, "0", {"unit_of_measurement": "W"})
    await _cycle(hass, entry, export=1200)
    termo = hass.states.get(_entity(hass, f"{_sub_id(entry, 'switch.termo')}_load_state"))
    assert termo.attributes["effective_priority"] == 2
    assert _state(hass, entry, "load_reason", POOL) == "fits"  # the normal order is back


async def test_plan_publishes_hours_energy_and_prices(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    _at(freezer, 14)
    _set_prices(hass, {14: 0.05})
    entry = await _setup(
        hass, loads=[_pump(**{CONF_MIN_HOURS: 1, CONF_DEADLINE: "20:00:00"})],
        **{CONF_BUY_PRICE_SENSOR: BUY, CONF_SELL_PRICE_SENSOR: SELL},
    )
    await _cycle(hass, entry, export=0)
    attributes = _objective(hass, entry, "objective_cost").attributes
    [slot] = attributes["plan"]
    assert (slot["hours"], slot["kwh"], slot["buy"], slot["sell"], slot["price"]) == (1, 0.53, 0.05, 0.005, 0.05)
    assert attributes["grid_kwh"] == 0.53 and attributes["short_hours"] == 0


async def test_hours_before_the_upgrade_count_for_a_day_starting_at_midnight(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage: dict[str, Any]
) -> None:
    # A memory saved by 0.2.0: 2 h on today, no objective day yet.
    _at(freezer, 12)
    hass_storage["solarsplit.solarsplit_test"] = {
        "version": 1, "key": "solarsplit.solarsplit_test",
        "data": {"saved_at": dt_util.utcnow().timestamp(), "loads": {"load_depuradora_piscina": {
            "today": dt_util.now().date().isoformat(), "on_today": 7200.0, "last_seen": False,
            "last_change": dt_util.utcnow().timestamp() - 3600,
        }}},
    }
    entry = await _setup(hass, loads=[_pump(**{CONF_MIN_HOURS: 3, CONF_DEADLINE: "20:00:00"})])
    await _cycle(hass, entry, export=0)
    assert float(_objective(hass, entry, "objective_pending").state) == pytest.approx(1)
