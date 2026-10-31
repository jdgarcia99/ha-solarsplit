"""Base entity: one device for the loop and one per load."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LoadConfig, SolarSplitCoordinator


class SolarSplitEntity(CoordinatorEntity[SolarSplitCoordinator]):
    """An entity of the loop device, or of a load device when `load` is given."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SolarSplitCoordinator, key: str, load: LoadConfig | None = None) -> None:
        """Set the unique ID, translation key and device."""
        super().__init__(coordinator)
        self.load = load
        entry_id = coordinator.config_entry.entry_id
        self._attr_translation_key = key
        if load is None:
            self._attr_unique_id = f"{entry_id}_{key}"
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, entry_id)}, name="SolarSplit", entry_type=DeviceEntryType.SERVICE
            )
        else:
            self._attr_unique_id = f"{load.id}_{key}"
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, load.id)}, name=load.name, entry_type=DeviceEntryType.SERVICE
            )
