"""Control engine, without Home Assistant."""

from custom_components.solarsplit.engine import (
    Action,
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
