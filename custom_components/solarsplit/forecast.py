"""Hourly solar production forecast from the energy platforms of other integrations.

Forecast.Solar (or Solcast, Open-Meteo Solar Forecast) gives the Energy dashboard
an hourly forecast through its `energy` platform (`async_get_solar_forecast`).
SolarSplit reads the same forecast from the config entries chosen in its settings
and sums them (one entry per string).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.loader import IntegrationNotFound, async_get_integration
from homeassistant.util import dt as dt_util

from .planner import PERIOD_END_DOMAINS, sum_forecasts

_LOGGER = logging.getLogger(__name__)

ENERGY_PLATFORM = "energy"


async def _forecast_function(hass: HomeAssistant, domain: str) -> Callable[..., Any] | None:
    """The `async_get_solar_forecast` of an integration, if it has one."""
    try:
        integration = await async_get_integration(hass, domain)
        if not integration.platforms_exists([ENERGY_PLATFORM]):
            return None
        platform = await integration.async_get_platform(ENERGY_PLATFORM)
    except (IntegrationNotFound, ImportError):
        return None
    return getattr(platform, "async_get_solar_forecast", None)


async def async_forecast_entries(hass: HomeAssistant) -> list[tuple[str, str]]:
    """(entry ID, label) of the config entries that give a solar forecast."""
    result = []
    for entry in hass.config_entries.async_entries():
        if entry.disabled_by is None and await _forecast_function(hass, entry.domain) is not None:
            result.append((entry.entry_id, f"{entry.title} ({entry.domain})"))
    return result


async def async_read_forecast(hass: HomeAssistant, entry_ids: Iterable[str]) -> dict[float, float] | None:
    """The summed forecast in Wh per local hour start (timestamp).

    None when no chosen entry gives a forecast now (not loaded yet, or removed).
    """
    forecasts = []
    for entry_id in entry_ids:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.state is not ConfigEntryState.LOADED:
            continue
        if (function := await _forecast_function(hass, entry.domain)) is None:
            continue
        try:
            data = await function(hass, entry_id)
        except Exception as err:  # noqa: BLE001 - another integration's failure must not stop the loop
            _LOGGER.debug("Solar forecast of %s failed: %s", entry.title, err)
            continue
        if data and (hours := data.get("wh_hours")):
            forecasts.append((hours, entry.domain in PERIOD_END_DOMAINS))
    if not forecasts:
        return None
    return sum_forecasts(forecasts, dt_util.get_default_time_zone())
