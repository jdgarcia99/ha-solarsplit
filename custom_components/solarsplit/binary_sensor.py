"""Binary sensors: maintenance in progress, and whether an on-demand load has an active request."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SolarSplitConfigEntry
from .entity import SolarSplitEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SolarSplitConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add the maintenance sensor, and a request sensor to every on-demand load."""
    coordinator = entry.runtime_data
    async_add_entities([MaintenanceSensor(coordinator, "maintenance")])
    for load in coordinator.loads:
        if load.on_demand:
            async_add_entities([RequestSensor(coordinator, "request", load)], config_subentry_id=load.id)


class RequestSensor(SolarSplitEntity, BinarySensorEntity):
    """On while someone else has switched the load on and nobody else has switched it off."""

    @property
    def is_on(self) -> bool:
        """Whether the request is active."""
        return self.coordinator.memory[self.load.id].request


class MaintenanceSensor(SolarSplitEntity, BinarySensorEntity):
    """On during a maintenance window: readings are not learned from."""

    @property
    def is_on(self) -> bool:
        """Whether a maintenance window is in force."""
        return self.coordinator.data.maintenance is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Why (entity, unavailable or restart), until when (None while it lasts) and which devices."""
        window = self.coordinator.data.maintenance
        if window is None:
            return {"source": None, "until": None, "devices": []}
        until = None if window.until is None else dt_util.as_local(dt_util.utc_from_timestamp(window.until)).isoformat()
        return {"source": window.source, "until": until, "devices": list(window.devices)}
