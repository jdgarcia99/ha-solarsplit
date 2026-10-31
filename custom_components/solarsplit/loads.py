"""Adapters between Home Assistant entities and the engine's view of a load."""

from __future__ import annotations

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
WATTS_PER = {UnitOfPower.WATT: 1.0, UnitOfPower.KILO_WATT: 1000.0, UnitOfPower.MEGA_WATT: 1_000_000.0}


def load_is_on(state: State | None) -> bool | None:
    """Whether a load entity is on; None if its state is not usable."""
    if state is None or state.state in UNUSABLE:
        return None
    if state.domain == CLIMATE:
        return state.state != HVAC_OFF
    return state.state == STATE_ON


def entity_is_on(state: State | None) -> bool:
    """Whether a boolean-like entity (condition, mode) is on. Unusable counts as off."""
    return state is not None and state.state == STATE_ON


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


async def async_switch_load(hass: HomeAssistant, entity_id: str, on: bool, hvac_mode: str) -> None:
    """Switch a load on or off with the service its domain needs."""
    domain = split_entity_id(entity_id)[0]
    if domain == CLIMATE:
        await hass.services.async_call(
            CLIMATE, "set_hvac_mode",
            {ATTR_ENTITY_ID: entity_id, "hvac_mode": hvac_mode if on else HVAC_OFF},
            blocking=True,
        )
        return
    await hass.services.async_call(
        domain, SERVICE_TURN_ON if on else SERVICE_TURN_OFF, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
