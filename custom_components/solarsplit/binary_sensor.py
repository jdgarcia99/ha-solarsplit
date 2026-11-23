"""Binary sensors: whether an on-demand load has an active request."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SolarSplitConfigEntry
from .entity import SolarSplitEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SolarSplitConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add a request sensor to every on-demand load."""
    coordinator = entry.runtime_data
    for load in coordinator.loads:
        if load.on_demand:
            async_add_entities([RequestSensor(coordinator, "request", load)], config_subentry_id=load.id)


class RequestSensor(SolarSplitEntity, BinarySensorEntity):
    """On while someone else has switched the load on and nobody else has switched it off."""

    @property
    def is_on(self) -> bool:
        """Whether the request is active."""
        return self.coordinator.memory[self.load.id].request
