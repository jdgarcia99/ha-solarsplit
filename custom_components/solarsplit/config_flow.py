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
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import (
    CONF_AUTO_STATE,
    CONF_CONDITION_ENTITY,
    CONF_CONDITION_STATE,
    CONF_CYCLE_INTERVAL,
    CONF_EXPORT_SENSOR,
    CONF_GLOBAL_CONDITION,
    CONF_GRID_SENSOR,
    CONF_GRID_SIGN,
    CONF_HOURS_SENSOR,
    CONF_HVAC_MODE,
    CONF_IMPORT_SENSOR,
    CONF_IMPORT_TOLERANCE,
    CONF_LIMIT_ENTITY,
    CONF_LIMIT_INTERVAL,
    CONF_LIMIT_MAX,
    CONF_LIMIT_MIN,
    CONF_LIMIT_THRESHOLD,
    CONF_LOAD_ENTITY,
    CONF_LOAD_TYPE,
    CONF_MIN_OFF,
    CONF_MIN_ON,
    CONF_MODE_ENTITY,
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
    CONF_STALE_TIMEOUT,
    CONF_TARGET_ENTITY,
    CONF_TARGET_HOURS,
    CONF_VARIABLE_ENTITY,
    CONF_ZERO_INJECTION_ENTITY,
    DOMAIN,
    GRID_SIGNS,
    HVAC_MODES,
    LOAD_DOMAINS,
    LOAD_TYPE_REGULABLE,
    LOAD_TYPES,
    MAX_CYCLE_INTERVAL,
    MIN_CYCLE_INTERVAL,
    SECTION_DAILY_TARGET,
    SECTION_REGULABLE,
    SUBENTRY_LOAD,
)
from .settings import DEFAULTS, LOAD_DEFAULTS, get_settings

POWER_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class="power")
)
BOOLEAN_ENTITY = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=["input_boolean", "binary_sensor", "switch"])
)
LOAD_ENTITY = selector.EntitySelector(selector.EntitySelectorConfig(domain=LOAD_DOMAINS))
LIMIT_ENTITY = selector.EntitySelector(selector.EntitySelectorConfig(domain=["number", "input_number"]))
MODE_ENTITY = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=["select", "input_select", "switch", "input_boolean"])
)
HOURS_SENSOR = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
TARGET_ENTITY = selector.EntitySelector(selector.EntitySelectorConfig(domain=["input_number", "number", "sensor"]))
HOURS = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0, max=24, step=0.1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement=UnitOfTime.HOURS
    )
)
SECTIONS = (SECTION_REGULABLE, SECTION_DAILY_TARGET)
OPTIONAL_ENTITIES = (
    CONF_GRID_SENSOR, CONF_EXPORT_SENSOR, CONF_IMPORT_SENSOR,
    CONF_PV_SENSOR, CONF_ZERO_INJECTION_ENTITY, CONF_GLOBAL_CONDITION,
)


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
            _optional(CONF_GRID_SENSOR, values): POWER_SENSOR,
            vol.Required(CONF_GRID_SIGN, default=values[CONF_GRID_SIGN]): selector.SelectSelector(
                selector.SelectSelectorConfig(options=GRID_SIGNS, translation_key=CONF_GRID_SIGN)
            ),
            _optional(CONF_EXPORT_SENSOR, values): POWER_SENSOR,
            _optional(CONF_IMPORT_SENSOR, values): POWER_SENSOR,
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
            vol.Required(CONF_STALE_TIMEOUT, default=values[CONF_STALE_TIMEOUT]): _number(
                0, 600, UnitOfTime.SECONDS
            ),
            vol.Required(CONF_SHADOW_MODE, default=values[CONF_SHADOW_MODE]): selector.BooleanSelector(),
        }
    )


def validate_grid(user_input: dict[str, Any]) -> dict[str, str]:
    """Either one signed grid sensor, or both the export and the import sensors."""
    signed = bool(user_input.get(CONF_GRID_SENSOR))
    split = [bool(user_input.get(key)) for key in (CONF_EXPORT_SENSOR, CONF_IMPORT_SENSOR)]
    if signed == any(split) or (not signed and not all(split)):
        return {"base": "grid_sensors"}
    return {}


