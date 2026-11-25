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
    VARIABLE = "variable"  # regulated elsewhere while its variable entity is on; only its consumption counts
    REGULABLE = "regulable"  # regulates itself; the engine sets its power limit


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
    GRID_UNKNOWN = "grid_unknown"  # a grid reading is missing or frozen: nothing is decided
    MODE_UNKNOWN = "mode_unknown"  # the zero-injection entity is not usable: nothing is decided
    SETTLING = "settling"
    STEADY = "steady"
    FITS = "fits"
    NO_BUDGET = "no_budget"
    WAITING_HIGHER = "waiting_higher"
    NOT_ELIGIBLE = "not_eligible"
    TARGET_REACHED = "target_reached"  # its daily target is met: not eligible until tomorrow
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
    SWITCHED_OFF = "switched_off"  # switched off by something else: never switched back on
    GLOBAL_UNKNOWN = "global_unknown"
    CONDITION_UNKNOWN = "condition_unknown"
    EXTERNAL = "external"  # a regulable load outside its automatic mode: grid power, not ours
    FULL_POWER = "full_power"  # a regulable load with nothing ahead of it: limit at its maximum
    LIMITED = "limited"  # a regulable load limited to what the loads ahead of it leave
    WAITING_REQUEST = "waiting_request"  # an on-demand load nobody asked for: not eligible
    OUT_OF_SEASON = "out_of_season"  # the current season is not one of the load's
    SEASON_UNKNOWN = "season_unknown"  # the season entity is not usable: left as it is
    NO_SOLAR = "no_solar"  # no solar production: surplus management does not apply, left as it is
    GRID_FILL = "grid_fill"  # its minimum needs the grid: on in one of the cheapest hours planned
    GRID_FILL_NO_PRICE = "grid_fill_no_price"  # same, without prices: in the last hours before the deadline
    GRID_LIMIT = "grid_limit"  # grid fill would take the import over the contracted power less the margin


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
    grid_limit: float | None = None  # W of import grid fill may reach (contracted power less margin)
    # A regulable load is saturated when it cannot take more: its output is at the top,
    # or surplus is still exported while it draws well below its limit (a thermostat
    # cut), for this long.
    saturation_output: float = 99  # %
    saturation_export: float = 150  # W
    saturation_time: float = 60  # s


