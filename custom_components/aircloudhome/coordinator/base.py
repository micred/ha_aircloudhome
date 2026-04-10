"""
Core DataUpdateCoordinator implementation for aircloudhome.

This module contains the main coordinator class that manages data fetching
and updates for all entities in the integration. It handles refresh cycles,
error handling, and triggers reauthentication when needed.

For more information on coordinators:
https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from custom_components.aircloudhome.api import AirCloudHomeApiClientAuthenticationError, AirCloudHomeApiClientError
from custom_components.aircloudhome.const import (
    CONF_ENABLE_ENERGY_MONITORING,
    DEFAULT_ENABLE_ENERGY_MONITORING,
    ENERGY_MONITORING_START_DATE,
    LOGGER,
)
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from custom_components.aircloudhome.data import AirCloudHomeConfigEntry

_ENERGY_SUMMARY_REFRESH_INTERVAL = timedelta(hours=1)
_POST_COMMAND_REFRESH_DELAY_SECONDS = 10.0


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

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize coordinator caches and shared refresh scheduling."""
        super().__init__(*args, **kwargs)
        self._cached_family_ids: tuple[int, ...] | None = None
        self._cached_energy_by_rac_id: dict[int, dict[str, Any]] = {}
        self._cached_energy_family_ids: frozenset[int] = frozenset()
        self._cached_energy_period: dict[str, str] | None = None
        self._last_energy_refresh_at: datetime | None = None
        self._cancel_pending_post_command_refresh: Callable[[], None] | None = None

    @callback
    def async_schedule_post_command_refresh(self) -> None:
        """Collapse rapid device commands into one follow-up refresh."""
        self.async_cancel_scheduled_post_command_refresh()

        @callback
        def _async_handle_refresh(_: datetime) -> None:
            """Trigger a single delayed coordinator refresh."""
            self._cancel_pending_post_command_refresh = None
            self.hass.async_create_task(self._async_refresh_after_command())

        self._cancel_pending_post_command_refresh = async_call_later(
            self.hass,
            _POST_COMMAND_REFRESH_DELAY_SECONDS,
            _async_handle_refresh,
        )

    @callback
    def async_cancel_scheduled_post_command_refresh(self) -> None:
        """Cancel any pending delayed refresh scheduled after a device command."""
        if self._cancel_pending_post_command_refresh is not None:
            self._cancel_pending_post_command_refresh()
            self._cancel_pending_post_command_refresh = None

    async def _async_refresh_after_command(self) -> None:
        """Refresh coordinator data after a debounced control command."""
        await self.async_request_refresh()

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

            family_ids = await self._async_resolve_family_ids()
            if not family_ids:
                LOGGER.warning("No family groups found for user")
                return {
                    "devices": [],
                    "energy_by_rac_id": {},
                    "energy_period": None,
                }

            # Fetch devices from all family groups
            devices = []
            for family_id in family_ids:
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
                energy_by_rac_id = await self._async_get_energy_summary_data(devices, energy_period)
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

    async def _async_resolve_family_ids(self) -> tuple[int, ...]:
        """Resolve and cache family IDs because account membership rarely changes."""
        if self._cached_family_ids is not None:
            return self._cached_family_ids

        client = self.config_entry.runtime_data.client
        family_ids: set[int] = set()

        for family_group in await client.async_get_family_groups():
            if (family_id := self._normalize_family_id(family_group.get("familyId"))) is not None:
                family_ids.add(family_id)

        if not family_ids:
            who_am_i = await client.async_get_who_am_i()
            if (family_id := self._normalize_family_id(who_am_i.get("familyId"))) is not None:
                family_ids.add(family_id)

        resolved_family_ids = tuple(sorted(family_ids))
        if resolved_family_ids:
            self._cached_family_ids = resolved_family_ids

        return resolved_family_ids

    async def _async_get_energy_summary_data(
        self,
        devices: list[dict[str, Any]],
        energy_period: dict[str, str],
    ) -> dict[int, dict[str, Any]]:
        """Fetch energy summaries on a slower cadence than device state polling."""
        family_ids = {
            family_id
            for device in devices
            if (family_id := self._normalize_family_id(device.get("familyId"))) is not None
        }

        if not family_ids:
            self._cached_energy_by_rac_id = {}
            self._cached_energy_family_ids = frozenset()
            self._cached_energy_period = dict(energy_period)
            self._last_energy_refresh_at = datetime.now(UTC)
            return {}

        if not self._should_refresh_energy_summary(family_ids, energy_period):
            return dict(self._cached_energy_by_rac_id)

        client = self.config_entry.runtime_data.client
        energy_by_rac_id: dict[int, dict[str, Any]] = {}
        for family_id in family_ids:
            summary = await client.async_get_energy_consumption_summary(
                family_id=family_id,
                from_date=energy_period["from"],
                to_date=energy_period["to"],
            )
            for item in summary.get("individualRacsData", []):
                if (rac_id := self._normalize_family_id(item.get("racId"))) is None:
                    continue
                energy_by_rac_id[rac_id] = item

        self._cached_energy_by_rac_id = energy_by_rac_id
        self._cached_energy_family_ids = frozenset(family_ids)
        self._cached_energy_period = dict(energy_period)
        self._last_energy_refresh_at = datetime.now(UTC)
        return dict(self._cached_energy_by_rac_id)

    def _should_refresh_energy_summary(
        self,
        family_ids: set[int],
        energy_period: dict[str, str],
    ) -> bool:
        """Return whether the energy summary cache should be refreshed."""
        if self._cached_energy_period != energy_period:
            return True

        if self._cached_energy_family_ids != frozenset(family_ids):
            return True

        if self._last_energy_refresh_at is None:
            return True

        return datetime.now(UTC) - self._last_energy_refresh_at >= _ENERGY_SUMMARY_REFRESH_INTERVAL

    @staticmethod
    def _normalize_family_id(value: Any) -> int | None:
        """Convert numeric API identifiers into integers."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
