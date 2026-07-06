"""Tests for AirCloudHome climate command queuing."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import Mock

import aiohttp
import pytest
from yarl import URL

from custom_components.aircloudhome.api import AirCloudHomeApiClientCommunicationError
from custom_components.aircloudhome.climate import air_conditioning
from custom_components.aircloudhome.climate.air_conditioning import (
    CLIMATE_ENTITY_DESCRIPTION,
    AirCloudHomeAirConditioner,
)
from homeassistant.const import ATTR_TEMPERATURE

pytestmark = pytest.mark.unit


class _RuntimeData:
    def __init__(self, client: Mock) -> None:
        self.client = client


class _ConfigEntry:
    entry_id = "entry-id"
    domain = "aircloudhome"

    def __init__(self, client: Mock) -> None:
        self.runtime_data = _RuntimeData(client)


class _Coordinator:
    last_update_success = True

    def __init__(self, client: Mock, device: dict[str, Any]) -> None:
        self.config_entry = _ConfigEntry(client)
        self.data = {"devices": [device]}
        self.async_schedule_post_command_refresh = Mock()

    def async_add_listener(self, *args: Any, **kwargs: Any) -> Mock:
        return Mock()


def _device(power: str = "ON") -> dict[str, Any]:
    return {
        "id": 116377,
        "familyId": 205606,
        "power": power,
        "mode": "COOLING",
        "iduTemperature": 22.0,
        "roomTemperature": 21.0,
        "fanSpeed": "AUTO",
        "fanSwing": "OFF",
        "online": True,
    }


def _entity(client: Mock, hass: Any, device: dict[str, Any] | None = None) -> AirCloudHomeAirConditioner:
    device = device or _device()
    entity = AirCloudHomeAirConditioner(_Coordinator(client, device), CLIMATE_ENTITY_DESCRIPTION, device)
    entity.hass = hass
    entity.async_write_ha_state = Mock()
    return entity


async def _wait_for_tasks() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def _wait_until(predicate: Any, timeout: float = 1.0) -> None:
    """Wait until a test predicate is true."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("Timed out waiting for predicate")
        await asyncio.sleep(0.005)


def _set_reported_temperature(entity: AirCloudHomeAirConditioner, temperature: float) -> None:
    """Update the coordinator payload as if the API reported a target temperature."""
    device = _device()
    device["iduTemperature"] = temperature
    entity.coordinator.data = {"devices": [device]}


