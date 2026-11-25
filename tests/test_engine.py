"""Control engine, without Home Assistant."""

from dataclasses import replace

from custom_components.solarsplit.engine import (
    Action,
    Decision,
    EngineConfig,
    LoadKind,
    LoadState,
    Measurements,
    Reason,
    Regulator,
    compute_budget,
    decide,
)

CFG = EngineConfig(safety_margin=100, on_margin=100, import_tolerance=150, settle_time=30)
NOW = 10_000.0


def pool(**kw) -> LoadState:
    return LoadState(**{"id": "pool", "priority": 1, "nominal_w": 750, "is_on": False, **kw})


def heater(**kw) -> LoadState:
    return LoadState(**{"id": "heater", "priority": 2, "nominal_w": 1500, "is_on": False, **kw})


def ac(**kw) -> LoadState:
    return LoadState(**{"id": "ac", "priority": 3, "nominal_w": 900, "is_on": False, **kw})


def test_budget_adds_back_controlled_loads() -> None:
    loads = [pool(is_on=True, actual_w=700), heater(is_on=True), ac(is_on=True, manual=True)]
    budget = compute_budget(Measurements(export_w=300, import_w=0), loads, CFG)
    assert budget == 300 + 700 + 1500 - 100  # the manual load is not ours to reassign


def test_switches_on_first_priority_that_fits() -> None:
    d = decide(NOW, Measurements(1000, 0), [pool(), heater(), ac()], CFG)
    assert (d.action, d.load_id, d.reason) == (Action.TURN_ON, "pool", Reason.FITS)
    assert d.desired == {"pool": True, "heater": False, "ac": False}


def test_one_change_per_cycle_and_skips_priorities() -> None:
    # 2000 W: pool (750+100) fits, heater (1500) no longer does, ac (900) does
    d = decide(NOW, Measurements(2100, 0), [pool(), heater(), ac()], CFG)
    assert d.desired == {"pool": True, "heater": False, "ac": True}
    assert (d.action, d.load_id) == (Action.TURN_ON, "pool")


def test_no_skip_waits_for_higher_priorities() -> None:
    d = decide(NOW, Measurements(1100, 0), [pool(is_on=True), heater(), ac(no_skip=True)], CFG)
    assert d.reasons["ac"] is Reason.WAITING_HIGHER
    assert d.action is Action.NONE


def test_not_eligible_load_does_not_block_no_skip() -> None:
    d = decide(NOW, Measurements(1100, 0), [pool(eligible=False), ac(no_skip=True)], CFG)
    assert d.reasons["pool"] is Reason.NOT_ELIGIBLE
    assert (d.action, d.load_id) == (Action.TURN_ON, "ac")


def test_on_load_tolerates_small_import() -> None:
    d = decide(NOW, Measurements(0, 40), [pool(is_on=True)], CFG)
    assert d.desired["pool"] and d.action is Action.NONE


def test_deficit_switches_off_lowest_priority_first() -> None:
    loads = [pool(is_on=True), ac(is_on=True)]
    d = decide(NOW, Measurements(0, 400), loads, CFG)
    assert (d.action, d.load_id) == (Action.TURN_OFF, "ac")


def test_min_on_time_blocks_switch_off_and_switch_on() -> None:
    loads = [pool(is_on=True), ac(is_on=True, min_on=900, last_change=NOW - 60), heater()]
    d = decide(NOW, Measurements(0, 400), loads, CFG)
    assert d.action is Action.NONE
    assert d.reasons["ac"] is Reason.MIN_ON_TIME


def test_min_off_time_blocks_switch_on() -> None:
    d = decide(NOW, Measurements(1000, 0), [pool(min_off=300, last_change=NOW - 10)], CFG)
    assert d.action is Action.NONE
    assert d.reasons["pool"] is Reason.MIN_OFF_TIME


def test_settle_time_after_last_action() -> None:
    d = decide(NOW, Measurements(1000, 0), [pool()], CFG, last_action=NOW - 5)
    assert (d.action, d.reason) == (Action.NONE, Reason.SETTLING)


