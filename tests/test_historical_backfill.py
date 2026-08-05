from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from solar_analytics.backfill import (
    build_backfill_daily,
    integrate_power_samples,
    parse_legacy_forecast_result,
    reconcile_energy_counter,
)
from solar_analytics.storage_v2 import SolarAnalyticsV2Store
from solar_analytics.v2_metrics import ENERGY_ENTITY, POWER_ENTITY

UTC = timezone.utc


def test_legacy_forecast_parsing_is_timezone_aware_and_preserves_sparse_quality() -> None:
    result = {
        "2026-07-27 05:18:59": 10,
        "2026-07-27 06:00:00": 20,
        "2026-07-28 05:20:22": 30,
    }
    parsed = parse_legacy_forecast_result(
        result,
        source_entity="sensor.forecast_solar_hourly_api",
        observed_at_utc=datetime(2026, 7, 27, 11, 55, tzinfo=UTC),
        timezone_name="Europe/Kyiv",
    )
    assert parsed.source_kind == "historical_legacy_rest"
    assert parsed.periods[0].interval_end_utc == datetime(2026, 7, 27, 2, 18, 59, tzinfo=UTC)
    assert parsed.periods[0].valid is False
    assert parsed.periods[0].exclusion_reason == "missing_previous_boundary"
    assert parsed.periods[1].valid is True
    assert parsed.periods[2].valid is False
    assert parsed.periods[2].exclusion_reason == "internal_gap_or_period_too_long"


def test_legacy_forecast_parser_rejects_duplicate_utc_keys_and_invalid_values() -> None:
    with pytest.raises(ValueError, match="duplicate_interval_end"):
        parse_legacy_forecast_result(
            {
                "2026-07-27T05:00:00+03:00": 10,
                "2026-07-27T02:00:00+00:00": 20,
            },
            source_entity="sensor.forecast_solar_hourly_api",
            observed_at_utc=datetime(2026, 7, 27, 12, tzinfo=UTC),
            timezone_name="Europe/Kyiv",
        )

    with pytest.raises(ValueError, match="invalid_forecast_value"):
        parse_legacy_forecast_result(
            {"2026-07-27 05:00:00": -1},
            source_entity="sensor.forecast_solar_hourly_api",
            observed_at_utc=datetime(2026, 7, 27, 12, tzinfo=UTC),
            timezone_name="Europe/Kyiv",
        )


def test_power_integration_is_time_weighted_and_fails_closed_on_long_gap() -> None:
    samples = [
        {"timestamp_utc": "2026-07-27T02:00:00+00:00", "power_w": 100.0},
        {"timestamp_utc": "2026-07-27T02:05:00+00:00", "power_w": 200.0},
        {"timestamp_utc": "2026-07-27T02:25:01+00:00", "power_w": 300.0},
    ]
    result = integrate_power_samples(
        samples,
        start_utc=datetime(2026, 7, 27, 2, tzinfo=UTC),
        end_utc=datetime(2026, 7, 27, 2, 30, tzinfo=UTC),
        max_gap_seconds=900,
    )
    assert result["covered_seconds"] == pytest.approx(300.0)
    assert result["energy_wh"] == pytest.approx(100.0 * 5 / 60)
    assert result["quality"] == "gap"


def test_counter_reconciliation_handles_reset_and_tolerance() -> None:
    samples = [
        {"timestamp_utc": "2026-07-27T02:00:00+00:00", "energy_kwh": 100.0},
        {"timestamp_utc": "2026-07-27T02:30:00+00:00", "energy_kwh": 100.5},
    ]
    assert reconcile_energy_counter(samples, power_energy_wh=500.0)["status"] == "reconciled"
    reset = [
        samples[0],
        {"timestamp_utc": "2026-07-27T02:30:00+00:00", "energy_kwh": 1.0},
    ]
    assert reconcile_energy_counter(reset, power_energy_wh=500.0)["status"] == "counter_reset_detected"


def test_backfill_daily_is_invalid_when_forecast_coverage_is_below_gate() -> None:
    forecast = parse_legacy_forecast_result(
        {
            "2026-07-27 06:00:00": 100,
            "2026-07-27 07:00:00": 100,
        },
        source_entity="sensor.forecast_solar_hourly_api",
        observed_at_utc=datetime(2026, 7, 27, 12, tzinfo=UTC),
        timezone_name="Europe/Kyiv",
    )
    power = [
        {"timestamp_utc": "2026-07-27T03:00:00+00:00", "power_w": 100.0},
        {"timestamp_utc": "2026-07-27T04:00:00+00:00", "power_w": 100.0},
        {"timestamp_utc": "2026-07-27T05:00:00+00:00", "power_w": 100.0},
    ]
    daily = build_backfill_daily(
        forecast.periods,
        power,
        [],
        target_local_date=date(2026, 7, 27),
        timezone_name="Europe/Kyiv",
    )
    assert daily["valid_paired_day"] is False
    assert daily["reason"] == "coverage_below_gate"
    assert daily["forecast_coverage"] < 0.95


