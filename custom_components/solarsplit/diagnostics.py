"""Downloadable diagnostics: settings, loads, memory and the last cycle."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from . import SolarSplitConfigEntry


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: SolarSplitConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        **coordinator.diagnostics(),
        "last_cycle": asdict(data) if data is not None else None,
    }
