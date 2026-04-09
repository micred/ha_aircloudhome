"""Constants for aircloudhome."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

# Integration metadata
DOMAIN = "aircloudhome"
ATTRIBUTION = "Data provided by AirCloud Home (Hitachi)"

# Platform parallel updates - applied to all platforms
PARALLEL_UPDATES = 1

# Default configuration values
DEFAULT_UPDATE_INTERVAL_MINUTES = 5
DEFAULT_ENABLE_DEBUGGING = False
DEFAULT_ENABLE_ENERGY_MONITORING = False

# Configuration option keys
CONF_UPDATE_INTERVAL_MINUTES = "update_interval_minutes"
CONF_ENABLE_ENERGY_MONITORING = "enable_energy_monitoring"

# Energy monitoring configuration
ENERGY_MONITORING_START_DATE = "2026-01-01"
