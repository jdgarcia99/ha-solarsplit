"""Constants for SolarSplit."""

from __future__ import annotations

DOMAIN = "solarsplit"

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

DEFAULT_CYCLE_INTERVAL = 5  # s, control loop period
DEFAULT_SAFETY_MARGIN = 100  # W, never hand out the last watts
DEFAULT_SMOOTHING_WINDOW = 30  # s, moving average of the measurements
DEFAULT_SETTLE_TIME = 30  # s, wait after a change before the next one
DEFAULT_ON_MARGIN = 100  # W, extra budget a load needs to be switched on
DEFAULT_IMPORT_TOLERANCE = 150  # W, deficit tolerated before a load is switched off

MIN_CYCLE_INTERVAL = 2
MAX_CYCLE_INTERVAL = 60
