"""End-to-end coverage of the daily gate over the real forecast-to-daily path.

Every other accuracy test feeds hand-written interval rows. This module drives a
realistic Forecast.Solar day (hourly daylight cells plus the single zero-Wh cell
that spans the whole night) through ``normalize_native_wh_hours``, snapshot
storage, interval pairing and ``_process_daily_sync`` on a real SQLite store,
which is where the day-boundary numerator and the DST denominator live. The day
itself is seeded by ``realistic_day.seed_day``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from realistic_day import DAYLIGHT_CELLS, KYIV, seed_day


def _run_day(
    coordinator_shell, tmp_path: Path, target: date, tz: ZoneInfo
) -> tuple[dict, list[dict]]:
    """Drive one target local day end to end and return its daily row plus intervals."""

    seeded = seed_day(tmp_path / "coverage.sqlite", target, tz)
    now = seeded.day_end_utc + timedelta(hours=6)
    shell = coordinator_shell(store=seeded.store, time_zone=tz)

    shell._process_recent_intervals_sync(seeded.lineage_id, now)
    intervals = seeded.store.list_intervals(
        lineage_id=seeded.lineage_id, local_date=target.isoformat()
    )
    daily = shell._process_daily_sync(seeded.lineage_id, now)
    seeded.store.close()

    row = next(item for item in daily if item["local_date"] == target.isoformat())
    return row, intervals


def test_night_cell_crossing_local_midnight_is_counted_for_the_local_day(
    coordinator_shell, tmp_path: Path
) -> None:
    """The zero-Wh overnight cell is clipped at midnight, so a normal day clears the gate."""

    row, intervals = _run_day(coordinator_shell, tmp_path, date(2026, 8, 10), KYIV)

    assert len(intervals) == DAYLIGHT_CELLS + 2
    assert sum(float(item["eligible_seconds"]) for item in intervals) == 86400
    assert row["forecast_coverage"] == pytest.approx(1.0)
    assert row["actual_coverage"] == pytest.approx(1.0)
    assert row["paired_coverage"] == pytest.approx(1.0)
    assert bool(row["valid_paired_day"]) is True
    assert row["reason"] == "valid_paired_day"


def test_spring_forward_day_is_measured_against_23_hours(coordinator_shell, tmp_path: Path) -> None:
    row, intervals = _run_day(coordinator_shell, tmp_path, date(2026, 3, 29), KYIV)

    assert sum(float(item["eligible_seconds"]) for item in intervals) == 82800
    assert row["forecast_coverage"] == pytest.approx(1.0)
    assert row["paired_coverage"] == pytest.approx(1.0)
    assert bool(row["valid_paired_day"]) is True


def test_fall_back_day_is_measured_against_25_hours_and_stays_clamped(
    coordinator_shell, tmp_path: Path
) -> None:
    row, intervals = _run_day(coordinator_shell, tmp_path, date(2026, 10, 25), KYIV)

    assert sum(float(item["eligible_seconds"]) for item in intervals) == 90000
    assert row["forecast_coverage"] == pytest.approx(1.0)
    assert row["forecast_coverage"] <= 1.0
    assert row["actual_coverage"] <= 1.0
    assert row["paired_coverage"] <= 1.0
    assert bool(row["valid_paired_day"]) is True
