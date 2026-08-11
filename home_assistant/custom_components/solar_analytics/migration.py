"""Pure config-entry migration helpers for Solar Analytics.

The migrator is deliberately dependency-free (no Home Assistant imports) so
that it can be unit-tested locally. ``async_migrate_entry`` in
``__init__.py`` wires it up against the real ``ConfigEntry`` at runtime.
"""

from __future__ import annotations

from typing import Any

CURRENT_ENTRY_VERSION = 5
DEFAULT_TIME_ZONE = "Europe/Kyiv"
DEFAULT_MORNING_HOUR = 6
DEFAULT_DAY_AHEAD_HOUR = 23
SUPPORTED_ENTRY_FIELDS = frozenset(
    {
        "native_forecast_entry_id",
        "time_zone",
        "actual_power_entity",
        "actual_energy_today_entity",
        "morning_snapshot_hour",
        "day_ahead_snapshot_hour",
    }
)


def migrate_entry_data(entry_version: int, entry_data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Return the migrated (version, data) for a config entry.

    Migration rules:

    - Fields not in ``SUPPORTED_ENTRY_FIELDS`` are dropped (removes stale
      keys from earlier schemas without leaking them into runtime).
    - Older entries without ``time_zone`` receive the historical Kyiv
      default; new installs write ``hass.config.time_zone`` via the
      config-flow, so this default only affects pre-v2 migrations.
    - Older entries without the snapshot-hour fields receive the historical
      06:00 / 23:00 defaults.
    - The returned version is bumped to :data:`CURRENT_ENTRY_VERSION`.
    """

    version = entry_version
    data = {key: value for key, value in dict(entry_data).items() if key in SUPPORTED_ENTRY_FIELDS}

    if version < 2:
        data.setdefault("time_zone", DEFAULT_TIME_ZONE)

    if version < 5:
        data.setdefault("morning_snapshot_hour", DEFAULT_MORNING_HOUR)
        data.setdefault("day_ahead_snapshot_hour", DEFAULT_DAY_AHEAD_HOUR)

    if version < CURRENT_ENTRY_VERSION:
        version = CURRENT_ENTRY_VERSION

    return version, data
