"""Grid fill plan and expected surplus, without Home Assistant."""

from datetime import UTC, datetime

import pytest

from custom_components.solarsplit.planner import (
    PROFILE_DAYS,
    SOURCE_FORECAST,
    SOURCE_LEARNED,
    SOURCE_NONE,
    HourlyProfile,
    Request,
    Slot,
    expected_surplus,
    plan,
    sum_forecasts,
)

H = 3600.0
# A November day like the real one: buying is cheapest at midday and dear from 17:00 to 23:00,
# selling is worth almost nothing at midday.
BUY = [0.17, 0.13, 0.10, 0.11, 0.10, 0.10, 0.11, 0.19, 0.24, 0.19, 0.18, 0.12,
       0.12, 0.12, 0.055, 0.105, 0.17, 0.27, 0.37, 0.38, 0.39, 0.39, 0.30, 0.28]
SELL = [0.12, 0.09, 0.06, 0.06, 0.06, 0.06, 0.07, 0.14, 0.17, 0.12, 0.05, 0.005,
        0.005, 0.005, 0.007, 0.05, 0.10, 0.18, 0.20, 0.21, 0.23, 0.22, 0.20, 0.19]


def _day(surplus: dict[int, float] | None = None, prices: bool = True) -> list[Slot]:
    """24 hourly slots from t = 0 (hour 0)."""
    return [
        Slot(h * H, (h + 1) * H, BUY[h] if prices else None, SELL[h] if prices else None, (surplus or {}).get(h, 0.0))
        for h in range(24)
    ]


def _pump(pending: float, deadline: float = 17 * H, **kw) -> Request:
    return Request(**{"load_id": "pool", "priority": 1, "power_w": 530, "pending_h": pending, "deadline": deadline, **kw})


