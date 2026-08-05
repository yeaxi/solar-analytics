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
            "actual_power_entity": "sensor.garage_cerbo_gx_pv_power",
        },
    )

    assert version == 4
    assert "unsupported_entity" not in data
    assert data["native_forecast_entry_id"] == "native-entry"
    assert data["actual_power_entity"] == "sensor.garage_cerbo_gx_pv_power"


def test_old_entry_migration_adds_timezone():
    migration = _load_migration_module()
    version, data = migration.migrate_entry_data(1, {})

    assert version == 4
    assert data["time_zone"] == "Europe/Kyiv"
