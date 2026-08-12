"""Constants for the read-only Solar Analytics integration.

Version identifiers and the manufacturer string are derived from
``manifest.json`` at import time so the integration exposes exactly one
authoritative value for each field. This prevents the ``manifest.version`` /
``const.VERSION`` / ``DeviceInfo.sw_version`` / ``NATIVE_ADAPTER_VERSION``
drift that used to make bumps a four-touch exercise.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final, cast

_MANIFEST_PATH: Final = Path(__file__).with_name("manifest.json")


def _load_manifest() -> dict[str, Any]:
    """Return the parsed manifest.json contents.

    Manifest reads happen exactly once at import time. HA calls
    ``async_get_integration`` for the same data at runtime; both paths must
    agree on the version, which is why we keep a single on-disk source.
    """

    with _MANIFEST_PATH.open("r", encoding="utf-8") as handle:
        return cast(dict[str, Any], json.load(handle))


_MANIFEST: Final = _load_manifest()

DOMAIN: Final[str] = str(_MANIFEST["domain"])
NAME: Final[str] = str(_MANIFEST["name"])
VERSION: Final[str] = str(_MANIFEST["version"])
MANUFACTURER: Final[str] = "Solar Analytics"

CONF_ACTUAL_POWER: Final = "actual_power_entity"
CONF_ACTUAL_ENERGY_TODAY: Final = "actual_energy_today_entity"
CONF_NATIVE_FORECAST_ENTRY_ID: Final = "native_forecast_entry_id"
CONF_TIME_ZONE: Final = "time_zone"
CONF_MORNING_HOUR: Final = "morning_snapshot_hour"
CONF_DAY_AHEAD_HOUR: Final = "day_ahead_snapshot_hour"

DEFAULT_MORNING_HOUR: Final[int] = 6
DEFAULT_DAY_AHEAD_HOUR: Final[int] = 23

SNAPSHOT_DAY_AHEAD: Final[str] = "day_ahead"
SNAPSHOT_MORNING: Final[str] = "morning"
