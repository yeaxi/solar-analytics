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


def test_entry_migration_keeps_supported_native_fields_and_drops_unknown_fields():
    migration = _load_migration_module()
    version, data = migration.migrate_entry_data(
        2,
        {
            "unsupported_entity": "sensor.retired_source",
            "native_forecast_entry_id": "native-entry",
            "actual_power_entity": "sensor.plant_pv_power",
        },
    )

    assert version == migration.CURRENT_ENTRY_VERSION
    assert "unsupported_entity" not in data
    assert data["native_forecast_entry_id"] == "native-entry"
    assert data["actual_power_entity"] == "sensor.plant_pv_power"
    assert data["morning_snapshot_hour"] == migration.DEFAULT_MORNING_HOUR
    assert data["day_ahead_snapshot_hour"] == migration.DEFAULT_DAY_AHEAD_HOUR


def test_old_entry_migration_adds_timezone_and_snapshot_hours():
    migration = _load_migration_module()
    version, data = migration.migrate_entry_data(1, {})

    assert version == migration.CURRENT_ENTRY_VERSION
    assert data["time_zone"] == migration.DEFAULT_TIME_ZONE
    assert data["morning_snapshot_hour"] == migration.DEFAULT_MORNING_HOUR
    assert data["day_ahead_snapshot_hour"] == migration.DEFAULT_DAY_AHEAD_HOUR


def test_migration_preserves_user_selected_snapshot_hours():
    migration = _load_migration_module()
    version, data = migration.migrate_entry_data(
        4,
        {
            "time_zone": "America/Los_Angeles",
            "morning_snapshot_hour": 5,
            "day_ahead_snapshot_hour": 22,
        },
    )

    assert version == migration.CURRENT_ENTRY_VERSION
    assert data["time_zone"] == "America/Los_Angeles"
    assert data["morning_snapshot_hour"] == 5
    assert data["day_ahead_snapshot_hour"] == 22