def test_backfill_storage_is_additive_idempotent_and_does_not_change_current_lineage(tmp_path) -> None:
    store = SolarAnalyticsV2Store(tmp_path / "backfill.sqlite")
    store.initialize()
    now = datetime(2026, 8, 3, 12, tzinfo=UTC)
    metadata = {
        "native_entry_id": "native-entry",
        "model_fingerprint": "native-model",
        "actual_energy_entity": ENERGY_ENTITY,
        "actual_power_entity": POWER_ENTITY,
        "adapter_version": "2.0.1",
        "native_contract_version": "ha_forecast_solar_energy_2026.7.4",
    }
    native_lineage = store.ensure_lineage(contract_key="native", metadata=metadata, now=now)
    backfill_lineage = store.create_backfill_lineage(
        run_id="run-1",
        source_kind="historical_legacy_rest",
        source_entity="sensor.forecast_solar_hourly_api",
        model_fingerprint="legacy-digest",
        now=now,
    )
    assert backfill_lineage != native_lineage
    assert store.current_lineage_id() == native_lineage
    store.create_backfill_run(
        run_id="run-1",
        lineage_id=backfill_lineage,
        source_kind="historical_legacy_rest",
        forecast_source_entity="sensor.forecast_solar_hourly_api",
        actual_power_entity=POWER_ENTITY,
        actual_energy_entity=ENERGY_ENTITY,
        timezone_name="Europe/Kyiv",
        source_start_utc=now,
        source_end_utc=now + timedelta(hours=1),
        forecast_row_count=2,
        actual_power_row_count=3,
        actual_energy_row_count=3,
        payload_sha256="sha256:run",
        metadata={"capture_mode": "historical_backfill"},
        status="completed",
    )
    store.create_backfill_run(
        run_id="run-1",
        lineage_id=backfill_lineage,
        source_kind="historical_legacy_rest",
        forecast_source_entity="sensor.forecast_solar_hourly_api",
        actual_power_entity=POWER_ENTITY,
        actual_energy_entity=ENERGY_ENTITY,
        timezone_name="Europe/Kyiv",
        source_start_utc=now,
        source_end_utc=now + timedelta(hours=1),
        forecast_row_count=2,
        actual_power_row_count=3,
        actual_energy_row_count=3,
        payload_sha256="sha256:run",
        metadata={"capture_mode": "historical_backfill"},
        status="completed",
    )
    assert len(store.list_backfill_runs()) == 1
    snapshot_id, inserted = store.ensure_backfill_snapshot(
        run_id="run-1",
        lineage_id=backfill_lineage,
        snapshot_type="historical_morning",
        target_local_date=date(2026, 7, 27),
        source_observed_at_utc=now,
        payload_sha256="sha256:forecast",
        status="admissible",
        admissible=True,
        exclusion_reason=None,
    )
    again, inserted_again = store.ensure_backfill_snapshot(
        run_id="run-1",
        lineage_id=backfill_lineage,
        snapshot_type="historical_morning",
        target_local_date=date(2026, 7, 27),
        source_observed_at_utc=now,
        payload_sha256="sha256:forecast",
        status="admissible",
        admissible=True,
        exclusion_reason=None,
    )
    assert snapshot_id == again and inserted is True and inserted_again is False
    store.save_backfill_accuracy(
        run_id="run-1",
        lineage_id=backfill_lineage,
        generated_at=now,
        window_days=30,
        valid_days=0,
        accuracy_ready=False,
        payload={"status": "insufficient_data"},
    )
    store.save_backfill_accuracy(
        run_id="run-1",
        lineage_id=backfill_lineage,
        generated_at=now + timedelta(minutes=1),
        window_days=30,
        valid_days=0,
        accuracy_ready=False,
        payload={"status": "insufficient_data", "rerun": True},
    )
    assert store.db.execute("SELECT count(*) FROM v2_backfill_accuracy_results WHERE run_id='run-1'").fetchone()[0] == 1
    assert store.db.execute("SELECT value_json FROM v2_runtime_state WHERE key='current_lineage_id'").fetchone()[0] == f'"{native_lineage}"'
    assert store.schema_version() == 4
    assert store.integrity_check() == "ok"
    store.close()