def _hours(p, grid: bool) -> list[int]:
    return [int(slot.start // H) for slot in p.slots if slot.grid is grid]


def test_minimum_met_with_sun_only() -> None:
    slots = _day({11: 1500, 12: 2000, 13: 1800, 14: 900})
    p = plan(8 * H, slots, [_pump(4)], None)["pool"]
    assert _hours(p, grid=True) == [] and _hours(p, grid=False) == [11, 12, 13, 14]
    assert p.short_h == 0 and not p.fill_at(9 * H)
    # Each surplus hour costs what that energy would have earned exported
    assert p.cost == pytest.approx(0.53 * (0.005 + 0.005 + 0.005 + 0.007))


def test_completed_with_the_grid_in_the_cheapest_hours_of_the_window() -> None:
    # No sun, from 08:00. The window is 10:00-17:00: 14:00 (0.055), 15:00 (0.105) and 11:00
    # (0.12, the earliest of three at that price), never 09:00 or 08:00 while the window has room.
    window = frozenset(h * H for h in range(10, 17))
    p = plan(8 * H, _day(), [_pump(3, preferred=window)], None)["pool"]
    assert _hours(p, grid=True) == [11, 14, 15]
    assert p.fill_at(14 * H + 60) and not p.fill_at(13 * H)
    # More than the window has: the rest from the cheapest hour outside it (09:00, not 08:00)
    p = plan(8 * H, _day(), [_pump(8, preferred=window)], None)["pool"]
    assert _hours(p, grid=True) == [9, 10, 11, 12, 13, 14, 15, 16]


def test_partial_sun_lowers_the_price_of_an_hour() -> None:
    # Before 14:00. 400 W of the pump's 530 W come from the surplus at 10:00: 75 % at the sell
    # price makes it cheaper than 11:00-13:00.
    p = plan(8 * H, _day({10: 400}), [_pump(1, deadline=14 * H)], None)["pool"]
    [slot] = p.slots
    assert int(slot.start // H) == 10 and slot.grid
    covered = 400 / 530
    assert slot.price == pytest.approx(covered * SELL[10] + (1 - covered) * BUY[10], abs=1e-5)


def test_current_hour_counts_only_what_is_left() -> None:
    p = plan(14.5 * H, _day(), [_pump(1)], None)["pool"]
    # Half of 14:00 is left (the cheapest), then 15:00
    assert [(int(s.start // H), s.hours) for s in p.slots] == [(14, 0.5), (15, 0.5)]


def test_no_prices_uses_the_last_hours_before_the_deadline() -> None:
    p = plan(8 * H, _day(prices=False), [_pump(2.5)], None)["pool"]
    assert not p.priced and p.cost is None
    assert [(int(s.start // H), s.hours) for s in p.slots] == [(14, 0.5), (15, 1), (16, 1)]


def test_two_loads_never_exceed_the_contracted_power() -> None:
    # Room for 1000 W of grid fill: the pump (530 W) and the radiator (600 W) cannot share an hour.
    radiator = Request("radiator", 2, 600, 1, 17 * H)
    plans = plan(8 * H, _day(), [_pump(1), radiator], 1000)
    assert _hours(plans["pool"], grid=True) == [14]
    assert _hours(plans["radiator"], grid=True) == [15]  # the next cheapest
    # With room for both they share the cheapest hour
    plans = plan(8 * H, _day(), [_pump(1), radiator], 1200)
    assert _hours(plans["radiator"], grid=True) == [14]
    # Unknown contracted power: one load per hour
    plans = plan(8 * H, _day(), [_pump(1), radiator], None)
    assert _hours(plans["radiator"], grid=True) == [15]


def test_higher_priority_takes_the_surplus_first() -> None:
    radiator = Request("radiator", 2, 600, 1, 17 * H)
    plans = plan(8 * H, _day({12: 800}), [radiator, _pump(1)], None)
    assert _hours(plans["pool"], grid=False) == [12]  # priority 1
    assert _hours(plans["radiator"], grid=True) == [14]  # only 270 W left at 12:00


def test_what_does_not_fit_before_the_deadline_is_short() -> None:
    p = plan(15 * H, _day(), [_pump(3)], None)["pool"]
    assert sum(s.hours for s in p.slots) == 2 and p.short_h == 1


def test_objective_across_midnight() -> None:
    # From 17:00 to 06:30 the next morning: the cheap hours after midnight are chosen.
    slots = [Slot(h * H, (h + 1) * H, BUY[h % 24], SELL[h % 24], 0.0, estimated=h >= 24) for h in range(17, 31)]
    p = plan(17 * H, slots, [_pump(3, deadline=30.5 * H)], None)["pool"]
    assert _hours(p, grid=True) == [26, 28, 29]  # 02:00, 04:00 and 05:00 of the next day
    assert all(slot.estimated for slot in p.slots)


def test_surplus_profile_is_the_median_of_the_last_days() -> None:
    profile = HourlyProfile()
    assert profile.expected(12) == 0
    for day, watts in enumerate([100, 2000, 1800, 1900]):
        profile.add(f"2026-11-{day + 1:02d}", 12, watts)
        profile.add(f"2026-11-{day + 1:02d}", 12, watts)
    assert profile.expected(12) == 1850
    profile.add("2026-11-02", 13, -300)  # an import counts as no surplus
    assert profile.expected(13) == 0
    for day in range(5, 5 + PROFILE_DAYS):
        profile.add(f"2026-11-{day:02d}", 12, 500)
    assert len(profile.days) == PROFILE_DAYS and profile.expected(12) == 500


# Expected surplus from the solar forecast

TZ = UTC


def _hour(h: int, day: int = 25) -> datetime:
    return datetime(2026, 11, day, h, tzinfo=TZ)


def test_forecasts_are_summed_and_period_ends_shifted() -> None:
    # Forecast.Solar: the value at 09:00 is what is produced from 08:00 to 09:00
    string_1 = {_hour(9).isoformat(): 400, _hour(10).isoformat(): 900}
    string_2 = {_hour(9).isoformat(): 100}
    other = {_hour(9).isoformat(): 50}  # an integration that gives the period start
    total = sum_forecasts([(string_1, True), (string_2, True), (other, False)], TZ)
    assert total == {_hour(8).timestamp(): 500, _hour(9).timestamp(): 950}


def test_expected_surplus_with_forecast_and_learned_base_load() -> None:
    forecast = {_hour(12).timestamp(): 3000.0}
    base = HourlyProfile()
    base.add("2026-11-24", 12, 900)
    learned = HourlyProfile()
    learned.add("2026-11-24", 12, 2500)
    covered = {_hour(12).date()}
    assert expected_surplus(_hour(12), forecast, covered, base, 400, learned) == (2100, SOURCE_FORECAST)


def test_expected_surplus_with_forecast_and_no_consumption_history() -> None:
    forecast = {_hour(12).timestamp(): 3000.0}
    covered = {_hour(12).date()}
    result = expected_surplus(_hour(12), forecast, covered, HourlyProfile(), 400, HourlyProfile())
    assert result == (2600, SOURCE_FORECAST)  # the fixed base load
    # A small forecast never makes a negative surplus
    assert expected_surplus(_hour(12), {_hour(12).timestamp(): 300.0}, covered, HourlyProfile(), 400,
                            HourlyProfile()) == (0, SOURCE_FORECAST)


def test_expected_surplus_without_forecast() -> None:
    learned = HourlyProfile()
    learned.add("2026-11-24", 12, 1800)
    assert expected_surplus(_hour(12), None, set(), HourlyProfile(), 400, learned) == (1800, SOURCE_LEARNED)
    assert expected_surplus(_hour(13), None, set(), HourlyProfile(), 400, learned) == (0, SOURCE_NONE)


def test_partial_forecast() -> None:
    # Today is covered (an hour it does not list, like the night, produces nothing);
    # the day after is not: the learned surplus.
    forecast = {_hour(12).timestamp(): 3000.0}
    covered = {_hour(12).date()}
    learned = HourlyProfile()
    for hour in (13, 12):
        learned.add("2026-11-24", hour, 1500)
    assert expected_surplus(_hour(13), forecast, covered, HourlyProfile(), 400, learned) == (0, SOURCE_FORECAST)
    assert expected_surplus(_hour(12, day=26), forecast, covered, HourlyProfile(), 400, learned) == (
        1500, SOURCE_LEARNED,
    )


def test_plan_reports_its_surplus_source() -> None:
    slots = [
        Slot(h * H, (h + 1) * H, BUY[h], SELL[h], 2000.0 if h == 12 else 0.0, source=SOURCE_FORECAST)
        for h in range(10, 14)
    ] + [Slot(h * H, (h + 1) * H, BUY[h], SELL[h], 0.0, source=SOURCE_LEARNED) for h in range(14, 17)]
    p = plan(10 * H, slots, [_pump(1)], None)["pool"]
    assert p.source == SOURCE_FORECAST and p.slots[0].source == SOURCE_FORECAST
    p = plan(10 * H, slots[4:], [_pump(1)], None)["pool"]
    assert p.source == SOURCE_LEARNED
    p = plan(10 * H, [Slot(10 * H, 11 * H, 0.1, 0.0)], [_pump(1)], None)["pool"]
    assert p.source == SOURCE_NONE


# Today's real prices (25/11): selling is worth a lot early and almost nothing at midday.
SELL_TODAY = {9: 0.12495, 10: 0.0527, 11: 0.00503, 12: 0.00512, 13: 0.00516, 14: 0.00669, 15: 0.04645,
              16: 0.10332}
BUY_TODAY = {9: 0.19182, 10: 0.17839, 11: 0.12255, 12: 0.12267, 13: 0.12244, 14: 0.05534, 15: 0.10494,
             16: 0.17239}


def test_sunny_hours_are_chosen_by_what_the_sun_would_earn_exported() -> None:
    # Enough surplus expected at every hour from 09:00 to 17:00: the pump (3 h, window
    # 10:00-17:00) must use the hours whose sun is worth least exported, not the first ones.
    slots = [Slot(h * H, (h + 1) * H, BUY_TODAY[h], SELL_TODAY[h], 2500.0) for h in range(9, 17)]
    window = frozenset(h * H for h in range(10, 17))
    p = plan(9 * H, slots, [_pump(3, preferred=window)], None)["pool"]
    assert _hours(p, grid=False) == [11, 12, 13] and _hours(p, grid=True) == []
    assert p.cost == pytest.approx(0.53 * (0.00503 + 0.00512 + 0.00516), abs=1e-5)  # about 0.008 €
    assert p.cost < 0.01
    assert not p.planned_at(9 * H + 60) and not p.planned_at(10 * H) and p.planned_at(11 * H)


def test_a_sunny_hour_outside_the_window_comes_last() -> None:
    # 09:00 is the cheapest to sell, but outside the window: only used if the window is not enough.
    sell = {**SELL_TODAY, 9: 0.001}
    slots = [Slot(h * H, (h + 1) * H, BUY_TODAY[h], sell[h], 2500.0) for h in range(9, 17)]
    window = frozenset(h * H for h in range(10, 17))
    p = plan(9 * H, slots, [_pump(3, preferred=window)], None)["pool"]
    assert 9 not in _hours(p, grid=False)


def test_a_cheap_grid_hour_beats_a_sunny_hour_worth_more_exported() -> None:
    # At 16:00 selling earns 0.10; buying at 14:00 without sun costs 0.055: 14:00 wins.
    slots = [Slot(14 * H, 15 * H, 0.055, 0.007, 0.0), Slot(16 * H, 17 * H, 0.17, 0.10, 2500.0)]
    p = plan(14 * H, slots, [_pump(1)], None)["pool"]
    assert _hours(p, grid=True) == [14] and _hours(p, grid=False) == []
