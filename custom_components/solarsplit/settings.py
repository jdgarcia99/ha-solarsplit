"""Default values and effective settings of the entry and of each load."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry

from .const import (
    CONF_CONDITION_ENTITY,
    CONF_CONDITION_STATE,
    CONF_CYCLE_INTERVAL,
    CONF_GLOBAL_CONDITION,
    CONF_HVAC_MODE,
    CONF_IMPORT_TOLERANCE,
    CONF_MIN_OFF,
    CONF_MIN_ON,
    CONF_NO_SKIP,
    CONF_OFF_DELAY,
    CONF_ON_DELAY,
    CONF_ON_MARGIN,
    CONF_POWER_SENSOR,
    CONF_PRIORITY,
    CONF_PV_SENSOR,
    CONF_RESPECT_GLOBAL,
    CONF_RESPECT_MANUAL,
    CONF_SAFETY_MARGIN,
    CONF_SETTLE_TIME,
    CONF_SHADOW_MODE,
    CONF_SMOOTHING_WINDOW,
    CONF_VARIABLE_ENTITY,
    CONF_ZERO_INJECTION_ENTITY,
    DEFAULT_CONDITION_STATE,
    DEFAULT_CYCLE_INTERVAL,
    DEFAULT_HVAC_MODE,
    DEFAULT_IMPORT_TOLERANCE,
    DEFAULT_MIN_OFF,
    DEFAULT_MIN_ON,
    DEFAULT_OFF_DELAY,
    DEFAULT_ON_DELAY,
    DEFAULT_ON_MARGIN,
    DEFAULT_PRIORITY,
    DEFAULT_SAFETY_MARGIN,
    DEFAULT_SETTLE_TIME,
    DEFAULT_SHADOW_MODE,
    DEFAULT_SMOOTHING_WINDOW,
)

DEFAULTS: dict[str, Any] = {
    CONF_PV_SENSOR: None,
    CONF_ZERO_INJECTION_ENTITY: None,
    CONF_GLOBAL_CONDITION: None,
    CONF_SHADOW_MODE: DEFAULT_SHADOW_MODE,
    CONF_CYCLE_INTERVAL: DEFAULT_CYCLE_INTERVAL,
    CONF_SAFETY_MARGIN: DEFAULT_SAFETY_MARGIN,
    CONF_SMOOTHING_WINDOW: DEFAULT_SMOOTHING_WINDOW,
    CONF_SETTLE_TIME: DEFAULT_SETTLE_TIME,
    CONF_ON_MARGIN: DEFAULT_ON_MARGIN,
    CONF_IMPORT_TOLERANCE: DEFAULT_IMPORT_TOLERANCE,
}


LOAD_DEFAULTS: dict[str, Any] = {
    CONF_POWER_SENSOR: None,
    CONF_PRIORITY: DEFAULT_PRIORITY,
    CONF_NO_SKIP: False,
    CONF_ON_DELAY: DEFAULT_ON_DELAY,
    CONF_OFF_DELAY: DEFAULT_OFF_DELAY,
    CONF_MIN_ON: DEFAULT_MIN_ON,
    CONF_MIN_OFF: DEFAULT_MIN_OFF,
    CONF_RESPECT_GLOBAL: True,
    CONF_CONDITION_ENTITY: None,
    CONF_CONDITION_STATE: DEFAULT_CONDITION_STATE,
    CONF_RESPECT_MANUAL: True,
    CONF_HVAC_MODE: DEFAULT_HVAC_MODE,
    CONF_VARIABLE_ENTITY: None,
}


def get_settings(entry: ConfigEntry) -> dict[str, Any]:
    """Effective settings: defaults, then entry data, then options."""
    return {**DEFAULTS, **entry.data, **entry.options}


def load_settings(subentry: ConfigSubentry) -> dict[str, Any]:
    """Effective settings of a load: defaults, then subentry data."""
    return {**LOAD_DEFAULTS, **subentry.data}


def config_snapshot(entry: ConfigEntry) -> tuple[dict[str, Any], dict[str, Any], dict[str, tuple[str, dict[str, Any]]]]:
    """Everything a reload would pick up: data, options and loads."""
    return (
        dict(entry.data),
        dict(entry.options),
        {sid: (sub.title, dict(sub.data)) for sid, sub in entry.subentries.items()},
    )
