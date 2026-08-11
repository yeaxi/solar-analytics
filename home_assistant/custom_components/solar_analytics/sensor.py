"""Read-only sensors published by Solar Analytics v2."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity

from .const import DOMAIN, MANUFACTURER, NAME, VERSION
from .coordinator import SolarAnalyticsCoordinator
from .entity_contract import DASHBOARD_ENTITY_OBJECT_IDS, DASHBOARD_ENTITY_UNIQUE_IDS


SENSOR_DEFINITIONS: tuple[tuple[str, str, str | None, str], ...] = (
    ("actual_pv_power", "Actual PV Power", UnitOfPower.WATT, "mdi:solar-power"),
    ("native_modules_power", "Native Forecast.Solar Module Power", UnitOfPower.WATT, "mdi:solar-panel-large"),
    ("forecast_solar_power", "Forecast.Solar Power", UnitOfPower.WATT, "mdi:weather-sunny"),
    ("vrm_forecast_power", "Victron VRM Forecast Power", UnitOfPower.WATT, "mdi:solar-power-variant"),
    ("analysis_status", "Analysis Status", None, "mdi:chart-bell-curve"),
    ("native_source_status", "Native Forecast.Solar Source Status", None, "mdi:source-branch"),
    ("forecast_coverage", "Forecast Coverage", None, "mdi:chart-donut"),
    ("actual_coverage", "Actual Coverage", None, "mdi:chart-donut-variant"),
    ("paired_coverage", "Paired Coverage", None, "mdi:link-variant"),
    ("lineage", "Solar Analytics Lineage", None, "mdi:source-commit"),
    ("current_limitation", "Current PV Limitation", None, "mdi:transmission-tower-off"),
    ("last_insight", "Last Solar Insight", None, "mdi:lightbulb-alert-outline"),
    ("insight_json", "Solar Insight JSON", None, "mdi:code-json"),
    ("accuracy", "Solar Forecast Accuracy", None, "mdi:chart-line"),
    ("daily_comparison", "Solar Daily Comparison", None, "mdi:calendar-range"),
    ("future_profile", "Solar Future Profile", None, "mdi:chart-timeline-variant"),
    ("heatmap", "Solar Performance Heatmap", None, "mdi:heatmap"),
    ("last_updated", "Solar Analytics Last Updated", None, "mdi:update"),
)


class SolarAnalyticsSensor(CoordinatorEntity[SolarAnalyticsCoordinator], SensorEntity):
    """Expose compact, bounded current/history/provenance outputs."""

    _attr_has_entity_name = False

    def __init__(self, coordinator: SolarAnalyticsCoordinator, key: str, name: str, unit: str | None, icon: str) -> None:
        super().__init__(coordinator)
        self._key = key
        object_id = DASHBOARD_ENTITY_OBJECT_IDS.get(key, f"solar_analytics_{key}")
        unique_id = DASHBOARD_ENTITY_UNIQUE_IDS.get(key, f"solar_analytics_{key}")
        self._attr_unique_id = unique_id
        self._attr_suggested_object_id = object_id
        self._attr_name = name
        self._attr_icon = icon
        self._attr_native_unit_of_measurement = unit
        if key in {"actual_pv_power", "native_modules_power", "forecast_solar_power", "vrm_forecast_power"}:
            self._attr_device_class = SensorDeviceClass.POWER
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "solar_analytics")},
            name=NAME,
            manufacturer=MANUFACTURER,
            model="Native Forecast.Solar period analytics",
            sw_version=VERSION,
        )

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data or {}
        if self._key == "actual_pv_power":
            return data.get("actual_power_w")
        if self._key == "native_modules_power":
            return (data.get("native_forecast_contract") or {}).get("modules_power_w")
        if self._key == "forecast_solar_power":
            return data.get("forecast_solar_power_w")
        if self._key == "vrm_forecast_power":
            return data.get("vrm_forecast_power_w")
        if self._key == "analysis_status":
            return data.get("status", "insufficient_data")
        if self._key == "native_source_status":
            return data.get("native_source_status", "unavailable")
        if self._key == "forecast_coverage":
            return data.get("forecast_coverage")
        if self._key == "actual_coverage":
            return data.get("actual_coverage")
        if self._key == "paired_coverage":
            return data.get("paired_coverage")
        if self._key == "lineage":
            return data.get("lineage_id") or "unavailable"
        if self._key == "current_limitation":
            return data.get("current_limitation", "not_claimed")
        if self._key == "last_insight":
            return data.get("last_insight", "insufficient_data")
        if self._key == "insight_json":
            return data.get("status", "insufficient_data")
        if self._key == "accuracy":
            return (data.get("accuracy") or {}).get("status", "insufficient_data")
        if self._key == "daily_comparison":
            points = data.get("daily_points") or []
            return points[-1][0] if points else "no_data"
        if self._key == "future_profile":
            return "ready" if data.get("future_points") else "unavailable"
        if self._key == "heatmap":
            return (data.get("heatmap") or {}).get("status", "unavailable")
        if self._key == "last_updated":
            return data.get("last_updated")
        return None

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
        if self._key == "native_modules_power":
            attrs["native_forecast_contract"] = data.get("native_forecast_contract", {})
        elif self._key == "insight_json":
            attrs.update({"insight": insight, "hermes_json": data.get("hermes_json", "")})
        elif self._key == "accuracy":
            attrs.update({"forecast_accuracy": data.get("accuracy", {}), "coverage": insight.get("coverage", {})})
        elif self._key == "daily_comparison":
            attrs.update({"points": data.get("daily_points", []), "schema": ["date", "actual_kwh", "forecast_kwh", "signed_error_kwh", "forecast_coverage", "actual_coverage", "valid_paired_day", "reason"]})
        elif self._key == "future_profile":
            attrs.update({"points": data.get("future_points", []), "storage": "SQLite v2; entity output bounded to 96 periods"})
        elif self._key == "heatmap":
            attrs.update({"heatmap": data.get("heatmap", {}), "schema": "unavailable_until_paired_history"})
        return attrs

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: SolarAnalyticsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [SolarAnalyticsSensor(coordinator, key, name, unit, icon) for key, name, unit, icon in SENSOR_DEFINITIONS]
    )
