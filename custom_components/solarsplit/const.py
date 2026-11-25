"""Constants for SolarSplit."""

from __future__ import annotations

DOMAIN = "solarsplit"
LABEL_NAME = "Gestión de excedentes"  # given to every entity, created if missing

CONF_GRID_SENSOR = "grid_sensor"  # one signed sensor, instead of export and import
CONF_GRID_SIGN = "grid_sign"
CONF_EXPORT_SENSOR = "export_sensor"
CONF_IMPORT_SENSOR = "import_sensor"
CONF_PV_SENSOR = "pv_sensor"
CONF_ZERO_INJECTION_ENTITY = "zero_injection_entity"
CONF_GLOBAL_CONDITION = "global_condition"
CONF_CYCLE_INTERVAL = "cycle_interval"
CONF_SAFETY_MARGIN = "safety_margin"
CONF_SMOOTHING_WINDOW = "smoothing_window"
CONF_SETTLE_TIME = "settle_time"
CONF_ON_MARGIN = "on_margin"
CONF_IMPORT_TOLERANCE = "import_tolerance"
CONF_SHADOW_MODE = "shadow_mode"
CONF_STALE_TIMEOUT = "stale_timeout"
CONF_SEASON_ENTITY = "season_entity"  # input_select or select with the current season
CONF_BUY_PRICE_SENSOR = "buy_price_sensor"  # €/kWh now, with attributes price_00h…price_23h
CONF_SELL_PRICE_SENSOR = "sell_price_sensor"  # same, for the energy exported
CONF_CONTRACTED_POWER = "contracted_power"  # W; 0 = unknown: one load at a time in grid fill
CONF_GRID_MARGIN = "grid_margin"  # W kept below the contracted power by grid fill
CONF_FORECAST_ENTRIES = "solar_forecast_entries"  # config entries with a solar forecast, summed
CONF_BASE_LOAD = "base_load"  # W the house uses in an hour not learned yet
CONF_SOLAR_ENTITY = "solar_entity"  # binary sensor: on while there is solar production
CONF_SOLAR_THRESHOLD = "solar_threshold"  # W of PV above which there is production, without that entity

GRID_SIGN_EXPORT_NEGATIVE = "export_negative"  # negative = export, positive = import
GRID_SIGN_EXPORT_POSITIVE = "export_positive"
GRID_SIGNS = [GRID_SIGN_EXPORT_NEGATIVE, GRID_SIGN_EXPORT_POSITIVE]

DEFAULT_GRID_SIGN = GRID_SIGN_EXPORT_NEGATIVE
DEFAULT_SHADOW_MODE = True  # decide and publish, but never switch a load
DEFAULT_CYCLE_INTERVAL = 5  # s, control loop period
DEFAULT_SAFETY_MARGIN = 100  # W, never hand out the last watts
DEFAULT_SMOOTHING_WINDOW = 30  # s, moving average of the measurements
DEFAULT_SETTLE_TIME = 30  # s, wait after a change before the next one
DEFAULT_ON_MARGIN = 100  # W, extra budget a load needs to be switched on
DEFAULT_IMPORT_TOLERANCE = 150  # W, deficit tolerated before a load is switched off
DEFAULT_STALE_TIMEOUT = 20  # s, a grid reading not reported for longer is unknown; 0 = never
DEFAULT_CONTRACTED_POWER = 0  # W, unknown
DEFAULT_GRID_MARGIN = 1000  # W
DEFAULT_BASE_LOAD = 400  # W
DEFAULT_SOLAR_THRESHOLD = 100  # W
SOLAR_HYSTERESIS = 50  # W: production ends below the threshold less this

MIN_CYCLE_INTERVAL = 2
MAX_CYCLE_INTERVAL = 60

# Load subentries
SUBENTRY_LOAD = "load"
LOAD_DOMAINS = ["switch", "input_boolean", "climate"]

CONF_LOAD_ENTITY = "entity_id"
CONF_NOMINAL_POWER = "nominal_power"
CONF_POWER_SENSOR = "power_sensor"
CONF_CIRCUIT_SENSOR = "circuit_sensor"  # power of the circuit the load is on, shared with others
CONF_PRIORITY = "priority"
CONF_MIN_ON = "min_on"
CONF_MIN_OFF = "min_off"
CONF_ON_DELAY = "on_delay"
CONF_OFF_DELAY = "off_delay"
CONF_NO_SKIP = "no_skip"
CONF_RESPECT_GLOBAL = "respect_global_condition"
CONF_CONDITION_ENTITY = "condition_entity"
CONF_CONDITION_STATE = "condition_state"
CONF_RESPECT_MANUAL = "respect_manual"
CONF_HVAC_MODE = "hvac_mode"
CONF_VARIABLE_ENTITY = "variable_entity"
CONF_LOAD_TYPE = "load_type"