@dataclass(frozen=True)
class Regulator:
    """The power limit of a regulable load (for example a diverter's maximum power)."""

    auto: bool  # the device is in its automatic mode: its power is surplus, and ours to limit
    current: float  # W, limit the device has now
    minimum: float  # W
    maximum: float  # W
    threshold: float = 100  # W, smallest change worth writing (bounds are always written)
    interval: float = 30  # s, minimum time between two writes
    written: float | None = None  # time of the last write
    lowered: bool = False  # SolarSplit set it below its maximum and has not restored it since
    output: float | None = None  # % of its output in use (a diverter's duty); None: not known


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
    switched_off: bool = False  # something else switched it off (a safety cut): never switched on
    owned: bool = False  # SolarSplit switched it on (in shadow mode: assumed for loads that are on)
    # The global condition holds, or this load ignores it. None: its entity is not
    # usable, and the load is left as it is.
    global_ok: bool | None = True
    eligible: bool | None = True  # its usage condition holds; None: unknown, left as it is
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
    regulator: Regulator | None = None  # only for regulable loads
    # Daily target: hours on today and hours wanted (None: no target). Once reached it
    # stays reached for the day: the caller stores it (Decision.target_reached) and
    # passes it back as `target_done`.
    hours_today: float | None = None
    target_hours: float | None = None
    target_done: bool = False
    # Season: the current season is one of the load's (or it has none). None: the
    # season entity is not usable, and the load is left as it is.
    in_season: bool | None = True
    # On demand: eligible only while a request is active (someone else switched it on,
    # and nobody else has switched it off since).
    on_demand: bool = False
    request: bool = False
    # Objective: minimum hours before a deadline. `min_pending` while it is not met and
    # the deadline has not passed; `min_priority` replaces the priority meanwhile, but only
    # in the hours the plan chose (`planned`): elsewhere the normal priority, so the load
    # does not get ahead of others to spend sun that is worth more exported.
    min_pending: bool = False
    min_priority: int | None = None
    planned: bool = False
    # Urgency: while its entity is on, `urgency_priority` replaces every other priority.
    urgent: bool = False
    urgency_priority: int | None = None
    # The plan buys this hour from the grid to meet the minimum (GRID_FILL or
    # GRID_FILL_NO_PRICE); None otherwise. Only for relay loads.
    grid_fill: Reason | None = None
    saturated_since: float | None = None  # regulable: since when it cannot take more
    # Solar production (the same for every load). Without it surplus management does not
    # apply: only loads SolarSplit switched on by surplus are managed (switched off when
    # the surplus is gone), and grid fill follows its plan. The rest are left alone.
    solar: bool = True

    @property
    def night(self) -> bool:
        """Whether the no-production rule leaves this load as it is."""
        if self.solar or self.filling:
            return False
        if self.regulator is not None or self.kind is LoadKind.VARIABLE:
            return True  # the water heater is TankWise's at night: its maximum back, nothing written
        if self.on_demand and self.request:
            return True
        return not (self.is_on and self.owned)

    @property
    def effective_priority(self) -> int:
        """Priority in this cycle: urgency first, then the one until the minimum in a planned hour, then the normal one."""
        if self.urgent and self.urgency_priority is not None:
            return self.urgency_priority
        if self.min_pending and self.planned and self.min_priority is not None:
            return self.min_priority
        return self.priority

    @property
    def plan_priority(self) -> int:
        """Order in which the plan serves the loads: urgency, then the priority until the minimum."""
        if self.urgent and self.urgency_priority is not None:
            return self.urgency_priority
        if self.min_pending and self.min_priority is not None:
            return self.min_priority
        return self.priority

    @property
    def filling(self) -> bool:
        """Whether it is a relay load the plan fills from the grid now: its power is not surplus."""
        return self.grid_fill is not None and self.kind is LoadKind.RELAY

    @property
    def consumption(self) -> float:
        """Power drawn now: measured if possible, else nominal while on."""
        if self.actual_w is not None:
            return max(self.actual_w, 0.0)
        if self.kind is not LoadKind.RELAY:
            return 0.0
        return self.nominal_w if self.is_on else 0.0

    @property
    def target_reached(self) -> bool:
        """Whether its daily target is met."""
        return self.target_done or (
            self.target_hours is not None and self.hours_today is not None and self.hours_today >= self.target_hours
        )

    @property
    def controlled(self) -> bool:
        """Whether the engine owns this load's power.

        Loads it does not own are left as they are, and their power is not reassigned.
        A load it switched on stays its own when the global condition fails, to switch it off.
        """
        return (
            self.managed and self.available and not self.manual and not self.switched_off
            and ((self.global_ok is True and self.eligible is not None) or self.shutdown)
            and (self.in_season is True or self.season_off or self.shutdown
                 or (self.in_season is False and self.regulator is not None))
            and (not self.on_demand or self.request or self.demand_off or self.season_off or self.shutdown)
            and (self.regulator is None or self.regulator.auto)
            and not self.filling
            and not self.night
        )

    @property
    def shutdown(self) -> bool:
        """Whether the global condition failing means switching this load off."""
        return self.global_ok is False and self.owned and self.is_on and self.kind is LoadKind.RELAY

    @property
    def demand_off(self) -> bool:
        """Whether an on-demand load SolarSplit left on must go off: nobody asks for it."""
        return (
            self.on_demand and not self.request and self.owned and self.is_on and self.kind is LoadKind.RELAY
        )

    @property
    def season_off(self) -> bool:
        """Whether being out of season means switching this load off: only if SolarSplit switched it on."""
        return self.in_season is False and self.owned and self.is_on and self.kind is LoadKind.RELAY


@dataclass(frozen=True)
class Decision:
    """Outcome of one cycle: at most one action, plus the reason for every load."""

    budget: float | None  # None when the grid readings are unknown
    action: Action = Action.NONE
    load_id: str | None = None
    reason: Reason = Reason.STEADY
    desired: dict[str, bool] = field(default_factory=dict)
    reasons: dict[str, Reason] = field(default_factory=dict)
    on_since: dict[str, float] = field(default_factory=dict)  # loads whose switch-on condition holds
    off_since: dict[str, float] = field(default_factory=dict)  # loads whose switch-off condition holds
    limits: dict[str, float] = field(default_factory=dict)  # W, limit each regulable load should have
    writes: dict[str, float] = field(default_factory=dict)  # W, limits to write in this cycle
    target_reached: tuple[str, ...] = ()  # loads whose daily target is met
    saturated_since: dict[str, float] = field(default_factory=dict)  # regulable loads that cannot take more
    saturated: tuple[str, ...] = ()  # ...for the saturation time already


