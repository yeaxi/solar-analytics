"""Pure config-entry migration helpers for Solar Analytics."""

from __future__ import annotations

from typing import Any

CURRENT_ENTRY_VERSION = 4
DEFAULT_TIME_ZONE = "Europe/Kyiv"
LEGACY_REST_FORECAST_ENTITY_KEY = "forecast_solar_hourly_entity"


def migrate_entry_data(entry_version: int, entry_data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Migrate legacy Solar Analytics data without changing native binding."""
    version = entry_version
    data = dict(entry_data)

    if version < 2:
        data.setdefault("time_zone", DEFAULT_TIME_ZONE)
        version = 2

    if version < CURRENT_ENTRY_VERSION:
        data.pop(LEGACY_REST_FORECAST_ENTITY_KEY, None)
        version = CURRENT_ENTRY_VERSION

    return version, data
