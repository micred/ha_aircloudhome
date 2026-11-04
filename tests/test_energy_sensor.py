"""Tests for the optional energy consumption sensors."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aircloudhome.const import CONF_ENABLE_ENERGY_MONITORING, CONF_REGION, DOMAIN
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load the integration from custom_components."""


@pytest.fixture
def client() -> Iterator[MagicMock]:
    """Replace the API client with one AC unit that consumed 12.345 kWh."""
    client = MagicMock()
    client.async_get_family_groups = AsyncMock(return_value=[{"familyId": 205606}])
    client.async_get_idu_list = AsyncMock(
        return_value=[{"id": 116377, "name": "Living room", "power": "OFF", "mode": "COOLING", "online": True}]
    )
    client.async_get_energy_consumption = AsyncMock(return_value={116377: 12.345})
    with patch("custom_components.aircloudhome.AirCloudHomeApiClient", return_value=client):
        yield client


async def _setup(hass: HomeAssistant, *, energy: bool) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user-example-com",
        data={CONF_REGION: "global", CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret"},
        options={CONF_ENABLE_ENERGY_MONITORING: energy},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _sensors(hass: HomeAssistant, entry: MockConfigEntry) -> list[er.RegistryEntry]:
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    return [entity for entity in entities if entity.domain == Platform.SENSOR]


async def test_energy_sensor_attached_to_ac_device(hass: HomeAssistant, client: MagicMock) -> None:
    """With the option on, each unit gets an energy sensor on its climate device."""
    entry = await _setup(hass, energy=True)

    (sensor,) = _sensors(hass, entry)
    state = hass.states.get(sensor.entity_id)
    assert state is not None
    assert float(state.state) == pytest.approx(12.345)
    assert state.attributes["unit_of_measurement"] == "kWh"
    assert state.attributes["device_class"] == "energy"

    device = dr.async_get(hass).async_get(sensor.device_id)
    assert device is not None
    assert device.name == "Living room"
    climate = hass.states.async_entity_ids("climate")
    assert er.async_get(hass).async_get(climate[0]).device_id == sensor.device_id


async def test_disabled_option_creates_no_sensors_or_requests(hass: HomeAssistant, client: MagicMock) -> None:
    """With the option off (default) there are no sensors and no energy requests."""
    entry = await _setup(hass, energy=False)

    assert _sensors(hass, entry) == []
    client.async_get_energy_consumption.assert_not_awaited()


async def test_disabling_option_removes_existing_sensors(hass: HomeAssistant, client: MagicMock) -> None:
    """Turning the option off removes the sensors instead of leaving them orphaned."""
    entry = await _setup(hass, energy=True)
    assert len(_sensors(hass, entry)) == 1

    hass.config_entries.async_update_entry(entry, options={CONF_ENABLE_ENERGY_MONITORING: False})
    await hass.async_block_till_done()

    assert _sensors(hass, entry) == []
