"""Characterization of ``integrate_accumulators`` over a requested UTC window.

These tests pin the arithmetic that the bounded query must preserve: a bucket is
prorated by how much of it overlaps the window, a bucket that ends exactly at
the window start contributes nothing, and buckets outside the window cannot
change the answer. They are written against the public store API so they hold
whichever SQL the read uses.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from solar_analytics.storage_v2 import SolarAnalyticsV2Store

WINDOW_START = datetime(2026, 8, 3, 20, tzinfo=UTC)
BUCKET = timedelta(minutes=30)
BUCKET_WH = 100.0
BUCKET_SECONDS = 1800.0
BUCKET_SAMPLES = 6


def _bucket(start: datetime, *, quality: str = "good") -> tuple[str, float, float, int, float, str]:
    return (start.isoformat(), BUCKET_WH, BUCKET_SECONDS, BUCKET_SAMPLES, 200.0, quality)


def _store(
    tmp_path: Path, buckets: Iterable[tuple[str, float, float, int, float, str]], *, name: str
) -> SolarAnalyticsV2Store:
    store = SolarAnalyticsV2Store(tmp_path / name)
    store.initialize()
    for row in buckets:
        store.db.execute(
            "INSERT INTO v2_accumulators(interval_start_utc,energy_wh,covered_seconds,"
            "sample_count,last_power_w,quality) VALUES(?,?,?,?,?,?)",
            row,
        )
    return store


def test_window_matching_one_bucket_exactly_takes_the_whole_bucket(tmp_path: Path) -> None:
    store = _store(tmp_path, [_bucket(WINDOW_START)], name="exact.sqlite")

    result = store.integrate_accumulators(WINDOW_START, WINDOW_START + BUCKET)
    store.close()

    assert result["energy_wh"] == pytest.approx(BUCKET_WH)
    assert result["covered_seconds"] == pytest.approx(BUCKET_SECONDS)
    assert result["sample_count"] == BUCKET_SAMPLES
    assert result["quality"] == "good"


def test_partial_first_and_last_buckets_are_prorated_by_overlap(tmp_path: Path) -> None:
    store = _store(
        tmp_path,
        [_bucket(WINDOW_START), _bucket(WINDOW_START + BUCKET)],
        name="partial.sqlite",
    )

    result = store.integrate_accumulators(
        WINDOW_START + timedelta(minutes=15), WINDOW_START + timedelta(minutes=45)
    )
    store.close()

    assert result["energy_wh"] == pytest.approx(BUCKET_WH)
    assert result["covered_seconds"] == pytest.approx(BUCKET_SECONDS)
    assert result["sample_count"] == BUCKET_SAMPLES * 2


def test_bucket_ending_at_the_window_start_contributes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path, [_bucket(WINDOW_START - BUCKET)], name="before.sqlite")

    result = store.integrate_accumulators(WINDOW_START, WINDOW_START + BUCKET)
    store.close()

    assert result["energy_wh"] is None
    assert result["covered_seconds"] == 0.0
    assert result["sample_count"] == 0
    assert result["quality"] == "missing"


def test_empty_window_reports_missing_rather_than_zero_production(tmp_path: Path) -> None:
    store = _store(tmp_path, [], name="empty.sqlite")

    result = store.integrate_accumulators(WINDOW_START, WINDOW_START + BUCKET)
    store.close()

    assert result["energy_wh"] is None
    assert result["covered_seconds"] == 0.0
    assert result["quality"] == "missing"


@pytest.mark.parametrize("end_offset", [timedelta(0), timedelta(minutes=-10), -BUCKET * 3])
def test_window_ending_at_or_before_its_start_reports_missing(
    tmp_path: Path, end_offset: timedelta
) -> None:
    store = _store(
        tmp_path,
        [_bucket(WINDOW_START - BUCKET), _bucket(WINDOW_START)],
        name="inverted.sqlite",
    )

    result = store.integrate_accumulators(WINDOW_START, WINDOW_START + end_offset)
    store.close()

    assert result["energy_wh"] is None
    assert result["covered_seconds"] == 0.0
    assert result["quality"] == "missing"


def test_one_gap_bucket_inside_the_window_degrades_quality(tmp_path: Path) -> None:
    store = _store(
        tmp_path,
        [_bucket(WINDOW_START), _bucket(WINDOW_START + BUCKET, quality="gap")],
        name="gap.sqlite",
    )

    result = store.integrate_accumulators(WINDOW_START, WINDOW_START + BUCKET * 2)
    store.close()

    assert result["quality"] == "gap"
    assert result["covered_seconds"] == pytest.approx(BUCKET_SECONDS * 2)


def test_bucket_straddling_the_window_start_is_still_prorated_in(tmp_path: Path) -> None:
    """The read must reach back one bucket, or a window opening mid-bucket loses it."""

    store = _store(tmp_path, [_bucket(WINDOW_START - BUCKET)], name="straddle.sqlite")

    result = store.integrate_accumulators(
        WINDOW_START - timedelta(minutes=15), WINDOW_START + timedelta(minutes=15)
    )
    store.close()

    assert result["energy_wh"] == pytest.approx(BUCKET_WH / 2)
    assert result["covered_seconds"] == pytest.approx(BUCKET_SECONDS / 2)
    assert result["quality"] == "good"


def test_unreadable_far_past_bucket_cannot_break_the_window_read(tmp_path: Path) -> None:
    """A row the window cannot need must never be fetched, let alone parsed."""

    store = _store(
        tmp_path,
        [
            ("2025-02-30T25:61:00+00:00", BUCKET_WH, BUCKET_SECONDS, BUCKET_SAMPLES, 200.0, "good"),
            _bucket(WINDOW_START),
        ],
        name="unreadable.sqlite",
    )

    result = store.integrate_accumulators(WINDOW_START, WINDOW_START + BUCKET)
    store.close()

    assert result["energy_wh"] == pytest.approx(BUCKET_WH)
    assert result["covered_seconds"] == pytest.approx(BUCKET_SECONDS)


def test_distant_history_cannot_change_the_window_result(tmp_path: Path) -> None:
    """A year of buckets outside the window must not move the answer by a Wh."""

    window = [_bucket(WINDOW_START), _bucket(WINDOW_START + BUCKET)]
    distant = [
        _bucket(WINDOW_START - timedelta(days=365) + BUCKET * index) for index in range(2 * 48)
    ] + [_bucket(WINDOW_START + timedelta(days=7) + BUCKET * index) for index in range(48)]

    lean = _store(tmp_path, window, name="lean.sqlite")
    crowded = _store(tmp_path, distant + window, name="crowded.sqlite")

    end = WINDOW_START + BUCKET * 2
    assert lean.integrate_accumulators(WINDOW_START, end) == crowded.integrate_accumulators(
        WINDOW_START, end
    )
    lean.close()
    crowded.close()
