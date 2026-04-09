"""Climate entity for aircloudhome AC devices."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from time import monotonic
from typing import Any

from custom_components.aircloudhome.coordinator import AirCloudHomeDataUpdateCoordinator
from custom_components.aircloudhome.entity import AirCloudHomeEntity
from custom_components.aircloudhome.entity_utils.climate_mappings import (
    API_FAN_SPEED_TO_HA,
    API_MODE_TO_HVAC_MODE,
    API_SWING_TO_HA,
    HA_FAN_SPEED_TO_API,
    HA_SWING_TO_API,
    HUMIDITY_MODES,
    HVAC_MODE_TO_API_MODE,
    PRESET_DRY_COOL,
)
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import PRESET_NONE, ClimateEntityFeature, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.event import async_call_later

# Climate entity description for AC units
CLIMATE_ENTITY_DESCRIPTION = EntityDescription(
    key="climate",
    name="Room Air Conditioner",
    translation_key="room_air_conditioner",
)

_OPTIMISTIC_OVERRIDE_TTL_SECONDS = 15.0
_REFRESH_DELAY_SECONDS = 5.0


class AirCloudHomeAirConditioner(ClimateEntity, AirCloudHomeEntity):
    """Climate entity for AirCloud Home AC device."""

    _base_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 16.0
    _attr_max_temp = 32.0
    _attr_target_temperature_step = 0.5
    _attr_supported_features = _base_supported_features
    _attr_hvac_modes = [
        HVACMode.HEAT,
        HVACMode.COOL,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
        HVACMode.AUTO,
        HVACMode.OFF,
    ]
    _attr_fan_modes = list(API_FAN_SPEED_TO_HA.values())
    _attr_swing_modes = list(API_SWING_TO_HA.values())
    _attr_preset_modes = [PRESET_NONE, PRESET_DRY_COOL]

    def __init__(
        self,
        coordinator: AirCloudHomeDataUpdateCoordinator,
        entity_description: EntityDescription,
        device: dict[str, Any],
    ) -> None:
        """Initialize the climate entity."""
        self._device_id = str(device["id"])
        self._last_known_device = dict(device)
        self._optimistic_overrides: dict[str, tuple[Any, float]] = {}
        self._cancel_pending_refresh: Callable[[], None] | None = None
        super().__init__(coordinator, entity_description, device_id=self._device_id)
        self._supports_humidity = False
        self._update_capabilities(self._last_known_device)

    def _clear_expired_overrides(self) -> None:
        """Drop optimistic overrides once the API has had enough time to catch up."""
        now = monotonic()
        expired_keys = [
            key for key, (_, expires_at) in self._optimistic_overrides.items() if expires_at <= now
        ]
        for key in expired_keys:
            self._optimistic_overrides.pop(key, None)

    def _merge_device_with_overrides(self, device: dict[str, Any]) -> dict[str, Any]:
        """Return the device payload with any active optimistic updates applied."""
        self._clear_expired_overrides()

        resolved_keys = [
            key for key, (value, _) in self._optimistic_overrides.items() if device.get(key) == value
        ]
        for key in resolved_keys:
            self._optimistic_overrides.pop(key, None)

        merged_device = dict(device)
        for key, (value, _) in self._optimistic_overrides.items():
            merged_device[key] = value
        return merged_device

    def _apply_optimistic_updates(self, **updates: Any) -> None:
        """Keep recently applied device values until the coordinator reflects them."""
        expires_at = monotonic() + _OPTIMISTIC_OVERRIDE_TTL_SECONDS
        for key, value in updates.items():
            if value is None:
                continue
            self._optimistic_overrides[key] = (value, expires_at)
            self._last_known_device[key] = value

    def _schedule_delayed_refresh(self) -> None:
        """Refresh after a short delay so eventual-consistency lag does not revert state."""
        if self.hass is None:
            return

        if self._cancel_pending_refresh is not None:
            self._cancel_pending_refresh()

        @callback
        def _async_handle_refresh(_: datetime) -> None:
            """Trigger a background refresh after the delay expires."""
            self._cancel_pending_refresh = None
            self.hass.async_create_task(self._async_refresh_after_command())

        self._cancel_pending_refresh = async_call_later(
            self.hass,
            _REFRESH_DELAY_SECONDS,
            _async_handle_refresh,
        )

    async def _async_refresh_after_command(self) -> None:
        """Retry refreshes while optimistic overrides are still waiting on coordinator data."""
        await self.coordinator.async_request_refresh()
        self._find_device()
        self._clear_expired_overrides()
        if self._optimistic_overrides:
            self._schedule_delayed_refresh()

    def _update_capabilities(self, device: dict[str, Any]) -> None:
        """Update optional features based on the current device payload."""
        self._supports_humidity = "humidity" in device
        self._attr_supported_features = self._base_supported_features
        if self._supports_humidity:
            self._attr_supported_features |= ClimateEntityFeature.TARGET_HUMIDITY
            self._attr_min_humidity = 40
            self._attr_max_humidity = 60

    def _find_device(self) -> dict[str, Any] | None:
        """Return the latest coordinator payload for this device."""
        devices = self.coordinator.data.get("devices", [])
        for device in devices:
            if str(device.get("id")) != self._device_id:
                continue
            resolved_device = self._merge_device_with_overrides(device)
            self._last_known_device = resolved_device
            self._update_capabilities(resolved_device)
            return resolved_device
        return None

    @property
    def _device(self) -> dict[str, Any]:
        """Return the current device data, falling back to the last known payload."""
        return self._find_device() or self._merge_device_with_overrides(self._last_known_device)

    def _get_device_info(self) -> DeviceInfo:
        """Get device information for this AC unit."""
        return DeviceInfo(
            identifiers={("aircloudhome", f"{self.coordinator.config_entry.entry_id}_{self._device['id']}")},
            name=self._device.get("name", f"AC Unit {self._device['id']}"),
            manufacturer=self._device.get("model"),
            serial_number=self._device.get("serialNumber"),
            hw_version=self._device.get("vendorThingId"),
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not super().available:
            return False

        if (device := self._find_device()) is None:
            return False

        if (online := device.get("online")) is not None:
            return bool(online)

        return any(
            key in device
            for key in ("power", "mode", "roomTemperature", "iduTemperature", "fanSpeed", "fanSwing")
        )

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        return self._device.get("roomTemperature")

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        return self._device.get("iduTemperature")

    @property
    def target_humidity(self) -> int | None:
        """Return the target humidity."""
        return self._device.get("humidity")

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode."""
        if self._device.get("power") == "OFF":
            return HVACMode.OFF

        api_mode = self._device.get("mode", "UNKNOWN")
        return API_MODE_TO_HVAC_MODE.get(api_mode, HVACMode.OFF)

    @property
    def fan_mode(self) -> str | None:
        """Return the fan mode."""
        api_speed = self._device.get("fanSpeed", "AUTO")
        return API_FAN_SPEED_TO_HA.get(api_speed, "auto")

    @property
    def swing_mode(self) -> str | None:
        """Return the swing mode."""
        api_swing = self._device.get("fanSwing", "OFF")
        return API_SWING_TO_HA.get(api_swing, "off")

    @property
    def preset_mode(self) -> str:
        """Return the current preset mode."""
        if self._device.get("power") == "ON" and self._device.get("mode") == "DRY_COOL":
            return PRESET_DRY_COOL
        return PRESET_NONE

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        # Round to nearest 0.5
        temperature = round(temperature * 2) / 2

        await self._async_update_device(idu_temperature=temperature)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target HVAC mode."""
        if hvac_mode == HVACMode.OFF:
            await self._async_update_device(power="OFF")
        else:
            api_mode = HVAC_MODE_TO_API_MODE.get(hvac_mode, "AUTO")
            await self._async_update_device(power="ON", mode=api_mode)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new fan mode."""
        api_speed = HA_FAN_SPEED_TO_API.get(fan_mode, "AUTO")
        await self._async_update_device(fan_speed=api_speed)

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set new swing mode."""
        api_swing = HA_SWING_TO_API.get(swing_mode, "OFF")
        await self._async_update_device(fan_swing=api_swing)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        if preset_mode == PRESET_DRY_COOL:
            await self._async_update_device(power="ON", mode="DRY_COOL")
        elif preset_mode == PRESET_NONE and self._device.get("mode") == "DRY_COOL":
            # Fall back to DRY when clearing the DRY_COOL preset
            await self._async_update_device(mode="DRY")

    async def async_set_humidity(self, humidity: int) -> None:
        """Set new target humidity."""
        if not self._supports_humidity:
            return

        humidity_value = max(self._attr_min_humidity, min(self._attr_max_humidity, float(humidity)))
        # Round to nearest 5
        humidity_target = int(round(humidity_value / 5) * 5)
        await self._async_update_device(humidity=humidity_target)

    async def async_turn_on(self) -> None:
        """Turn on the AC."""
        await self._async_update_device(power="ON")

    async def async_turn_off(self) -> None:
        """Turn off the AC."""
        await self._async_update_device(power="OFF")

    async def async_will_remove_from_hass(self) -> None:
        """Cancel any scheduled refresh callbacks when the entity is removed."""
        if self._cancel_pending_refresh is not None:
            self._cancel_pending_refresh()
            self._cancel_pending_refresh = None
        await super().async_will_remove_from_hass()

    async def _async_update_device(
        self,
        power: str | None = None,
        mode: str | None = None,
        fan_speed: str | None = None,
        fan_swing: str | None = None,
        idu_temperature: float | None = None,
        humidity: int | None = None,
    ) -> None:
        """Update device state through the API."""
        device = self._device

        # Use current values for parameters not being updated
        current_power = device.get("power", "ON")
        current_mode = device.get("mode", "AUTO")
        current_fan_speed = device.get("fanSpeed", "AUTO")
        current_fan_swing = device.get("fanSwing", "OFF")
        current_temp = device.get("iduTemperature", 22.0)
        # humidity is the target humidity setpoint retrieved from the device, not the measured room humidity.
        target_humidity_raw = device.get("humidity")
        target_humidity_setpoint = (
            int(round(target_humidity_raw)) if isinstance(target_humidity_raw, (int, float)) else None
        )

        effective_power = power or current_power
        effective_mode = mode or current_mode
        # humidity is only valid for DRY / DRY_COOL modes; sending it in other modes causes a 400 error
        resolved_humidity = (
            (humidity if humidity is not None else target_humidity_setpoint)
            if effective_power == "ON" and effective_mode in HUMIDITY_MODES
            else None
        )

        await self.coordinator.config_entry.runtime_data.client.async_control_device(
            rac_id=device["id"],
            family_id=device["familyId"],
            power=effective_power,
            mode=effective_mode,
            fan_speed=fan_speed or current_fan_speed,
            fan_swing=fan_swing or current_fan_swing,
            idu_temperature=idu_temperature if idu_temperature is not None else current_temp,
            humidity=resolved_humidity,
        )

        # Keep command results visible until the API reports them back.
        self._apply_optimistic_updates(
            power=power,
            mode=mode,
            fanSpeed=fan_speed,
            fanSwing=fan_swing,
            iduTemperature=idu_temperature,
            humidity=humidity,
        )

        self.async_write_ha_state()

        # Delay refreshes slightly to avoid stale API payloads reverting the UI.
        self._schedule_delayed_refresh()
