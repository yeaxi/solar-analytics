"""End-to-end coverage of the daily gate over the real forecast-to-daily path.

Every other accuracy test feeds hand-written interval rows. This module drives a
realistic Forecast.Solar day (hourly daylight cells plus the single zero-Wh cell
that spans the whole night) through ``normalize_native_wh_hours``, snapshot
storage, interval pairing and ``_process_daily_sync`` on a real SQLite store,
which is where the day-boundary numerator and the DST denominator live.
"""

from __future__ import annotations

import types
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from solar_analytics.native import normalize_native_wh_hours
from solar_analytics.storage_v2 import METRIC_VERSION, NORMALIZATION_VERSION, SolarAnalyticsV2Store

KYIV = ZoneInfo("Europe/Kyiv")
# Both DST transitions in Europe/Kyiv happen between 03:00 and 04:00 local, so a
# daylight window starting at 05:00 keeps every hourly cell exactly one hour long
# on transition days.
SUNRISE_HOUR = 5
SUNSET_HOUR = 20
DAYLIGHT_WH = 900.0
ACTUAL_POWER_W = 3000.0

# Test-only fixture values; the shipping integration resolves these entity IDs
# at runtime from the user's config-flow selection.
POWER_ENTITY = "sensor.example_pv_power"
ENERGY_ENTITY = "sensor.example_pv_energy"


def _native_payload(days: tuple[date, ...], tz: ZoneInfo) -> dict[str, dict[str, float]]:
    payload: dict[str, float] = {}
    for day in days:
        for hour in range(SUNRISE_HOUR + 1, SUNSET_HOUR + 1):
            end = datetime.combine(day, time(hour), tzinfo=tz).astimezone(UTC)
            payload[end.isoformat()] = DAYLIGHT_WH
        night_end = datetime.combine(
            day + timedelta(days=1), time(SUNRISE_HOUR), tzinfo=tz
        ).astimezone(UTC)
        payload[night_end.isoformat()] = 0.0
    return {"wh_hours": payload}


def _run_day(
    coordinator_module, tmp_path: Path, target: date, tz: ZoneInfo
) -> tuple[dict, list[dict]]:
    """Drive one target local day end to end and return its daily row plus intervals."""

    profile = normalize_native_wh_hours(_native_payload((target - timedelta(days=1), target), tz))
    assert profile.status == "complete"

    day_start = datetime.combine(target, time.min, tzinfo=tz).astimezone(UTC)
    day_end = datetime.combine(target + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    scheduled = day_start + timedelta(hours=6)

    store = SolarAnalyticsV2Store(tmp_path / "coverage.sqlite")
    store.initialize()
    lineage_id = store.ensure_lineage(
        contract_key="coverage-gate",
        metadata={
            "source_kind": "native",
            "native_entry_id": "entry",
            "model_fingerprint": "sha256:coverage-gate",
            "model": {},
            "actual_energy_entity": ENERGY_ENTITY,
            "actual_power_entity": POWER_ENTITY,
            "adapter_version": "test",
            "native_contract_version": "test",
            "normalization_version": NORMALIZATION_VERSION,
            "metric_version": METRIC_VERSION,
        },
        now=scheduled,
    )
    slot_id, _ = store.ensure_snapshot_slot(
        lineage_id=lineage_id,
        source_kind="native",
        snapshot_type="morning",
        scheduled_at_utc=scheduled,
        target_local_date=target,
        timezone_name=str(tz),
        observed_at_utc=scheduled - timedelta(minutes=5),
        native_updated_at_utc=scheduled - timedelta(minutes=5),
        observation_sequence=1,
        payload_sha256=profile.payload_sha256,
        adapter_version="test",
        normalization_version=NORMALIZATION_VERSION,
        metric_version=METRIC_VERSION,
        status="admissible",
        admissible=True,
        exclusion_reason=None,
    )
    store.insert_snapshot_periods(slot_id, profile.as_storage_rows())

    sample = day_start - timedelta(hours=2)
    last_sample = day_end + timedelta(hours=2)
    while sample <= last_sample:
        local_hour = sample.astimezone(tz).hour
        daylight = SUNRISE_HOUR <= local_hour < SUNSET_HOUR
        store.add_power_sample(sample, ACTUAL_POWER_W if daylight else 0.0)
        sample += timedelta(minutes=5)

    now = day_end + timedelta(hours=6)
    shell = types.SimpleNamespace(store=store, time_zone=tz)
    coordinator = coordinator_module.SolarAnalyticsCoordinator
    coordinator._process_recent_intervals_sync(shell, lineage_id, now)
    intervals = store.list_intervals(lineage_id=lineage_id, local_date=target.isoformat())
    daily = coordinator._process_daily_sync(shell, lineage_id, now)
    store.close()

    row = next(item for item in daily if item["local_date"] == target.isoformat())
    return row, intervals


def test_night_cell_crossing_local_midnight_never_reaches_the_daily_gate(
    coordinator_module, tmp_path: Path
) -> None:
    """The zero-Wh overnight cell is dropped for both adjacent days, capping coverage."""

    row, intervals = _run_day(coordinator_module, tmp_path, date(2026, 8, 10), KYIV)

    assert len(intervals) == SUNSET_HOUR - SUNRISE_HOUR
    assert sum(float(item["eligible_seconds"]) for item in intervals) == 15 * 3600
    assert row["forecast_coverage"] == pytest.approx(0.625)
    assert row["actual_coverage"] == pytest.approx(0.625)
    assert row["paired_coverage"] == pytest.approx(0.625)
    assert bool(row["valid_paired_day"]) is False
    assert row["reason"] == "coverage_below_gate"