def test_paused_does_nothing() -> None:
    d = decide(NOW, Measurements(5000, 0), [pool()], CFG, active=False)
    assert (d.action, d.reason) == (Action.NONE, Reason.PAUSED)


def test_variable_load_takes_its_share_first() -> None:
    # FreeDS heater uses 1200 W of a 1300 W surplus; pool must not count on it
    loads = [heater(priority=1, kind=LoadKind.VARIABLE, actual_w=1200), pool(priority=2)]
    d = decide(NOW, Measurements(100, 0), loads, CFG)
    assert d.reasons["heater"] is Reason.VARIABLE
    assert d.desired == {"pool": False}


def test_unmanaged_and_manual_loads_are_ignored() -> None:
    d = decide(NOW, Measurements(5000, 0), [pool(managed=False), ac(manual=True)], CFG)
    assert d.reasons == {"pool": Reason.UNMANAGED, "ac": Reason.MANUAL}
    assert d.action is Action.NONE


def test_global_condition_and_unavailable_leave_loads_as_they_are() -> None:
    # Deficit, but neither load is ours to switch off, nor is its power reassigned
    loads = [pool(is_on=True, global_ok=False), ac(is_on=True, available=False), heater()]
    d = decide(NOW, Measurements(0, 400), loads, CFG)
    assert d.reasons["pool"] is Reason.GLOBAL_CONDITION
    assert d.reasons["ac"] is Reason.UNAVAILABLE
    assert d.budget == -500 and d.action is Action.NONE


def test_global_shutdown_switches_off_owned_loads_only() -> None:
    loads = [
        pool(is_on=True, owned=True, global_ok=False, off_delay=300),  # ours: off, without waiting
        ac(is_on=True, owned=False, global_ok=False),  # switched on by someone else: untouched
        heater(is_on=True, owned=True, global_ok=False, manual=True),  # manual: untouched
    ]
    d = decide(NOW, Measurements(5000, 0), loads, CFG)
    assert d.reasons == {
        "pool": Reason.GLOBAL_SHUTDOWN, "ac": Reason.GLOBAL_CONDITION, "heater": Reason.MANUAL,
    }
    assert (d.action, d.load_id, d.reason) == (Action.TURN_OFF, "pool", Reason.GLOBAL_SHUTDOWN)


def test_global_shutdown_respects_min_on_time() -> None:
    load = pool(is_on=True, owned=True, global_ok=False, min_on=900, last_change=NOW - 60)
    d = decide(NOW, Measurements(5000, 0), [load], CFG)
    assert d.action is Action.NONE and d.reasons["pool"] is Reason.MIN_ON_TIME


def test_global_shutdown_skips_variable_loads() -> None:
    load = heater(is_on=True, owned=True, global_ok=False, kind=LoadKind.VARIABLE, actual_w=800)
    d = decide(NOW, Measurements(0, 0), [load], CFG)
    assert d.reasons["heater"] is Reason.GLOBAL_CONDITION and d.action is Action.NONE


def cycle(now: float, meas: Measurements, load: LoadState) -> tuple[Decision, LoadState]:
    """Run one cycle and store the marks back, as the coordinator does."""
    d = decide(now, meas, [load], CFG)
    return d, replace(load, on_since=d.on_since.get(load.id), off_since=d.off_since.get(load.id))


def test_on_delay_not_met_does_not_act() -> None:
    load = pool(on_delay=60)
    d, load = cycle(NOW, Measurements(1000, 0), load)
    assert d.action is Action.NONE and d.reasons["pool"] is Reason.ON_DELAY
    assert load.on_since == NOW
    d, load = cycle(NOW + 30, Measurements(1000, 0), load)
    assert d.action is Action.NONE and load.on_since == NOW


def test_on_delay_met_acts() -> None:
    load = pool(on_delay=60)
    _, load = cycle(NOW, Measurements(1000, 0), load)
    d, _ = cycle(NOW + 60, Measurements(1000, 0), load)
    assert (d.action, d.load_id) == (Action.TURN_ON, "pool")


