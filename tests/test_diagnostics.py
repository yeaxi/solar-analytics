"""Coverage for the diagnostics endpoint.

The endpoint is a thin async function that reads
``entry.runtime_data.native_adapter.binding`` and the coordinator's data. We
exercise it against a stub ``hass`` / ``entry`` / ``coordinator`` after
installing minimal Home Assistant module stubs so the shipping module is
importable.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from datetime import timedelta
from pathlib import Path

import pytest

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "solar_analytics"


def _install_ha_stub() -> None:
    """Install the minimum HA import surface that diagnostics.py and coordinator.py need."""

    if "homeassistant" in sys.modules and getattr(
        sys.modules["homeassistant"], "_solar_analytics_diag_stub", False
    ):
        return

    ha = types.ModuleType("homeassistant")
    ha._solar_analytics_diag_stub = True  # type: ignore[attr-defined]

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

    ha_helpers_event = types.ModuleType("homeassistant.helpers.event")
    ha_helpers_event.async_track_point_in_utc_time = lambda hass, action, when: lambda: None
    ha_helpers_event.async_track_state_change_event = lambda hass, entity_ids, action: lambda: None

    ha_helpers_uc = types.ModuleType("homeassistant.helpers.update_coordinator")

    from typing import Generic, TypeVar

    _T = TypeVar("_T")

    class _DataUpdateCoordinator(Generic[_T]):
        def __init__(self, *args, **kwargs):
            self.last_update_success = True
            self.update_interval = kwargs.get("update_interval")

    class _UpdateFailed(Exception):
        pass

    ha_helpers_uc.DataUpdateCoordinator = _DataUpdateCoordinator
    ha_helpers_uc.UpdateFailed = _UpdateFailed

    ha_components = types.ModuleType("homeassistant.components")
    ha_diag = types.ModuleType("homeassistant.components.diagnostics")

    def _async_redact_data(payload, redact_keys):
        redacted = dict(payload)
        for key in redact_keys:
            if key in redacted:
                redacted[key] = "**REDACTED**"
        return redacted

    ha_diag.async_redact_data = _async_redact_data

    sys.modules.update(
        {
            "homeassistant": ha,
            "homeassistant.const": ha_const,
            "homeassistant.config_entries": ha_config,
            "homeassistant.core": ha_core,
            "homeassistant.helpers": ha_helpers,
            "homeassistant.helpers.issue_registry": ha_helpers_ir,
            "homeassistant.helpers.event": ha_helpers_event,
            "homeassistant.helpers.update_coordinator": ha_helpers_uc,
            "homeassistant.components": ha_components,
            "homeassistant.components.diagnostics": ha_diag,
        }
    )
    parent = types.ModuleType("custom_components")
    parent.__path__ = [str(COMPONENT.parent)]
    package = types.ModuleType("custom_components.solar_analytics")
    package.__path__ = [str(COMPONENT)]
    sys.modules["custom_components"] = parent
    sys.modules["custom_components.solar_analytics"] = package


class _FakeBinding:
    def __init__(self):
        self.status = "ok"
        self.native_entry_id = "native-1"
        self.forecast_entity_id = None
        self.actual_power_entity = "sensor.example_pv_power"
        self.actual_energy_entity = "sensor.example_pv_energy"
        self.reason = None


class _FakeAdapter:
    def __init__(self):
        self.binding = _FakeBinding()


class _FakeCoordinator:
    def __init__(self):
        self.last_update_success = True
        self.update_interval = timedelta(minutes=5)
        self.time_zone = "Europe/Berlin"
        self.morning_hour = 5
        self.day_ahead_hour = 22
        self.source_kind = "native"
        self.native_adapter = _FakeAdapter()
        self.data = {
            "status": "ready",
            "actual_power_w": 1500.0,
            "native_source_status": "ok",
        }


class _FakeEntry:
    def __init__(self, runtime_data=None):
        self.entry_id = "entry-1"
        self.version = 5
        self.data = {
            "native_forecast_entry_id": "native-1",
            "actual_power_entity": "sensor.example_pv_power",
            "actual_energy_today_entity": "sensor.example_pv_energy",
            "time_zone": "Europe/Berlin",
            "morning_snapshot_hour": 5,
            "day_ahead_snapshot_hour": 22,
            "unique_id": "should-be-redacted",
        }
        self.options = {}
        self.runtime_data = runtime_data


@pytest.fixture(scope="module")
def diagnostics():
    _install_ha_stub()
    return importlib.import_module("custom_components.solar_analytics.diagnostics")


def test_diagnostics_returns_expected_shape_and_redacts_unique_id(diagnostics) -> None:
    coordinator = _FakeCoordinator()
    entry = _FakeEntry(runtime_data=coordinator)

    result = asyncio.run(diagnostics.async_get_config_entry_diagnostics(object(), entry))

    assert set(result.keys()) == {"config_entry", "coordinator", "binding", "payload"}
    assert result["config_entry"]["entry_id"] == "entry-1"
    assert result["config_entry"]["version"] == 5
    assert result["config_entry"]["data"]["unique_id"] == "**REDACTED**"
    assert result["config_entry"]["data"]["actual_power_entity"] == "sensor.example_pv_power"

    assert result["coordinator"]["last_update_success"] is True
    assert result["coordinator"]["update_interval_seconds"] == 300.0
    assert result["coordinator"]["time_zone"] == "Europe/Berlin"
    assert result["coordinator"]["morning_hour"] == 5
    assert result["coordinator"]["day_ahead_hour"] == 22

    assert result["binding"] == {
        "status": "ok",
        "source_kind": "native",
        "native_entry_id": "native-1",
        "forecast_entity_id": None,
        "actual_power_entity": "sensor.example_pv_power",
        "actual_energy_entity": "sensor.example_pv_energy",
        "reason": None,
    }
    assert result["payload"]["status"] == "ready"


def test_diagnostics_survives_missing_runtime_data(diagnostics) -> None:
    """A config entry that failed setup has no runtime_data; diagnostics must not raise."""

    entry = _FakeEntry(runtime_data=None)

    result = asyncio.run(diagnostics.async_get_config_entry_diagnostics(object(), entry))

    assert result["coordinator"]["last_update_success"] is None
    assert result["coordinator"]["update_interval_seconds"] is None
    assert result["coordinator"]["time_zone"] is None
    assert result["binding"] is None
    assert result["payload"] is None
    assert result["config_entry"]["data"]["unique_id"] == "**REDACTED**"


def test_diagnostics_never_exposes_secrets_beyond_declared_redactions(diagnostics) -> None:
    """Every entry.data key we currently ship is enumerated in this test.

    If a future config-flow field is added it must land here explicitly, so
    we catch cases where a new key needs to be added to _REDACT_ENTRY_KEYS.
    """

    entry = _FakeEntry(runtime_data=_FakeCoordinator())
    result = asyncio.run(diagnostics.async_get_config_entry_diagnostics(object(), entry))
    assert set(result["config_entry"]["data"].keys()) == {
        "native_forecast_entry_id",
        "actual_power_entity",
        "actual_energy_today_entity",
        "time_zone",
        "morning_snapshot_hour",
        "day_ahead_snapshot_hour",
        "unique_id",
    }
