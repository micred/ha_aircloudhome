"""Helpers for building Home Assistant device metadata."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo

_PLACEHOLDER_SERIAL_PATTERN = re.compile(r"^[Xx]+(?:[-\s][Xx]+)*$")


def normalize_serial_number(value: Any) -> str | None:
    """Return a usable serial number or ``None`` for placeholder values."""
    if not isinstance(value, str):
        return None

    serial_number = value.strip()
    if not serial_number:
        return None

    if _PLACEHOLDER_SERIAL_PATTERN.fullmatch(serial_number):
        return None

    return serial_number


def build_rac_device_info(
    domain: str,
    entry_id: str,
    device: Mapping[str, Any],
) -> DeviceInfo:
    """Build Home Assistant device metadata for an RAC unit."""
    device_id = device.get("id", "unknown")
    return DeviceInfo(
        identifiers={(domain, f"{entry_id}_{device_id}")},
        name=device.get("name", f"AC Unit {device_id}"),
        manufacturer=device.get("model"),
        serial_number=normalize_serial_number(device.get("serialNumber")),
        hw_version=device.get("vendorThingId"),
    )
