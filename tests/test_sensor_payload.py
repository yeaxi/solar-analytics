"""Coverage for the sensor and binary-sensor entity descriptions.

The value_fn / attributes_fn callables on each description are pure functions
of the coordinator payload; this test file exercises them directly against
fixture payloads without needing to boot a real Home Assistant runtime.
Only the HA imports the descriptions require are stubbed.
"""

from __future__ import annotations

import importlib
import sys
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "solar_analytics"


def _install_entity_stubs() -> None:
    """Install minimal Home Assistant stubs for sensor.py / binary_sensor.py imports."""

    if "homeassistant" in sys.modules and hasattr(
        sys.modules["homeassistant"], "_solar_analytics_stub"
    ):
        return

    ha = types.ModuleType("homeassistant")
    ha._solar_analytics_stub = True  # type: ignore[attr-defined]
    ha_const = types.ModuleType("homeassistant.const")

    class _EntityCategory:
        DIAGNOSTIC = "diagnostic"
        CONFIG = "config"

    class _UnitOfPower:
        WATT = "W"
        KILO_WATT = "kW"

    ha_const.EntityCategory = _EntityCategory
    ha_const.UnitOfPower = _UnitOfPower
    ha_const.__version__ = "2026.7.4"

    ha_config = types.ModuleType("homeassistant.config_entries")
    ha_config.ConfigEntry = object

    ha_core = types.ModuleType("homeassistant.core")
    ha_core.HomeAssistant = object

    def _passthrough(func):
        return func

    ha_core.callback = _passthrough

    ha_helpers = types.ModuleType("homeassistant.helpers")
    ha_helpers_ir = types.ModuleType("homeassistant.helpers.issue_registry")

    class _IssueSeverity:
        WARNING = "warning"
        ERROR = "error"

    ha_helpers_ir.IssueSeverity = _IssueSeverity
    ha_helpers_ir.async_create_issue = lambda *args, **kwargs: None
    ha_helpers_ir.async_delete_issue = lambda *args, **kwargs: None

    ha_helpers_dr = types.ModuleType("homeassistant.helpers.device_registry")

    class _DeviceInfo(dict):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

    ha_helpers_dr.DeviceInfo = _DeviceInfo

    ha_helpers_ep = types.ModuleType("homeassistant.helpers.entity_platform")
    ha_helpers_ep.AddEntitiesCallback = object

    ha_helpers_uc = types.ModuleType("homeassistant.helpers.update_coordinator")

    from typing import Generic, TypeVar

    _T = TypeVar("_T")

    class _CoordinatorEntity(Generic[_T]):
        def __init__(self, coordinator):
            self.coordinator = coordinator

    class _DataUpdateCoordinator(Generic[_T]):
        def __init__(self, *args, **kwargs):
            pass

    class _UpdateFailed(Exception):
        pass

    ha_helpers_uc.CoordinatorEntity = _CoordinatorEntity
    ha_helpers_uc.DataUpdateCoordinator = _DataUpdateCoordinator
    ha_helpers_uc.UpdateFailed = _UpdateFailed

    ha_helpers_event = types.ModuleType("homeassistant.helpers.event")
    ha_helpers_event.async_track_point_in_utc_time = lambda hass, action, when: lambda: None

    ha_components = types.ModuleType("homeassistant.components")

    ha_sensor = types.ModuleType("homeassistant.components.sensor")

    class _SensorDeviceClass:
        POWER = "power"
        ENERGY = "energy"
        TIMESTAMP = "timestamp"
        ENUM = "enum"

    class _SensorStateClass:
        MEASUREMENT = "measurement"
        TOTAL = "total"
        TOTAL_INCREASING = "total_increasing"

    class _SensorEntity:
        pass

    from dataclasses import dataclass

    @dataclass(frozen=True, kw_only=True)
    class _SensorEntityDescription:
        key: str
        translation_key: str | None = None
        icon: str | None = None
        native_unit_of_measurement: str | None = None
        device_class: str | None = None
        state_class: str | None = None
        options: list[str] | None = None
        entity_category: str | None = None
        entity_registry_enabled_default: bool = True

    ha_sensor.SensorDeviceClass = _SensorDeviceClass
    ha_sensor.SensorEntity = _SensorEntity
    ha_sensor.SensorEntityDescription = _SensorEntityDescription
    ha_sensor.SensorStateClass = _SensorStateClass

    ha_binary = types.ModuleType("homeassistant.components.binary_sensor")

    class _BinarySensorDeviceClass:
        PROBLEM = "problem"

    class _BinarySensorEntity:
        pass

    @dataclass(frozen=True, kw_only=True)
    class _BinarySensorEntityDescription:
        key: str
        translation_key: str | None = None
        icon: str | None = None
        device_class: str | None = None
        entity_category: str | None = None
        entity_registry_enabled_default: bool = True

    ha_binary.BinarySensorDeviceClass = _BinarySensorDeviceClass
    ha_binary.BinarySensorEntity = _BinarySensorEntity
    ha_binary.BinarySensorEntityDescription = _BinarySensorEntityDescription

    sys.modules.update(
        {
            "homeassistant": ha,
            "homeassistant.const": ha_const,
            "homeassistant.config_entries": ha_config,
            "homeassistant.core": ha_core,
            "homeassistant.helpers": ha_helpers,
            "homeassistant.helpers.issue_registry": ha_helpers_ir,
            "homeassistant.helpers.device_registry": ha_helpers_dr,
            "homeassistant.helpers.entity_platform": ha_helpers_ep,
            "homeassistant.helpers.update_coordinator": ha_helpers_uc,
            "homeassistant.helpers.event": ha_helpers_event,
            "homeassistant.components": ha_components,
            "homeassistant.components.sensor": ha_sensor,
            "homeassistant.components.binary_sensor": ha_binary,
        }
    )
    parent = types.ModuleType("custom_components")
    parent.__path__ = [str(COMPONENT.parent)]
    package = types.ModuleType("custom_components.solar_analytics")
    package.__path__ = [str(COMPONENT)]
    sys.modules["custom_components"] = parent
    sys.modules["custom_components.solar_analytics"] = package


