"""Tests for the optional energy polling of the AirCloud Home coordinator."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from freezegun.api import FrozenDateTimeFactory
import pytest

from custom_components.aircloudhome.api import AirCloudHomeApiClientAuthenticationError, AirCloudHomeApiClientError
from custom_components.aircloudhome.const import CONF_ENABLE_ENERGY_MONITORING, ENERGY_MONITORING_START_DATE
from custom_components.aircloudhome.coordinator import AirCloudHomeDataUpdateCoordinator
from custom_components.aircloudhome.coordinator.base import ENERGY_REFRESH_INTERVAL
from homeassistant.exceptions import ConfigEntryAuthFailed

pytestmark = pytest.mark.unit


def _coordinator(client: MagicMock, *, energy: bool = False) -> AirCloudHomeDataUpdateCoordinator:
    """Build a coordinator bound to ``client`` without a running Home Assistant."""
    coordinator = AirCloudHomeDataUpdateCoordinator.__new__(AirCloudHomeDataUpdateCoordinator)
    coordinator.config_entry = SimpleNamespace(  # type: ignore[assignment]
        runtime_data=SimpleNamespace(client=client),
        options={CONF_ENABLE_ENERGY_MONITORING: energy},
    )
    return coordinator


def _client(family_groups: list[dict[str, Any]]) -> MagicMock:
    client = MagicMock()
    client.async_get_family_groups = AsyncMock(return_value=family_groups)
    client.async_get_idu_list = AsyncMock(side_effect=lambda family_id: [{"id": family_id * 10}])
    client.async_get_energy_consumption = AsyncMock(side_effect=lambda family_id, *_: {family_id * 10: 1.5})
    return client


async def test_energy_is_not_fetched_when_disabled() -> None:
    """Energy monitoring is opt-in and costs no requests when disabled."""
    client = _client([{"familyId": 1}])

    data = await _coordinator(client)._async_update_data()  # noqa: SLF001

    assert data["energy"] == {}
    client.async_get_energy_consumption.assert_not_awaited()


async def test_energy_is_fetched_for_every_family(freezer: FrozenDateTimeFactory) -> None:
    """Consumption since the fixed start date is fetched for each family group."""
    freezer.move_to("2026-11-04 12:00:00+00:00")
    client = _client([{"familyId": 1}, {"familyId": 2}])

    data = await _coordinator(client, energy=True)._async_update_data()  # noqa: SLF001

    assert data["energy"] == {10: 1.5, 20: 1.5}
    assert client.async_get_energy_consumption.await_args.args == (2, ENERGY_MONITORING_START_DATE, date(2026, 11, 4))


async def test_energy_is_refreshed_at_most_hourly(freezer: FrozenDateTimeFactory) -> None:
    """Device polls between energy refreshes reuse the cached consumption."""
    client = _client([{"familyId": 1}])
    coordinator = _coordinator(client, energy=True)

    await coordinator._async_update_data()  # noqa: SLF001
    freezer.tick(ENERGY_REFRESH_INTERVAL - timedelta(minutes=1))
    data = await coordinator._async_update_data()  # noqa: SLF001
    assert data["energy"] == {10: 1.5}
    assert client.async_get_energy_consumption.await_count == 1

    freezer.tick(timedelta(minutes=1))
    await coordinator._async_update_data()  # noqa: SLF001
    assert client.async_get_energy_consumption.await_count == 2


async def test_energy_failure_keeps_devices_and_previous_values(freezer: FrozenDateTimeFactory) -> None:
    """An energy API error never fails the device update and backs off for an hour."""
    client = _client([{"familyId": 1}])
    coordinator = _coordinator(client, energy=True)
    await coordinator._async_update_data()  # noqa: SLF001

    client.async_get_energy_consumption.side_effect = AirCloudHomeApiClientError("boom")
    freezer.tick(ENERGY_REFRESH_INTERVAL)
    data = await coordinator._async_update_data()  # noqa: SLF001
    assert data == {"devices": [{"id": 10, "familyId": 1}], "energy": {10: 1.5}}

    await coordinator._async_update_data()  # noqa: SLF001
    assert client.async_get_energy_consumption.await_count == 2


async def test_energy_authentication_error_triggers_reauth() -> None:
    """Authentication errors from the energy endpoint still start reauthentication."""
    client = _client([{"familyId": 1}])
    client.async_get_energy_consumption.side_effect = AirCloudHomeApiClientAuthenticationError("expired")

    with pytest.raises(ConfigEntryAuthFailed):
        await _coordinator(client, energy=True)._async_update_data()  # noqa: SLF001
