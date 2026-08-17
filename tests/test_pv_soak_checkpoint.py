from __future__ import annotations

import stat
from pathlib import Path

import pytest

from tools.pv_soak_checkpoint import (
    MIN_SOAK_HOURS,
    SCHEMA_VERSION,
    SOURCE_ENERGY_ENTRY,
    SOURCE_FORECAST_ENTITY,
    CheckpointValidationError,
    analyze_snapshot,
    write_immutable_snapshot,
)

FIXED_ENTITY_IDS = [
    "sensor.solar_analytics_native_forecast_solar_source_status",
    "sensor.solar_analytics_analysis_status",
    "sensor.solar_analytics_last_updated",
    "sensor.solar_analytics_solar_forecast_accuracy",
    "sensor.solar_analytics_solar_future_profile",
]
ACTUAL_POWER = "sensor.example_pv_power"
ACTUAL_ENERGY = "sensor.example_pv_energy"
TABLE_NAMES = [
    "v2_lineages",
    "v2_current_profile_cache",
    "v2_snapshot_intervals",
    "v2_daily_comparisons",
    "v2_accuracy_results",
    "v2_runtime_state.last_actual_sample",
]


def _logs(*names: str) -> dict:
    return {
        name: {
            "fresh": True,
            "mutation_mentions": 0,
            "excerpt_digest": "sha256:" + "b" * 64,
        }
        for name in names
    }


def collector_payload() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "checkpoint_id": "checkpoint-001",
        "collected_at_utc": "2026-08-05T12:00:00Z",
        "baseline_utc": "2026-08-01T00:00:00Z",
        "forecast_source_type": SOURCE_ENERGY_ENTRY,
        "provider_domain": "forecast_solar",
        "collection": {
            "method": "read_only_ssh",
            "physical_calls": 0,
            "mutations": 0,
            "network_writes": 0,
            "forbidden_actions": [],
        },
        "actual_pv_entities": {"power": ACTUAL_POWER, "energy": ACTUAL_ENERGY},
        "ha": {
            "core_check": {
                "status": "PASS",
                "checked_at_utc": "2026-08-05T11:59:00Z",
                "output_digest": "sha256:" + "a" * 64,
            },
            "logs": _logs("solar_analytics", "forecast_solar"),
        },
        "entities": {
            entity_id: {
                "state": "ok",
                "updated_at_utc": "2026-08-05T11:58:00Z",
            }
            for entity_id in [*FIXED_ENTITY_IDS, ACTUAL_POWER, ACTUAL_ENERGY]
        },
        "sqlite": {
            "path": "/config/solar_analytics/solar_analytics.sqlite",
            "mode": "ro",
            "integrity": "ok",
            "tables": {table: {"present": True, "rows": 1} for table in TABLE_NAMES},
            "last_actual_sample": "2026-08-05T11:57:00Z",
        },
    }


def test_valid_snapshot_is_immutable_and_passes_analysis(tmp_path: Path):
    path = write_immutable_snapshot(collector_payload(), tmp_path)

    assert path.exists()
    assert not (path.stat().st_mode & stat.S_IWUSR)

    result = analyze_snapshot(path)

    assert result["status"] == "PASS"
    assert result["physical_calls"] == 0
    assert result["blockers"] == []

    assert write_immutable_snapshot(collector_payload(), tmp_path) == path


def test_unallowlisted_entity_is_rejected(tmp_path: Path):
    payload = collector_payload()
    payload["entities"]["switch.boiler_socket_1"] = {"state": "on"}

    with pytest.raises(CheckpointValidationError, match="unallowlisted entity"):
        write_immutable_snapshot(payload, tmp_path)


def test_actual_pv_entities_must_be_declared_and_populated(tmp_path: Path):
    payload = collector_payload()
    payload["actual_pv_entities"] = {"power": "", "energy": ACTUAL_ENERGY}

    with pytest.raises(CheckpointValidationError, match="actual_pv_entities.power"):
        write_immutable_snapshot(payload, tmp_path)


def test_nonzero_physical_calls_block_analysis(tmp_path: Path):
    payload = collector_payload()
    payload["collection"]["physical_calls"] = 1

    result = analyze_snapshot(payload)

    assert result["status"] == "BLOCKED"
    assert result["physical_calls"] == 1
    assert any("physical_calls" in blocker for blocker in result["blockers"])


