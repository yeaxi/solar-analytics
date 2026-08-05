from __future__ import annotations

import stat
from pathlib import Path

import pytest

from tools.pv_soak_checkpoint import (
    BASELINE_UTC,
    CheckpointValidationError,
    analyze_snapshot,
    write_immutable_snapshot,
)


ENTITY_IDS = [
    "sensor.solar_analytics_native_forecast_solar_source_status",
    "sensor.solar_analytics_analysis_status",
    "sensor.solar_analytics_last_updated",
    "sensor.solar_analytics_solar_forecast_accuracy",
    "sensor.solar_analytics_solar_future_profile",
    "sensor.garage_cerbo_gx_pv_power",
    "sensor.garage_cerbo_gx_pv_energy",
]
TABLE_NAMES = [
    "v2_lineages",
    "v2_current_profile_cache",
    "v2_snapshot_intervals",
    "v2_daily_comparisons",
    "v2_accuracy_results",
    "v2_runtime_state.last_actual_sample",
]


def collector_payload() -> dict:
    return {
        "schema_version": 1,
        "checkpoint_id": "checkpoint-001",
        "collected_at_utc": "2026-08-05T12:00:00Z",
        "baseline_utc": BASELINE_UTC,
        "collection": {
            "method": "read_only_ssh",
            "physical_calls": 0,
            "mutations": 0,
            "network_writes": 0,
            "forbidden_actions": [],
        },
        "ha": {
            "core_check": {
                "status": "PASS",
                "checked_at_utc": "2026-08-05T11:59:00Z",
                "output_digest": "sha256:" + "a" * 64,
            },
            "logs": {
                name: {
                    "fresh": True,
                    "mutation_mentions": 0,
                    "excerpt_digest": "sha256:" + "b" * 64,
                }
                for name in ("solar_analytics", "victron_mqtt", "forecast_solar")
            },
        },
        "entities": {
            entity_id: {
                "state": "ok",
                "updated_at_utc": "2026-08-05T11:58:00Z",
            }
            for entity_id in ENTITY_IDS
        },
        "sqlite": {
            "path": "/config/solar_analytics/solar_analytics.sqlite",
            "mode": "ro",
            "integrity": "ok",
            "tables": {
                table: {"present": True, "rows": 1}
                for table in TABLE_NAMES
            },
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

    # Repeating the same collection is idempotent and does not overwrite it.
    assert write_immutable_snapshot(collector_payload(), tmp_path) == path


def test_unallowlisted_entity_is_rejected(tmp_path: Path):
    payload = collector_payload()
    payload["entities"]["switch.boiler_socket_1"] = {"state": "on"}

    with pytest.raises(CheckpointValidationError, match="unallowlisted entity"):
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
