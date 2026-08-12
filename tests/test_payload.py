"""Coverage for the pure payload builder.

``payload.build_payload`` is a pure function of its parameters; this module
exercises it directly without any Home Assistant stubbing. It complements
``test_sensor_payload.py`` (which tests the ``value_fn`` mapping on top of a
built payload) by pinning the shape and status-classification rules of the
payload itself.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "solar_analytics"


def _install_ha_stub_for_payload() -> None:
    """Install just enough HA stubs for ``native_adapter.py`` to import.

    ``payload.py`` imports ``NativeRead`` from ``native_adapter.py`` which in
    turn imports ``homeassistant.config_entries`` and ``homeassistant.core``.
    Neither is exercised at test time; a minimal stub is sufficient.
    """

    if "homeassistant" in sys.modules and getattr(
        sys.modules["homeassistant"], "_solar_analytics_payload_stub", False
    ):
        return
    ha = types.ModuleType("homeassistant")
    ha._solar_analytics_payload_stub = True  # type: ignore[attr-defined]
    ha_const = types.ModuleType("homeassistant.const")
    ha_const.__version__ = "2026.7.4"
    ha_config = types.ModuleType("homeassistant.config_entries")
    ha_config.ConfigEntry = object
    ha_core = types.ModuleType("homeassistant.core")
    ha_core.HomeAssistant = object
    sys.modules.update(
        {
            "homeassistant": ha,
            "homeassistant.const": ha_const,
            "homeassistant.config_entries": ha_config,
            "homeassistant.core": ha_core,
        }
    )
    parent = types.ModuleType("custom_components")
    parent.__path__ = [str(COMPONENT.parent)]
    package = types.ModuleType("custom_components.solar_analytics")
    package.__path__ = [str(COMPONENT)]
    sys.modules["custom_components"] = parent
    sys.modules["custom_components.solar_analytics"] = package


@pytest.fixture(scope="module")
def payload_module():
    _install_ha_stub_for_payload()
    import importlib

    return importlib.import_module("custom_components.solar_analytics.payload")


@dataclass(frozen=True)
class _Binding:
    status: str = "ok"
    native_entry_id: str | None = "native-1"
    actual_power_entity: str | None = "sensor.example_pv_power"
    actual_energy_entity: str | None = "sensor.example_pv_energy"
    reason: str | None = None


@dataclass(frozen=True)
class _Model:
    values: dict = None  # type: ignore[assignment]
    fingerprint: str | None = "sha256:model"

    def __post_init__(self):
        if self.values is None:
            object.__setattr__(self, "values", {"modules_power_w": 5000.0})


@dataclass(frozen=True)
class _Period:
    start_utc: datetime | None
    end_utc: datetime
    energy_wh: float | None
    duration_seconds: float | None
    power_w: float | None


@dataclass(frozen=True)
class _Profile:
    valid_periods: tuple


@dataclass(frozen=True)
class _Observation:
    profile: _Profile
    observed_at_utc: datetime
    native_updated_at_utc: datetime
    observation_sequence: int
    payload_sha256: str | None
    model: _Model


@dataclass(frozen=True)
class _NativeRead:
    status: str
    binding: _Binding
    model: _Model | None = None
    observation: _Observation | None = None
    reason: str | None = None


@dataclass(frozen=True)
class _ActualState:
    entity_id: str
    value: float | None
    unit: str | None
    observed_at_utc: datetime | None
    status: str
    reason: str | None = None

    @property
    def valid(self) -> bool:
        return self.status == "valid" and self.value is not None


def _now() -> datetime:
    return datetime(2026, 8, 12, 4, 0, tzinfo=UTC)


def _future_periods() -> tuple:
    return (
        _Period(
            start_utc=datetime(2026, 8, 12, 3, 0, tzinfo=UTC),
            end_utc=datetime(2026, 8, 12, 5, 0, tzinfo=UTC),
            energy_wh=1200.0,
            duration_seconds=7200.0,
            power_w=600.0,
        ),
    )


def _ready_inputs() -> dict:
    return {
        "native_read": _NativeRead(
            status="ok",
            binding=_Binding(),
            model=_Model(),
            observation=_Observation(
                profile=_Profile(valid_periods=_future_periods()),
                observed_at_utc=_now(),
                native_updated_at_utc=_now(),
                observation_sequence=42,
                payload_sha256="sha256:payload",
                model=_Model(),
            ),
        ),
        "actual_power": _ActualState("sensor.example_pv_power", 1234.5, "W", _now(), "valid"),
        "actual_energy": _ActualState("sensor.example_pv_energy", 12.3, "kWh", _now(), "valid"),
        "accuracy": {
            "status": "ready",
            "accuracy_ready": True,
            "valid_paired_days": 21,
        },
        "daily_rows": [
            {
                "local_date": "2026-08-11",
                "actual_kwh": 30.0,
                "forecast_kwh": 29.5,
                "signed_error_kwh": 0.5,
                "forecast_coverage": 0.98,
                "actual_coverage": 0.95,
                "valid_paired_day": True,
                "reason": "valid_paired_day",
                "paired_coverage": 0.94,
            }
        ],
        "lineage_id": "lineage-abc",
        "reconciliation_status": "reconciled",
        "now_utc": _now(),
    }


def test_ready_payload_classifies_as_ready(payload_module) -> None:
    result = payload_module.build_payload(**_ready_inputs())

    assert result["status"] == "ready"
    assert result["analysis_valid"] is True
    assert result["forecast_profile_analysis_allowed"] is True
    assert result["actual_power_w"] == 1234.5
    assert result["forecast_solar_power_w"] == 600.0
    assert result["lineage_id"] == "lineage-abc"
    assert result["reconciliation_status"] == "reconciled"
    assert result["future_points"][0]["energy_wh"] == 1200.0
    assert result["daily_points"][0][0] == "2026-08-11"
    assert result["source_map"]["actual_power"] == "sensor.example_pv_power"
    assert result["insight"]["forecast_accuracy"]["accuracy_ready"] is True


def test_native_source_unavailable_shadows_actual_state(payload_module) -> None:
    inputs = _ready_inputs()
    inputs["native_read"] = _NativeRead(
        status="native_source_unavailable",
        binding=_Binding(status="ok"),
        reason="native_update_not_observed",
    )
    inputs["accuracy"] = {"accuracy_ready": False, "status": "insufficient_data"}
    inputs["daily_rows"] = []

    result = payload_module.build_payload(**inputs)

    assert result["status"] == "native_source_unavailable"
    assert result["validity_reason"] == "native_update_not_observed"
    assert result["analysis_valid"] is False
    assert result["forecast_solar_power_w"] is None
    assert result["future_points"] == []
    assert result["daily_points"] == []


def test_actual_source_stale_beats_insufficient_data(payload_module) -> None:
    inputs = _ready_inputs()
    inputs["actual_power"] = _ActualState(
        "sensor.example_pv_power", None, "W", _now(), "stale", "age_seconds:1200"
    )
    inputs["accuracy"] = {"accuracy_ready": False, "status": "insufficient_data"}

    result = payload_module.build_payload(**inputs)

    assert result["status"] == "actual_source_stale"
    assert result["validity_reason"] == "age_seconds:1200"


def test_insufficient_data_when_native_and_actual_valid_but_below_gate(payload_module) -> None:
    inputs = _ready_inputs()
    inputs["accuracy"] = {"accuracy_ready": False, "status": "insufficient_data"}

    result = payload_module.build_payload(**inputs)

    assert result["status"] == "insufficient_data"
    assert result["validity_reason"] == "native_and_actual_valid_but_history_below_gate"


def test_future_points_are_capped_and_daily_points_are_last_30(payload_module) -> None:
    inputs = _ready_inputs()
    periods = tuple(
        _Period(
            start_utc=datetime(2026, 8, 12, hour=hour % 24, tzinfo=UTC),
            end_utc=datetime(2026, 8, 12, hour=(hour + 1) % 24, tzinfo=UTC)
            if (hour + 1) % 24
            else datetime(2026, 8, 13, tzinfo=UTC),
            energy_wh=100.0,
            duration_seconds=3600.0,
            power_w=100.0,
        )
        for hour in range(120)
    )
    inputs["native_read"] = _NativeRead(
        status="ok",
        binding=_Binding(),
        model=_Model(),
        observation=_Observation(
            profile=_Profile(valid_periods=periods),
            observed_at_utc=_now(),
            native_updated_at_utc=_now(),
            observation_sequence=1,
            payload_sha256="sha256:payload",
            model=_Model(),
        ),
    )
    inputs["daily_rows"] = [
        {
            "local_date": f"2026-08-{day:02d}",
            "actual_kwh": None,
            "forecast_kwh": None,
            "signed_error_kwh": None,
            "forecast_coverage": None,
            "actual_coverage": None,
            "valid_paired_day": False,
            "reason": "coverage_below_gate",
        }
        for day in range(1, 40)
    ]

    result = payload_module.build_payload(**inputs)

    assert len(result["future_points"]) == 96
    assert len(result["daily_points"]) == 30
    assert result["daily_points"][-1][0] == "2026-08-39"


def test_hermes_json_is_deterministic_and_canonical(payload_module) -> None:
    """Two identical inputs must produce the same hermes_json bytes (sorted keys)."""

    first = payload_module.build_payload(**_ready_inputs())
    second = payload_module.build_payload(**_ready_inputs())
    assert first["hermes_json"] == second["hermes_json"]
    assert first["hermes_json"].startswith("{")