@pytest.fixture(autouse=True)
def fast_command_timing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep timing-based command tests fast."""
    monkeypatch.setattr(air_conditioning, "_COMMAND_DEBOUNCE_SECONDS", 0.01)
    monkeypatch.setattr(air_conditioning, "_COMMAND_RETRY_TIMEOUT_SECONDS", 0.05, raising=False)
    monkeypatch.setattr(air_conditioning, "_COMMAND_RETRY_BACKOFF_SECONDS", (0.01,), raising=False)
    monkeypatch.setattr(air_conditioning, "_PENDING_CONFIRMATION_TIMEOUT_SECONDS", 0.2, raising=False)
    monkeypatch.setattr(air_conditioning, "_PENDING_CONFIRMATION_RESEND_SECONDS", 1.0, raising=False)
    monkeypatch.setattr(air_conditioning, "_PENDING_CONFIRMATION_MAX_RESENDS", 2, raising=False)


async def test_temperature_service_returns_before_cloud_command_completes(hass: Any) -> None:
    """Service call should update HA state without waiting for the cloud API."""
    command_started = asyncio.Event()
    release_command = asyncio.Event()

    async def async_control_device(**kwargs: Any) -> dict[str, Any]:
        command_started.set()
        await release_command.wait()
        return {}

    client = Mock()
    client.async_control_device = async_control_device
    entity = _entity(client, hass)

    await asyncio.wait_for(entity.async_set_temperature(**{ATTR_TEMPERATURE: 23.0}), timeout=0.01)

    assert entity.target_temperature == 23.0
    entity.async_write_ha_state.assert_called()

    await asyncio.wait_for(command_started.wait(), timeout=1)
    release_command.set()
    await _wait_for_tasks()


async def test_rapid_temperature_changes_send_one_final_cloud_command(hass: Any) -> None:
    """Rapid UI changes should collapse to one API command with the final value."""
    calls: list[dict[str, Any]] = []

    async def async_control_device(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {}

    client = Mock()
    client.async_control_device = async_control_device
    entity = _entity(client, hass)

    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 22.5})
    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 23.0})
    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 23.5})

    await asyncio.sleep(0.05)

    assert [call["idu_temperature"] for call in calls] == [23.5]
    entity.coordinator.async_schedule_post_command_refresh.assert_called_once()


async def test_temperature_change_does_not_send_power_off_from_stale_snapshot(hass: Any) -> None:
    """Temperature-only service calls should not accidentally turn the AC off."""
    calls: list[dict[str, Any]] = []

    async def async_control_device(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {}

    client = Mock()
    client.async_control_device = async_control_device
    entity = _entity(client, hass, _device(power="OFF"))

    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 23.0})
    await asyncio.sleep(0.05)

    assert calls[0]["power"] == "ON"
    assert calls[0]["idu_temperature"] == 23.0


async def test_429_retries_in_background_without_service_error(hass: Any) -> None:
    """A 429-like failure should be retried after the service call returns."""
    calls = 0

    async def async_control_device(**kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            request_info = aiohttp.RequestInfo(
                url=URL(
                    "https://api-global-prod.aircloudhome.com/rac/basic-idu-control/general-control-command/116377"
                ),
                method="PUT",
                headers={},
                real_url=URL(
                    "https://api-global-prod.aircloudhome.com/rac/basic-idu-control/general-control-command/116377"
                ),
            )
            raise aiohttp.ClientResponseError(request_info, (), status=429, message="Too Many Requests")
        return {}

    client = Mock()
    client.async_control_device = async_control_device
    entity = _entity(client, hass)

    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 24.0})
    await asyncio.sleep(0.05)

    assert calls == 2
    assert entity.target_temperature == 24.0
    entity.coordinator.async_schedule_post_command_refresh.assert_called_once()


async def test_newer_command_supersedes_retrying_command(hass: Any) -> None:
    """A newer desired state should replace an older command while it is retrying."""
    calls: list[float] = []

    async def async_control_device(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["idu_temperature"])
        if len(calls) == 1:
            raise aiohttp.ClientResponseError(
                aiohttp.RequestInfo(
                    url=URL("https://example.test"), method="PUT", headers={}, real_url=URL("https://example.test")
                ),
                (),
                status=429,
                message="Too Many Requests",
            )
        return {}

    client = Mock()
    client.async_control_device = async_control_device
    entity = _entity(client, hass)

    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 23.0})
    await asyncio.sleep(0.02)
    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 25.0})
    await asyncio.sleep(0.05)

    assert calls == [23.0, 25.0]
    assert entity.target_temperature == 25.0


async def test_retry_timeout_restores_last_reported_state(hass: Any) -> None:
    """Retry exhaustion should roll back optimistic state to the last API payload."""

    async def async_control_device(**kwargs: Any) -> dict[str, Any]:
        raise aiohttp.ClientResponseError(
            aiohttp.RequestInfo(
                url=URL("https://example.test"), method="PUT", headers={}, real_url=URL("https://example.test")
            ),
            (),
            status=429,
            message="Too Many Requests",
        )

    client = Mock()
    client.async_control_device = async_control_device
    entity = _entity(client, hass)

    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 24.0})
    await asyncio.sleep(0.12)

    assert entity.target_temperature == 22.0


async def test_transport_disconnect_after_429_keeps_retrying(hass: Any) -> None:
    """The cloud may disconnect while a previous command is still finishing."""
    calls = 0

    async def async_control_device(**kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise aiohttp.ClientResponseError(
                aiohttp.RequestInfo(
                    url=URL("https://example.test"), method="PUT", headers={}, real_url=URL("https://example.test")
                ),
                (),
                status=429,
                message="Too Many Requests",
            )
        if calls == 2:
            msg = "Error fetching information - Server disconnected"
            raise AirCloudHomeApiClientCommunicationError(msg)
        return {}

    client = Mock()
    client.async_control_device = async_control_device
    entity = _entity(client, hass)

    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 24.0})
    await asyncio.sleep(0.08)

    assert calls == 3
    assert entity.target_temperature == 24.0
    entity.coordinator.async_schedule_post_command_refresh.assert_called_once()


async def test_stale_refresh_keeps_latest_temperature_pending_confirmation(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stale coordinator data must not expire an accepted command before confirmation."""
    monkeypatch.setattr(air_conditioning, "_OPTIMISTIC_OVERRIDE_TTL_SECONDS", 0.05)
    calls: list[dict[str, Any]] = []

    async def async_control_device(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {}

    client = Mock()
    client.async_control_device = async_control_device
    entity = _entity(client, hass)

    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 23.0})
    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 24.0})
    await _wait_until(lambda: len(calls) == 1)

    _set_reported_temperature(entity, 22.0)
    await asyncio.sleep(0.06)

    assert calls[0]["idu_temperature"] == 24.0
    assert entity.target_temperature == 24.0


