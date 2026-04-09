"""Energy consumption sensor for aircloudhome RAC devices."""

from __future__ import annotations

from typing import Any

from custom_components.aircloudhome.const import DOMAIN
from custom_components.aircloudhome.coordinator import AirCloudHomeDataUpdateCoordinator
from custom_components.aircloudhome.entity import AirCloudHomeEntity
from custom_components.aircloudhome.entity_utils.device_info import build_rac_device_info
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.helpers.device_registry import DeviceInfo

ENERGY_SENSOR_DESCRIPTION = SensorEntityDescription(
    key="energy_consumption",
    name="Energy Consumed",
    device_class=SensorDeviceClass.ENERGY,
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    state_class=SensorStateClass.TOTAL,
    suggested_display_precision=3,
)


class AirCloudHomeEnergyConsumptionSensor(SensorEntity, AirCloudHomeEntity):
    """Expose cumulative energy consumption for a single RAC unit."""

    def __init__(
        self,
        coordinator: AirCloudHomeDataUpdateCoordinator,
        entity_description: SensorEntityDescription,
        device: dict[str, Any],
    ) -> None:
        """Initialize the energy sensor."""
        self._device_id = str(device["id"])
        self._device_id_int = int(device["id"])
        self._last_known_device = dict(device)
        super().__init__(coordinator, entity_description, device_id=self._device_id)

    def _find_device(self) -> dict[str, Any] | None:
        """Return the latest coordinator payload for this device."""
        devices = self.coordinator.data.get("devices", [])
        for device in devices:
            if str(device.get("id")) != self._device_id:
                continue
            self._last_known_device = dict(device)
            return self._last_known_device
        return None

    @property
    def _device(self) -> dict[str, Any]:
        """Return the latest device payload, falling back to cached data."""
        return self._find_device() or self._last_known_device

    def _get_energy_summary(self) -> dict[str, Any] | None:
        """Return the normalized energy summary for this RAC."""
        energy_by_rac_id = self.coordinator.data.get("energy_by_rac_id", {})
        if not isinstance(energy_by_rac_id, dict):
            return None

        summary = energy_by_rac_id.get(self._device_id_int)
        return summary if isinstance(summary, dict) else None

    def _get_device_info(self) -> DeviceInfo:
        """Return device information for this RAC."""
        return build_rac_device_info(
            DOMAIN,
            self.coordinator.config_entry.entry_id,
            self._device,
        )

    @property
    def available(self) -> bool:
        """Return if the entity is available."""
        if not super().available:
            return False

        if self._find_device() is None:
            return False

        return self._get_energy_summary() is not None

    @property
    def native_value(self) -> float | None:
        """Return the cumulative energy consumed by this RAC."""
        if (summary := self._get_energy_summary()) is None:
            return None

        energy_consumed = summary.get("energyConsumed")
        if not isinstance(energy_consumed, (int, float)) or isinstance(energy_consumed, bool):
            return None

        return round(float(energy_consumed), 3)
