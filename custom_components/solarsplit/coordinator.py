"""Control loop: reads Home Assistant, runs the engine, stores its marks and, outside shadow mode, acts.

The coordinator never decides. It turns entity states into engine inputs, keeps
the data that must survive between cycles, and executes the engine's action.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_NAME, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AUTO_MANAGE,
    CONF_AUTO_STATE,
    CONF_BASE_LOAD,
    CONF_BUY_PRICE_SENSOR,
    CONF_CIRCUIT_SENSOR,
    CONF_CONDITION_ENTITY,
    CONF_CONDITION_STATE,
    CONF_CONTRACTED_POWER,
    CONF_CYCLE_INTERVAL,
    CONF_DAY_START,
    CONF_DEADLINE,
    CONF_DEADLINE_ENTITY,
    CONF_EXPORT_SENSOR,
    CONF_FORECAST_ENTRIES,
    CONF_GLOBAL_CONDITION,
    CONF_GRID_MARGIN,
    CONF_GRID_SENSOR,
    CONF_GRID_SIGN,
    CONF_HOURS_SENSOR,
    CONF_HVAC_MODE,
    CONF_IMPORT_SENSOR,
    CONF_IMPORT_TOLERANCE,
    CONF_LIMIT_ENTITY,
    CONF_LIMIT_INTERVAL,
    CONF_LIMIT_MAX,
    CONF_LIMIT_MIN,
    CONF_LIMIT_THRESHOLD,
    CONF_LOAD_ENTITY,
    CONF_LOAD_TYPE,
    CONF_MANAGE_AT,
    CONF_MIN_ENTITY,
    CONF_MIN_HOURS,
    CONF_MIN_OFF,
    CONF_MIN_ON,
    CONF_MIN_PRIORITY,
    CONF_MODE_ENTITY,
    CONF_NO_SKIP,
    CONF_NOMINAL_POWER,
    CONF_OFF_DELAY,
    CONF_ON_DELAY,
    CONF_ON_DEMAND,
    CONF_ON_MARGIN,
    CONF_OUTPUT_ENTITY,
    CONF_POWER_SENSOR,
    CONF_PRIORITY,
    CONF_PV_SENSOR,
    CONF_RESPECT_GLOBAL,
    CONF_RESPECT_MANUAL,
    CONF_SAFETY_MARGIN,
    CONF_SEASON_ENTITY,
    CONF_SEASONS,
    CONF_SELL_PRICE_SENSOR,
    CONF_SETTLE_TIME,
    CONF_SHADOW_MODE,
    CONF_SMOOTHING_WINDOW,
    CONF_STALE_TIMEOUT,
    CONF_TARGET_ENTITY,
    CONF_TARGET_HOURS,
    CONF_URGENCY_ENTITY,
    CONF_URGENCY_PRIORITY,
    CONF_VARIABLE_ENTITY,
    CONF_WINDOW_END,
    CONF_WINDOW_START,
    CONF_ZERO_INJECTION_ENTITY,
    DOMAIN,
    GRID_SIGN_EXPORT_NEGATIVE,
    LOAD_TYPE_REGULABLE,
)
from .engine import (
    Action,
    Decision,
    EngineConfig,
    LoadKind,
    LoadState,
    Measurements,
    Reason,
    Regulator,
    decide,
)
from .forecast import async_read_forecast
from .learning import KEEP, Change, CircuitLearner, learned_power
from .loads import (
    CLIMATE,
    async_set_limit,
    async_switch_load,
    climate_settings,
    entity_has_state,
    entity_is_on,
    load_is_on,
    read_hours,
    read_number,
    read_watts,
)
from .planner import HourlyProfile, Plan, Request, Slot, expected_surplus, plan
from .settings import config_snapshot, get_settings, load_settings

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SAVE_DELAY = 10  # s, batch memory writes
MARKS_MAX_GAP = 120  # s, delay marks older than this gap are dropped on restore
FORECAST_REFRESH = 300  # s between two reads of the solar forecast

MODE_NORMAL = "normal"
MODE_ZERO_INJECTION = "zero_injection"
MODE_PAUSED = "paused"
MODES = [MODE_NORMAL, MODE_ZERO_INJECTION, MODE_PAUSED]

LOAD_ON = "on"
LOAD_OFF = "off"
LOAD_WAITING = "waiting"  # a change is wanted but a timing rule holds it
LOAD_BLOCKED = "blocked"  # off, and not allowed to start even with surplus
LOAD_MANUAL = "manual"
LOAD_UNMANAGED = "unmanaged"
LOAD_UNAVAILABLE = "unavailable"
LOAD_EXTERNAL = "external"  # a regulable load outside its automatic mode
LOAD_STATES = [
    LOAD_ON, LOAD_OFF, LOAD_WAITING, LOAD_BLOCKED, LOAD_MANUAL, LOAD_UNMANAGED, LOAD_UNAVAILABLE, LOAD_EXTERNAL,
]

# Reasons that keep a load off whatever the surplus. Lack of surplus (no_budget,
# waiting_higher, grid_limit) is not one of them: such a load is just off.
_BLOCKING = (
    Reason.NOT_ELIGIBLE, Reason.GLOBAL_CONDITION, Reason.GLOBAL_UNKNOWN, Reason.CONDITION_UNKNOWN,
    Reason.SWITCHED_OFF, Reason.TARGET_REACHED, Reason.WAITING_REQUEST, Reason.OUT_OF_SEASON,
    Reason.SEASON_UNKNOWN,
)
_STATE_FOR = {
    Reason.MANUAL: LOAD_MANUAL,
    Reason.UNMANAGED: LOAD_UNMANAGED,
    Reason.UNAVAILABLE: LOAD_UNAVAILABLE,
    Reason.EXTERNAL: LOAD_EXTERNAL,
}


def _next_time(clock: str) -> float:
    """Timestamp of the next local occurrence of a time of day ("HH:MM[:SS]")."""
    return _occurrence(clock, dt_util.now(), after=True).timestamp()


def _occurrence(clock: str, moment: datetime, after: bool) -> datetime:
    """The first local occurrence of a time of day after `moment`, or the last one at or before it."""
    parsed = dt_util.parse_time(clock) or dt_util.parse_time("00:00:00")
    local = dt_util.as_local(moment)
    found = local.replace(hour=parsed.hour, minute=parsed.minute, second=parsed.second, microsecond=0)
    if after and found <= local:
        found += timedelta(days=1)
    elif not after and found > local:
        found -= timedelta(days=1)
    return found


def _in_window(hour: datetime, start: str, end: str) -> bool:
    """Whether a local hour starts inside a daily window (which may cross midnight)."""
    first, last = dt_util.parse_time(start), dt_util.parse_time(end)
    if first is None or last is None:
        return False
    clock = hour.time()
    return first <= clock < last if first <= last else clock >= first or clock < last


def _price_at(state: State | None, hour: datetime, today: date) -> tuple[float | None, bool]:
    """Price of a local hour from the attributes of a price sensor, and whether it is estimated.

    Today's prices are `price_HHh`; tomorrow's, `price_next_day_HHh` when published.
    Until then, and for any later day, the same hour of today is the estimate.
    """
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None, False
    key = f"price_{hour.hour:02d}h"
    estimated = hour.date() != today
    if hour.date() == today + timedelta(days=1) and f"price_next_day_{hour.hour:02d}h" in state.attributes:
        key, estimated = f"price_next_day_{hour.hour:02d}h", False
    try:
        return float(state.attributes[key]), estimated
    except (KeyError, TypeError, ValueError):
        return None, False


def _deadline_from(state: State | None, day_start: datetime) -> float | None:
    """Deadline from an entity: a date and time as it is, or a time of day after the day's start."""
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    if (moment := dt_util.parse_datetime(state.state)) is not None:
        return dt_util.as_local(moment).timestamp() if moment.tzinfo else moment.replace(
            tzinfo=dt_util.get_default_time_zone()
        ).timestamp()
    if dt_util.parse_time(state.state) is None:
        return None
    return _occurrence(state.state, day_start, after=True).timestamp()


