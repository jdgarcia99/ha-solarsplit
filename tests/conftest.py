"""Shared fixtures."""

import pytest
from homeassistant.core import HomeAssistant

pytest_plugins = "pytest_homeassistant_custom_component"

EXPORT = "sensor.export"
IMPORT = "sensor.import"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ in every test."""
    yield


@pytest.fixture(autouse=True)
def grid(hass: HomeAssistant) -> None:
    """Grid sensors with no surplus and no import."""
    hass.states.async_set(EXPORT, "0", {"unit_of_measurement": "W"})
    hass.states.async_set(IMPORT, "0", {"unit_of_measurement": "W"})
