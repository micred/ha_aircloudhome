"""
Sensor platform for aircloudhome.

Energy consumption sensors are optional (Options → "Enable energy monitoring")
because they add requests to the AirCloud Home energy summary API.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from custom_components.aircloudhome.const import CONF_ENABLE_ENERGY_MONITORING, DEFAULT_ENABLE_ENERGY_MONITORING
from homeassistant.const import Platform
from homeassistant.helpers import entity_registry as er

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
    """Set up energy sensors, or remove them when energy monitoring is disabled."""
    if not entry.options.get(CONF_ENABLE_ENERGY_MONITORING, DEFAULT_ENABLE_ENERGY_MONITORING):
        registry = er.async_get(hass)
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.domain == Platform.SENSOR:
                registry.async_remove(entity.entity_id)
        return

    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        AirCloudHomeEnergyConsumptionSensor(
            coordinator=coordinator,
            entity_description=ENERGY_SENSOR_DESCRIPTION,
            device=device,
        )
        for device in coordinator.data.get("devices", [])
    )