async def test_matching_refresh_clears_pending_temperature_confirmation(hass: Any) -> None:
    """A matching coordinator payload confirms the accepted desired state."""
    calls: list[dict[str, Any]] = []

    async def async_control_device(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {}

    client = Mock()
    client.async_control_device = async_control_device
    entity = _entity(client, hass)

    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 24.0})
    await _wait_until(lambda: len(calls) == 1)

    _set_reported_temperature(entity, 24.0)
    await asyncio.sleep(0.04)

    assert entity.target_temperature == 24.0
    assert len(calls) == 1


async def test_stale_refresh_resends_latest_pending_temperature(hass: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale reported value should resend the latest accepted desired command."""
    monkeypatch.setattr(air_conditioning, "_PENDING_CONFIRMATION_RESEND_SECONDS", 0.02, raising=False)
    calls: list[dict[str, Any]] = []

    async def async_control_device(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {}

    client = Mock()
    client.async_control_device = async_control_device
    entity = _entity(client, hass)

    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 23.0})
    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 24.0})
    await _wait_until(lambda: len(calls) == 1)

    _set_reported_temperature(entity, 22.0)
    assert entity.target_temperature == 24.0
    await _wait_until(lambda: len(calls) == 2)

    assert [call["idu_temperature"] for call in calls] == [24.0, 24.0]


async def test_confirmation_timeout_rolls_back_to_reported_temperature(
    hass: Any, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A never-confirmed command should roll back to the reported cloud state with a warning."""
    monkeypatch.setattr(air_conditioning, "_PENDING_CONFIRMATION_TIMEOUT_SECONDS", 0.03, raising=False)
    monkeypatch.setattr(air_conditioning, "_PENDING_CONFIRMATION_RESEND_SECONDS", 1.0, raising=False)
    calls: list[dict[str, Any]] = []

    async def async_control_device(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {}

    client = Mock()
    client.async_control_device = async_control_device
    entity = _entity(client, hass)

    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 24.0})
    await _wait_until(lambda: len(calls) == 1)

    _set_reported_temperature(entity, 22.0)
    await asyncio.sleep(0.04)

    with caplog.at_level(logging.WARNING, logger=air_conditioning.LOGGER.name):
        assert entity.target_temperature == 22.0

    assert "confirmation timed out" in caplog.text
    assert entity.async_write_ha_state.call_count >= 2