@dataclass(frozen=True)
class LoadConfig:
    """A load's configuration, from its subentry."""

    id: str  # subentry id
    name: str
    entity_id: str
    nominal_w: float
    power_sensor: str | None
    priority: int
    no_skip: bool
    on_delay: float  # s
    off_delay: float  # s
    min_on: float  # s
    min_off: float  # s
    respect_global: bool
    condition_entity: str | None
    condition_state: str
    respect_manual: bool
    hvac_mode: str
    variable_entity: str | None
    regulable: bool = False
    limit_entity: str | None = None
    mode_entity: str | None = None
    auto_state: str = ""
    limit_min: float = 0  # W
    limit_max: float = 0  # W
    limit_threshold: float = 0  # W
    limit_interval: float = 0  # s
    output_entity: str | None = None  # % of its output in use (the diverter's duty)
    hours_sensor: str | None = None  # hours on today; None: SolarSplit's own count
    target_entity: str | None = None  # hours wanted per day, read every cycle
    target_hours: float = 0  # h, fixed target when there is no target entity; 0 = none
    on_demand: bool = False  # eligible only while a request is active (replaces respect_manual)
    auto_manage: bool = False  # Manage switches back on when the load goes off, or at manage_at
    manage_at: str = "00:00:00"  # local time
    seasons: tuple[str, ...] = ()  # options of the season entity; empty = all year
    circuit_sensor: str | None = None  # power of its circuit: its power is learned from the jumps
    urgency_entity: str | None = None  # while on, the load uses urgency_priority
    urgency_priority: int = 1
    min_hours: float = 0  # h guaranteed before the deadline (relay loads); 0 = no minimum
    min_entity: str | None = None  # minimum hours, read every cycle
    day_start: str = "00:00:00"  # local time at which the objective's day starts
    deadline: str | None = None  # local time; None: the end of the objective's day
    deadline_entity: str | None = None  # input_datetime, time or timestamp sensor
    min_priority: int = 0  # priority while the minimum is not met; 0 = the normal one
    window_start: str | None = None  # preferred window for grid fill
    window_end: str | None = None

    @classmethod
    def from_subentry(cls, subentry: ConfigSubentry) -> LoadConfig:
        """Build from a load subentry."""
        s = load_settings(subentry)
        return cls(
            id=subentry.subentry_id,
            name=s.get(CONF_NAME) or subentry.title,
            entity_id=s[CONF_LOAD_ENTITY],
            nominal_w=float(s[CONF_NOMINAL_POWER]),
            power_sensor=s[CONF_POWER_SENSOR],
            priority=int(s[CONF_PRIORITY]),
            no_skip=s[CONF_NO_SKIP],
            on_delay=float(s[CONF_ON_DELAY]),
            off_delay=float(s[CONF_OFF_DELAY]),
            min_on=float(s[CONF_MIN_ON]) * 60,
            min_off=float(s[CONF_MIN_OFF]) * 60,
            respect_global=s[CONF_RESPECT_GLOBAL],
            condition_entity=s[CONF_CONDITION_ENTITY],
            condition_state=s[CONF_CONDITION_STATE],
            respect_manual=s[CONF_RESPECT_MANUAL],
            hvac_mode=s[CONF_HVAC_MODE],
            variable_entity=s[CONF_VARIABLE_ENTITY],
            regulable=s[CONF_LOAD_TYPE] == LOAD_TYPE_REGULABLE,
            limit_entity=s[CONF_LIMIT_ENTITY],
            mode_entity=s[CONF_MODE_ENTITY],
            auto_state=s[CONF_AUTO_STATE],
            limit_min=float(s[CONF_LIMIT_MIN]),
            limit_max=float(s[CONF_LIMIT_MAX]),
            limit_threshold=float(s[CONF_LIMIT_THRESHOLD]),
            limit_interval=float(s[CONF_LIMIT_INTERVAL]),
            output_entity=s[CONF_OUTPUT_ENTITY],
            hours_sensor=s[CONF_HOURS_SENSOR],
            target_entity=s[CONF_TARGET_ENTITY],
            target_hours=float(s[CONF_TARGET_HOURS]),
            # A regulable load is never switched: requests make no sense for it.
            on_demand=bool(s[CONF_ON_DEMAND]) and s[CONF_LOAD_TYPE] != LOAD_TYPE_REGULABLE,
            auto_manage=bool(s[CONF_AUTO_MANAGE]),
            manage_at=s[CONF_MANAGE_AT] or "00:00:00",
            seasons=tuple(s[CONF_SEASONS] or ()),
            circuit_sensor=s[CONF_CIRCUIT_SENSOR],
            urgency_entity=s[CONF_URGENCY_ENTITY],
            urgency_priority=int(s[CONF_URGENCY_PRIORITY]),
            # A regulable load is never switched: SolarSplit cannot guarantee it hours.
            min_hours=0 if s[CONF_LOAD_TYPE] == LOAD_TYPE_REGULABLE else float(s[CONF_MIN_HOURS] or 0),
            min_entity=None if s[CONF_LOAD_TYPE] == LOAD_TYPE_REGULABLE else s[CONF_MIN_ENTITY],
            day_start=s[CONF_DAY_START] or "00:00:00",
            deadline=s[CONF_DEADLINE] or None,
            deadline_entity=s[CONF_DEADLINE_ENTITY],
            min_priority=int(s[CONF_MIN_PRIORITY] or 0),
            window_start=s[CONF_WINDOW_START] or None,
            window_end=s[CONF_WINDOW_END] or None,
        )

    @property
    def is_climate(self) -> bool:
        """Whether the load is a climate entity."""
        return self.entity_id.startswith(f"{CLIMATE}.")

    @property
    def has_target(self) -> bool:
        """Whether the load has a daily target."""
        return self.target_entity is not None or self.target_hours > 0

    @property
    def has_minimum(self) -> bool:
        """Whether the load has a minimum to meet before a deadline."""
        return self.min_entity is not None or self.min_hours > 0


