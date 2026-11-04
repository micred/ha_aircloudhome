"""Tests for how the AirCloud Home climate entity reads device state."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.aircloudhome.climate import air_conditioning
from custom_components.aircloudhome.climate.air_conditioning import (
    CLIMATE_ENTITY_DESCRIPTION,
    AirCloudHomeAirConditioner,
)
from homeassistant.components.climate.const import HVACMode
from homeassistant.const import ATTR_TEMPERATURE

pytestmark = pytest.mark.unit


class _Coordinator:
    """Minimal stand-in for AirCloudHomeDataUpdateCoordinator."""

    last_update_success = True

    def __init__(self, client: Mock, device: dict[str, Any]) -> None:
        self.config_entry = Mock(entry_id="entry-id", runtime_data=Mock(client=client))
        self.data = {"devices": [device]}
        self.async_schedule_post_command_refresh = Mock()

    def async_add_listener(self, *args: Any, **kwargs: Any) -> Mock:
        return Mock()

    def report(self, **changes: Any) -> None:
        """Replace coordinator data as a poll would, with ``changes`` applied."""
        self.data = {"devices": [_device(**changes)]}


def _device(**changes: Any) -> dict[str, Any]:
    return {
        "id": 116377,
        "familyId": 205606,
        "power": "ON",
        "mode": "COOLING",
        "iduTemperature": 22.0,
        "roomTemperature": 21.0,
        "fanSpeed": "AUTO",
        "fanSwing": "OFF",
        "online": True,
    } | changes


def _entity(hass: Any, client: Mock | None = None) -> AirCloudHomeAirConditioner:
    client = client or Mock(async_control_device=AsyncMock(return_value={}))
    device = _device()
    entity = AirCloudHomeAirConditioner(_Coordinator(client, device), CLIMATE_ENTITY_DESCRIPTION, device)
    entity.hass = hass
    entity.async_write_ha_state = Mock()
    return entity


async def _wait_until(predicate: Any, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.005)


async def test_state_follows_coordinator_data(hass: Any) -> None:
    """Changes made outside Home Assistant show up after the next poll."""
    entity = _entity(hass)
    assert entity.hvac_mode is HVACMode.COOL

    entity.coordinator.report(mode="HEATING", iduTemperature=25.0, roomTemperature=19.5)

    assert entity.hvac_mode is HVACMode.HEAT
    assert entity.target_temperature == 25.0
    assert entity.current_temperature == 19.5


@pytest.mark.parametrize(
    ("update_ok", "devices", "available"),
    [
        (True, [_device()], True),
        (True, [_device(online=False)], False),
        (True, [], False),
        (False, [_device()], False),
    ],
    ids=["online", "offline", "removed", "update-failed"],
)
async def test_availability(hass: Any, update_ok: bool, devices: list[dict[str, Any]], available: bool) -> None:
    """The entity is available only while the cloud reports the unit online."""
    entity = _entity(hass)
    entity.coordinator.last_update_success = update_ok
    entity.coordinator.data = {"devices": devices}

    assert entity.available is available


async def test_humidity_support_follows_device_payload(hass: Any) -> None:
    """Humidity control is offered only while the payload reports a target humidity."""
    entity = _entity(hass)
    assert entity.target_humidity is None

    entity.coordinator.report(humidity=50)

    assert entity.target_humidity == 50
    assert entity.supported_features & air_conditioning.ClimateEntityFeature.TARGET_HUMIDITY


async def test_commanded_value_survives_stale_refresh(hass: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale poll right after a command must not revert the new target."""
    monkeypatch.setattr(air_conditioning, "_COMMAND_DEBOUNCE_SECONDS", 0.01, raising=False)
    client = Mock(async_control_device=AsyncMock(return_value={}))
    entity = _entity(hass, client)

    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 24.0})
    await _wait_until(lambda: client.async_control_device.await_count == 1)
    entity.coordinator.async_schedule_post_command_refresh.assert_called_once()

    entity.coordinator.report(iduTemperature=22.0)  # cloud has not caught up yet
    assert entity.target_temperature == 24.0

    entity.coordinator.report(iduTemperature=24.0)  # cloud confirms the command
    assert entity.target_temperature == 24.0

    entity.coordinator.report(iduTemperature=26.0)  # later change from the remote
    assert entity.target_temperature == 26.0
