"""Power of a load learned from the jumps of its circuit's power when it switches.

A circuit meter (for example an IoTaWatt channel) measures several appliances at
once, so it cannot be a load's power sensor. But when one load switches and no
other load of the circuit does, the jump of the circuit's power is that load's
power. No Home Assistant imports: times are seconds on any clock.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

BEFORE = 20.0  # s, the circuit's mean power before a change...
AFTER_START = 20.0  # s, ...against its mean from this long after the change (skips a compressor's start)...
AFTER_END = 60.0  # s, ...to this long after it
MIN_JUMP = 50.0  # W, smaller jumps are noise
MAX_FACTOR = 2.0  # a jump above twice the nominal power is something else
KEEP = 10  # measurements kept (moving average)
MIN_MEASUREMENTS = 3  # needed to use the learned power instead of the nominal one
HISTORY = BEFORE + AFTER_END + 60.0  # s of circuit readings and changes kept


@dataclass(frozen=True)
class Change:
    """A load of a circuit switched on or off at `time`."""

    load_id: str
    time: float
    on: bool


@dataclass(frozen=True)
class Measurement:
    """Outcome of a change once its window has passed: the load's power, or None if discarded."""

    change: Change
    power: float | None


def learned_power(measurements: list[float]) -> float | None:
    """Mean of the measurements, once there are enough of them."""
    if len(measurements) < MIN_MEASUREMENTS:
        return None
    return sum(measurements) / len(measurements)


class CircuitLearner:
    """Readings and load changes of every circuit, and the measurements still waiting for their window."""

    def __init__(self) -> None:
        """Start with no history."""
        self._samples: dict[str, deque[tuple[float, float]]] = {}
        self._changes: dict[str, deque[Change]] = {}
        self._pending: list[tuple[str, Change, float]] = []

    def sample(self, circuit: str, now: float, watts: float) -> None:
        """Record the circuit's power now (one reading per cycle)."""
        samples = self._samples.setdefault(circuit, deque())
        samples.append((now, watts))
        while samples and samples[0][0] < now - HISTORY:
            samples.popleft()

    def change(self, circuit: str, change: Change, nominal: float) -> None:
        """Record that a load of the circuit switched, and measure it when its window has passed."""
        changes = self._changes.setdefault(circuit, deque())
        changes.append(change)
        while changes and changes[0].time < change.time - HISTORY:
            changes.popleft()
        self._pending.append((circuit, change, nominal))

    def due(self, now: float) -> list[Measurement]:
        """Measurements whose window has passed: the jump, or None when it cannot be trusted."""
        done = [item for item in self._pending if now >= item[1].time + AFTER_END]
        self._pending = [item for item in self._pending if now < item[1].time + AFTER_END]
        return [Measurement(change, self._measure(circuit, change, nominal)) for circuit, change, nominal in done]

    def _measure(self, circuit: str, change: Change, nominal: float) -> float | None:
        start, end = change.time - BEFORE, change.time + AFTER_END
        if any(
            other.load_id != change.load_id and start <= other.time <= end
            for other in self._changes.get(circuit, ())
        ):
            return None  # another load of the circuit switched meanwhile
        samples = self._samples.get(circuit, ())
        before = [watts for time, watts in samples if start <= time < change.time]
        after = [watts for time, watts in samples if change.time + AFTER_START <= time <= end]
        if not before or not after:
            return None
        jump = sum(after) / len(after) - sum(before) / len(before)
        if not change.on:
            jump = -jump
        if jump < MIN_JUMP or jump > MAX_FACTOR * nominal:
            return None
        return jump