def test_stale_log_blocks_analysis(tmp_path: Path):
    payload = collector_payload()
    payload["ha"]["logs"]["forecast_solar"]["fresh"] = False
    path = write_immutable_snapshot(payload, tmp_path)

    result = analyze_snapshot(path)

    assert result["status"] == "BLOCKED"
    assert any("forecast_solar" in blocker for blocker in result["blockers"])


def solcast_payload() -> dict:
    payload = collector_payload()
    payload["provider_domain"] = "solcast_solar"
    payload["ha"]["logs"] = _logs("solar_analytics", "solcast_solar")
    return payload


def forecast_entity_payload() -> dict:
    payload = collector_payload()
    payload["forecast_source_type"] = SOURCE_FORECAST_ENTITY
    del payload["provider_domain"]
    payload["ha"]["logs"] = _logs("solar_analytics")
    return payload


def test_solcast_energy_provider_soak_passes(tmp_path: Path):
    path = write_immutable_snapshot(solcast_payload(), tmp_path)

    result = analyze_snapshot(path)

    assert result["status"] == "PASS"
    assert result["forecast_source_type"] == SOURCE_ENERGY_ENTRY
    assert result["provider_domain"] == "solcast_solar"
    assert result["blockers"] == []


def test_forecast_entity_soak_passes_without_provider_logger(tmp_path: Path):
    path = write_immutable_snapshot(forecast_entity_payload(), tmp_path)

    result = analyze_snapshot(path)

    assert result["status"] == "PASS"
    assert result["forecast_source_type"] == SOURCE_FORECAST_ENTITY
    assert result["provider_domain"] is None
    assert result["blockers"] == []


def test_solcast_soak_rejects_forecast_solar_only_log_allowlist(tmp_path: Path):
    payload = solcast_payload()
    payload["ha"]["logs"] = _logs("solar_analytics", "forecast_solar")

    with pytest.raises(CheckpointValidationError, match="ha.logs"):
        write_immutable_snapshot(payload, tmp_path)


def test_forecast_entity_soak_rejects_provider_logger(tmp_path: Path):
    payload = forecast_entity_payload()
    payload["ha"]["logs"] = _logs("solar_analytics", "forecast_solar")

    with pytest.raises(CheckpointValidationError, match="ha.logs"):
        write_immutable_snapshot(payload, tmp_path)


def test_forecast_entity_soak_rejects_provider_domain(tmp_path: Path):
    payload = forecast_entity_payload()
    payload["provider_domain"] = "forecast_solar"

    with pytest.raises(CheckpointValidationError, match="provider_domain must be absent"):
        write_immutable_snapshot(payload, tmp_path)


def test_energy_soak_requires_provider_domain(tmp_path: Path):
    payload = collector_payload()
    del payload["provider_domain"]

    with pytest.raises(CheckpointValidationError, match="provider_domain"):
        write_immutable_snapshot(payload, tmp_path)


def test_missing_forecast_source_type_is_blocked(tmp_path: Path):
    payload = collector_payload()
    del payload["forecast_source_type"]

    with pytest.raises(CheckpointValidationError, match="forecast_source_type"):
        write_immutable_snapshot(payload, tmp_path)

    assert analyze_snapshot(payload)["status"] == "BLOCKED"


def test_short_soak_window_is_blocked(tmp_path: Path):
    payload = collector_payload()
    payload["baseline_utc"] = "2026-08-05T00:00:00Z"  # 12h before collected_at_utc

    with pytest.raises(CheckpointValidationError, match=f"at least {MIN_SOAK_HOURS} hours"):
        write_immutable_snapshot(payload, tmp_path)

    result = analyze_snapshot(payload)
    assert result["status"] == "BLOCKED"
    assert any(str(MIN_SOAK_HOURS) in blocker for blocker in result["blockers"])


def test_zero_duration_soak_is_blocked(tmp_path: Path):
    payload = collector_payload()
    payload["baseline_utc"] = payload["collected_at_utc"]

    with pytest.raises(CheckpointValidationError, match=f"at least {MIN_SOAK_HOURS} hours"):
        write_immutable_snapshot(payload, tmp_path)
