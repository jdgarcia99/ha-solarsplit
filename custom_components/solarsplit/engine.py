"""Control engine: a pure function from measurements and load states to one decision.

It has no Home Assistant imports, so it can be tested, and replayed over real
days, with plain pytest. Times are seconds on any monotonic clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class LoadKind(StrEnum):
    """How the engine treats a load."""

    RELAY = "relay"  # switched on and off by the engine
    VARIABLE = "variable"  # regulated elsewhere (FreeDS); only its consumption counts


class Action(StrEnum):
    """What the engine asks for in a cycle."""

    NONE = "none"
    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"


class Reason(StrEnum):
    """Why a load is in its desired state, or why the cycle did nothing.

    Codes, not text: the UI translates them.
    """

    PAUSED = "paused"
    SETTLING = "settling"
    STEADY = "steady"
    FITS = "fits"
    NO_BUDGET = "no_budget"
    WAITING_HIGHER = "waiting_higher"
    NOT_ELIGIBLE = "not_eligible"
    MIN_ON_TIME = "min_on_time"
    MIN_OFF_TIME = "min_off_time"
    ON_DELAY = "on_delay"
    OFF_DELAY = "off_delay"
    MANUAL = "manual"
    UNMANAGED = "unmanaged"
    UNAVAILABLE = "unavailable"
    GLOBAL_CONDITION = "global_condition"
    GLOBAL_SHUTDOWN = "global_shutdown"
    VARIABLE = "variable"


@dataclass(frozen=True)
class Measurements:
    """Smoothed grid readings, in W. Both are positive or zero."""

    export_w: float
    import_w: float


@dataclass(frozen=True)
class EngineConfig:
    """Global tuning of the engine."""

    safety_margin: float = 100  # W
    on_margin: float = 100  # W, extra budget needed to switch a load on
    import_tolerance: float = 150  # W, deficit tolerated before switching a load off
    settle_time: float = 30  # s


@dataclass(frozen=True)
class LoadState:
    """A load as the engine sees it in one cycle."""

    id: str
    priority: int  # 1 = first
    nominal_w: float
    is_on: bool
    actual_w: float | None = None
    kind: LoadKind = LoadKind.RELAY
    managed: bool = True
    available: bool = True  # its entity reports a usable state
    manual: bool = False
    owned: bool = False  # SolarSplit switched it on (in shadow mode: assumed for loads that are on)
    global_ok: bool = True  # the global condition holds, or this load ignores it
    eligible: bool = True  # its usage condition holds
    no_skip: bool = False  # only enters when every eligible load before it is on
    min_on: float = 0  # s
    min_off: float = 0  # s
    on_delay: float = 0  # s, the switch-on condition must hold this long
    off_delay: float = 0  # s, the switch-off condition must hold this long
    last_change: float | None = None  # time of the last on/off transition
    # Since when the switch-on / switch-off condition holds without interruption.
    # The engine computes them (Decision.on_since / off_since); the caller stores
    # them and passes them back in the next cycle.
    on_since: float | None = None
    off_since: float | None = None

    @property
    def consumption(self) -> float:
        """Power drawn now: measured if possible, else nominal while on."""
        if self.actual_w is not None:
            return max(self.actual_w, 0.0)
        if self.kind is LoadKind.VARIABLE:
            return 0.0
        return self.nominal_w if self.is_on else 0.0

    @property
    def controlled(self) -> bool:
        """Whether the engine owns this load's power.

        Loads it does not own are left as they are, and their power is not reassigned.
        A load it switched on stays its own when the global condition fails, to switch it off.
        """
        return self.managed and self.available and not self.manual and (self.global_ok or self.shutdown)

    @property
    def shutdown(self) -> bool:
        """Whether the global condition failing means switching this load off."""
        return not self.global_ok and self.owned and self.is_on and self.kind is LoadKind.RELAY


@dataclass(frozen=True)
class Decision:
    """Outcome of one cycle: at most one action, plus the reason for every load."""

    budget: float
    action: Action = Action.NONE
    load_id: str | None = None
    reason: Reason = Reason.STEADY
    desired: dict[str, bool] = field(default_factory=dict)
    reasons: dict[str, Reason] = field(default_factory=dict)
    on_since: dict[str, float] = field(default_factory=dict)  # loads whose switch-on condition holds
    off_since: dict[str, float] = field(default_factory=dict)  # loads whose switch-off condition holds


def _since(previous: float | None, now: float) -> float:
    """Start of the current uninterrupted run of a condition that holds now."""
    return now if previous is None else previous


def compute_budget(meas: Measurements, loads: list[LoadState], cfg: EngineConfig) -> float:
    """Power available to hand out, counting what controlled loads already use."""
    assigned = sum(load.consumption for load in loads if load.controlled)
    return meas.export_w - meas.import_w + assigned - cfg.safety_margin


def decide(
    now: float,
    meas: Measurements,
    loads: list[LoadState],
    cfg: EngineConfig,
    last_action: float | None = None,
    active: bool = True,
) -> Decision:
    """Run one control cycle."""
    budget = compute_budget(meas, loads, cfg)
    if not active:
        return Decision(budget, reason=Reason.PAUSED)

    desired: dict[str, bool] = {}
    reasons: dict[str, Reason] = {}
    remaining = budget
    gap = False  # an eligible relay load before this one was left off

    for load in sorted(loads, key=lambda load: (load.priority, load.id)):
        if not load.managed:
            reasons[load.id] = Reason.UNMANAGED
            continue
        if not load.available:
            reasons[load.id] = Reason.UNAVAILABLE
            continue
        if load.manual:
            reasons[load.id] = Reason.MANUAL
            continue
        if load.shutdown:
            desired[load.id] = False
            reasons[load.id] = Reason.GLOBAL_SHUTDOWN
            continue
        if not load.global_ok:
            reasons[load.id] = Reason.GLOBAL_CONDITION
            continue
        if load.kind is LoadKind.VARIABLE:
            remaining -= load.consumption
            reasons[load.id] = Reason.VARIABLE
            continue
        if not load.eligible:
            desired[load.id] = False
            reasons[load.id] = Reason.NOT_ELIGIBLE
            continue
        if gap and load.no_skip:
            desired[load.id] = False
            reasons[load.id] = Reason.WAITING_HIGHER
            continue
        if load.is_on:
            power = load.consumption
            fits = remaining + cfg.import_tolerance >= power
        else:
            power = load.nominal_w
            fits = remaining >= power + cfg.on_margin
        desired[load.id] = fits
        if fits:
            remaining -= power
            reasons[load.id] = Reason.FITS
        else:
            reasons[load.id] = Reason.NO_BUDGET
            gap = True

    by_id = {load.id: load for load in loads}
    offs = sorted(
        (by_id[i] for i, on in desired.items() if not on and by_id[i].is_on),
        key=lambda load: (-load.priority, load.id),
    )
    ons = sorted(
        (by_id[i] for i, on in desired.items() if on and not by_id[i].is_on),
        key=lambda load: (load.priority, load.id),
    )
    # A load missing from these dicts does not meet the condition: its mark resets.
    on_since = {load.id: _since(load.on_since, now) for load in ons}
    off_since = {load.id: _since(load.off_since, now) for load in offs}

    def result(action: Action = Action.NONE, load_id: str | None = None,
               reason: Reason = Reason.STEADY) -> Decision:
        return Decision(budget, action, load_id, reason, desired, reasons, on_since, off_since)

    if not offs and not ons:
        return result()
    if last_action is not None and now - last_action < cfg.settle_time:
        return result(reason=Reason.SETTLING)

    # Switch-offs first; never switch on while a needed switch-off is blocked.
    action = Action.TURN_OFF if offs else Action.TURN_ON
    for load in offs or ons:
        since = float("inf") if load.last_change is None else now - load.last_change
        if action is Action.TURN_OFF:
            if since < load.min_on:
                reasons[load.id] = Reason.MIN_ON_TIME
                continue
            # A global shutdown is deliberate, not a passing cloud: no off delay,
            # but the minimum on time still protects compressors and pumps.
            if not load.shutdown and now - off_since[load.id] < load.off_delay:
                reasons[load.id] = Reason.OFF_DELAY
                continue
        else:
            if since < load.min_off:
                reasons[load.id] = Reason.MIN_OFF_TIME
                continue
            if now - on_since[load.id] < load.on_delay:
                reasons[load.id] = Reason.ON_DELAY
                continue
        return result(action, load.id, reasons[load.id])
    return result()
