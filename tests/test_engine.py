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
