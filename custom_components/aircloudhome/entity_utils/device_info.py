"""Helpers for building Home Assistant device metadata."""

from __future__ import annotations

import re
from typing import Any

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
