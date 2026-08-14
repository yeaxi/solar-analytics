"""Coverage for the forecast-entity provider and its attribute extractor.

The pure extractor is imported through the ``solar_analytics.native`` path
alias. The provider needs the Home Assistant config-entry/core stubs its
sibling adapter imports, installed here per the suite's usual pattern.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

from solar_analytics.native import extract_forecast_entity_wh_hours

COMPONENT = Path(__file__).parents[1] / "custom_components" / "solar_analytics"


def _install_ha_stub() -> None:
    ha = types.ModuleType("homeassistant")
    ha_const = types.ModuleType("homeassistant.const")
    ha_const.__version__ = "2026.7.4"
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    sys.modules.update(
        {
            "homeassistant": ha,
            "homeassistant.const": ha_const,
            "homeassistant.config_entries": config_entries,
            "homeassistant.core": core,
        }
    )
    parent = types.ModuleType("custom_components")
    parent.__path__ = [str(COMPONENT.parent)]
    package = types.ModuleType("custom_components.solar_analytics")
    package.__path__ = [str(COMPONENT)]
    sys.modules["custom_components"] = parent
    sys.modules["custom_components.solar_analytics"] = package


class FakeState:
    def __init__(self, state, attributes, last_updated):
        self.state = state
        self.attributes = attributes
        self.last_updated = last_updated
        self.last_changed = last_updated


class FakeStates:
    def __init__(self, mapping):
        self._mapping = mapping

    def get(self, entity_id):
        return self._mapping.get(entity_id)


class FakeHass:
    def __init__(self, states):
        self.states = FakeStates(states)

    async def async_add_executor_job(self, target, *args):
        return target(*args)


class FakeEntry:
    def __init__(self, data):
        self.entry_id = "analytics-1"
        self.data = data
        self.options = {}


def _entry_data(**overrides):
    data = {
        "forecast_source_type": "forecast_entity",
        "forecast_entity_id": "sensor.my_forecast",
        "actual_power_entity": "sensor.pv_power",
        "actual_energy_today_entity": "sensor.pv_energy",
    }
    data.update(overrides)
    return data


def _provider(states, **overrides):
    _install_ha_stub()
    module = importlib.import_module("custom_components.solar_analytics.forecast_source")
    return module.EntityForecastProvider(FakeHass(states), FakeEntry(_entry_data(**overrides)))


def test_extractor_accepts_known_wh_maps_and_rejects_scalars() -> None:
    assert extract_forecast_entity_wh_hours({"wh_hours": {"2026-08-03T01:00:00+00:00": 100}}) == {
        "wh_hours": {"2026-08-03T01:00:00+00:00": 100}
    }
    assert extract_forecast_entity_wh_hours(
        {"watt_hours_period": {"2026-08-03T01:00:00+00:00": 5}}
    ) == {"wh_hours": {"2026-08-03T01:00:00+00:00": 5}}
    assert extract_forecast_entity_wh_hours({"pv_estimate": 3.2}) is None
    assert extract_forecast_entity_wh_hours({"wh_hours": {}}) is None
    assert extract_forecast_entity_wh_hours({}) is None


def test_entity_provider_captures_timestamped_profile() -> None:
    now = datetime.now(UTC)
    state = FakeState(
        "on",
        {
            "wh_hours": {
                "2026-08-03T00:00:00+00:00": 0,
                "2026-08-03T01:00:00+00:00": 100,
                "2026-08-03T02:00:00+00:00": 200,
            },
            "unit_of_measurement": "Wh",
        },
        now,
    )
    provider = _provider({"sensor.my_forecast": state})

    async def run():
        binding = await provider.async_initialize()
        return binding, await provider.async_capture()

    binding, read = asyncio.run(run())
    assert binding.status == "ok"
    assert binding.forecast_entity_id == "sensor.my_forecast"
    assert provider.source_kind == "forecast_entity"
    assert read.status == "ok"
    assert read.observation is not None
    assert read.observation.profile.valid_periods[0].energy_wh == 100
    assert read.observation.model.fingerprint.startswith("sha256:")


def test_entity_provider_fails_closed_on_scalar_only_entity() -> None:
    state = FakeState("3.2", {"unit_of_measurement": "kW"}, datetime.now(UTC))
    provider = _provider({"sensor.my_forecast": state})
    read = asyncio.run(provider.async_capture())
    assert read.status == "unsupported_forecast_entity_contract"
    assert read.reason == "no_timestamped_profile"


def test_entity_provider_marks_stale_profile() -> None:
    stale = datetime.now(UTC) - timedelta(hours=3)
    state = FakeState(
        "on",
        {
            "wh_hours": {
                "2026-08-03T00:00:00+00:00": 0,
                "2026-08-03T01:00:00+00:00": 100,
                "2026-08-03T02:00:00+00:00": 200,
            }
        },
        stale,
    )
    provider = _provider({"sensor.my_forecast": state})
    read = asyncio.run(provider.async_capture())
    assert read.status == "native_source_stale"


def test_entity_provider_reports_missing_entity() -> None:
    provider = _provider({})
    read = asyncio.run(provider.async_capture())
    assert read.status == "native_source_unavailable"
    assert read.reason == "forecast_entity_missing"


def test_entity_provider_requires_forecast_entity_id() -> None:
    provider = _provider({}, forecast_entity_id="")
    binding = asyncio.run(provider.async_resolve_binding())
    assert binding.status == "binding_unavailable"
    assert binding.reason == "forecast_entity_id_missing"


def test_migration_preserves_forecast_source_fields() -> None:
    migration = importlib.import_module("solar_analytics.migration")
    version, data = migration.migrate_entry_data(
        5,
        {
            "forecast_source_type": "forecast_entity",
            "forecast_entity_id": "sensor.my_forecast",
            "actual_power_entity": "sensor.pv_power",
        },
    )
    assert version == migration.CURRENT_ENTRY_VERSION == 6
    assert data["forecast_source_type"] == "forecast_entity"
    assert data["forecast_entity_id"] == "sensor.my_forecast"
