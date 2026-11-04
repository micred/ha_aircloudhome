"""Energy consumption sensor for aircloudhome AC devices."""

from __future__ import annotations

from typing import Any

from custom_components.aircloudhome.const import DOMAIN
from custom_components.aircloudhome.coordinator import AirCloudHomeDataUpdateCoordinator
from custom_components.aircloudhome.entity import AirCloudHomeEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import UnitOfEnergy
from homeassistant.helpers.device_registry import DeviceInfo

ENERGY_SENSOR_DESCRIPTION = SensorEntityDescription(
    key="energy_consumption",
    translation_key="energy_consumption",
    device_class=SensorDeviceClass.ENERGY,
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    # Cumulative since ENERGY_MONITORING_START_DATE. TOTAL (rather than
    # TOTAL_INCREASING) tolerates small downward corrections by the cloud
    # without being mistaken for a meter reset.
    state_class=SensorStateClass.TOTAL,
    suggested_display_precision=3,
)


class AirCloudHomeEnergyConsumptionSensor(SensorEntity, AirCloudHomeEntity):
    """Energy consumed by one AC unit since ``ENERGY_MONITORING_START_DATE``."""

    def __init__(
        self,
        coordinator: AirCloudHomeDataUpdateCoordinator,
        entity_description: SensorEntityDescription,
        device: dict[str, Any],
    ) -> None:
        """Initialize the energy sensor for ``device``."""
        self._device_id = int(device["id"])
        self._device_name: str | None = device.get("name")
        super().__init__(coordinator, entity_description, device_id=str(self._device_id))

    def _get_device_info(self) -> DeviceInfo:
        """Attach the sensor to the AC device created by the climate entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.coordinator.config_entry.entry_id}_{self._device_id}")},
            name=self._device_name,
        )

    @property
    def available(self) -> bool:
        """Return whether the cloud reported consumption for this unit."""
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> float | None:
        """Return the energy consumed in kWh."""
        return self.coordinator.data.get("energy", {}).get(self._device_id)
