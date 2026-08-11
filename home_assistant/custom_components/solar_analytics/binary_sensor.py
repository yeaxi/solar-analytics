"""Fail-closed validity sensors for Solar Analytics v2."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, NAME, VERSION
from .coordinator import SolarAnalyticsCoordinator


BINARY_DEFINITIONS: tuple[tuple[str, str, str | None], ...] = (
    ("pv_performance_analysis_valid", "PV Performance Analysis Valid", None),
    ("near_zero_anomaly", "PV Near-zero Anomaly", BinarySensorDeviceClass.PROBLEM),
    ("possible_underperformance", "PV Possible Underperformance", BinarySensorDeviceClass.PROBLEM),
    ("storm_follow_up", "PV Storm Follow-up", BinarySensorDeviceClass.PROBLEM),
    ("data_quality_problem", "PV Analysis Data-quality Problem", BinarySensorDeviceClass.PROBLEM),
    ("curtailment_detected", "PV Curtailment Detected", None),
)


class SolarAnalyticsBinarySensor(CoordinatorEntity[SolarAnalyticsCoordinator], BinarySensorEntity):
    _attr_has_entity_name = False

    def __init__(self, coordinator: SolarAnalyticsCoordinator, key: str, name: str, device_class: str | None) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = key
        self._attr_suggested_object_id = key
        self._attr_name = name
        self._attr_device_class = device_class
        self._attr_icon = "mdi:solar-power"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "solar_analytics")},
            name=NAME,
            manufacturer=MANUFACTURER,
            model="Native Forecast.Solar period analytics",
            sw_version=VERSION,
        )

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if not data:
            return None
        if self._key == "pv_performance_analysis_valid":
            return bool(data.get("analysis_valid"))
        # v2 does not manufacture anomaly/curtailment/storm claims from missing
        # telemetry. These legacy entity IDs remain present but neutral/false.
        if self._key in {"near_zero_anomaly", "possible_underperformance", "storm_follow_up", "curtailment_detected"}:
            return False
        if self._key == "data_quality_problem":
            return data.get("native_source_status") != "ok" or data.get("actual_power_w") is None
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {
            "schema": "solar-analytics-v2",
            "status": data.get("status"),
            "validity_reason": data.get("validity_reason"),
            "native_source_status": data.get("native_source_status"),
            "lineage_id": data.get("lineage_id"),
            "accuracy": data.get("accuracy", {}),
            "claim_policy": "underperformance_claims_blocked unless independent quality gates are proven",
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SolarAnalyticsCoordinator = entry.runtime_data
    async_add_entities(
        [SolarAnalyticsBinarySensor(coordinator, key, name, device_class) for key, name, device_class in BINARY_DEFINITIONS]
    )
