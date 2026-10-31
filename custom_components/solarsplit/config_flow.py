"""Config and options flows for SolarSplit."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import UnitOfPower
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import DEFAULTS, get_settings
from .const import (
    CONF_CYCLE_INTERVAL,
    CONF_EXPORT_SENSOR,
    CONF_GLOBAL_CONDITION,
    CONF_IMPORT_SENSOR,
    CONF_IMPORT_TOLERANCE,
    CONF_ON_MARGIN,
    CONF_PV_SENSOR,
    CONF_SAFETY_MARGIN,
    CONF_SETTLE_TIME,
    CONF_SMOOTHING_WINDOW,
    CONF_ZERO_INJECTION_ENTITY,
    DOMAIN,
    MAX_CYCLE_INTERVAL,
    MIN_CYCLE_INTERVAL,
)

POWER_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class="power")
)
BOOLEAN_ENTITY = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=["input_boolean", "binary_sensor", "switch"])
)
OPTIONAL_ENTITIES = (CONF_PV_SENSOR, CONF_ZERO_INJECTION_ENTITY, CONF_GLOBAL_CONDITION)


def _number(minimum: int, maximum: int, unit: str) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum, max=maximum, step=1, unit_of_measurement=unit,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def settings_schema(values: dict[str, Any]) -> vol.Schema:
    """Schema of the global settings, shared by the config and options flows."""

    def optional(key: str) -> vol.Optional:
        # suggested_value instead of default, so an optional entity can be cleared
        return vol.Optional(key, description={"suggested_value": values.get(key)})

    return vol.Schema(
        {
            vol.Required(CONF_EXPORT_SENSOR, default=values.get(CONF_EXPORT_SENSOR, vol.UNDEFINED)): POWER_SENSOR,
            vol.Required(CONF_IMPORT_SENSOR, default=values.get(CONF_IMPORT_SENSOR, vol.UNDEFINED)): POWER_SENSOR,
            optional(CONF_PV_SENSOR): POWER_SENSOR,
            optional(CONF_ZERO_INJECTION_ENTITY): BOOLEAN_ENTITY,
            optional(CONF_GLOBAL_CONDITION): BOOLEAN_ENTITY,
            vol.Required(CONF_CYCLE_INTERVAL, default=values[CONF_CYCLE_INTERVAL]): _number(
                MIN_CYCLE_INTERVAL, MAX_CYCLE_INTERVAL, "s"
            ),
            vol.Required(CONF_SAFETY_MARGIN, default=values[CONF_SAFETY_MARGIN]): _number(0, 2000, UnitOfPower.WATT),
            vol.Required(CONF_SMOOTHING_WINDOW, default=values[CONF_SMOOTHING_WINDOW]): _number(0, 600, "s"),
            vol.Required(CONF_SETTLE_TIME, default=values[CONF_SETTLE_TIME]): _number(0, 600, "s"),
            vol.Required(CONF_ON_MARGIN, default=values[CONF_ON_MARGIN]): _number(0, 2000, UnitOfPower.WATT),
            vol.Required(CONF_IMPORT_TOLERANCE, default=values[CONF_IMPORT_TOLERANCE]): _number(
                0, 2000, UnitOfPower.WATT
            ),
        }
    )


class SolarSplitConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create the single SolarSplit entry."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the grid sensors and the global settings."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="SolarSplit", data=user_input)
        return self.async_show_form(step_id="user", data_schema=settings_schema(DEFAULTS))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return SolarSplitOptionsFlow()


class SolarSplitOptionsFlow(OptionsFlow):
    """Edit the global settings."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the global settings."""
        if user_input is not None:
            # A cleared optional entity is missing from the input; store it as None
            # so it does not fall back to the value in the entry data.
            cleared = dict.fromkeys(OPTIONAL_ENTITIES)
            return self.async_create_entry(data={**cleared, **user_input})
        return self.async_show_form(
            step_id="init", data_schema=settings_schema(get_settings(self.config_entry))
        )