@dataclass
class LoadMemory:
    """What the coordinator remembers about a load between cycles, and across restarts."""

    managed: bool = True
    on_since: float | None = None
    off_since: float | None = None
    commanded: bool | None = None  # last state SolarSplit set; None = never, or adopted
    last_seen: bool | None = None  # last usable on/off state observed
    last_change: float | None = None  # time of the last on/off transition observed
    switched_off: bool = False  # switched off by something else; cleared when it is on again
    limit_written: float | None = None  # regulable: time SolarSplit last wrote the limit
    lowered: bool = False  # regulable: SolarSplit set the limit below the maximum
    today: str | None = None  # local date the counters below refer to
    on_today: float = 0  # s on today in finished runs; the current run is added by on_time()
    target_done: str | None = None  # start of the objective's day in which its maximum was reached
    day_start: float | None = None  # start of the objective's day the counter below refers to
    day_on: float = 0  # s on since day_start in finished runs; the current run is added by day_time()
    request: bool = False  # on demand: someone else switched it on and nobody else switched it off
    request_settings: dict[str, Any] | None = None  # climate: what that person set, restored on switch-on
    rearm_at: float | None = None  # Manage switches back on at this time (auto manage)
    learned: list[float] = field(default_factory=list)  # W, last jumps of its circuit when it switched
    learned_at: float | None = None  # time of the change of the last valid measurement
    saturated_since: float | None = None  # regulable: since when it cannot take more

    def new_day(self, today: str, day_start: float | None = None, midnight: float | None = None) -> None:
        """Reset the day's counters when the local date, or the objective's day, changes.

        The first time (a memory saved before objectives had their own day), an objective
        day that starts at midnight takes today's hours so far.
        """
        if self.today != today:
            self.today, self.on_today = today, 0.0
        if day_start is not None and self.day_start != day_start:
            first = self.day_start is None and day_start == midnight
            self.day_start, self.day_on = day_start, self.on_today if first else 0.0

    def _running(self, now: float, since: float) -> float:
        """Seconds of the current run since `since` at the latest."""
        return max(now - max(self.last_change, since), 0.0) if self.last_seen and self.last_change else 0.0

    def on_time(self, now: float, midnight: float) -> float:
        """Seconds on today, counting the current run since midnight at the latest."""
        return self.on_today + self._running(now, midnight)

    def day_time(self, now: float) -> float:
        """Seconds on in the objective's day, counting the current run since its start at the latest."""
        return self.day_on + self._running(now, self.day_start or 0.0)

    def observe(
        self, is_on: bool | None, state: State | None, detect_off: bool, midnight: float = 0,
        on_demand: bool = False,
    ) -> None:
        """Track on/off transitions with our own clock, which survives restarts.

        Home Assistant resets `last_changed` on restart; ours keeps the real time of
        the last transition as long as the load is still in the state we last saw.
        With `detect_off`, a switch-off SolarSplit did not order (a safety cut, a
        person, an automation) marks the load as switched off until it is on again.
        An on-demand load instead starts a request when something else switches it on
        (or it is first seen on) and ends it when something else switches it off; a
        switch-off ordered by SolarSplit keeps the request.
        """
        if is_on is None or state is None or is_on == self.last_seen:
            return
        if on_demand:
            if is_on and self.commanded is not True:
                self.request = True
            elif not is_on and self.last_seen and self.commanded is not False:
                self.request, self.request_settings = False, None
        if self.last_seen and self.last_change is not None:  # a run ends: add it to today
            end = state.last_changed.timestamp()
            self.on_today += max(end - max(self.last_change, midnight), 0.0)
            self.day_on += max(end - max(self.last_change, self.day_start or 0.0), 0.0)
        if self.last_seen is not None or self.last_change is None:
            self.last_change = state.last_changed.timestamp()
        if is_on:
            self.switched_off = False
        elif self.last_seen and detect_off and not on_demand and self.commanded is not False:
            self.switched_off = True
        self.last_seen = is_on