def _since(previous: float | None, now: float) -> float:
    """Start of the current uninterrupted run of a condition that holds now."""
    return now if previous is None else previous


def _limit_writes(now: float, loads: list[LoadState], limits: dict[str, float]) -> dict[str, float]:
    """Limits worth writing now, so the device is not flooded.

    A target is written when it moves by the threshold or reaches a bound, and no
    sooner than the interval after the last write. A regulable load SolarSplit no
    longer limits (paused, not managed, outside its automatic mode…) gets its
    maximum back at once, but only if SolarSplit lowered it.
    """
    writes: dict[str, float] = {}
    for load in loads:
        if (reg := load.regulator) is None or not load.available:
            continue
        if (target := limits.get(load.id)) is None:
            if reg.lowered and reg.current < reg.maximum:
                writes[load.id] = reg.maximum
            continue
        change = abs(target - reg.current)
        bound = target in (reg.minimum, reg.maximum)
        if change < 1 or (change < reg.threshold and not bound):
            continue
        if reg.written is not None and now - reg.written < reg.interval:
            continue
        writes[load.id] = target
    return writes


def compute_budget(meas: Measurements | None, loads: list[LoadState], cfg: EngineConfig) -> float | None:
    """Power available to hand out, counting what controlled loads already use."""
    if meas is None:
        return None
    assigned = sum(load.consumption for load in loads if load.controlled)
    return meas.export_w - meas.import_w + assigned - cfg.safety_margin


