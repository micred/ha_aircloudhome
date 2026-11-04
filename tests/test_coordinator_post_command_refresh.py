"""Tests for the refresh the coordinator schedules after device commands."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.aircloudhome.const import DOMAIN, LOGGER
from custom_components.aircloudhome.coordinator import AirCloudHomeDataUpdateCoordinator
from custom_components.aircloudhome.coordinator.base import POST_COMMAND_REFRESH_DELAY_SECONDS
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

pytestmark = pytest.mark.integration


def _coordinator(hass: HomeAssistant) -> AirCloudHomeDataUpdateCoordinator:
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    return AirCloudHomeDataUpdateCoordinator(hass, LOGGER, name=DOMAIN, config_entry=entry, update_interval=None)


async def _advance(hass: HomeAssistant, seconds: float) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


async def test_command_burst_triggers_one_delayed_refresh(hass: HomeAssistant) -> None:
    """Several commands in a row lead to a single refresh after the last one."""
    coordinator = _coordinator(hass)

    with patch.object(coordinator, "async_request_refresh", AsyncMock()) as refresh:
        coordinator.async_schedule_post_command_refresh()
        coordinator.async_schedule_post_command_refresh()
        await _advance(hass, POST_COMMAND_REFRESH_DELAY_SECONDS - 1)
        refresh.assert_not_awaited()

        await _advance(hass, POST_COMMAND_REFRESH_DELAY_SECONDS + 1)
        refresh.assert_awaited_once()


async def test_cancelled_refresh_does_not_run(hass: HomeAssistant) -> None:
    """Unloading the entry cancels a pending post-command refresh."""
    coordinator = _coordinator(hass)

    with patch.object(coordinator, "async_request_refresh", AsyncMock()) as refresh:
        coordinator.async_schedule_post_command_refresh()
        coordinator.async_cancel_scheduled_post_command_refresh()
        await _advance(hass, POST_COMMAND_REFRESH_DELAY_SECONDS + 1)

    refresh.assert_not_awaited()