def test_on_delay_restarts_when_condition_breaks() -> None:
    load = pool(on_delay=60)
    _, load = cycle(NOW, Measurements(1000, 0), load)
    d, load = cycle(NOW + 40, Measurements(200, 0), load)  # a cloud
    assert load.on_since is None and d.on_since == {}
    d, load = cycle(NOW + 50, Measurements(1000, 0), load)
    assert load.on_since == NOW + 50
    d, load = cycle(NOW + 70, Measurements(1000, 0), load)  # 70 s since the first mark, 20 s of this run
    assert d.action is Action.NONE and d.reasons["pool"] is Reason.ON_DELAY
    d, _ = cycle(NOW + 110, Measurements(1000, 0), load)
    assert d.action is Action.TURN_ON


def test_off_delay_not_met_then_met() -> None:
    load = pool(is_on=True, off_delay=30)
    d, load = cycle(NOW, Measurements(0, 400), load)
    assert d.action is Action.NONE and d.reasons["pool"] is Reason.OFF_DELAY
    d, load = cycle(NOW + 30, Measurements(0, 400), load)
    assert (d.action, d.load_id) == (Action.TURN_OFF, "pool")


def test_off_delay_restarts_when_condition_breaks() -> None:
    load = pool(is_on=True, off_delay=30)
    _, load = cycle(NOW, Measurements(0, 400), load)
    _, load = cycle(NOW + 20, Measurements(300, 0), load)  # the sun is back
    assert load.off_since is None
    d, load = cycle(NOW + 35, Measurements(0, 400), load)
    assert d.action is Action.NONE and load.off_since == NOW + 35


def test_marks_progress_while_settling() -> None:
    d = decide(NOW, Measurements(1000, 0), [pool(on_delay=60)], CFG, last_action=NOW - 5)
    assert d.reason is Reason.SETTLING and d.on_since == {"pool": NOW}


def test_unknown_grid_decides_nothing_and_resets_marks() -> None:
    load = pool(on_delay=60, on_since=NOW - 30)
    d = decide(NOW, None, [load, ac(is_on=True)], CFG)
    assert (d.action, d.reason, d.budget) == (Action.NONE, Reason.GRID_UNKNOWN, None)
    assert d.desired == {} and d.on_since == {} and d.off_since == {}


def test_pause_reason_is_reported() -> None:
    d = decide(NOW, Measurements(5000, 0), [pool()], CFG, active=False, pause_reason=Reason.MODE_UNKNOWN)
    assert (d.action, d.reason) == (Action.NONE, Reason.MODE_UNKNOWN)


def test_unknown_global_condition_leaves_loads_as_they_are() -> None:
    # Not a shutdown: an entity that stops reporting is not the condition failing
    loads = [pool(is_on=True, owned=True, global_ok=None), ac(global_ok=None)]
    d = decide(NOW, Measurements(0, 2000), loads, CFG)
    assert d.reasons == {"pool": Reason.GLOBAL_UNKNOWN, "ac": Reason.GLOBAL_UNKNOWN}
    assert d.action is Action.NONE and d.budget == -2100  # the pool's power is not reassigned


def test_unknown_usage_condition_leaves_the_load_as_it_is() -> None:
    loads = [ac(is_on=True, eligible=None), pool()]
    d = decide(NOW, Measurements(0, 0), loads, CFG)
    assert d.reasons["ac"] is Reason.CONDITION_UNKNOWN and "ac" not in d.desired
    assert d.action is Action.NONE  # the 900 W it draws are not handed to the pool


def test_switched_off_load_is_never_switched_on() -> None:
    d = decide(NOW, Measurements(5000, 0), [pool(switched_off=True), ac()], CFG)
    assert d.reasons["pool"] is Reason.SWITCHED_OFF and "pool" not in d.desired
    assert (d.action, d.load_id) == (Action.TURN_ON, "ac")
    assert not d.reasons["ac"] is Reason.WAITING_HIGHER  # it does not hold the loads behind it



def termo(actual_w: float = 0, priority: int = 2, **reg) -> LoadState:
    """A water heater behind a diverter: limit 0-1600 W, now at its maximum."""
    regulator = Regulator(**{"auto": True, "current": 1600, "minimum": 0, "maximum": 1600, **reg})
    return LoadState(
        id="termo", priority=priority, nominal_w=1600, is_on=True, actual_w=actual_w,
        kind=LoadKind.REGULABLE, regulator=regulator,
    )


