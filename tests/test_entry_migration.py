from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[1]
    / "home_assistant"
    / "custom_components"
    / "solar_analytics"
    / "migration.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("solar_analytics_migration", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v2_migration_removes_only_legacy_rest_entity_key():
    migration = _load_migration_module()
    version, data = migration.migrate_entry_data(
        2,
        {
            "forecast_solar_hourly_entity": "sensor.forecast_solar_hourly_api",
            "native_forecast_entry_id": "native-entry",
            "actual_power_entity": "sensor.garage_cerbo_gx_pv_power",
        },
    )

    assert version == 4
    assert "forecast_solar_hourly_entity" not in data
    assert data["native_forecast_entry_id"] == "native-entry"
    assert data["actual_power_entity"] == "sensor.garage_cerbo_gx_pv_power"


def test_v1_migration_adds_timezone_and_removes_legacy_key():
    migration = _load_migration_module()
    version, data = migration.migrate_entry_data(
        1,
        {"forecast_solar_hourly_entity": "sensor.forecast_solar_hourly_api"},
    )

    assert version == 4
    assert data["time_zone"] == "Europe/Kyiv"
    assert "forecast_solar_hourly_entity" not in data