@dataclass(frozen=True)
class Objective:
    """A load's objective in one cycle. Hours count from the start of the objective's day."""

    day_start: float
    deadline: float | None  # None: its entity is not usable
    done: float | None  # h; None: the hours sensor is not usable
    minimum: float | None  # h; None: no minimum, or its entity is not usable
    maximum: float | None  # h; None: no maximum, or its entity is not usable

    @property
    def key(self) -> str:
        """The objective's day, as stored in the memory."""
        return f"{self.day_start:.0f}"

    @property
    def pending(self) -> float | None:
        """Hours missing to the minimum; None when unknown."""
        if self.minimum is None or self.done is None:
            return None
        return max(self.minimum - self.done, 0.0)

    def open(self, now: float) -> bool:
        """Whether the minimum is still missing and its deadline has not passed."""
        return bool(self.pending) and self.deadline is not None and now < self.deadline


@dataclass(frozen=True)
class LoadView:
    """A load as the entities show it after a cycle."""

    state: str
    reason: Reason
    is_on: bool | None
    power: float
    limit: float | None = None  # regulable: the limit it should have
    on_today: float = 0  # h on today, by SolarSplit's own count
    effective_priority: int = 0
    saturated: bool = False  # regulable: it cannot take more surplus
    objective: Objective | None = None
    plan: Plan | None = None


@dataclass(frozen=True)
class CycleResult:
    """Everything a cycle produced."""

    time: float
    mode: str
    shadow: bool
    export_w: float | None  # smoothed; None when a grid reading is missing or frozen
    import_w: float | None
    assigned_w: float
    decision: Decision
    loads: dict[str, LoadView] = field(default_factory=dict)