def test_regulable_first_in_line_keeps_its_maximum() -> None:
    # The diverter takes 1200 W; the pool (after it) only gets what it leaves
    d = decide(NOW, Measurements(50, 0), [termo(1200, priority=1, current=1600), pool(priority=2)], CFG)
    assert d.reasons["termo"] is Reason.FULL_POWER and d.limits == {"termo": 1600}
    assert d.writes == {} and d.desired == {"pool": False}
    assert d.budget == 50 + 1200 - 100  # its power is surplus, counted in the budget


def test_regulable_before_loads_that_are_off_keeps_its_maximum() -> None:
    d = decide(NOW, Measurements(50, 0), [pool(eligible=False, priority=1), termo(1200)], CFG)
    assert d.limits == {"termo": 1600} and d.writes == {}


def test_regulable_behind_a_load_gets_what_is_left() -> None:
    # Budget 50 + 1200 - 100 = 1150: the pool (750) enters first, the heater keeps 400
    d = decide(NOW, Measurements(50, 0), [pool(), termo(1200)], CFG)
    assert d.desired["pool"] is True
    assert d.reasons["termo"] is Reason.LIMITED
    assert d.limits == {"termo": 400} and d.writes == {"termo": 400}


def test_regulable_limit_is_clamped() -> None:
    d = decide(NOW, Measurements(0, 0), [pool(is_on=True), termo(0, minimum=200)], CFG)
    assert d.limits == {"termo": 200}  # remaining is -100
    d = decide(NOW, Measurements(5000, 0), [pool(is_on=True), termo(0, current=500)], CFG)
    assert d.limits == {"termo": 1600} and d.reasons["termo"] is Reason.FULL_POWER


def test_regulable_writes_only_big_changes() -> None:
    loads = [pool(is_on=True), termo(400, current=420)]  # remaining 50 + 750 + 400 - 100 - 750 = 350
    d = decide(NOW, Measurements(50, 0), loads, CFG)
    assert d.limits == {"termo": 350} and d.writes == {}  # 100 W change needed
    d = decide(NOW, Measurements(0, 0), loads, CFG)
    assert d.writes == {"termo": 300}


def test_regulable_bounds_are_always_written() -> None:
    d = decide(NOW, Measurements(5000, 0), [pool(is_on=True), termo(0, current=1550)], CFG)
    assert d.writes == {"termo": 1600}
    d = decide(NOW, Measurements(0, 30), [pool(is_on=True), termo(0, current=50)], CFG)
    assert d.writes == {"termo": 0}


def test_regulable_writes_at_most_every_interval() -> None:
    loads = [pool(is_on=True), termo(0, current=1600, written=NOW - 10, interval=30)]
    d = decide(NOW, Measurements(0, 0), loads, CFG)
    assert d.limits == {"termo": 0} and d.writes == {}
    loads = [pool(is_on=True), termo(0, current=1600, written=NOW - 30, interval=30)]
    assert decide(NOW, Measurements(0, 0), loads, CFG).writes == {"termo": 0}


def test_regulable_outside_auto_is_grid_power() -> None:
    # Manual or off on the diverter: its 1600 W are not surplus to hand out
    loads = [termo(1600, priority=1, auto=False), pool(priority=2)]
    d = decide(NOW, Measurements(0, 1600), loads, CFG)
    assert d.reasons["termo"] is Reason.EXTERNAL and d.limits == {}
    assert d.budget == -1700 and d.desired == {"pool": False}


