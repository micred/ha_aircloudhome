"""Climate entity for aircloudhome AC devices."""

from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any

from custom_components.aircloudhome.const import DOMAIN
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
from custom_components.aircloudhome.entity_utils.device_info import build_rac_device_info
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import PRESET_NONE, ClimateEntityFeature, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription

# Climate entity description for AC units
CLIMATE_ENTITY_DESCRIPTION = EntityDescription(
    key="climate",
    name="Room Air Conditioner",
    translation_key="room_air_conditioner",
)

_OPTIMISTIC_OVERRIDE_TTL_SECONDS = 15.0
_COMMAND_DEBOUNCE_SECONDS = 0.3


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
        self._last_reported_device = dict(device)
        self._last_known_device = dict(device)
        self._optimistic_overrides: dict[str, tuple[Any, float]] = {}
        self._command_worker_task: asyncio.Task[None] | None = None
        self._pending_command_generation = 0
        self._completed_command_generation = 0
        self._command_waiters: dict[int, list[asyncio.Future[None]]] = {}
        super().__init__(coordinator, entity_description, device_id=self._device_id)
        self._supports_humidity = False
        self._update_capabilities(self._last_known_device)

    def _clear_expired_overrides(self) -> None:
        """Drop optimistic overrides once the API has had enough time to catch up."""
        now = monotonic()
        expired_keys = [key for key, (_, expires_at) in self._optimistic_overrides.items() if expires_at <= now]
        for key in expired_keys:
            self._optimistic_overrides.pop(key, None)

    def _restore_last_reported_device(self) -> None:
        """Restore the last device payload received from the coordinator."""
        self._optimistic_overrides.clear()
        self._last_known_device = dict(self._last_reported_device)
        self._update_capabilities(self._last_known_device)

    def _merge_device_with_overrides(self, device: dict[str, Any]) -> dict[str, Any]:
        """Return the device payload with any active optimistic updates applied."""
        self._clear_expired_overrides()

        resolved_keys = [key for key, (value, _) in self._optimistic_overrides.items() if device.get(key) == value]
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
            self._last_reported_device = dict(device)
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
        return build_rac_device_info(
            DOMAIN,
            self.coordinator.config_entry.entry_id,
            self._device,
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
            key in device for key in ("power", "mode", "roomTemperature", "iduTemperature", "fanSpeed", "fanSwing")
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
        """Allow any queued device command to finish before removal."""
        if self._command_worker_task is not None and not self._command_worker_task.done():
            await self._command_worker_task
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
        """Apply optimistic update and schedule a debounced API command."""
        # Show state change immediately without waiting for the API round-trip.
        self._apply_optimistic_updates(
            power=power,
            mode=mode,
            fanSpeed=fan_speed,
            fanSwing=fan_swing,
            iduTemperature=idu_temperature,
            humidity=humidity,
        )
        self.async_write_ha_state()

        generation = self._pending_command_generation + 1
        self._pending_command_generation = generation
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._command_waiters.setdefault(generation, []).append(waiter)
        self._async_ensure_command_worker()
        await waiter

    def _async_ensure_command_worker(self) -> None:
        """Ensure a background task exists to debounce and send device commands."""
        if self.hass is None:
            return

        if self._command_worker_task is None or self._command_worker_task.done():
            self._command_worker_task = self.hass.async_create_task(self._async_command_worker())

    async def _async_command_worker(self) -> None:
        """Batch rapid state changes into the minimum number of API writes."""
        try:
            while self._completed_command_generation < self._pending_command_generation:
                generation = self._pending_command_generation
                await asyncio.sleep(_COMMAND_DEBOUNCE_SECONDS)
                if generation != self._pending_command_generation:
                    continue

                await self._async_flush_command()
                self._completed_command_generation = generation
                self._async_resolve_command_waiters(generation)
        except Exception as exception:  # noqa: BLE001 - queued callers must receive any command failure
            self._completed_command_generation = self._pending_command_generation
            self._restore_last_reported_device()
            self.async_write_ha_state()
            self._async_fail_command_waiters(exception)
        finally:
            self._command_worker_task = None
            if self._completed_command_generation < self._pending_command_generation:
                self._async_ensure_command_worker()

    def _async_resolve_command_waiters(self, generation: int) -> None:
        """Resolve callers whose updates were included in a successful API write."""
        generations = [key for key in self._command_waiters if key <= generation]
        for resolved_generation in generations:
            for waiter in self._command_waiters.pop(resolved_generation, []):
                if not waiter.done():
                    waiter.set_result(None)

    def _async_fail_command_waiters(self, exception: Exception) -> None:
        """Fail every queued caller when the merged API command fails."""
        for generation in list(self._command_waiters):
            for waiter in self._command_waiters.pop(generation, []):
                if not waiter.done():
                    waiter.set_exception(exception)

    async def _async_flush_command(self) -> None:
        """Send the merged optimistic state to the API."""
        device = self._device

        effective_power = device.get("power", "ON")
        effective_mode = device.get("mode", "AUTO")
        # humidity is only valid for DRY / DRY_COOL modes; sending it in other modes causes a 400 error
        device_humidity = device.get("humidity")
        resolved_humidity = (
            (int(round(device_humidity)) if isinstance(device_humidity, (int, float)) else None)
            if effective_power == "ON" and effective_mode in HUMIDITY_MODES
            else None
        )

        await self.coordinator.config_entry.runtime_data.client.async_control_device(
            rac_id=device["id"],
            family_id=device["familyId"],
            power=effective_power,
            mode=effective_mode,
            fan_speed=device.get("fanSpeed", "AUTO"),
            fan_swing=device.get("fanSwing", "OFF"),
            idu_temperature=device.get("iduTemperature", 22.0),
            humidity=resolved_humidity,
        )

        # Delay refreshes slightly to avoid stale API payloads reverting the UI,
        # but collapse rapid command bursts into one coordinator refresh.
        self.coordinator.async_schedule_post_command_refresh()