class SolarSplitCoordinator(DataUpdateCoordinator[CycleResult]):
    """Runs the control loop every cycle interval."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Read the settings and the loads of the entry."""
        self.settings = get_settings(entry)
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=DOMAIN,
            update_interval=timedelta(seconds=self.settings[CONF_CYCLE_INTERVAL]),
        )
        self.loads = [LoadConfig.from_subentry(sub) for sub in entry.subentries.values()]
        self.memory = {load.id: LoadMemory() for load in self.loads}
        self.snapshot = config_snapshot(entry)  # configuration this coordinator was built from
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.engine_config = EngineConfig(
            safety_margin=self.settings[CONF_SAFETY_MARGIN],
            on_margin=self.settings[CONF_ON_MARGIN],
            import_tolerance=self.settings[CONF_IMPORT_TOLERANCE],
            settle_time=self.settings[CONF_SETTLE_TIME],
            grid_limit=self.grid_cap,
        )
        self.shadow: bool = self.settings[CONF_SHADOW_MODE]
        self.active = True  # master switch
        self.last_action: float | None = None
        self._samples: deque[tuple[float, float, float]] = deque()
        self._today = ""  # local date and start of day of the current cycle
        self._midnight = 0.0
        self.learner = CircuitLearner()
        self._circuits: dict[str, float] = {}  # W, each circuit's reading in this cycle
        self._circuit_on: dict[str, int] = {}  # loads of each circuit that are on in this cycle
        self.profile = HourlyProfile()  # surplus seen at every hour of the last days
        self.base = HourlyProfile()  # house's power at every hour of the last days, without SolarSplit's loads
        self.forecast: dict[float, float] | None = None  # Wh per local hour start, summed
        self._forecast_read: float | None = None  # time of the last read
        self.objectives: dict[str, Objective] = {}
        self.plans: dict[str, Plan] = {}
        self._plan_key: tuple | None = None  # what the plans were made from: replanned when it changes

    @property
    def grid_cap(self) -> float | None:
        """W of import grid fill may reach: the contracted power less the margin; None if unknown."""
        contracted = self.settings[CONF_CONTRACTED_POWER]
        return max(contracted - self.settings[CONF_GRID_MARGIN], 0.0) if contracted else None

    async def async_load_memory(self) -> None:
        """Restore the memory saved by the previous run.

        Delay marks only survive a short gap: nobody watched the condition while
        Home Assistant was stopped.
        """
        if (data := await self._store.async_load()) is None:
            return
        self.active = data.get("active", True)
        self.last_action = data.get("last_action")
        self.profile = HourlyProfile(data.get("surplus", {}))
        self.base = HourlyProfile(data.get("base", {}))
        fresh = dt_util.utcnow().timestamp() - data.get("saved_at", 0) <= MARKS_MAX_GAP
        for load_id, saved in data.get("loads", {}).items():
            if load_id not in self.memory:
                continue  # load deleted since
            memory = LoadMemory(**{k: v for k, v in saved.items() if k in LoadMemory.__dataclass_fields__})
            if not fresh:
                memory.on_since = memory.off_since = memory.saturated_since = None
            self.memory[load_id] = memory

    def _memory_data(self) -> dict[str, Any]:
        return {
            "saved_at": dt_util.utcnow().timestamp(),
            "active": self.active,
            "last_action": self.last_action,
            "surplus": self.profile.days,
            "base": self.base.days,
            "loads": {load_id: asdict(memory) for load_id, memory in self.memory.items()},
        }

    def save_memory(self) -> None:
        """Save the memory soon, batching changes."""
        self._store.async_delay_save(self._memory_data, SAVE_DELAY)

    async def async_save_memory(self) -> None:
        """Save the memory now (before an unload, so a reload finds it)."""
        await self._store.async_save(self._memory_data())

    async def async_remove_memory(self) -> None:
        """Delete the saved memory when the entry is removed."""
        await self._store.async_remove()

    def set_active(self, active: bool) -> None:
        """Master switch."""
        self.active = active
        self.save_memory()

    def set_managed(self, load_id: str, managed: bool) -> None:
        """Include or exclude a load. Re-including it adopts its current state.

        With auto manage, excluding it also sets when it is included again by itself:
        the next time of day configured (the switch-off of the load may come first).
        """
        memory = self.memory[load_id]
        if managed and not memory.managed:
            memory.commanded = None
            memory.switched_off = False
            memory.target_done = None
        memory.managed = managed
        load = self.load(load_id)
        memory.rearm_at = _next_time(load.manage_at) if not managed and load.auto_manage else None
        self.save_memory()

    def load(self, load_id: str) -> LoadConfig:
        """Current configuration of a load."""
        return next(load for load in self.loads if load.id == load_id)

    def set_priority(self, load_id: str, priority: int) -> None:
        """Change a load's priority on the fly and store it in its subentry, without reloading."""
        self.loads = [replace(load, priority=priority) if load.id == load_id else load for load in self.loads]
        entry = self.config_entry
        subentry = entry.subentries[load_id]
        data = {**subentry.data, CONF_PRIORITY: priority}
        # The update listener compares the entry against this snapshot and skips the
        # reload. It may run inside async_update_subentry (eager task): set it first.
        entry_data, options, loads = config_snapshot(entry)
        self.snapshot = (entry_data, options, {**loads, load_id: (subentry.title, data)})
        self.hass.config_entries.async_update_subentry(entry, subentry, data=data)

    def _state(self, entity_id: str | None) -> State | None:
        return self.hass.states.get(entity_id) if entity_id else None

    def _smoothed(self, now: float, export_w: float, import_w: float) -> tuple[float, float]:
        """Moving average of the grid readings over the smoothing window."""
        window = self.settings[CONF_SMOOTHING_WINDOW]
        while self._samples and self._samples[0][0] <= now - window:
            self._samples.popleft()
        self._samples.append((now, export_w, import_w))
        n = len(self._samples)
        return sum(s[1] for s in self._samples) / n, sum(s[2] for s in self._samples) / n

    def _grid(self, now: float) -> tuple[float, float] | None:
        """Raw export and import in W, or None if a reading is missing or frozen.

        One signed sensor is frozen when it has not been reported within the timeout.
        A meter split into two sensors only writes the side that changes, so a side
        resting at 0 W can stay quiet for hours while the other one moves: there,
        frozen data is a non-zero reading that has not been reported within the
        timeout, or both sides quiet at once.
        """
        timeout = self.settings[CONF_STALE_TIMEOUT]
        if (signed := self.settings[CONF_GRID_SENSOR]) is not None:
            state = self._state(signed)
            if (value := read_watts(state)) is None:
                return None
            if timeout and now - state.last_reported.timestamp() > timeout:
                return None
            if self.settings[CONF_GRID_SIGN] == GRID_SIGN_EXPORT_NEGATIVE:
                value = -value
            return max(value, 0.0), max(-value, 0.0)
        readings: list[tuple[float, bool]] = []
        for key in (CONF_EXPORT_SENSOR, CONF_IMPORT_SENSOR):
            state = self._state(self.settings[key])
            if (value := read_watts(state)) is None:
                return None
            fresh = not timeout or now - state.last_reported.timestamp() <= timeout
            readings.append((max(value, 0.0), fresh))
        if not any(fresh for _, fresh in readings):
            return None
        if any(value and not fresh for value, fresh in readings):
            return None
        return readings[0][0], readings[1][0]

    def _objective(self, load: LoadConfig, memory: LoadMemory, now: float) -> Objective:
        """Hours done since the objective's day started, minimum, maximum and deadline."""
        start = _occurrence(load.day_start, dt_util.utc_from_timestamp(now), after=False)
        if load.deadline_entity is not None:
            deadline = _deadline_from(self._state(load.deadline_entity), start)
        else:
            deadline = _occurrence(load.deadline or load.day_start, start, after=True).timestamp()
        if load.hours_sensor is None:
            done = memory.day_time(now) / 3600
        else:
            done = read_hours(self._state(load.hours_sensor))
        minimum = maximum = None
        if load.has_minimum:
            minimum = load.min_hours if load.min_entity is None else read_number(self._state(load.min_entity))
        if load.has_target:
            maximum = load.target_hours if load.target_entity is None else read_number(self._state(load.target_entity))
        return Objective(start.timestamp(), deadline, done, minimum, maximum)

    def _read_circuits(self, now: float) -> None:
        """Read every circuit sensor, feed the learner and store the measurements that are due."""
        self._circuits, self._circuit_on = {}, {}
        for load in self.loads:
            if (circuit := load.circuit_sensor) is None:
                continue
            if circuit not in self._circuits and (watts := read_watts(self._state(circuit))) is not None:
                self._circuits[circuit] = watts
                self.learner.sample(circuit, now, watts)
            if load_is_on(self._state(load.entity_id)):
                self._circuit_on[circuit] = self._circuit_on.get(circuit, 0) + 1
        for measurement in self.learner.due(now):
            if measurement.power is None or measurement.change.load_id not in self.memory:
                continue
            memory = self.memory[measurement.change.load_id]
            memory.learned = [*memory.learned, round(measurement.power, 1)][-KEEP:]
            memory.learned_at = measurement.change.time

    def _season(self) -> str | None:
        """The current season (an option of the season entity), or None if it is not usable."""
        state = self._state(self.settings[CONF_SEASON_ENTITY])
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        return state.state

    def _in_season(self, load: LoadConfig, season: str | None) -> bool | None:
        """Whether the load is in season; None when the season is unknown."""
        if not load.seasons or self.settings[CONF_SEASON_ENTITY] is None:
            return True
        return None if season is None else season in load.seasons

    def _load_state(
        self, load: LoadConfig, global_on: bool | None, now: float, season: str | None = None
    ) -> LoadState:
        """Engine input for one load."""
        memory = self.memory[load.id]
        state = self._state(load.entity_id)
        is_on = load_is_on(state)
        day_start = _occurrence(load.day_start, dt_util.utc_from_timestamp(now), after=False).timestamp()
        memory.new_day(self._today, day_start, self._midnight)
        was_on = memory.last_seen
        # In shadow mode nothing is switched by us, so every switch-off would look external.
        memory.observe(
            is_on, state, detect_off=not self.shadow, midnight=self._midnight, on_demand=load.on_demand
        )
        if load.circuit_sensor and was_on is not None and is_on is not None and is_on != was_on:
            # Any switch, by SolarSplit or not: measure the jump of its circuit.
            self.learner.change(load.circuit_sensor, Change(load.id, memory.last_change, is_on), load.nominal_w)
        if load.on_demand and memory.request and is_on and state is not None:
            # What the person set, restored when SolarSplit switches it back on.
            memory.request_settings = climate_settings(state)
        if load.auto_manage and not memory.managed and (
            (was_on and is_on is False) or (memory.rearm_at is not None and now >= memory.rearm_at)
        ):
            self.set_managed(load.id, True)
        manual = (
            load.respect_manual and not load.on_demand and memory.commanded is not None
            and is_on is not None and is_on != memory.commanded
        )
        # In shadow mode nothing was switched by us: simulate owning every load that is on.
        owned = bool(is_on) if self.shadow else memory.commanded is True
        eligible = load.condition_entity is None or entity_has_state(
            self._state(load.condition_entity), load.condition_state
        )
        objective = self.objectives[load.id] = self._objective(load, memory, now)
        hours, target = (objective.done, objective.maximum) if load.has_target else (None, None)
        target_done = memory.target_done == objective.key
        if load.has_target and None in (hours, target) and not target_done and eligible:
            eligible = None  # unknown progress: left as it is
        actual_w = read_watts(self._state(load.power_sensor))
        nominal = load.nominal_w
        if load.power_sensor is None and load.circuit_sensor is not None:
            # Its own meter always wins; otherwise the power learned from its circuit.
            nominal = learned_power(memory.learned) or nominal
            circuit = self._circuits.get(load.circuit_sensor)
            if load.is_climate and is_on and circuit is not None:
                # A compressor's power varies: never count less than its share of the circuit.
                share = circuit / max(self._circuit_on.get(load.circuit_sensor, 1), 1)
                actual_w = max(nominal, share)
        regulator = None
        switched_off = memory.switched_off and not self.shadow and is_on is False
        if load.regulable:
            # SolarSplit never switches it: its switch only says whether it can draw power
            # (off: a safety cut, for example). Its power must be known, never taken as 0 W.
            kind = LoadKind.REGULABLE
            auto = entity_has_state(self._state(load.mode_entity), load.auto_state)
            current = read_number(self._state(load.limit_entity))
            if current is not None and current >= load.limit_max:
                memory.lowered = False  # back at its maximum, whoever put it there
            available = None not in (is_on, auto, current, actual_w)
            regulator = Regulator(
                auto=bool(auto), current=current or 0.0, minimum=load.limit_min, maximum=load.limit_max,
                threshold=load.limit_threshold, interval=load.limit_interval,
                written=memory.limit_written, lowered=memory.lowered,
                output=read_number(self._state(load.output_entity)),
            )
            manual, switched_off = False, is_on is False
        else:
            variable = load.variable_entity is not None and entity_is_on(self._state(load.variable_entity))
            kind = LoadKind.VARIABLE if variable else LoadKind.RELAY
            # Unknown data is unknown, never 0 W: a variable load whose power or regulation
            # entity cannot be read is left as it is.
            available = is_on is not None and variable is not None and not (
                variable and load.power_sensor is not None and actual_w is None
            )
        return LoadState(
            id=load.id,
            priority=load.priority,
            nominal_w=nominal,
            is_on=bool(is_on),
            actual_w=actual_w,
            kind=kind,
            managed=memory.managed,
            available=available,
            manual=manual,
            switched_off=switched_off,
            owned=owned,
            global_ok=True if not load.respect_global else global_on,
            eligible=eligible,
            no_skip=load.no_skip,
            min_on=load.min_on,
            min_off=load.min_off,
            on_delay=load.on_delay,
            off_delay=load.off_delay,
            last_change=memory.last_change,
            on_since=memory.on_since,
            off_since=memory.off_since,
            regulator=regulator,
            hours_today=hours,
            target_hours=target,
            target_done=target_done,
            in_season=self._in_season(load, season),
            on_demand=load.on_demand,
            request=memory.request,
            min_pending=objective.open(now),
            min_priority=load.min_priority or None,
            # Unknown urgency is not urgency: the normal order is the safe one.
            urgent=load.urgency_entity is not None and entity_is_on(self._state(load.urgency_entity)) is True,
            urgency_priority=load.urgency_priority if load.urgency_entity else None,
            saturated_since=memory.saturated_since,
        )

    async def _async_update_forecast(self, now: float) -> None:
        """Read the solar forecast of the chosen entries every few minutes; None without one."""
        entries = self.settings[CONF_FORECAST_ENTRIES]
        if not entries:
            self.forecast = None
            return
        if self._forecast_read is not None and now - self._forecast_read < FORECAST_REFRESH:
            return
        self._forecast_read = now
        self.forecast = await async_read_forecast(self.hass, entries)

    def _plan(self, now: float, states: list[LoadState]) -> None:
        """Plan the grid hours of the loads whose minimum is missing, when what it depends on changes.

        It is replanned every hour, when a price sensor or the solar forecast updates, and when the set of
        loads to plan, their priority, power, minimum or deadline change; not when
        the hours done move, so a plan holds within the hour.
        """
        requests: list[Request] = []
        for load, state in zip(self.loads, states, strict=True):
            objective = self.objectives[load.id]
            if not (state.min_pending and state.managed and state.kind is LoadKind.RELAY):
                continue
            requests.append(Request(
                load.id, state.plan_priority, state.nominal_w, objective.pending or 0.0,
                objective.deadline or now,
            ))
        hour = dt_util.as_local(dt_util.utc_from_timestamp(now)).replace(minute=0, second=0, microsecond=0)
        buy = self._state(self.settings[CONF_BUY_PRICE_SENSOR])
        sell = self._state(self.settings[CONF_SELL_PRICE_SENSOR])
        key = (
            hour.timestamp(),
            None if buy is None else buy.last_updated, None if sell is None else sell.last_updated,
            None if self.forecast is None else tuple(sorted(self.forecast.items())),
            tuple(
                (r.load_id, r.priority, round(r.power_w), r.deadline, self.objectives[r.load_id].minimum)
                for r in requests
            ),
        )
        if key == self._plan_key:
            return
        self._plan_key = key
        if not requests:
            self.plans = {}
            return
        today = hour.date()
        covered = {dt_util.as_local(dt_util.utc_from_timestamp(t)).date() for t in self.forecast or {}}
        slots: list[Slot] = []
        start = hour
        while start.timestamp() < max(r.deadline for r in requests):
            buy_price, estimated = _price_at(buy, start, today)
            sell_price, _ = _price_at(sell, start, today)
            surplus, source = expected_surplus(
                start, self.forecast, covered, self.base, self.settings[CONF_BASE_LOAD], self.profile
            )
            slots.append(Slot(
                start.timestamp(), start.timestamp() + 3600, buy_price, sell_price, surplus, estimated, source,
            ))
            start = dt_util.as_local(start + timedelta(hours=1))
        for i, request in enumerate(requests):
            load = self.load(request.load_id)
            if load.window_start and load.window_end:
                preferred = frozenset(
                    slot.start for slot in slots
                    if _in_window(dt_util.as_local(dt_util.utc_from_timestamp(slot.start)),
                                  load.window_start, load.window_end)
                )
                requests[i] = replace(request, preferred=preferred)
        self.plans = plan(now, slots, requests, self.grid_cap)

    def _fill(self, state: LoadState, now: float) -> LoadState:
        """Mark a load whose plan counts on this hour, and a relay load it fills from the grid."""
        current = self.plans.get(state.id)
        if current is None or not current.planned_at(now) or not state.min_pending:
            return state
        state = replace(state, planned=True)
        if not current.fill_at(now) or state.eligible is not True or state.kind is not LoadKind.RELAY:
            return state
        return replace(state, grid_fill=Reason.GRID_FILL if current.priced else Reason.GRID_FILL_NO_PRICE)

    async def _async_update_data(self) -> CycleResult:
        """Run one control cycle."""
        now = dt_util.utcnow().timestamp()
        self._today = dt_util.now().date().isoformat()
        self._midnight = dt_util.start_of_local_day().timestamp()
        meas = None
        if (grid := self._grid(now)) is not None:
            meas = Measurements(*self._smoothed(now, *grid))

        global_entity = self.settings[CONF_GLOBAL_CONDITION]
        global_on = True if global_entity is None else entity_is_on(self._state(global_entity))
        zero_entity = self.settings[CONF_ZERO_INJECTION_ENTITY]
        zero_injection = False if zero_entity is None else entity_is_on(self._state(zero_entity))
        pause_reason = Reason.PAUSED
        if not self.active:
            mode = MODE_PAUSED
        elif zero_injection is None:
            mode, pause_reason = MODE_PAUSED, Reason.MODE_UNKNOWN
        elif zero_injection:
            mode = MODE_ZERO_INJECTION  # probing is not implemented yet: the engine stays paused
        else:
            mode = MODE_NORMAL

        self._read_circuits(now)
        season = self._season()
        states = [self._load_state(load, global_on, now, season) for load in self.loads]
        await self._async_update_forecast(now)
        self._plan(now, states)
        states = [self._fill(state, now) for state in states]
        decision = decide(
            now, meas, states, self.engine_config,
            self.last_action, active=mode == MODE_NORMAL, pause_reason=pause_reason,
        )
        for load in self.loads:
            memory = self.memory[load.id]
            memory.on_since = decision.on_since.get(load.id)
            memory.off_since = decision.off_since.get(load.id)
            memory.saturated_since = decision.saturated_since.get(load.id)
            if load.id in decision.target_reached:
                memory.target_done = self.objectives[load.id].key
        if meas is not None:
            # What managed loads could use, even the power of grid fill: the hour's surplus.
            used = sum(state.consumption for state in states if state.controlled or state.filling)
            local = dt_util.as_local(dt_util.utc_from_timestamp(now))
            self.profile.add(self._today, local.hour, meas.export_w - meas.import_w + used)
            if (pv := read_watts(self._state(self.settings[CONF_PV_SENSOR]))) is not None:
                # The house's base load: what it uses without any of SolarSplit's loads.
                loads = sum(state.consumption for state in states if state.available)
                self.base.add(self._today, local.hour, pv + meas.import_w - meas.export_w - loads)

        for load_id, value in decision.writes.items():
            await self._async_write_limit(self.load(load_id), value, now)
        if decision.action is not Action.NONE and not self.shadow:
            await self._async_execute(decision, now)
        self.save_memory()

        return CycleResult(
            time=now, mode=mode, shadow=self.shadow,
            export_w=meas.export_w if meas else None, import_w=meas.import_w if meas else None,
            assigned_w=sum(s.consumption for s in states if s.controlled),
            decision=decision,
            loads={
                s.id: self._view(
                    s, decision, self.memory[s.id].on_time(now, self._midnight) / 3600,
                    self.objectives.get(s.id), self.plans.get(s.id),
                )
                for s in states
            },
        )

    async def _async_execute(self, decision: Decision, now: float) -> None:
        """Switch the load the engine chose."""
        load = self.load(decision.load_id)
        on = decision.action is Action.TURN_ON
        # An on-demand climate goes back to what the person who asked for it set.
        restore = self.memory[load.id].request_settings if on and load.on_demand else None
        try:
            await async_switch_load(self.hass, load.entity_id, on, load.hvac_mode, restore)
        except HomeAssistantError as err:
            _LOGGER.warning("Could not switch %s %s: %s", load.entity_id, "on" if on else "off", err)
            return
        self.memory[load.id].commanded = on
        self.last_action = now
        _LOGGER.debug("Switched %s %s (budget %.0f W)", load.entity_id, "on" if on else "off", decision.budget)

    async def _async_write_limit(self, load: LoadConfig, value: float, now: float) -> None:
        """Write a regulable load's power limit and remember it. Never in shadow mode.

        Every write goes through here, so this is the one place that guards it.
        """
        if self.shadow:
            return
        try:
            written = await async_set_limit(self.hass, load.limit_entity, value)
        except HomeAssistantError as err:
            _LOGGER.warning("Could not set the limit of %s to %.0f W: %s", load.limit_entity, value, err)
            return
        memory = self.memory[load.id]
        memory.limit_written = now
        memory.lowered = written < load.limit_max
        _LOGGER.debug("Set the limit of %s to %.0f W", load.limit_entity, written)

    async def async_release_limits(self) -> None:
        """Give every regulable load SolarSplit lowered its maximum back (on unload)."""
        now = dt_util.utcnow().timestamp()
        for load in self.loads:
            if load.regulable and self.memory[load.id].lowered:
                await self._async_write_limit(load, load.limit_max, now)

    async def async_release_on_start(self) -> None:
        """Before the first cycle, give back the limit of regulable loads left outside automatic mode.

        A restart must not leave a heater boost in Manual mode held at a limit SolarSplit
        set before. Only a limit the saved memory says SolarSplit lowered is written, and
        only when the mode and the limit can be read; the cycle handles the rest.
        """
        now = dt_util.utcnow().timestamp()
        for load in self.loads:
            memory = self.memory[load.id]
            if not load.regulable or not memory.lowered:
                continue
            auto = entity_has_state(self._state(load.mode_entity), load.auto_state)
            current = read_number(self._state(load.limit_entity))
            if auto is False and current is not None and current < load.limit_max:
                await self._async_write_limit(load, load.limit_max, now)

    @staticmethod
    def _view(
        state: LoadState, decision: Decision, on_today: float,
        objective: Objective | None = None, current: Plan | None = None,
    ) -> LoadView:
        """Summarise a load for its state and reason entities."""
        reason = decision.reasons.get(state.id, decision.reason)
        if (fixed := _STATE_FOR.get(reason)) is not None:
            shown = fixed
        elif state.id in decision.desired and decision.desired[state.id] != state.is_on:
            shown = LOAD_WAITING
            if decision.reason is Reason.SETTLING:
                reason = Reason.SETTLING
        elif not state.is_on and reason in _BLOCKING:
            shown = LOAD_BLOCKED
        else:
            shown = LOAD_ON if state.is_on else LOAD_OFF
        return LoadView(
            shown, reason, state.is_on if state.available else None, state.consumption,
            decision.limits.get(state.id), on_today, state.effective_priority,
            state.id in decision.saturated, objective, current,
        )

    def diagnostics(self) -> dict[str, Any]:
        """Data for the downloadable diagnostics."""
        return {
            "settings": self.settings,
            "loads": [load.__dict__ for load in self.loads],
            "memory": {k: asdict(v) for k, v in self.memory.items()},
            "active": self.active,
            "last_action": self.last_action,
            "objectives": {k: asdict(v) for k, v in self.objectives.items()},
            "plans": {k: asdict(v) for k, v in self.plans.items()},
            "surplus_profile": self.profile.days,
            "base_profile": self.base.days,
            "forecast": self.forecast,
        }