def test_regulable_limit_is_restored_when_solarsplit_lets_go() -> None:
    lowered = {"current": 300, "lowered": True, "written": NOW - 1}
    cases = [
        decide(NOW, Measurements(0, 0), [termo(auto=False, **lowered)], CFG),
        decide(NOW, Measurements(0, 0), [replace(termo(**lowered), managed=False)], CFG),
        decide(NOW, Measurements(0, 0), [replace(termo(**lowered), global_ok=False)], CFG),
        decide(NOW, Measurements(0, 0), [termo(**lowered)], CFG, active=False),
    ]
    assert [d.writes for d in cases] == [{"termo": 1600}] * 4
    # Only a limit SolarSplit lowered is restored, and never on unknown data
    assert decide(NOW, Measurements(0, 0), [termo(auto=False, current=300)], CFG).writes == {}
    assert decide(NOW, Measurements(0, 0), [termo(auto=False, current=1600, lowered=True)], CFG).writes == {}
    # Unknown grid: a limit still in use is left as it is; one SolarSplit no longer uses is given back
    assert decide(NOW, None, [termo(**lowered)], CFG).writes == {}
    assert decide(NOW, None, [termo(auto=False, **lowered)], CFG).writes == {"termo": 1600}
    assert decide(NOW, None, [replace(termo(**lowered), managed=False)], CFG).writes == {"termo": 1600}
    unavailable = replace(termo(auto=False, **lowered), available=False)
    assert decide(NOW, Measurements(0, 0), [unavailable], CFG).writes == {}


def test_regulable_switched_off_is_left_alone() -> None:
    d = decide(NOW, Measurements(0, 0), [replace(termo(current=300), switched_off=True, is_on=False)], CFG)
    assert d.reasons["termo"] is Reason.SWITCHED_OFF and d.writes == {}


def test_regulable_not_eligible_gets_its_minimum() -> None:
    d = decide(NOW, Measurements(3000, 0), [replace(termo(500), eligible=False)], CFG)
    assert d.reasons["termo"] is Reason.NOT_ELIGIBLE and d.writes == {"termo": 0}


def test_regulable_is_never_switched() -> None:
    d = decide(NOW, Measurements(0, 3000), [replace(termo(1600), owned=True, global_ok=False)], CFG)
    assert d.action is Action.NONE and d.reasons["termo"] is Reason.GLOBAL_CONDITION


def test_daily_target_reached_switches_off_and_is_reported() -> None:
    loads = [pool(is_on=True, hours_today=7.0, target_hours=7.0), ac()]
    d = decide(NOW, Measurements(1000, 0), loads, CFG)
    assert d.reasons["pool"] is Reason.TARGET_REACHED and d.target_reached == ("pool",)
    assert (d.action, d.load_id, d.reason) == (Action.TURN_OFF, "pool", Reason.TARGET_REACHED)
    assert d.desired["ac"] is True  # the pool's power goes to the next load, which is not held back


def test_daily_target_not_reached_yet() -> None:
    d = decide(NOW, Measurements(1000, 0), [pool(hours_today=3.5, target_hours=7.0)], CFG)
    assert d.reasons["pool"] is Reason.FITS and d.target_reached == ()


def test_daily_target_stays_reached_for_the_day() -> None:
    # The target was raised, or the hours sensor dropped: still done until tomorrow
    d = decide(NOW, Measurements(1000, 0), [pool(hours_today=2.0, target_hours=8.0, target_done=True)], CFG)
    assert d.reasons["pool"] is Reason.TARGET_REACHED and d.action is Action.NONE


def test_daily_target_respects_min_on_time() -> None:
    load = pool(is_on=True, hours_today=7.0, target_hours=7.0, min_on=900, last_change=NOW - 60)
    d = decide(NOW, Measurements(1000, 0), [load], CFG)
    assert d.action is Action.NONE and d.reasons["pool"] is Reason.MIN_ON_TIME
    assert d.target_reached == ("pool",)


def test_regulable_with_target_reached_gets_its_minimum() -> None:
    d = decide(NOW, Measurements(3000, 0), [replace(termo(500), hours_today=3, target_hours=2)], CFG)
    assert d.reasons["termo"] is Reason.TARGET_REACHED and d.writes == {"termo": 0}


# On demand and season


def test_on_demand_load_never_starts_without_a_request() -> None:
    """Even first in line and with plenty of surplus."""
    d = decide(NOW, Measurements(5000, 0), [pool(on_demand=True), ac()], CFG)
    assert d.reasons["pool"] is Reason.WAITING_REQUEST
    assert "pool" not in d.desired
    assert (d.action, d.load_id) == (Action.TURN_ON, "ac")


