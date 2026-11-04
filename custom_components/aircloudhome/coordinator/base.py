"""
Core DataUpdateCoordinator implementation for aircloudhome.

This module contains the main coordinator class that manages data fetching
and updates for all entities in the integration. It handles refresh cycles,
error handling, and triggers reauthentication when needed.

For more information on coordinators:
https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from custom_components.aircloudhome.api import AirCloudHomeApiClientAuthenticationError, AirCloudHomeApiClientError
from custom_components.aircloudhome.const import (
    CONF_ENABLE_ENERGY_MONITORING,
    DEFAULT_ENABLE_ENERGY_MONITORING,
    ENERGY_MONITORING_START_DATE,
    LOGGER,
)
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from custom_components.aircloudhome.data import AirCloudHomeConfigEntry

# Energy figures change slowly, so they are fetched at most this often,
# independently of the (usually much shorter) device polling interval.
ENERGY_REFRESH_INTERVAL = timedelta(hours=1)


class AirCloudHomeDataUpdateCoordinator(DataUpdateCoordinator):
    """
    Class to manage fetching data from the API.

    This coordinator handles all data fetching for the integration and distributes
    updates to all entities. It manages:
    - Periodic data updates based on update_interval
    - Error handling and recovery
    - Authentication failure detection and reauthentication triggers
    - Data distribution to all entities
    - Context-based data fetching (only fetch data for active entities)

    For more information:
    https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities

    Attributes:
        config_entry: The config entry for this integration instance.
    """

    config_entry: AirCloudHomeConfigEntry

    # Last energy consumption fetched (kWh by device ID) and when it was fetched.
    _energy: dict[int, float] | None = None
    _energy_fetched_at: datetime | None = None

    async def _async_setup(self) -> None:
        """
        Set up the coordinator.

        This method is called automatically during async_config_entry_first_refresh()
        and is the ideal place for one-time initialization tasks such as:
        - Loading device information
        - Setting up event listeners
        - Initializing caches

        This runs before the first data fetch, ensuring any required setup
        is complete before entities start requesting data.
        """
        # Example: Fetch device info once at startup
        # device_info = await self.config_entry.runtime_data.client.get_device_info()
        # self._device_id = device_info["id"]
        LOGGER.debug("Coordinator setup complete for %s", self.config_entry.entry_id)

    async def _async_update_data(self) -> Any:
        """
        Fetch data from API endpoint.

        This method fetches device data from the AirCloud Home API.
        It retrieves family group information and the list of indoor units (AC devices).

        Returns:
            A dictionary with structure: {
                "devices": [
                    {
                        "id": int,
                        "name": str,
                        "power": "ON"|"OFF",
                        "mode": str,
                        "iduTemperature": float,
                        "roomTemperature": float,
                        "fanSpeed": str,
                        "fanSwing": str,
                        "humidity": int,
                        "online": bool,
                        "familyId": int,
                    }
                ],
                "energy": {  # empty unless energy monitoring is enabled
                    device_id: kWh consumed since ENERGY_MONITORING_START_DATE,
                },
            }

        Raises:
            ConfigEntryAuthFailed: If authentication fails, triggers reauthentication.
            UpdateFailed: If data fetching fails for other reasons, optionally with retry_after.
        """
        try:
            client = self.config_entry.runtime_data.client

            # Fetch family groups
            family_groups = await client.async_get_family_groups()
            if not family_groups:
                LOGGER.warning("No family groups found for user")
                return {"devices": [], "energy": {}}

            # Fetch devices from all family groups
            devices = []
            family_ids: list[int] = []
            for family_group in family_groups:
                family_id = family_group.get("familyId")
                if not family_id:
                    LOGGER.warning("Family group missing familyId")
                    continue

                family_ids.append(family_id)
                idu_list = await client.async_get_idu_list(family_id)

                for device in idu_list:
                    device["familyId"] = family_id
                    devices.append(device)

            energy = await self._async_get_energy(family_ids) if self._energy_monitoring_enabled else {}
        except AirCloudHomeApiClientAuthenticationError as exception:
            LOGGER.warning("Authentication error - %s", exception)
            raise ConfigEntryAuthFailed(
                translation_domain="aircloudhome",
                translation_key="authentication_failed",
            ) from exception
        except AirCloudHomeApiClientError as exception:
            LOGGER.exception("Error communicating with API")
            raise UpdateFailed(
                translation_domain="aircloudhome",
                translation_key="update_failed",
            ) from exception
        else:
            return {"devices": devices, "energy": energy}

    @property
    def _energy_monitoring_enabled(self) -> bool:
        """Return whether the user enabled the optional energy sensors."""
        return self.config_entry.options.get(CONF_ENABLE_ENERGY_MONITORING, DEFAULT_ENABLE_ENERGY_MONITORING)

    async def _async_get_energy(self, family_ids: list[int]) -> dict[int, float]:
        """
        Return energy consumption per device, refreshed at most once per hour.

        Failures other than authentication errors are logged and keep the
        previous values, so a problem with the energy endpoint never makes the
        climate entities unavailable. A failed attempt is also not retried
        before the next refresh interval.

        Args:
            family_ids: The family groups whose devices are polled.

        Returns:
            Energy consumed in kWh since ``ENERGY_MONITORING_START_DATE``,
            keyed by device ID.

        """
        now = dt_util.utcnow()
        if self._energy_fetched_at is not None and now - self._energy_fetched_at < ENERGY_REFRESH_INTERVAL:
            return self._energy or {}

        self._energy_fetched_at = now
        client = self.config_entry.runtime_data.client
        today = dt_util.now().date()
        energy: dict[int, float] = {}
        try:
            for family_id in family_ids:
                energy |= await client.async_get_energy_consumption(family_id, ENERGY_MONITORING_START_DATE, today)
        except AirCloudHomeApiClientAuthenticationError:
            raise
        except AirCloudHomeApiClientError as exception:
            LOGGER.warning("Could not update energy consumption, keeping previous values: %s", exception)
            return self._energy or {}

        self._energy = energy
        return energy
