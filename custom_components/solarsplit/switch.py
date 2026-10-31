"""Switches: the master switch of the loop and the "managed" switch of every load."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

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


class RestoredSwitch(SolarSplitEntity, SwitchEntity, RestoreEntity):
    """A switch that keeps its state across restarts and refreshes the loop when changed."""

    def _set(self, on: bool) -> None:
        raise NotImplementedError

    async def async_added_to_hass(self) -> None:
        """Restore the last state; on by default."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None and last.state == STATE_OFF:
            self._set(False)
            await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on."""
        self._set(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        self._set(False)
        await self.coordinator.async_request_refresh()


class ActiveSwitch(RestoredSwitch):
    """Master switch: when off, the loop leaves every load as it is."""

    def _set(self, on: bool) -> None:
        self.coordinator.active = on

    @property
    def is_on(self) -> bool:
        """Whether the loop is active."""
        return self.coordinator.active


class ManagedSwitch(RestoredSwitch):
    """Include or exclude a load without deleting it."""

    def _set(self, on: bool) -> None:
        self.coordinator.set_managed(self.load.id, on)

    @property
    def is_on(self) -> bool:
        """Whether the load is managed."""
        return self.coordinator.memory[self.load.id].managed
