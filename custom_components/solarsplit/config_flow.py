"""Config, options and load subentry flows for SolarSplit."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME, UnitOfPower, UnitOfTime
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import DEFAULTS, LOAD_DEFAULTS, get_settings
from .const import (
    CONF_CONDITION_ENTITY,
    CONF_CONDITION_STATE,
    CONF_CYCLE_INTERVAL,
    CONF_EXPORT_SENSOR,
    CONF_GLOBAL_CONDITION,
    CONF_HVAC_MODE,
    CONF_IMPORT_SENSOR,
    CONF_IMPORT_TOLERANCE,
    CONF_LOAD_ENTITY,
    CONF_MIN_OFF,
    CONF_MIN_ON,
    CONF_NO_SKIP,
    CONF_NOMINAL_POWER,
    CONF_OFF_DELAY,
    CONF_ON_DELAY,
    CONF_ON_MARGIN,
    CONF_POWER_SENSOR,
    CONF_PRIORITY,
    CONF_PV_SENSOR,
    CONF_RESPECT_GLOBAL,
    CONF_RESPECT_MANUAL,
    CONF_SAFETY_MARGIN,
    CONF_SETTLE_TIME,
    CONF_SHADOW_MODE,
    CONF_SMOOTHING_WINDOW,
    CONF_VARIABLE_ENTITY,
    CONF_ZERO_INJECTION_ENTITY,
    DOMAIN,
    HVAC_MODES,
    LOAD_DOMAINS,
    MAX_CYCLE_INTERVAL,
    MIN_CYCLE_INTERVAL,
    SUBENTRY_LOAD,
)

POWER_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class="power")
)
BOOLEAN_ENTITY = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=["input_boolean", "binary_sensor", "switch"])
)
LOAD_ENTITY = selector.EntitySelector(selector.EntitySelectorConfig(domain=LOAD_DOMAINS))
OPTIONAL_ENTITIES = (CONF_PV_SENSOR, CONF_ZERO_INJECTION_ENTITY, CONF_GLOBAL_CONDITION)


def _number(minimum: int, maximum: int, unit: str | None = None) -> selector.NumberSelector:
    config = selector.NumberSelectorConfig(
        min=minimum, max=maximum, step=1, mode=selector.NumberSelectorMode.BOX
    )
    if unit:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


def _optional(key: str, values: dict[str, Any]) -> vol.Optional:
    # suggested_value instead of default, so an optional entity can be cleared
    return vol.Optional(key, description={"suggested_value": values.get(key)})


def settings_schema(values: dict[str, Any]) -> vol.Schema:
    """Schema of the global settings, shared by the config and options flows."""
    return vol.Schema(
        {
            vol.Required(CONF_EXPORT_SENSOR, default=values.get(CONF_EXPORT_SENSOR, vol.UNDEFINED)): POWER_SENSOR,
            vol.Required(CONF_IMPORT_SENSOR, default=values.get(CONF_IMPORT_SENSOR, vol.UNDEFINED)): POWER_SENSOR,
            _optional(CONF_PV_SENSOR, values): POWER_SENSOR,
            _optional(CONF_ZERO_INJECTION_ENTITY, values): BOOLEAN_ENTITY,
            _optional(CONF_GLOBAL_CONDITION, values): BOOLEAN_ENTITY,
            vol.Required(CONF_CYCLE_INTERVAL, default=values[CONF_CYCLE_INTERVAL]): _number(
                MIN_CYCLE_INTERVAL, MAX_CYCLE_INTERVAL, UnitOfTime.SECONDS
            ),
            vol.Required(CONF_SAFETY_MARGIN, default=values[CONF_SAFETY_MARGIN]): _number(0, 2000, UnitOfPower.WATT),
            vol.Required(CONF_SMOOTHING_WINDOW, default=values[CONF_SMOOTHING_WINDOW]): _number(
                0, 600, UnitOfTime.SECONDS
            ),
            vol.Required(CONF_SETTLE_TIME, default=values[CONF_SETTLE_TIME]): _number(0, 600, UnitOfTime.SECONDS),
            vol.Required(CONF_ON_MARGIN, default=values[CONF_ON_MARGIN]): _number(0, 2000, UnitOfPower.WATT),
            vol.Required(CONF_IMPORT_TOLERANCE, default=values[CONF_IMPORT_TOLERANCE]): _number(
                0, 2000, UnitOfPower.WATT
            ),
            vol.Required(CONF_SHADOW_MODE, default=values[CONF_SHADOW_MODE]): selector.BooleanSelector(),
        }
    )


def load_schema(values: dict[str, Any]) -> vol.Schema:
    """Schema of a load subentry, shared by its creation and reconfiguration."""

    def required(key: str) -> vol.Required:
        return vol.Required(key, default=values.get(key, vol.UNDEFINED))

    return vol.Schema(
        {
            required(CONF_NAME): selector.TextSelector(),
            required(CONF_LOAD_ENTITY): LOAD_ENTITY,
            required(CONF_NOMINAL_POWER): _number(1, 20000, UnitOfPower.WATT),
            _optional(CONF_POWER_SENSOR, values): POWER_SENSOR,
            required(CONF_PRIORITY): _number(1, 100),
            required(CONF_NO_SKIP): selector.BooleanSelector(),
            required(CONF_ON_DELAY): _number(0, 3600, UnitOfTime.SECONDS),
            required(CONF_OFF_DELAY): _number(0, 3600, UnitOfTime.SECONDS),
            required(CONF_MIN_ON): _number(0, 240, UnitOfTime.MINUTES),
            required(CONF_MIN_OFF): _number(0, 240, UnitOfTime.MINUTES),
            required(CONF_RESPECT_GLOBAL): selector.BooleanSelector(),
            _optional(CONF_CONDITION_ENTITY, values): selector.EntitySelector(),
            required(CONF_CONDITION_STATE): selector.TextSelector(),
            required(CONF_RESPECT_MANUAL): selector.BooleanSelector(),
            required(CONF_HVAC_MODE): selector.SelectSelector(
                selector.SelectSelectorConfig(options=HVAC_MODES, translation_key=CONF_HVAC_MODE)
            ),
            _optional(CONF_VARIABLE_ENTITY, values): BOOLEAN_ENTITY,
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

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Loads are subentries of the entry."""
        return {SUBENTRY_LOAD: LoadSubentryFlow}


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