def test_on_demand_load_with_a_request_is_managed_by_priority() -> None:
    d = decide(NOW, Measurements(1000, 0), [pool(on_demand=True, request=True)], CFG)
    assert (d.action, d.load_id, d.reason) == (Action.TURN_ON, "pool", Reason.FITS)
    # No surplus: switched off like any load, after its delays and minimum on time.
    on = pool(on_demand=True, request=True, is_on=True, owned=True, last_change=NOW - 60, min_on=300)
    d = decide(NOW, Measurements(0, 500), [on], CFG)
    assert d.desired == {"pool": False}
    assert d.reasons["pool"] is Reason.MIN_ON_TIME
    d = decide(NOW, Measurements(0, 500), [replace(on, min_on=0)], CFG)
    assert (d.action, d.load_id) == (Action.TURN_OFF, "pool")


def test_on_demand_load_without_a_request_is_not_ours() -> None:
    """Its power is not reassigned: someone else's load is the house's consumption."""
    loads = [pool(on_demand=True, is_on=True, actual_w=700)]
    assert compute_budget(Measurements(300, 0), loads, CFG) == 300 - 100
    loads = [pool(on_demand=True, request=True, is_on=True, actual_w=700)]
    assert compute_budget(Measurements(300, 0), loads, CFG) == 300 + 700 - 100


def test_on_demand_load_left_on_by_us_without_a_request_goes_off() -> None:
    d = decide(NOW, Measurements(5000, 0), [pool(on_demand=True, is_on=True, owned=True)], CFG)
    assert (d.action, d.load_id, d.reason) == (Action.TURN_OFF, "pool", Reason.WAITING_REQUEST)


def test_out_of_season_load_is_not_eligible() -> None:
    d = decide(NOW, Measurements(5000, 0), [pool(in_season=False), ac()], CFG)
    assert d.reasons["pool"] is Reason.OUT_OF_SEASON
    assert (d.action, d.load_id) == (Action.TURN_ON, "ac")


def test_out_of_season_switches_off_only_what_solarsplit_switched_on() -> None:
    ours = pool(in_season=False, is_on=True, owned=True, off_delay=600)
    d = decide(NOW, Measurements(5000, 0), [ours], CFG)
    # Deliberate, like a global shutdown: no off delay.
    assert (d.action, d.load_id, d.reason) == (Action.TURN_OFF, "pool", Reason.OUT_OF_SEASON)
    d = decide(NOW, Measurements(5000, 0), [replace(ours, last_change=NOW - 60, min_on=300)], CFG)
    assert d.reasons["pool"] is Reason.MIN_ON_TIME
    theirs = pool(in_season=False, is_on=True, actual_w=700)
    d = decide(NOW, Measurements(300, 0), [theirs], CFG)
    assert d.action is Action.NONE and d.reasons["pool"] is Reason.OUT_OF_SEASON
    assert d.budget == 300 - 100  # not ours: not reassigned


def test_unknown_season_leaves_the_load() -> None:
    d = decide(NOW, Measurements(5000, 0), [pool(in_season=None, is_on=True, owned=True)], CFG)
    assert d.action is Action.NONE and d.reasons["pool"] is Reason.SEASON_UNKNOWN


def test_season_and_usage_condition_must_both_hold() -> None:
    d = decide(NOW, Measurements(5000, 0), [pool(in_season=True, eligible=False)], CFG)
    assert d.reasons["pool"] is Reason.NOT_ELIGIBLE
    d = decide(NOW, Measurements(5000, 0), [pool(in_season=False, eligible=True)], CFG)
    assert d.reasons["pool"] is Reason.OUT_OF_SEASON


def test_regulable_out_of_season_gets_its_minimum() -> None:
    reg = Regulator(auto=True, current=1600, minimum=0, maximum=1600)
    load = heater(kind=LoadKind.REGULABLE, regulator=reg, is_on=True, actual_w=0, in_season=False)
    d = decide(NOW, Measurements(3000, 0), [load], CFG)
    assert d.limits["heater"] == 0 and d.reasons["heater"] is Reason.OUT_OF_SEASON