@pytest.fixture(scope="module")
def entities():
    _install_entity_stubs()
    sensor = importlib.import_module("custom_components.solar_analytics.sensor")
    binary_sensor = importlib.import_module("custom_components.solar_analytics.binary_sensor")
    return sensor, binary_sensor


def _ready_payload() -> dict:
    return {
        "status": "ready",
        "analysis_valid": True,
        "actual_power_w": 1234.5,
        "actual_energy_kwh": 12.3,
        "forecast_solar_power_w": 987.6,
        "vrm_forecast_power_w": None,
        "native_source_status": "ok",
        "native_forecast_contract": {"modules_power_w": 5360.0},
        "forecast_coverage": 0.98,
        "actual_coverage": 0.94,
        "paired_coverage": 0.92,
        "lineage_id": "lineage-abc",
        "current_limitation": "not_claimed",
        "last_insight": "paired_history_ready",
        "insight": {"generated_at": "2026-08-11T20:30:00+00:00", "coverage": {}},
        "hermes_json": "{}",
        "accuracy": {"status": "ready", "accuracy_ready": True},
        "daily_points": [
            ["2026-08-10", 30.0, 29.5, 0.5, 0.98, 0.95, True, "valid_paired_day"],
        ],
        "future_points": [
            {
                "start_utc": "2026-08-11T20:00:00+00:00",
                "end_utc": "2026-08-11T21:00:00+00:00",
                "energy_wh": 800.0,
                "duration_seconds": 3600.0,
                "power_w": 800.0,
            }
        ],
        "heatmap": {"status": "unavailable"},
        "last_updated": "2026-08-11T20:35:00+00:00",
        "source_map": {},
    }


def _blocked_payload() -> dict:
    return {
        "status": "native_source_unavailable",
        "analysis_valid": False,
        "actual_power_w": None,
        "actual_energy_kwh": None,
        "forecast_solar_power_w": None,
        "native_source_status": "native_source_unavailable",
        "native_forecast_contract": {},
        "forecast_coverage": None,
        "actual_coverage": None,
        "paired_coverage": None,
        "lineage_id": None,
        "current_limitation": "not_claimed",
        "last_insight": "insufficient_data",
        "insight": {},
        "hermes_json": "",
        "accuracy": {"status": "insufficient_data", "accuracy_ready": False},
        "daily_points": [],
        "future_points": [],
        "heatmap": {"status": "unavailable"},
        "last_updated": "2026-08-11T20:36:00+00:00",
        "source_map": {},
    }


def test_all_sensor_descriptions_have_translation_keys(entities) -> None:
    sensor, _ = entities
    for description in sensor.SENSOR_DESCRIPTIONS:
        assert description.translation_key == description.key


def test_sensor_value_fns_on_ready_payload(entities) -> None:
    sensor, _ = entities
    values = {d.key: d.value_fn(_ready_payload()) for d in sensor.SENSOR_DESCRIPTIONS}
    assert values["actual_pv_power"] == 1234.5
    assert values["forecast_solar_power"] == 987.6
    assert values["native_modules_power"] == 5360.0
    assert values["analysis_status"] == "ready"
    assert values["native_source_status"] == "ok"
    assert values["accuracy"] == "ready"
    assert values["future_profile"] == "ready"
    assert values["heatmap"] == "unavailable"
    assert values["lineage"] == "lineage-abc"
    assert values["last_insight"] == "paired_history_ready"
    assert values["forecast_coverage"] == 0.98
    assert values["actual_coverage"] == 0.94
    assert values["paired_coverage"] == 0.92
    assert values["current_limitation"] == "not_claimed"
    assert values["daily_comparison"] == "2026-08-10"
    assert values["last_updated"] == datetime(2026, 8, 11, 20, 35, tzinfo=UTC)


