"""Sensor platform for aircloudhome."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .energy_consumption import ENERGY_SENSOR_DESCRIPTION, AirCloudHomeEnergyConsumptionSensor

if TYPE_CHECKING:
    from custom_components.aircloudhome.data import AirCloudHomeConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AirCloudHomeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinator = entry.runtime_data.coordinator
    devices = coordinator.data.get("devices", [])

    async_add_entities(
        AirCloudHomeEnergyConsumptionSensor(
            coordinator=coordinator,
            entity_description=ENERGY_SENSOR_DESCRIPTION,
            device=device,
        )
        for device in devices
    )