def test_on_demand_out_of_season_and_unmanaged() -> None:
    """Combinations: the season and Manage come before the request."""
    asked = pool(on_demand=True, request=True)
    d = decide(NOW, Measurements(5000, 0), [replace(asked, in_season=False)], CFG)
    assert d.reasons["pool"] is Reason.OUT_OF_SEASON and d.action is Action.NONE
    d = decide(NOW, Measurements(5000, 0), [replace(asked, managed=False)], CFG)
    assert d.reasons["pool"] is Reason.UNMANAGED and d.action is Action.NONE


# Priority until the minimum, urgency, grid fill and saturation


def depuradora(**kw) -> LoadState:
    """A pool pump with a minimum: priority 3, 1 while the minimum is missing."""
    return LoadState(**{"id": "depuradora", "priority": 3, "nominal_w": 530, "is_on": False, "min_priority": 1, **kw})


def water_heater(**kw) -> LoadState:
    """A water heater behind a diverter, priority 2, urgent priority 1, drawing nothing yet."""
    regulator = Regulator(auto=True, current=1600, minimum=0, maximum=1600)
    return LoadState(**{
        "id": "termo", "priority": 2, "nominal_w": 1600, "is_on": True, "actual_w": 0,
        "kind": LoadKind.REGULABLE, "regulator": regulator, "urgency_priority": 1, **kw,
    })


def test_priority_until_the_minimum_and_after() -> None:
    meas = Measurements(1100, 0)  # 1000 W: room for one of them
    pump = replace(depuradora(), priority=5)
    # In an hour the plan chose, the minimum still missing: priority 1
    planned = replace(pump, min_pending=True, planned=True)
    d = decide(NOW, meas, [planned, ac(priority=2)], CFG)
    assert planned.effective_priority == 1
    assert (d.action, d.load_id) == (Action.TURN_ON, "depuradora")
    # Minimum met: back to priority 5
    d = decide(NOW, meas, [pump, ac(priority=2)], CFG)
    assert pump.effective_priority == 5
    assert (d.action, d.load_id) == (Action.TURN_ON, "ac") and d.reasons["depuradora"] is Reason.NO_BUDGET


def test_priority_until_the_minimum_only_in_planned_hours() -> None:
    # Minimum missing, but the plan chose other (cheaper) hours: the normal priority, so the
    # pump does not get ahead to spend sun that is worth more exported now.
    pump = depuradora(priority=5, min_pending=True, planned=False)
    assert pump.effective_priority == 5 and pump.plan_priority == 1
    d = decide(NOW, Measurements(1100, 0), [pump, ac(priority=2)], CFG)
    assert (d.action, d.load_id) == (Action.TURN_ON, "ac")
    # With surplus left after the loads ahead, it may still use it
    d = decide(NOW, Measurements(1800, 0), [pump, ac(priority=2, is_on=True)], CFG)
    assert (d.action, d.load_id) == (Action.TURN_ON, "depuradora")


def test_urgency_puts_the_heater_ahead_of_a_minimum_and_then_lets_go() -> None:
    meas = Measurements(1200, 0)
    pump = depuradora(min_pending=True, planned=True)  # its minimum is missing in a planned hour: priority 1
    d = decide(NOW, meas, [water_heater(urgent=True), pump], CFG)
    # Urgent priority 1 against priority 1 until the minimum: the urgent load goes first,
    # and keeps what it could take up to its limit, so the pump does not start.
    assert water_heater(urgent=True).effective_priority == 1
    assert d.reasons["termo"] is Reason.FULL_POWER and d.limits["termo"] == 1600
    assert d.reasons["depuradora"] is Reason.NO_BUDGET and d.action is Action.NONE
    d = decide(NOW, meas, [water_heater(), pump], CFG)  # urgency off: the normal order is back
    assert (d.action, d.load_id) == (Action.TURN_ON, "depuradora")
    assert d.reasons["termo"] is Reason.LIMITED and d.limits["termo"] == 1100 - 530