def test_sensor_value_fns_on_blocked_payload(entities) -> None:
    sensor, _ = entities
    values = {d.key: d.value_fn(_blocked_payload()) for d in sensor.SENSOR_DESCRIPTIONS}
    assert values["actual_pv_power"] is None
    assert values["forecast_solar_power"] is None
    assert values["analysis_status"] == "native_source_unavailable"
    assert values["native_source_status"] == "native_source_unavailable"
    assert values["accuracy"] == "insufficient_data"
    assert values["future_profile"] == "unavailable"
    assert values["lineage"] == "unavailable"
    assert values["daily_comparison"] == "no_data"


def test_sensor_value_fns_on_empty_payload_never_raise(entities) -> None:
    sensor, _ = entities
    for description in sensor.SENSOR_DESCRIPTIONS:
        description.value_fn({})


def test_binary_sensor_predicates(entities) -> None:
    _, binary_sensor = entities
    predicates = {d.key: d.value_fn for d in binary_sensor.BINARY_DESCRIPTIONS}
    ready = _ready_payload()
    blocked = _blocked_payload()

    assert predicates["pv_performance_analysis_valid"](ready) is True
    assert predicates["pv_performance_analysis_valid"](blocked) is False
    assert predicates["pv_performance_analysis_valid"]({}) is None

    assert predicates["data_quality_problem"](ready) is False
    assert predicates["data_quality_problem"](blocked) is True
    assert predicates["data_quality_problem"]({}) is None

    for neutral in (
        "near_zero_anomaly",
        "possible_underperformance",
        "storm_follow_up",
        "curtailment_detected",
    ):
        assert predicates[neutral](ready) is False
        assert predicates[neutral](blocked) is False
        assert predicates[neutral]({}) is False


def test_enum_sensor_options_include_actual_state(entities) -> None:
    """A missing option list would render as an unknown state in the frontend."""

    sensor, _ = entities
    lookup = {d.key: d for d in sensor.SENSOR_DESCRIPTIONS}
    assert "ready" in lookup["analysis_status"].options
    assert "native_source_unavailable" in lookup["analysis_status"].options
    assert "storage_failure" in lookup["analysis_status"].options
    assert "ok" in lookup["native_source_status"].options
    assert "not_claimed" in lookup["current_limitation"].options
    assert "unavailable" in lookup["future_profile"].options


def test_imported_history_sensor_is_a_separate_labelled_view(entities) -> None:
    """Reconstructed history must never leak into the accuracy-bearing entities."""

    sensor, _ = entities
    lookup = {d.key: d for d in sensor.SENSOR_DESCRIPTIONS}
    description = lookup["imported_actual_history"]
    payload = {
        "accuracy": {"status": "insufficient_data", "accuracy_ready": False},
        "daily_points": [],
        "imported_actual_history": {
            "status": "imported",
            "provenance": "reconstructed_from_recorder_statistics",
            "day_count": 2,
            "points": [["2026-08-01", 12.5, 1.0, 0], ["2026-08-02", 9.25, 1.0, 0]],
        },
    }

    assert description.value_fn(payload) == "imported"
    assert description.value_fn({}) == "uninitialized"
    assert set(description.options) == {
        "uninitialized",
        "imported",
        "no_statistics",
        "no_actual_energy_entity",
        "recorder_unavailable",
        "import_failed",
    }
    attributes = description.attributes_fn(payload)
    assert attributes["provenance"] == "reconstructed_from_recorder_statistics"
    assert len(attributes["points"]) == 2
    assert lookup["accuracy"].value_fn(payload) == "insufficient_data"
    assert lookup["daily_comparison"].value_fn(payload) == "no_data"
    assert "imported" not in lookup["analysis_status"].options


def test_expected_diagnostic_entities_are_hidden_by_default(entities) -> None:
    sensor, binary_sensor = entities
    hidden = {d.key for d in sensor.SENSOR_DESCRIPTIONS if not d.entity_registry_enabled_default}
    assert "vrm_forecast_power" in hidden
    assert "insight_json" in hidden
    assert "heatmap" in hidden
    assert "imported_actual_history" in hidden
    hidden_binary = {
        d.key for d in binary_sensor.BINARY_DESCRIPTIONS if not d.entity_registry_enabled_default
    }
    assert {
        "near_zero_anomaly",
        "possible_underperformance",
        "storm_follow_up",
        "curtailment_detected",
    } <= hidden_binary
