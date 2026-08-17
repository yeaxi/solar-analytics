"""Read-only sensor entities published by Solar Analytics.

Every entity uses the modern Home Assistant patterns required for the
platinum quality tier: ``_attr_has_entity_name = True`` with a
``translation_key``, explicit ``device_class`` / ``state_class`` /
``entity_category`` / ``options`` where applicable, and per-entity
``available`` logic. Legacy unique IDs and object IDs are preserved via
:mod:`entity_contract` so upgrading from earlier versions does not create
``_2`` duplicate entities.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, NAME, VERSION
from .coordinator import SolarAnalyticsCoordinator
from .entity_contract import DASHBOARD_ENTITY_OBJECT_IDS, DASHBOARD_ENTITY_UNIQUE_IDS

# Coordinator-fanout entities read from a shared payload; there is no remote
# work and no shared mutable state, so unlimited parallelism is safe.
PARALLEL_UPDATES = 0

_ANALYSIS_STATUS_OPTIONS = (
    "ready",
    "insufficient_data",
    "native_source_unavailable",
    "native_source_stale",
    "unsupported_native_contract",
    "unsupported_forecast_entity_contract",
    "actual_source_stale",
    "actual_source_unavailable",
    "binding_unavailable",
    "binding_ambiguous",
    "binding_changed",
    "canonical_actual_mismatch",
    "native_entry_unavailable",
    "storage_failure",
)
_NATIVE_SOURCE_OPTIONS = (
    "ok",
    "uninitialized",
    "unsupported_native_contract",
    "unsupported_forecast_entity_contract",
    "native_source_unavailable",
    "native_source_stale",
    "binding_unavailable",
    "binding_ambiguous",
    "binding_changed",
    "canonical_actual_mismatch",
    "native_entry_unavailable",
)
_LIMITATION_OPTIONS = ("not_claimed", "curtailment", "external_control", "inverter_limitation")
_ACCURACY_OPTIONS = ("ready", "insufficient_data")
_FUTURE_PROFILE_OPTIONS = ("ready", "unavailable")
_HEATMAP_OPTIONS = ("unavailable",)
_IMPORTED_HISTORY_OPTIONS = (
    "uninitialized",
    "imported",
    "no_statistics",
    "no_actual_energy_entity",
    "recorder_unavailable",
    "import_failed",
)


@dataclass(frozen=True, kw_only=True)
class SolarAnalyticsSensorEntityDescription(SensorEntityDescription):
    """Entity description binding a coordinator-payload key to sensor attributes."""

    value_fn: Callable[[Mapping[str, Any]], Any]
    attributes_fn: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None
    available_fn: Callable[[Mapping[str, Any]], bool] | None = None


def _to_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _last_daily_local_date(data: Mapping[str, Any]) -> Any:
    points = data.get("daily_points") or []
    return points[-1][0] if points else "no_data"


def _accuracy_status(data: Mapping[str, Any]) -> str:
    accuracy = data.get("accuracy") or {}
    return str(accuracy.get("status") or "insufficient_data")


def _future_profile_status(data: Mapping[str, Any]) -> str:
    return "ready" if data.get("future_points") else "unavailable"


def _heatmap_status(data: Mapping[str, Any]) -> str:
    return str((data.get("heatmap") or {}).get("status") or "unavailable")


def _imported_history(data: Mapping[str, Any]) -> Mapping[str, Any]:
    block = data.get("imported_actual_history")
    return block if isinstance(block, Mapping) else {}


def _imported_history_status(data: Mapping[str, Any]) -> str:
    return str(_imported_history(data).get("status") or "uninitialized")


def _lineage_value(data: Mapping[str, Any]) -> str:
    return data.get("lineage_id") or "unavailable"


def _native_source_status(data: Mapping[str, Any]) -> str:
    return str(data.get("native_source_status") or "uninitialized")


def _payload_available(data: Mapping[str, Any]) -> bool:
    return bool(data)


def _analysis_available(data: Mapping[str, Any]) -> bool:
    return bool(data) and data.get("status") is not None


def _last_updated_value(data: Mapping[str, Any]) -> datetime | None:
    return _to_datetime(data.get("last_updated"))


SENSOR_DESCRIPTIONS: tuple[SolarAnalyticsSensorEntityDescription, ...] = (
    SolarAnalyticsSensorEntityDescription(
        key="actual_pv_power",
        translation_key="actual_pv_power",
        icon="mdi:solar-power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("actual_power_w"),
    ),
    SolarAnalyticsSensorEntityDescription(
        key="native_modules_power",
        translation_key="native_modules_power",
        icon="mdi:solar-panel-large",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (data.get("native_forecast_contract") or {}).get("modules_power_w"),
        attributes_fn=lambda data: {
            "native_forecast_contract": data.get("native_forecast_contract", {}),
        },
    ),
    SolarAnalyticsSensorEntityDescription(
        key="forecast_solar_power",
        translation_key="forecast_solar_power",
        icon="mdi:weather-sunny",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("forecast_solar_power_w"),
    ),
    SolarAnalyticsSensorEntityDescription(
        key="analysis_status",
        translation_key="analysis_status",
        icon="mdi:chart-bell-curve",
        device_class=SensorDeviceClass.ENUM,
        options=list(_ANALYSIS_STATUS_OPTIONS),
        value_fn=lambda data: data.get("status") or "insufficient_data",
        available_fn=_analysis_available,
    ),
    SolarAnalyticsSensorEntityDescription(
        key="native_source_status",
        translation_key="native_source_status",
        icon="mdi:source-branch",
        device_class=SensorDeviceClass.ENUM,
        options=list(_NATIVE_SOURCE_OPTIONS),
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_native_source_status,
    ),
    SolarAnalyticsSensorEntityDescription(
        key="forecast_coverage",
        translation_key="forecast_coverage",
        icon="mdi:chart-donut",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("forecast_coverage"),
    ),
    SolarAnalyticsSensorEntityDescription(
        key="actual_coverage",
        translation_key="actual_coverage",
        icon="mdi:chart-donut-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("actual_coverage"),
    ),
    SolarAnalyticsSensorEntityDescription(
        key="paired_coverage",
        translation_key="paired_coverage",
        icon="mdi:link-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("paired_coverage"),
    ),
    SolarAnalyticsSensorEntityDescription(
        key="lineage",
        translation_key="lineage",
        icon="mdi:source-commit",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_lineage_value,
    ),
    SolarAnalyticsSensorEntityDescription(
        key="current_limitation",
        translation_key="current_limitation",
        icon="mdi:transmission-tower-off",
        device_class=SensorDeviceClass.ENUM,
        options=list(_LIMITATION_OPTIONS),
        value_fn=lambda data: data.get("current_limitation") or "not_claimed",
    ),
    SolarAnalyticsSensorEntityDescription(
        key="last_insight",
        translation_key="last_insight",
        icon="mdi:lightbulb-alert-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("last_insight") or "insufficient_data",
    ),
    SolarAnalyticsSensorEntityDescription(
        key="insight_json",
        translation_key="insight_json",
        icon="mdi:code-json",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.get("status") or "insufficient_data",
        attributes_fn=lambda data: {
            "insight": data.get("insight") or {},
            "hermes_json": data.get("hermes_json", ""),
        },
    ),
    SolarAnalyticsSensorEntityDescription(
        key="accuracy",
        translation_key="accuracy",
        icon="mdi:chart-line",
        device_class=SensorDeviceClass.ENUM,
        options=list(_ACCURACY_OPTIONS),
        value_fn=_accuracy_status,
        attributes_fn=lambda data: {
            "forecast_accuracy": data.get("accuracy", {}),
            "coverage": (data.get("insight") or {}).get("coverage", {}),
        },
    ),
    SolarAnalyticsSensorEntityDescription(
        key="daily_comparison",
        translation_key="daily_comparison",
        icon="mdi:calendar-range",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_last_daily_local_date,
        attributes_fn=lambda data: {
            "points": data.get("daily_points", []),
            "schema": [
                "date",
                "actual_kwh",
                "forecast_kwh",
                "signed_error_kwh",
                "forecast_coverage",
                "actual_coverage",
                "valid_paired_day",
                "reason",
            ],
        },
    ),
    SolarAnalyticsSensorEntityDescription(
        key="future_profile",
        translation_key="future_profile",
        icon="mdi:chart-timeline-variant",
        device_class=SensorDeviceClass.ENUM,
        options=list(_FUTURE_PROFILE_OPTIONS),
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_future_profile_status,
        attributes_fn=lambda data: {
            "points": data.get("future_points", []),
            "storage": "SQLite v2; entity output bounded to 96 periods",
        },
    ),
    SolarAnalyticsSensorEntityDescription(
        key="imported_actual_history",
        translation_key="imported_actual_history",
        icon="mdi:history",
        device_class=SensorDeviceClass.ENUM,
        options=list(_IMPORTED_HISTORY_OPTIONS),
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_imported_history_status,
        attributes_fn=lambda data: dict(_imported_history(data)),
    ),
    SolarAnalyticsSensorEntityDescription(
        key="heatmap",
        translation_key="heatmap",
        icon="mdi:heatmap",
        device_class=SensorDeviceClass.ENUM,
        options=list(_HEATMAP_OPTIONS),
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_heatmap_status,
        attributes_fn=lambda data: {
            "heatmap": data.get("heatmap", {}),
            "schema": "unavailable_until_paired_history",
        },
    ),
    SolarAnalyticsSensorEntityDescription(
        key="last_updated",
        translation_key="last_updated",
        icon="mdi:update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_last_updated_value,
    ),
)


class SolarAnalyticsSensor(CoordinatorEntity[SolarAnalyticsCoordinator], SensorEntity):
    """Expose one compact, bounded output from the coordinator payload."""

    _attr_has_entity_name = True
    entity_description: SolarAnalyticsSensorEntityDescription

    def __init__(
        self,
        coordinator: SolarAnalyticsCoordinator,
        description: SolarAnalyticsSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        key = description.key
        self._attr_unique_id = DASHBOARD_ENTITY_UNIQUE_IDS.get(key, f"solar_analytics_{key}")
        self._attr_suggested_object_id = DASHBOARD_ENTITY_OBJECT_IDS.get(
            key, f"solar_analytics_{key}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "solar_analytics")},
            name=NAME,
            manufacturer=MANUFACTURER,
            model="Forecast vs actual PV analytics",
            sw_version=VERSION,
        )

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data or {})

    @property
    def available(self) -> bool:
        data = self.coordinator.data or {}
        if self.entity_description.available_fn is not None:
            return self.entity_description.available_fn(data)
        return _payload_available(data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        insight = data.get("insight") or {}
        attrs: dict[str, Any] = {
            "schema": "solar-analytics-v2",
            "generated_at": insight.get("generated_at"),
            "overall_status": data.get("status"),
            "validity_reason": data.get("validity_reason"),
            "native_source_status": data.get("native_source_status"),
            "forecast_profile_analysis_allowed": data.get("forecast_profile_analysis_allowed"),
            "lineage_id": data.get("lineage_id"),
            "native_observation_sequence": data.get("native_observation_sequence"),
            "native_payload_sha256": data.get("native_payload_sha256"),
            "native_observed_at": data.get("native_observed_at"),
            "native_updated_at": data.get("native_updated_at"),
            "source_map": data.get("source_map", {}),
        }
        extra = self.entity_description.attributes_fn
        if extra is not None:
            attrs.update(dict(extra(data)))
        return attrs

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SolarAnalyticsCoordinator = entry.runtime_data
    async_add_entities(
        SolarAnalyticsSensor(coordinator, description) for description in SENSOR_DESCRIPTIONS
    )
