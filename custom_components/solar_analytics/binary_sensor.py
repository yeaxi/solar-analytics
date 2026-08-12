"""Fail-closed binary-sensor entities published by Solar Analytics.

The v2 read-only design intentionally keeps the legacy binary-sensor entity
IDs present but neutral: near-zero anomaly, possible underperformance, storm
follow-up, and curtailment claims are all suppressed until independent
quality gates prove them, so they always report ``off``. Only the
performance-analysis-valid and data-quality-problem sensors carry live
state.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, NAME, VERSION
from .coordinator import SolarAnalyticsCoordinator

# Coordinator-fanout entities read from a shared payload; there is no remote
# work and no shared mutable state, so unlimited parallelism is safe.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SolarAnalyticsBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Bind a coordinator-payload predicate to a binary-sensor description."""

    value_fn: Callable[[Mapping[str, Any]], bool | None]


def _analysis_valid(data: Mapping[str, Any]) -> bool | None:
    if not data:
        return None
    return bool(data.get("analysis_valid"))


def _data_quality_problem(data: Mapping[str, Any]) -> bool | None:
    if not data:
        return None
    return data.get("native_source_status") != "ok" or data.get("actual_power_w") is None


def _neutral_false(_data: Mapping[str, Any]) -> bool:
    return False


BINARY_DESCRIPTIONS: tuple[SolarAnalyticsBinarySensorEntityDescription, ...] = (
    SolarAnalyticsBinarySensorEntityDescription(
        key="pv_performance_analysis_valid",
        translation_key="pv_performance_analysis_valid",
        icon="mdi:solar-power",
        value_fn=_analysis_valid,
    ),
    SolarAnalyticsBinarySensorEntityDescription(
        key="near_zero_anomaly",
        translation_key="near_zero_anomaly",
        icon="mdi:solar-power",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        value_fn=_neutral_false,
    ),
    SolarAnalyticsBinarySensorEntityDescription(
        key="possible_underperformance",
        translation_key="possible_underperformance",
        icon="mdi:solar-power",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        value_fn=_neutral_false,
    ),
    SolarAnalyticsBinarySensorEntityDescription(
        key="storm_follow_up",
        translation_key="storm_follow_up",
        icon="mdi:solar-power",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        value_fn=_neutral_false,
    ),
    SolarAnalyticsBinarySensorEntityDescription(
        key="data_quality_problem",
        translation_key="data_quality_problem",
        icon="mdi:solar-power",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_data_quality_problem,
    ),
    SolarAnalyticsBinarySensorEntityDescription(
        key="curtailment_detected",
        translation_key="curtailment_detected",
        icon="mdi:solar-power",
        entity_registry_enabled_default=False,
        value_fn=_neutral_false,
    ),
)


class SolarAnalyticsBinarySensor(CoordinatorEntity[SolarAnalyticsCoordinator], BinarySensorEntity):
    """Expose one boolean from the coordinator payload."""

    _attr_has_entity_name = True
    entity_description: SolarAnalyticsBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: SolarAnalyticsCoordinator,
        description: SolarAnalyticsBinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = description.key
        self._attr_suggested_object_id = description.key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "solar_analytics")},
            name=NAME,
            manufacturer=MANUFACTURER,
            model="Native Forecast.Solar period analytics",
            sw_version=VERSION,
        )

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.data or {})

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)

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
        SolarAnalyticsBinarySensor(coordinator, description) for description in BINARY_DESCRIPTIONS
    )
