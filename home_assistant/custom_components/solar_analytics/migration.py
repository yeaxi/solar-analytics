"""Pure config-entry migration helpers for Solar Analytics."""

from __future__ import annotations

from typing import Any

CURRENT_ENTRY_VERSION = 4
DEFAULT_TIME_ZONE = "Europe/Kyiv"
SUPPORTED_ENTRY_FIELDS = frozenset(
    {
        "native_forecast_entry_id",
        "time_zone",
        "actual_power_entity",
        "actual_energy_today_entity",
    }
)


def migrate_entry_data(entry_version: int, entry_data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Migrate an entry while retaining only fields used by the native contract."""
    version = entry_version
    data = {key: value for key, value in dict(entry_data).items() if key in SUPPORTED_ENTRY_FIELDS}

    if version < 2:
        data.setdefault("time_zone", DEFAULT_TIME_ZONE)

    if version < CURRENT_ENTRY_VERSION:
        version = CURRENT_ENTRY_VERSION

    return version, data
