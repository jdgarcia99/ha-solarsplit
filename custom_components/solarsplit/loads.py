"""Adapters between Home Assistant entities and the engine's view of a load."""

from __future__ import annotations

from math import floor
from typing import Any

from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_UNIT_OF_MEASUREMENT,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant, State, split_entity_id

CLIMATE = "climate"
HVAC_OFF = "off"
UNUSABLE = (STATE_UNAVAILABLE, STATE_UNKNOWN)
HOURS_PER = {"h": 1.0, "min": 1 / 60, "s": 1 / 3600}
# Climate attributes a person sets, kept for an on-demand load and restored on switch-on.
TEMPERATURE_KEYS = ("temperature", "target_temp_low", "target_temp_high")
FAN_MODE = "fan_mode"
WATTS_PER = {UnitOfPower.WATT: 1.0, UnitOfPower.KILO_WATT: 1000.0, UnitOfPower.MEGA_WATT: 1_000_000.0}


def load_is_on(state: State | None) -> bool | None:
    """Whether a load entity is on; None if its state is not usable."""
    if state is None or state.state in UNUSABLE:
        return None
    if state.domain == CLIMATE:
        return state.state != HVAC_OFF
    return state.state == STATE_ON


def entity_is_on(state: State | None) -> bool | None:
    """Whether a boolean-like entity (condition, mode) is on; None if its state is not usable."""
    if state is None or state.state in UNUSABLE:
        return None
    return state.state == STATE_ON


def entity_has_state(state: State | None, expected: str) -> bool | None:
    """Whether an entity has the expected state; None if its state is not usable."""
    if state is None or state.state in UNUSABLE:
        return None
    return state.state == expected


def read_watts(state: State | None) -> float | None:
    """A power sensor's value in W, or None if it is not usable."""
    if state is None or state.state in UNUSABLE:
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT, UnitOfPower.WATT)
    return value * WATTS_PER.get(unit, 1.0)


def read_hours(state: State | None) -> float | None:
    """A duration sensor's value in hours (also min and s), or None if it is not usable."""
    if (value := read_number(state)) is None:
        return None
    return value * HOURS_PER.get(state.attributes.get(ATTR_UNIT_OF_MEASUREMENT, "h"), 1.0)


def read_number(state: State | None) -> float | None:
    """A number entity's value, or None if it is not usable."""
    if state is None or state.state in UNUSABLE:
        return None
    try:
        return float(state.state)
    except ValueError:
        return None


async def async_set_limit(hass: HomeAssistant, entity_id: str, value: float) -> float:
    """Write a power limit to a `number` or `input_number`, on its step and within its range.

    Rounds down, so the device never gets more than it was given. Returns the value written.
    """
    attributes = state.attributes if (state := hass.states.get(entity_id)) else {}
    step = float(attributes.get("step") or 1)
    value = floor(value / step + 1e-9) * step
    if (low := attributes.get("min")) is not None:
        value = max(value, float(low))
    if (high := attributes.get("max")) is not None:
        value = min(value, float(high))
    await hass.services.async_call(
        split_entity_id(entity_id)[0], "set_value", {ATTR_ENTITY_ID: entity_id, "value": value}, blocking=True
    )
    return value


def climate_settings(state: State) -> dict[str, Any] | None:
    """HVAC mode, target temperature and fan mode of a climate that is on; None otherwise."""
    if state.domain != CLIMATE or state.state in (*UNUSABLE, HVAC_OFF):
        return None
    settings: dict[str, Any] = {"hvac_mode": state.state}
    for key in (*TEMPERATURE_KEYS, FAN_MODE):
        if (value := state.attributes.get(key)) is not None:
            settings[key] = value
    return settings


async def async_switch_load(
    hass: HomeAssistant, entity_id: str, on: bool, hvac_mode: str, restore: dict[str, Any] | None = None
) -> None:
    """Switch a load on or off with the service its domain needs.

    A climate switched on with `restore` gets back the HVAC mode, target temperature
    and fan mode saved in it, instead of the load's configured mode.
    """
    domain = split_entity_id(entity_id)[0]
    if domain == CLIMATE:
        mode = (restore or {}).get("hvac_mode", hvac_mode) if on else HVAC_OFF
        await hass.services.async_call(
            CLIMATE, "set_hvac_mode", {ATTR_ENTITY_ID: entity_id, "hvac_mode": mode}, blocking=True
        )
        if on and restore:
            if temperatures := {key: restore[key] for key in TEMPERATURE_KEYS if key in restore}:
                await hass.services.async_call(
                    CLIMATE, "set_temperature", {ATTR_ENTITY_ID: entity_id, **temperatures}, blocking=True
                )
            if FAN_MODE in restore:
                await hass.services.async_call(
                    CLIMATE, "set_fan_mode", {ATTR_ENTITY_ID: entity_id, FAN_MODE: restore[FAN_MODE]},
                    blocking=True,
                )
        return
    await hass.services.async_call(
        domain, SERVICE_TURN_ON if on else SERVICE_TURN_OFF, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
