"""Switches: the master switch of the loop and the "managed" switch of every load.

Their state lives in the coordinator's memory, which is saved across restarts.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SolarSplitConfigEntry
from .entity import SolarSplitEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SolarSplitConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add the master switch and one managed switch per load."""
    coordinator = entry.runtime_data
    async_add_entities([ActiveSwitch(coordinator, "active")])
    for load in coordinator.loads:
        async_add_entities([ManagedSwitch(coordinator, "managed", load)], config_subentry_id=load.id)


class LoopSwitch(SolarSplitEntity, SwitchEntity):
    """A switch that refreshes the loop when changed."""

    def _set(self, on: bool) -> None:
        raise NotImplementedError

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on."""
        self._set(True)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        self._set(False)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


class ActiveSwitch(LoopSwitch):
    """Master switch: when off, the loop leaves every load as it is."""

    def _set(self, on: bool) -> None:
        self.coordinator.set_active(on)

    @property
    def is_on(self) -> bool:
        """Whether the loop is active."""
        return self.coordinator.active


class ManagedSwitch(LoopSwitch):
    """Include or exclude a load without deleting it."""

    def _set(self, on: bool) -> None:
        self.coordinator.set_managed(self.load.id, on)

    @property
    def is_on(self) -> bool:
        """Whether the load is managed."""
        return self.coordinator.memory[self.load.id].managed
