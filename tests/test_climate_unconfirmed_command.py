"""End-to-end test of a command the cloud never confirms, through the real coordinator."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aircloudhome.climate import air_conditioning
from custom_components.aircloudhome.const import DOMAIN
from custom_components.aircloudhome.coordinator import base as coordinator_base
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load the integration from custom_components."""


@pytest.fixture(autouse=True)
def _fast_timing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink every command delay so the scenario runs in well under a second."""
    monkeypatch.setattr(air_conditioning, "_COMMAND_DEBOUNCE_SECONDS", 0.01)
    monkeypatch.setattr(air_conditioning, "_PENDING_CONFIRMATION_RESEND_SECONDS", 0.05)
    monkeypatch.setattr(air_conditioning, "_PENDING_CONFIRMATION_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(coordinator_base, "POST_COMMAND_REFRESH_DELAY_SECONDS", 0.05)


def _reported_device() -> dict[str, Any]:
    """The state the cloud keeps reporting: unit OFF, fan AUTO."""
    return {
        "id": 116376,
        "name": "Living room",
        "power": "OFF",
        "mode": "AUTO",
        "iduTemperature": 25.0,
        "roomTemperature": 21.0,
        "fanSpeed": "AUTO",
        "fanSwing": "OFF",
        "online": True,
    }


async def _wait_until(predicate: Any, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.01)


async def test_unconfirmed_command_is_resent_then_rolled_back(hass: HomeAssistant) -> None:
    """
    Stale polls must still reach the entity.

    The coordinator uses ``always_update=False``, so a poll that returns the
    same pre-command state notifies nobody. The entity must still resend the
    command a bounded number of times and finally show the cloud state again,
    instead of keeping the unconfirmed value until the next regular poll.
    """
    client = Mock()
    client.async_get_family_groups = AsyncMock(return_value=[{"familyId": 205606}])
    client.async_get_idu_list = AsyncMock(side_effect=lambda _family_id: [_reported_device()])
    client.async_control_device = AsyncMock(return_value={"commandId": "abc"})

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret"})
    entry.add_to_hass(hass)
    with patch("custom_components.aircloudhome.AirCloudHomeApiClient", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    entity_id = next(iter(hass.states.async_entity_ids("climate")))
    assert hass.states.get(entity_id).attributes["fan_mode"] == "auto"

    await hass.services.async_call(
        "climate", "set_fan_mode", {"entity_id": entity_id, "fan_mode": "level_3"}, blocking=True
    )
    assert hass.states.get(entity_id).attributes["fan_mode"] == "level_3"

    await _wait_until(lambda: hass.states.get(entity_id).attributes["fan_mode"] == "auto")

    sent = [call.kwargs["fan_speed"] for call in client.async_control_device.await_args_list]
    assert sent == ["LV3"] * (1 + air_conditioning._PENDING_CONFIRMATION_MAX_RESENDS)  # noqa: SLF001

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
