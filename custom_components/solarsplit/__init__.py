"""SolarSplit: share PV surplus among prioritised loads in Home Assistant."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CYCLE_INTERVAL,
    CONF_GLOBAL_CONDITION,
    CONF_IMPORT_TOLERANCE,
    CONF_ON_MARGIN,
    CONF_PV_SENSOR,
    CONF_SAFETY_MARGIN,
    CONF_SETTLE_TIME,
    CONF_SMOOTHING_WINDOW,
    CONF_ZERO_INJECTION_ENTITY,
    DEFAULT_CYCLE_INTERVAL,
    DEFAULT_IMPORT_TOLERANCE,
    DEFAULT_ON_MARGIN,
    DEFAULT_SAFETY_MARGIN,
    DEFAULT_SETTLE_TIME,
    DEFAULT_SMOOTHING_WINDOW,
)

DEFAULTS: dict[str, Any] = {
    CONF_PV_SENSOR: None,
    CONF_ZERO_INJECTION_ENTITY: None,
    CONF_GLOBAL_CONDITION: None,
    CONF_CYCLE_INTERVAL: DEFAULT_CYCLE_INTERVAL,
    CONF_SAFETY_MARGIN: DEFAULT_SAFETY_MARGIN,
    CONF_SMOOTHING_WINDOW: DEFAULT_SMOOTHING_WINDOW,
    CONF_SETTLE_TIME: DEFAULT_SETTLE_TIME,
    CONF_ON_MARGIN: DEFAULT_ON_MARGIN,
    CONF_IMPORT_TOLERANCE: DEFAULT_IMPORT_TOLERANCE,
}


def get_settings(entry: ConfigEntry) -> dict[str, Any]:
    """Effective settings: defaults, then entry data, then options."""
    return {**DEFAULTS, **entry.data, **entry.options}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SolarSplit from a config entry."""
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
