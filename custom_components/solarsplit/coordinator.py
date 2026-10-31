"""Control loop: reads Home Assistant, runs the engine, stores its marks and, outside shadow mode, acts.

The coordinator never decides. It turns entity states into engine inputs, keeps
the data that must survive between cycles, and executes the engine's action.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CONDITION_ENTITY,
    CONF_CONDITION_STATE,
    CONF_CYCLE_INTERVAL,
    CONF_EXPORT_SENSOR,
    CONF_GLOBAL_CONDITION,
    CONF_HVAC_MODE,
    CONF_IMPORT_SENSOR,
    CONF_IMPORT_TOLERANCE,
    CONF_LOAD_ENTITY,
    CONF_MIN_OFF,
    CONF_MIN_ON,
    CONF_NO_SKIP,
    CONF_NOMINAL_POWER,
    CONF_OFF_DELAY,
    CONF_ON_DELAY,
    CONF_ON_MARGIN,
    CONF_POWER_SENSOR,
    CONF_PRIORITY,
    CONF_RESPECT_GLOBAL,
    CONF_RESPECT_MANUAL,
    CONF_SAFETY_MARGIN,
    CONF_SETTLE_TIME,
    CONF_SHADOW_MODE,
    CONF_SMOOTHING_WINDOW,
    CONF_VARIABLE_ENTITY,
    CONF_ZERO_INJECTION_ENTITY,
    DOMAIN,
)
from .engine import (
    Action,
    Decision,
    EngineConfig,
    LoadKind,
    LoadState,
    Measurements,
    Reason,
    decide,
)
from .loads import async_switch_load, entity_is_on, load_is_on, read_watts
from .settings import get_settings, load_settings

_LOGGER = logging.getLogger(__name__)

MODE_NORMAL = "normal"
MODE_ZERO_INJECTION = "zero_injection"
MODE_PAUSED = "paused"
MODES = [MODE_NORMAL, MODE_ZERO_INJECTION, MODE_PAUSED]

LOAD_ON = "on"
LOAD_OFF = "off"
LOAD_WAITING = "waiting"  # a change is wanted but a timing rule holds it
LOAD_BLOCKED = "blocked"  # off, and not allowed to enter
LOAD_MANUAL = "manual"
LOAD_UNMANAGED = "unmanaged"
LOAD_UNAVAILABLE = "unavailable"
LOAD_STATES = [LOAD_ON, LOAD_OFF, LOAD_WAITING, LOAD_BLOCKED, LOAD_MANUAL, LOAD_UNMANAGED, LOAD_UNAVAILABLE]

_BLOCKING = (Reason.NOT_ELIGIBLE, Reason.WAITING_HIGHER, Reason.NO_BUDGET, Reason.GLOBAL_CONDITION)
_STATE_FOR = {
    Reason.MANUAL: LOAD_MANUAL,
    Reason.UNMANAGED: LOAD_UNMANAGED,
    Reason.UNAVAILABLE: LOAD_UNAVAILABLE,
}


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
        )


@dataclass
class LoadMemory:
    """What the coordinator remembers about a load between cycles."""

    managed: bool = True
    on_since: float | None = None
    off_since: float | None = None
    commanded: bool | None = None  # last state SolarSplit set; None = never, or adopted


@dataclass(frozen=True)
class LoadView:
    """A load as the entities show it after a cycle."""

    state: str
    reason: Reason
    is_on: bool | None
    power: float


@dataclass(frozen=True)
class CycleResult:
    """Everything a cycle produced."""

    time: float
    mode: str
    shadow: bool
    export_w: float
    import_w: float
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
        self.engine_config = EngineConfig(
            safety_margin=self.settings[CONF_SAFETY_MARGIN],
            on_margin=self.settings[CONF_ON_MARGIN],
            import_tolerance=self.settings[CONF_IMPORT_TOLERANCE],
            settle_time=self.settings[CONF_SETTLE_TIME],
        )
        self.shadow: bool = self.settings[CONF_SHADOW_MODE]
        self.active = True  # master switch
        self.last_action: float | None = None
        self._samples: deque[tuple[float, float, float]] = deque()

    def set_managed(self, load_id: str, managed: bool) -> None:
        """Include or exclude a load. Re-including it adopts its current state."""
        memory = self.memory[load_id]
        if managed and not memory.managed:
            memory.commanded = None
        memory.managed = managed

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

    def _load_state(self, load: LoadConfig, global_on: bool) -> LoadState:
        """Engine input for one load."""
        memory = self.memory[load.id]
        state = self._state(load.entity_id)
        is_on = load_is_on(state)
        manual = (
            load.respect_manual and memory.commanded is not None
            and is_on is not None and is_on != memory.commanded
        )
        eligible = load.condition_entity is None or (
            (cond := self._state(load.condition_entity)) is not None and cond.state == load.condition_state
        )
        variable = load.variable_entity is not None and entity_is_on(self._state(load.variable_entity))
        return LoadState(
            id=load.id,
            priority=load.priority,
            nominal_w=load.nominal_w,
            is_on=bool(is_on),
            actual_w=read_watts(self._state(load.power_sensor)),
            kind=LoadKind.VARIABLE if variable else LoadKind.RELAY,
            managed=memory.managed,
            available=is_on is not None,
            manual=manual,
            global_ok=global_on or not load.respect_global,
            eligible=eligible,
            no_skip=load.no_skip,
            min_on=load.min_on,
            min_off=load.min_off,
            on_delay=load.on_delay,
            off_delay=load.off_delay,
            last_change=state.last_changed.timestamp() if state is not None else None,
            on_since=memory.on_since,
            off_since=memory.off_since,
        )

    async def _async_update_data(self) -> CycleResult:
        """Run one control cycle."""
        now = dt_util.utcnow().timestamp()
        export_w = read_watts(self._state(self.settings[CONF_EXPORT_SENSOR]))
        import_w = read_watts(self._state(self.settings[CONF_IMPORT_SENSOR]))
        if export_w is None or import_w is None:
            raise UpdateFailed("Grid export or import sensor is unavailable")
        export_w, import_w = self._smoothed(now, max(export_w, 0.0), max(import_w, 0.0))

        global_entity = self.settings[CONF_GLOBAL_CONDITION]
        global_on = global_entity is None or entity_is_on(self._state(global_entity))
        if not self.active:
            mode = MODE_PAUSED
        elif entity_is_on(self._state(self.settings[CONF_ZERO_INJECTION_ENTITY])):
            mode = MODE_ZERO_INJECTION  # probing is not implemented yet: the engine stays paused
        else:
            mode = MODE_NORMAL

        states = [self._load_state(load, global_on) for load in self.loads]
        decision = decide(
            now, Measurements(export_w, import_w), states, self.engine_config,
            self.last_action, active=mode == MODE_NORMAL,
        )
        for load in self.loads:
            memory = self.memory[load.id]
            memory.on_since = decision.on_since.get(load.id)
            memory.off_since = decision.off_since.get(load.id)

        if decision.action is not Action.NONE and not self.shadow:
            await self._async_execute(decision, now)

        return CycleResult(
            time=now, mode=mode, shadow=self.shadow, export_w=export_w, import_w=import_w,
            assigned_w=sum(s.consumption for s in states if s.controlled),
            decision=decision,
            loads={s.id: self._view(s, decision) for s in states},
        )

    async def _async_execute(self, decision: Decision, now: float) -> None:
        """Switch the load the engine chose."""
        load = next(load for load in self.loads if load.id == decision.load_id)
        on = decision.action is Action.TURN_ON
        try:
            await async_switch_load(self.hass, load.entity_id, on, load.hvac_mode)
        except HomeAssistantError as err:
            _LOGGER.warning("Could not switch %s %s: %s", load.entity_id, "on" if on else "off", err)
            return
        self.memory[load.id].commanded = on
        self.last_action = now
        _LOGGER.debug("Switched %s %s (budget %.0f W)", load.entity_id, "on" if on else "off", decision.budget)

    @staticmethod
    def _view(state: LoadState, decision: Decision) -> LoadView:
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
        return LoadView(shown, reason, state.is_on if state.available else None, state.consumption)

    def diagnostics(self) -> dict[str, Any]:
        """Data for the downloadable diagnostics."""
        return {
            "settings": self.settings,
            "loads": [load.__dict__ for load in self.loads],
            "memory": {k: v.__dict__ for k, v in self.memory.items()},
            "active": self.active,
            "last_action": self.last_action,
        }
