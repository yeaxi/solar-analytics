"""Constants for the read-only Solar Analytics v2 integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "solar_analytics"
NAME: Final = "Solar Analytics"
VERSION: Final = "2.0.0"
MANUFACTURER: Final = "Hermes Agent by Nous Research"

CONF_ACTUAL_POWER: Final = "actual_power_entity"
CONF_ACTUAL_ENERGY_TODAY: Final = "actual_energy_today_entity"
CONF_NATIVE_FORECAST_ENTRY_ID: Final = "native_forecast_entry_id"
CONF_TIME_ZONE: Final = "time_zone"

# Exact IDs verified as the Energy Dashboard solar inputs. There is no fallback.
DEFAULT_ENTITIES: Final[dict[str, str]] = {
    CONF_ACTUAL_POWER: "sensor.garage_cerbo_gx_pv_power",
    CONF_ACTUAL_ENERGY_TODAY: "sensor.garage_cerbo_gx_pv_energy",
}

DEFAULT_TIME_ZONE: Final[str] = "Europe/Kyiv"
SNAPSHOT_DAY_AHEAD: Final[str] = "day_ahead"
SNAPSHOT_MORNING: Final[str] = "morning"