# Regulable loads (a diverter): section of the load form, flattened when stored
SECTION_REGULABLE = "regulable"
CONF_LIMIT_ENTITY = "limit_entity"
CONF_MODE_ENTITY = "mode_entity"
CONF_AUTO_STATE = "auto_state"
CONF_LIMIT_MIN = "limit_min"
CONF_LIMIT_MAX = "limit_max"
CONF_LIMIT_THRESHOLD = "limit_threshold"
CONF_LIMIT_INTERVAL = "limit_interval"
CONF_OUTPUT_ENTITY = "output_entity"  # % of its output in use: at the top, it cannot take more

# Manual control and season: section of the load form, flattened when stored
SECTION_CONTROL = "control"
CONF_ON_DEMAND = "on_demand"  # eligible only while someone else has switched it on
CONF_AUTO_MANAGE = "auto_manage"  # Manage switches itself back on
CONF_MANAGE_AT = "manage_at"  # local time at which Manage switches back on
CONF_SEASONS = "seasons"  # options of the season entity; empty = all year
CONF_URGENCY_ENTITY = "urgency_entity"  # while on, the load uses its urgency priority
CONF_URGENCY_PRIORITY = "urgency_priority"

# Daily objective: section of the load form, flattened when stored. The maximum keeps
# the keys of the former daily target.
SECTION_DAILY_TARGET = "daily_target"
CONF_HOURS_SENSOR = "hours_sensor"
CONF_TARGET_ENTITY = "target_entity"  # maximum hours, only with surplus
CONF_TARGET_HOURS = "target_hours"
CONF_MIN_HOURS = "min_hours"  # minimum hours, guaranteed before the deadline (relay loads)
CONF_MIN_ENTITY = "min_entity"
CONF_DAY_START = "day_start"  # local time at which the objective's day starts
CONF_DEADLINE = "deadline"  # local time by which the minimum must be met
CONF_DEADLINE_ENTITY = "deadline_entity"
CONF_MIN_PRIORITY = "min_priority"  # priority while the minimum is not met; 0 = the normal one
CONF_WINDOW_START = "window_start"  # preferred window for grid fill
CONF_WINDOW_END = "window_end"

LOAD_TYPE_RELAY = "relay"  # switched on and off (regulated externally while its variable entity is on)
LOAD_TYPE_REGULABLE = "regulable"  # regulates itself; SolarSplit only writes its power limit
LOAD_TYPES = [LOAD_TYPE_RELAY, LOAD_TYPE_REGULABLE]

DEFAULT_PRIORITY = 10
DEFAULT_MIN_ON = 5  # min
DEFAULT_MIN_OFF = 5  # min
DEFAULT_ON_DELAY = 60  # s
DEFAULT_OFF_DELAY = 30  # s
DEFAULT_CONDITION_STATE = "on"
DEFAULT_HVAC_MODE = "cool"
HVAC_MODES = ["cool", "heat", "dry", "fan_only", "heat_cool", "auto"]
DEFAULT_TARGET_HOURS = 0  # h, 0 = no fixed daily target
DEFAULT_AUTO_STATE = "Auto"
DEFAULT_LIMIT_MIN = 0  # W
DEFAULT_LIMIT_MAX = 2000  # W
DEFAULT_LIMIT_THRESHOLD = 100  # W, smallest change of the limit worth writing
DEFAULT_LIMIT_INTERVAL = 30  # s, minimum time between two writes of the limit
DEFAULT_MANAGE_AT = "00:00:00"
DEFAULT_DAY_START = "00:00:00"
DEFAULT_MIN_HOURS = 0  # h, 0 = no minimum
DEFAULT_MIN_PRIORITY = 0  # 0 = keep the normal priority
DEFAULT_URGENCY_PRIORITY = 1
URGENCY_DOMAINS = ["binary_sensor", "input_boolean", "switch"]
DEADLINE_DOMAINS = ["input_datetime", "sensor", "time"]
PRICE_DOMAINS = ["sensor"]
SEASON_DOMAINS = ["input_select", "select"]
