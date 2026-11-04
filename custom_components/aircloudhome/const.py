"""Constants for aircloudhome."""

from datetime import date
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

# Configuration data keys
# Cloud region of the account (see AirCloudHomeRegion). Entries created before
# region support have no value and use the Japan API.
CONF_REGION = "region"

# Configuration option keys
CONF_UPDATE_INTERVAL_MINUTES = "update_interval_minutes"
CONF_ENABLE_ENERGY_MONITORING = "enable_energy_monitoring"

# Energy monitoring
# The energy sensors report the consumption summed from this fixed date up to
# today, so their value only grows and can feed the Energy dashboard. Changing
# it would make long-term statistics jump, so keep it constant.
ENERGY_MONITORING_START_DATE = date(2026, 1, 1)
