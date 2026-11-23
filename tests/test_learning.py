"""Power learned from a circuit's jumps, without Home Assistant."""

from custom_components.solarsplit.learning import (
    AFTER_END,
    MIN_MEASUREMENTS,
    Change,
    CircuitLearner,
    learned_power,
)

CIRCUIT = "sensor.iotawatt_aires"
STEP = 5.0  # s, one reading per cycle


def _feed(learner: CircuitLearner, power, start: float = 0.0, end: float = 200.0) -> None:
    """Readings every cycle; `power(t)` gives the circuit's power at time t."""
    t = start
    while t <= end:
        learner.sample(CIRCUIT, t, power(t))
        t += STEP


def _measure(learner: CircuitLearner, change: Change, nominal: float = 1000) -> float | None:
    learner.change(CIRCUIT, change, nominal)
    early = learner.due(change.time + AFTER_END - 1)
    assert change not in [item.change for item in early]  # the window has not passed yet
    [measurement] = [item for item in learner.due(change.time + AFTER_END) if item.change == change]
    return measurement.power


def test_clean_jump_is_the_load_power() -> None:
    learner = CircuitLearner()
    _feed(learner, lambda t: 300 if t < 100 else 1200)
    assert _measure(learner, Change("salon", 100, on=True)) == 900


def test_switch_off_is_measured_as_a_positive_power() -> None:
    learner = CircuitLearner()
    _feed(learner, lambda t: 1200 if t < 100 else 300)
    assert _measure(learner, Change("salon", 100, on=False)) == 900


def test_compressor_start_peak_is_skipped() -> None:
    """3 kW for 15 s at the start; the after window begins 20 s after the change."""
    learner = CircuitLearner()
    _feed(learner, lambda t: 300 if t < 100 else (3300 if t < 115 else 1200))
    assert _measure(learner, Change("salon", 100, on=True)) == 900


def test_another_load_of_the_circuit_changing_meanwhile_discards_it() -> None:
    learner = CircuitLearner()
    _feed(learner, lambda t: 300 if t < 100 else (1200 if t < 130 else 1800))
    learner.change(CIRCUIT, Change("sara", 130, on=True), 900)
    assert _measure(learner, Change("salon", 100, on=True)) is None


def test_another_load_changing_just_before_also_discards_it() -> None:
    learner = CircuitLearner()
    _feed(learner, lambda t: 300 if t < 90 else 1200)
    learner.change(CIRCUIT, Change("sara", 90, on=True), 900)
    assert _measure(learner, Change("salon", 100, on=True)) is None


def test_the_same_load_changing_again_is_not_another_load() -> None:
    learner = CircuitLearner()
    _feed(learner, lambda t: 300 if t < 100 else 1200)
    learner.change(CIRCUIT, Change("salon", 50, on=False), 1000)
    assert _measure(learner, Change("salon", 100, on=True)) == 900


def test_jumps_too_small_or_too_large_are_discarded() -> None:
    small = CircuitLearner()
    _feed(small, lambda t: 300 if t < 100 else 340)
    assert _measure(small, Change("salon", 100, on=True)) is None
    large = CircuitLearner()
    _feed(large, lambda t: 300 if t < 100 else 2400)
    assert _measure(large, Change("salon", 100, on=True), nominal=1000) is None  # 2100 > 2 x 1000


def test_no_readings_in_a_window_discards_it() -> None:
    learner = CircuitLearner()
    _feed(learner, lambda t: 1200, start=100)  # nothing before the change
    assert _measure(learner, Change("salon", 100, on=True)) is None


def test_learned_power_needs_three_measurements() -> None:
    assert MIN_MEASUREMENTS == 3
    assert learned_power([]) is None
    assert learned_power([900, 950]) is None
    assert learned_power([900, 950, 1000]) == 950
