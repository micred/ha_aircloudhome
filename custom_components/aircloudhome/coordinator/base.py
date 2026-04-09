"""
Core DataUpdateCoordinator implementation for aircloudhome.

This module contains the main coordinator class that manages data fetching
and updates for all entities in the integration. It handles refresh cycles,
error handling, and triggers reauthentication when needed.

For more information on coordinators:
https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
"""

from __future__ import annotations

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
                "energy_by_rac_id": {
                    10001: {
                        "racId": 10001,
                        "energyConsumed": 123.45,
                        "cost": 12.34,
                    }
                },
                "energy_period": {
                    "from": "2000-01-01",
                    "to": "2026-04-09",
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
                who_am_i = await client.async_get_who_am_i()
                family_id = who_am_i.get("familyId")
                if family_id:
                    family_groups = [{"familyId": family_id}]
                else:
                    LOGGER.warning("No family groups found for user")
                    return {
                        "devices": [],
                        "energy_by_rac_id": {},
                        "energy_period": None,
                    }

            # Fetch devices from all family groups
            devices = []
            for family_group in family_groups:
                family_id = family_group.get("familyId")
                if not family_id:
                    LOGGER.warning("Family group missing familyId")
                    continue

                idu_list = await client.async_get_idu_list(family_id)

                for device in idu_list:
                    device["familyId"] = family_id
                    devices.append(device)

            energy_by_rac_id: dict[int, dict[str, Any]] = {}
            energy_period: dict[str, str] | None = None
            if self.config_entry.options.get(
                CONF_ENABLE_ENERGY_MONITORING,
                DEFAULT_ENABLE_ENERGY_MONITORING,
            ):
                energy_period = self._get_energy_summary_period()
                family_ids = {int(device["familyId"]) for device in devices if device.get("familyId") is not None}
                for family_id in family_ids:
                    summary = await client.async_get_energy_consumption_summary(
                        family_id=family_id,
                        from_date=energy_period["from"],
                        to_date=energy_period["to"],
                    )
                    for item in summary.get("individualRacsData", []):
                        if (rac_id := item.get("racId")) is None:
                            continue
                        energy_by_rac_id[int(rac_id)] = item
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
            return {
                "devices": devices,
                "energy_by_rac_id": energy_by_rac_id,
                "energy_period": energy_period,
            }

    def _get_energy_summary_period(self) -> dict[str, str]:
        """Return the date range used for cumulative energy monitoring."""
        return {
            "from": ENERGY_MONITORING_START_DATE,
            "to": dt_util.now().date().isoformat(),
        }