def decide(
    now: float,
    meas: Measurements | None,
    loads: list[LoadState],
    cfg: EngineConfig,
    last_action: float | None = None,
    active: bool = True,
    pause_reason: Reason = Reason.PAUSED,
) -> Decision:
    """Run one control cycle.

    `meas` is None when a grid reading is missing or frozen: the cycle decides
    nothing and every delay mark resets, since nobody saw the condition hold.
    """
    budget = compute_budget(meas, loads, cfg)
    if not active:
        return Decision(budget, reason=pause_reason, writes=_limit_writes(now, loads, {}))
    if budget is None:
        # Giving back the limit of a load SolarSplit no longer limits needs no grid reading.
        released = [load for load in loads if load.regulator and (not load.managed or not load.regulator.auto)]
        return Decision(budget, reason=Reason.GRID_UNKNOWN, writes=_limit_writes(now, released, {}))

    desired: dict[str, bool] = {}
    reasons: dict[str, Reason] = {}
    limits: dict[str, float] = {}
    remaining = budget
    gap = False  # an eligible relay load before this one was left off
    claimed = False  # a load before this one uses, or is about to use, part of the budget

    def order(load: LoadState) -> tuple[int, bool, int, str]:
        # On a tie, an urgent load goes first.
        return load.effective_priority, not (load.urgent and load.urgency_priority is not None), load.priority, load.id

    saturated_since: dict[str, float] = {}
    saturated: list[str] = []
    for load in sorted(loads, key=order):
        if not load.managed:
            reasons[load.id] = Reason.UNMANAGED
            continue
        if not load.available:
            reasons[load.id] = Reason.UNAVAILABLE
            continue
        if load.switched_off:
            reasons[load.id] = Reason.SWITCHED_OFF
            continue
        if load.regulator is not None and not load.regulator.auto:
            reasons[load.id] = Reason.EXTERNAL
            continue
        if load.manual:
            reasons[load.id] = Reason.MANUAL
            continue
        if load.shutdown:
            desired[load.id] = False
            reasons[load.id] = Reason.GLOBAL_SHUTDOWN
            continue
        if load.global_ok is None:
            reasons[load.id] = Reason.GLOBAL_UNKNOWN
            continue
        if not load.global_ok:
            reasons[load.id] = Reason.GLOBAL_CONDITION
            continue
        if load.in_season is None:
            reasons[load.id] = Reason.SEASON_UNKNOWN
            continue
        if not load.in_season and load.regulator is None:
            if load.season_off:
                desired[load.id] = False
            reasons[load.id] = Reason.OUT_OF_SEASON
            continue
        if load.eligible is None:
            reasons[load.id] = Reason.CONDITION_UNKNOWN
            continue
        if load.night:
            # Nothing is switched on by surplus and nothing someone else switched on is switched
            # off; a regulable load gets its maximum back (it is not limited).
            reasons[load.id] = Reason.NO_SOLAR
            continue
        if load.on_demand and not load.request and load.regulator is None:
            if load.demand_off:
                desired[load.id] = False
            reasons[load.id] = Reason.WAITING_REQUEST
            continue
        if load.kind is LoadKind.VARIABLE:
            remaining -= load.consumption
            claimed = claimed or load.consumption > 0
            reasons[load.id] = Reason.VARIABLE
            continue
        if (reg := load.regulator) is not None:
            # Its consumption is in the budget, so `remaining` is what it may take.
            if not load.in_season:
                limits[load.id], reasons[load.id] = reg.minimum, Reason.OUT_OF_SEASON
            elif not load.eligible:
                limits[load.id], reasons[load.id] = reg.minimum, Reason.NOT_ELIGIBLE
            elif load.target_reached:
                limits[load.id], reasons[load.id] = reg.minimum, Reason.TARGET_REACHED
            elif claimed:
                limits[load.id] = min(max(remaining, reg.minimum), reg.maximum)
                reasons[load.id] = Reason.LIMITED if limits[load.id] < reg.maximum else Reason.FULL_POWER
            else:
                limits[load.id], reasons[load.id] = reg.maximum, Reason.FULL_POWER
            # Until it is saturated it claims what it could take up to its limit: a load
            # behind it does not start with that. Saturated, only what it draws.
            unused = meas.export_w - meas.import_w > cfg.saturation_export
            if (reg.output is not None and reg.output >= cfg.saturation_output) or (
                unused and load.consumption < reg.current - reg.threshold
            ):
                saturated_since[load.id] = _since(load.saturated_since, now)
            if load.id in saturated_since and now - saturated_since[load.id] >= cfg.saturation_time:
                saturated.append(load.id)
                remaining -= load.consumption
            else:
                remaining -= max(load.consumption, min(max(remaining, 0.0), limits[load.id]))
            claimed = claimed or load.consumption > 0
            continue
        if not load.eligible:
            desired[load.id] = False
            reasons[load.id] = Reason.NOT_ELIGIBLE
            continue
        if load.target_reached:
            desired[load.id] = False
            reasons[load.id] = Reason.TARGET_REACHED
            continue
        if load.filling:
            # Deliberate, from the plan: no budget, no skipping rule. Its power is not in
            # the budget, so it takes nothing from the loads behind it.
            fill_w = 0.0 if load.is_on else load.nominal_w
            if cfg.grid_limit is not None and meas.import_w + fill_w > cfg.grid_limit:
                desired[load.id], reasons[load.id] = False, Reason.GRID_LIMIT
            else:
                desired[load.id], reasons[load.id] = True, load.grid_fill
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
            claimed = True
            reasons[load.id] = Reason.FITS
        else:
            reasons[load.id] = Reason.NO_BUDGET
            gap = True

    by_id = {load.id: load for load in loads}
    reached = tuple(sorted(i for i, reason in reasons.items() if reason is Reason.TARGET_REACHED))
    offs = sorted(
        (by_id[i] for i, on in desired.items() if not on and by_id[i].is_on),
        key=lambda load: (-load.effective_priority, load.id),
    )
    ons = sorted((by_id[i] for i, on in desired.items() if on and not by_id[i].is_on), key=order)
    # A load missing from these dicts does not meet the condition: its mark resets.
    on_since = {load.id: _since(load.on_since, now) for load in ons}
    off_since = {load.id: _since(load.off_since, now) for load in offs}

    def result(action: Action = Action.NONE, load_id: str | None = None,
               reason: Reason = Reason.STEADY) -> Decision:
        return Decision(
            budget, action, load_id, reason, desired, reasons, on_since, off_since,
            limits, _limit_writes(now, loads, limits), reached, saturated_since, tuple(sorted(saturated)),
        )

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
            # A global shutdown or the end of the season is deliberate, not a passing
            # cloud: no off delay, but the minimum on time still protects compressors.
            deliberate = load.shutdown or load.season_off or reasons[load.id] is Reason.GRID_LIMIT
            if not deliberate and now - off_since[load.id] < load.off_delay:
                reasons[load.id] = Reason.OFF_DELAY
                continue
        else:
            if since < load.min_off:
                reasons[load.id] = Reason.MIN_OFF_TIME
                continue
            if load.grid_fill is None and now - on_since[load.id] < load.on_delay:
                reasons[load.id] = Reason.ON_DELAY
                continue
        return result(action, load.id, reasons[load.id])
    return result()