def test_unsaturated_regulable_keeps_what_it_could_take() -> None:
    # Just started drawing less than offered: not saturated yet, the relay behind it waits.
    d = decide(NOW, Measurements(1200, 0), [water_heater(urgency_priority=None), ac()], CFG)
    assert d.reasons["ac"] is Reason.NO_BUDGET and d.action is Action.NONE
    assert d.saturated_since == {"termo": NOW} and d.saturated == ()


def test_saturated_regulable_lets_a_relay_through() -> None:
    # A thermostat cut: 0 W with surplus still exported for a minute.
    heater_ = water_heater(saturated_since=NOW - 60)
    d = decide(NOW, Measurements(1200, 0), [heater_, ac()], CFG)
    assert d.saturated == ("termo",)
    assert (d.action, d.load_id, d.reasons["ac"]) == (Action.TURN_ON, "ac", Reason.FITS)


def test_regulable_at_full_output_is_saturated() -> None:
    # Drawing all it can (1550 W at 100 %, close to its limit): what it cannot take is free for the relay.
    reg = Regulator(auto=True, current=1600, minimum=0, maximum=1600, output=100)
    heater_ = water_heater(actual_w=1550, regulator=reg, saturated_since=NOW - 60)
    d = decide(NOW, Measurements(1100, 0), [heater_, ac()], CFG)
    assert d.saturated == ("termo",) and d.reasons["ac"] is Reason.FITS
    # The same draw at 60 %: it could take up to its limit, the relay waits
    reg = replace(reg, output=60)
    d = decide(NOW, Measurements(1100, 0), [replace(heater_, regulator=reg), ac()], CFG)
    assert d.saturated == () and d.saturated_since == {} and d.reasons["ac"] is Reason.NO_BUDGET


def test_grid_fill_switches_on_without_surplus_or_on_delay() -> None:
    load = depuradora(grid_fill=Reason.GRID_FILL, min_pending=True, on_delay=300)
    d = decide(NOW, Measurements(0, 300), [load, ac()], CFG)
    assert (d.action, d.load_id, d.reason) == (Action.TURN_ON, "depuradora", Reason.GRID_FILL)
    d = decide(NOW, Measurements(0, 300), [replace(load, grid_fill=Reason.GRID_FILL_NO_PRICE)], CFG)
    assert d.reason is Reason.GRID_FILL_NO_PRICE


def test_grid_fill_respects_min_off_time() -> None:
    load = depuradora(grid_fill=Reason.GRID_FILL, min_off=600, last_change=NOW - 60)
    d = decide(NOW, Measurements(0, 300), [load], CFG)
    assert d.action is Action.NONE and d.reasons["depuradora"] is Reason.MIN_OFF_TIME


def test_grid_fill_power_is_not_surplus() -> None:
    # On from the grid: its 530 W are not added back to the budget, so the AC cannot use them.
    load = depuradora(is_on=True, grid_fill=Reason.GRID_FILL)
    assert compute_budget(Measurements(0, 530), [load], CFG) == -630
    d = decide(NOW, Measurements(0, 530), [load, ac()], CFG)
    assert d.desired == {"depuradora": True, "ac": False} and d.action is Action.NONE


def test_grid_fill_stays_under_the_contracted_power() -> None:
    cfg = replace(CFG, grid_limit=8200)  # 9200 W contracted, 1000 W margin
    load = depuradora(grid_fill=Reason.GRID_FILL)
    d = decide(NOW, Measurements(0, 7800), [load], cfg)
    assert d.action is Action.NONE and d.reasons["depuradora"] is Reason.GRID_LIMIT
    d = decide(NOW, Measurements(0, 7600), [load], cfg)
    assert (d.action, d.load_id) == (Action.TURN_ON, "depuradora")
    # Already on and the house now draws more: switched off at once (no off delay)
    on = replace(load, is_on=True, off_delay=300)
    d = decide(NOW, Measurements(0, 8500), [on], cfg)
    assert (d.action, d.load_id, d.reason) == (Action.TURN_OFF, "depuradora", Reason.GRID_LIMIT)