class LoadSubentryFlow(ConfigSubentryFlow):
    """Add or edit one load."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add a load."""
        errors = self._validate(user_input, None)
        if user_input is not None and not errors:
            return self.async_create_entry(
                title=user_input[CONF_NAME], data=user_input, unique_id=user_input[CONF_LOAD_ENTITY]
            )
        values = {**LOAD_DEFAULTS, **(user_input or {})}
        return self.async_show_form(step_id="user", data_schema=load_schema(values), errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Edit a load."""
        subentry = self._get_reconfigure_subentry()
        errors = self._validate(user_input, subentry.subentry_id)
        if user_input is not None and not errors:
            return self.async_update_and_abort(
                self._get_entry(), subentry, title=user_input[CONF_NAME],
                data=user_input, unique_id=user_input[CONF_LOAD_ENTITY],
            )
        values = {**LOAD_DEFAULTS, **subentry.data, **(user_input or {})}
        return self.async_show_form(step_id="reconfigure", data_schema=load_schema(values), errors=errors)

    def _validate(self, user_input: dict[str, Any] | None, own_id: str | None) -> dict[str, str]:
        """An entity can belong to one load only."""
        if user_input is None:
            return {}
        for subentry in self._get_entry().subentries.values():
            if subentry.subentry_id != own_id and subentry.unique_id == user_input[CONF_LOAD_ENTITY]:
                return {CONF_LOAD_ENTITY: "entity_in_use"}
        return {}
