"""
Core DataUpdateCoordinator implementation for aircloudhome.

This module contains the main coordinator class that manages data fetching
and updates for all entities in the integration. It handles refresh cycles,
error handling, and triggers reauthentication when needed.

For more information on coordinators:
https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from custom_components.aircloudhome.api import AirCloudHomeApiClientAuthenticationError, AirCloudHomeApiClientError
from custom_components.aircloudhome.const import LOGGER
from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

if TYPE_CHECKING:
    from custom_components.aircloudhome.data import AirCloudHomeConfigEntry

# The cloud needs a few seconds before idu-list reflects a control command, so
# refreshing immediately would only fetch (and briefly show) the old state.
POST_COMMAND_REFRESH_DELAY_SECONDS = 10.0


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

    _cancel_post_command_refresh: CALLBACK_TYPE | None = None

    @callback
    def async_schedule_post_command_refresh(self) -> None:
        """
        Schedule one refresh shortly after a device command.

        Each call restarts the delay, so a burst of commands (one or several
        units) results in a single refresh after the last one.
        """
        self.async_cancel_scheduled_post_command_refresh()

        @callback
        def _async_refresh(_now: datetime) -> None:
            self._cancel_post_command_refresh = None
            self.hass.async_create_task(self._async_refresh_after_command())

        self._cancel_post_command_refresh = async_call_later(
            self.hass,
            POST_COMMAND_REFRESH_DELAY_SECONDS,
            _async_refresh,
        )

    async def _async_refresh_after_command(self) -> None:
        """
        Refresh, and notify entities even if the cloud still reports the old state.

        With ``always_update=False`` an unchanged payload notifies nobody, yet an
        entity waiting for its command to be confirmed must see exactly that
        stale payload to resend the command or give up on it.
        """
        previous = self.data
        await self.async_refresh()
        if self.last_update_success and self.data == previous:
            self.async_update_listeners()

    @callback
    def async_cancel_scheduled_post_command_refresh(self) -> None:
        """Cancel a refresh scheduled by ``async_schedule_post_command_refresh``."""
        if self._cancel_post_command_refresh is not None:
            self._cancel_post_command_refresh()
            self._cancel_post_command_refresh = None

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
                ]
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
                return {"devices": []}

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
            return {"devices": devices}
