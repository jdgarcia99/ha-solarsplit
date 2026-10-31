"""SolarSplit: share PV surplus among prioritised loads in Home Assistant."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr

from .const import LABEL_NAME
from .coordinator import SolarSplitCoordinator
from .settings import (
    DEFAULTS,
    LOAD_DEFAULTS,
    config_snapshot,
    get_settings,
    load_settings,
)

__all__ = ["DEFAULTS", "LOAD_DEFAULTS", "get_settings", "load_settings"]

PLATFORMS: list[Platform] = [Platform.NUMBER, Platform.SENSOR, Platform.SWITCH]

type SolarSplitConfigEntry = ConfigEntry[SolarSplitCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SolarSplitConfigEntry) -> bool:
    """Set up SolarSplit from a config entry."""
    coordinator = SolarSplitCoordinator(hass, entry)
    await coordinator.async_load_memory()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_label_entities(hass, entry)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SolarSplitConfigEntry) -> bool:
    """Unload a config entry, saving the loop's memory first so a reload finds it."""
    await entry.runtime_data.async_save_memory()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: SolarSplitConfigEntry) -> None:
    """Delete the saved memory with the entry."""
    await SolarSplitCoordinator(hass, entry).async_remove_memory()


async def _async_reload(hass: HomeAssistant, entry: SolarSplitConfigEntry) -> None:
    """Reload when the options or the loads change, but not for changes applied on the fly."""
    if config_snapshot(entry) == entry.runtime_data.snapshot:
        return
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _async_label_entities(hass: HomeAssistant, entry: SolarSplitConfigEntry) -> None:
    """Give every entity of the entry the surplus management label, creating it if needed."""
    labels = lr.async_get(hass)
    label = labels.async_get_label_by_name(LABEL_NAME) or labels.async_create(LABEL_NAME, icon="mdi:solar-power")
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if label.label_id not in entity.labels:
            registry.async_update_entity(entity.entity_id, labels=entity.labels | {label.label_id})
