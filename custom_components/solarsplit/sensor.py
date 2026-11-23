"""Sensors: budget, assigned power, mode and last decision; state, reason, time on today and learned power of every load."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SolarSplitConfigEntry
from .coordinator import LOAD_STATES, MODES, CycleResult, LoadView
from .engine import Action, Reason
from .entity import SolarSplitEntity
from .learning import MIN_MEASUREMENTS

ACTION_OPTIONS = [action.value for action in Action]
REASON_OPTIONS = [reason.value for reason in Reason]


async def async_setup_entry(
    hass: HomeAssistant, entry: SolarSplitConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add the loop sensors and the sensors of every load."""
    coordinator = entry.runtime_data
    async_add_entities([
        PowerSensor(coordinator, "budget"),
        PowerSensor(coordinator, "assigned_power"),
        ModeSensor(coordinator, "mode"),
        DecisionSensor(coordinator, "last_decision"),
    ])
    for load in coordinator.loads:
        async_add_entities(
            [
                LoadStateSensor(coordinator, "load_state", load),
                LoadReasonSensor(coordinator, "load_reason", load),
                OnTodaySensor(coordinator, "on_today", load),
            ],
            config_subentry_id=load.id,
        )
        if load.circuit_sensor:
            async_add_entities([LearnedPowerSensor(coordinator, "learned_power", load)], config_subentry_id=load.id)


class PowerSensor(SolarSplitEntity, SensorEntity):
    """Available budget or power assigned to managed loads."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float | None:
        """Value of the last cycle; the budget is unknown without grid readings."""
        data: CycleResult = self.coordinator.data
        return data.decision.budget if self._attr_translation_key == "budget" else data.assigned_w


class ModeSensor(SolarSplitEntity, SensorEntity):
    """Normal, zero injection or paused."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = MODES

    @property
    def native_value(self) -> str:
        """Mode of the last cycle."""
        return self.coordinator.data.mode


class DecisionSensor(SolarSplitEntity, SensorEntity):
    """What the last cycle did, or would have done in shadow mode."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ACTION_OPTIONS

    @property
    def native_value(self) -> str:
        """Action of the last cycle."""
        return self.coordinator.data.decision.action.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Load, reason, budget and whether it was only simulated."""
        data: CycleResult = self.coordinator.data
        names = {load.id: load.name for load in self.coordinator.loads}
        return {
            "load": names.get(data.decision.load_id),
            "reason": data.decision.reason.value,
            "budget": None if data.decision.budget is None else round(data.decision.budget),
            "shadow": data.shadow,
        }


class LoadSensor(SolarSplitEntity, SensorEntity):
    """Base of the per-load sensors."""

    _attr_device_class = SensorDeviceClass.ENUM

    @property
    def view(self) -> LoadView | None:
        """This load in the last cycle."""
        return self.coordinator.data.loads.get(self.load.id)

    @property
    def available(self) -> bool:
        """Available while the cycle covers this load."""
        return super().available and self.view is not None


class LoadStateSensor(LoadSensor):
    """On, off, waiting, blocked, manual, unmanaged or unavailable."""

    _attr_options = LOAD_STATES

    @property
    def native_value(self) -> str:
        """State of the load."""
        return self.view.state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Power counted for the load and, for a regulable load, the limit it should have."""
        attributes: dict[str, Any] = {"power": round(self.view.power)}
        if self.load.regulable:
            attributes["limit"] = None if self.view.limit is None else round(self.view.limit)
        return attributes


class LoadReasonSensor(LoadSensor):
    """Why the load is in its state."""

    _attr_options = REASON_OPTIONS

    @property
    def native_value(self) -> str:
        """Reason code; the UI translates it."""
        return self.view.reason.value


class OnTodaySensor(LoadSensor):
    """Hours the load has been on today, by SolarSplit's own count of its state."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float:
        """Hours on today."""
        return round(self.view.on_today, 4)


class LearnedPowerSensor(SolarSplitEntity, SensorEntity):
    """Power learned from the jumps of the load's circuit when it switches."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float | None:
        """Mean of the valid measurements; unknown before the first one."""
        learned = self.coordinator.memory[self.load.id].learned
        return round(sum(learned) / len(learned), 1) if learned else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Number of measurements, time of the last one and whether it replaces the nominal power."""
        memory = self.coordinator.memory[self.load.id]
        last = memory.learned_at
        return {
            "measurements": len(memory.learned),
            "last_measurement": None if last is None else dt_util.utc_from_timestamp(last).isoformat(),
            "in_use": len(memory.learned) >= MIN_MEASUREMENTS and self.load.power_sensor is None,
        }
