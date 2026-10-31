"""Number: the priority of every load, editable on the fly."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SolarSplitConfigEntry
from .const import CONF_PRIORITY
from .entity import SolarSplitEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SolarSplitConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add one priority per load."""
    coordinator = entry.runtime_data
    for load in coordinator.loads:
        async_add_entities([PriorityNumber(coordinator, "priority", load)], config_subentry_id=load.id)


class PriorityNumber(SolarSplitEntity, NumberEntity):
    """Priority of a load. It is stored in the load's subentry, so changing it reloads the entry."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 1
    _attr_native_max_value = 100
    _attr_native_step = 1

    @property
    def native_value(self) -> float:
        """Configured priority."""
        return self.load.priority

    async def async_set_native_value(self, value: float) -> None:
        """Store the new priority in the subentry."""
        entry = self.coordinator.config_entry
        subentry = entry.subentries[self.load.id]
        self.hass.config_entries.async_update_subentry(
            entry, subentry, data={**subentry.data, CONF_PRIORITY: int(value)}
        )
