"""Diagnostics endpoint for Solar Analytics.

Returns a bounded, JSON-serializable snapshot of what the coordinator most
recently computed, plus the native binding it resolved to. Secrets never
enter this payload: the config entry only ever contains entity IDs, an IANA
timezone, snapshot hours, and a Forecast.Solar config-entry id, none of
which are secrets, but we still funnel through ``async_redact_data`` on the
config-entry data as a defense-in-depth measure.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import SolarAnalyticsCoordinator

_REDACT_ENTRY_KEYS = {"unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a Solar Analytics config entry."""

    coordinator: SolarAnalyticsCoordinator | None = getattr(entry, "runtime_data", None)
    payload = coordinator.data if coordinator is not None else None
    binding = coordinator.native_adapter.binding if coordinator is not None else None
    return {
        "config_entry": {
            "entry_id": entry.entry_id,
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), _REDACT_ENTRY_KEYS),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": bool(coordinator.last_update_success) if coordinator else None,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator and coordinator.update_interval
                else None
            ),
            "time_zone": str(coordinator.time_zone) if coordinator else None,
            "morning_hour": coordinator.morning_hour if coordinator else None,
            "day_ahead_hour": coordinator.day_ahead_hour if coordinator else None,
        },
        "binding": (
            {
                "status": binding.status,
                "source_kind": getattr(coordinator, "source_kind", None) if coordinator else None,
                "native_entry_id": binding.native_entry_id,
                "forecast_entity_id": binding.forecast_entity_id,
                "actual_power_entity": binding.actual_power_entity,
                "actual_energy_entity": binding.actual_energy_entity,
                "reason": binding.reason,
            }
            if binding is not None
            else None
        ),
        "payload": payload,
    }