def load_schema(values: dict[str, Any]) -> vol.Schema:
    """Schema of a load subentry, shared by its creation and reconfiguration."""

    def required(key: str) -> vol.Required:
        return vol.Required(key, default=values.get(key, vol.UNDEFINED))

    regulable = vol.Schema(
        {
            _optional(CONF_LIMIT_ENTITY, values): LIMIT_ENTITY,
            _optional(CONF_MODE_ENTITY, values): MODE_ENTITY,
            required(CONF_AUTO_STATE): selector.TextSelector(),
            required(CONF_LIMIT_MIN): _number(0, 20000, UnitOfPower.WATT),
            required(CONF_LIMIT_MAX): _number(0, 20000, UnitOfPower.WATT),
            required(CONF_LIMIT_THRESHOLD): _number(0, 2000, UnitOfPower.WATT),
            required(CONF_LIMIT_INTERVAL): _number(0, 3600, UnitOfTime.SECONDS),
        }
    )
    daily_target = vol.Schema(
        {
            _optional(CONF_HOURS_SENSOR, values): HOURS_SENSOR,
            _optional(CONF_TARGET_ENTITY, values): TARGET_ENTITY,
            required(CONF_TARGET_HOURS): HOURS,
        }
    )
    return vol.Schema(
        {
            required(CONF_NAME): selector.TextSelector(),
            required(CONF_LOAD_TYPE): selector.SelectSelector(
                selector.SelectSelectorConfig(options=LOAD_TYPES, translation_key=CONF_LOAD_TYPE)
            ),
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
            vol.Optional(SECTION_REGULABLE, default={}): section(
                regulable, {"collapsed": values[CONF_LOAD_TYPE] != LOAD_TYPE_REGULABLE}
            ),
            vol.Optional(SECTION_DAILY_TARGET, default={}): section(
                daily_target, {"collapsed": not (values[CONF_TARGET_ENTITY] or values[CONF_TARGET_HOURS])}
            ),
        }
    )


def flatten(user_input: dict[str, Any]) -> dict[str, Any]:
    """Store the fields of the form's sections at the top level of the load's data."""
    data = {key: value for key, value in user_input.items() if key not in SECTIONS}
    for key in SECTIONS:
        data.update(user_input.get(key, {}))
    return data


class SolarSplitConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create the single SolarSplit entry."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the grid sensors and the global settings."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors = validate_grid(user_input) if user_input is not None else {}
        if user_input is not None and not errors:
            return self.async_create_entry(title="SolarSplit", data=user_input)
        values = {**DEFAULTS, **(user_input or {})}
        return self.async_show_form(step_id="user", data_schema=settings_schema(values), errors=errors)

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
        errors = validate_grid(user_input) if user_input is not None else {}
        if user_input is not None and not errors:
            # A cleared optional entity is missing from the input; store it as None
            # so it does not fall back to the value in the entry data.
            cleared = dict.fromkeys(OPTIONAL_ENTITIES)
            return self.async_create_entry(data={**cleared, **user_input})
        values = {**get_settings(self.config_entry), **(user_input or {})}
        return self.async_show_form(step_id="init", data_schema=settings_schema(values), errors=errors)


class LoadSubentryFlow(ConfigSubentryFlow):
    """Add or edit one load."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add a load."""
        data = flatten(user_input or {})
        errors = self._validate(user_input and data, None)
        if user_input is not None and not errors:
            return self.async_create_entry(
                title=data[CONF_NAME], data=data, unique_id=data[CONF_LOAD_ENTITY]
            )
        values = {**LOAD_DEFAULTS, **data}
        return self.async_show_form(step_id="user", data_schema=load_schema(values), errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Edit a load."""
        subentry = self._get_reconfigure_subentry()
        data = flatten(user_input or {})
        errors = self._validate(user_input and data, subentry.subentry_id)
        if user_input is not None and not errors:
            return self.async_update_and_abort(
                self._get_entry(), subentry, title=data[CONF_NAME],
                data=data, unique_id=data[CONF_LOAD_ENTITY],
            )
        values = {**LOAD_DEFAULTS, **subentry.data, **data}
        return self.async_show_form(step_id="reconfigure", data_schema=load_schema(values), errors=errors)

    def _validate(self, data: dict[str, Any] | None, own_id: str | None) -> dict[str, str]:
        """An entity belongs to one load only; a regulable load needs its entities and a valid range."""
        if not data:
            return {}
        for subentry in self._get_entry().subentries.values():
            if subentry.subentry_id != own_id and subentry.unique_id == data[CONF_LOAD_ENTITY]:
                return {CONF_LOAD_ENTITY: "entity_in_use"}
        if data[CONF_LOAD_TYPE] == LOAD_TYPE_REGULABLE:
            if not all(data.get(key) for key in (CONF_POWER_SENSOR, CONF_LIMIT_ENTITY, CONF_MODE_ENTITY)):
                return {"base": "regulable_incomplete"}
            if data[CONF_LIMIT_MIN] >= data[CONF_LIMIT_MAX]:
                return {"base": "limit_range"}
        return {}
