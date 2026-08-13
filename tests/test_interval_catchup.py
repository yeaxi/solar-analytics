"""What the interval pass rebuilds on each five-minute tick, and what it skips.

These tests drive the real coordinator pass against a real store and count the
``integrate_accumulators`` calls it makes, because the cost being fixed here is
work done per tick rather than a returned value. The rows a finished day
produces must stay identical whether the pass rebuilt them or skipped them.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from realistic_day import DAYLIGHT_CELLS, DAYLIGHT_WH, daylight_windows, seed_day
from solar_analytics.interval_watermark import (
    INTERVAL_BUILD_REVISION,
    WATERMARK_RUNTIME_KEY,
    FinalizationWatermark,
)
from solar_analytics.storage_v2 import METRIC_VERSION, NORMALIZATION_VERSION, SolarAnalyticsV2Store

KYIV = ZoneInfo("Europe/Kyiv")
TODAY = date(2026, 8, 10)
FIRST_CELL_HOUR = 10
CELLS_PER_DAY = 3
CELL_WH = 900.0
ACTUAL_POWER_W = 900.0

POWER_ENTITY = "sensor.example_pv_power"
ENERGY_ENTITY = "sensor.example_pv_energy"


class _CountingStore:
    """Store proxy that records every window the pass integrates."""

    def __init__(self, store: SolarAnalyticsV2Store) -> None:
        self._store = store
        self.windows: list[tuple[datetime, datetime]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    def integrate_accumulators(self, start: datetime, end: datetime) -> Any:
        self.windows.append((start, end))
        return self._store.integrate_accumulators(start, end)


class _FailingStore(_CountingStore):
    """Counting store that fails partway through one day's interval writes."""

    def __init__(self, store: SolarAnalyticsV2Store, *, fail_on: date, after_writes: int) -> None:
        super().__init__(store)
        self.fail_on = fail_on.isoformat()
        self.after_writes = after_writes
        self.armed = True
        self.writes = 0

    def upsert_interval(self, payload: Any) -> None:
        if self.armed and payload["target_local_date"] == self.fail_on:
            if self.writes >= self.after_writes:
                raise RuntimeError("simulated interval write failure")
            self.writes += 1
        self._store.upsert_interval(payload)


def _cells(day: date) -> list[dict[str, Any]]:
    rows = []
    for index in range(CELLS_PER_DAY):
        start = datetime.combine(day, time(FIRST_CELL_HOUR + index), tzinfo=KYIV).astimezone(UTC)
        end = start + timedelta(hours=1)
        rows.append(
            {
                "interval_start_utc": start.isoformat(),
                "interval_end_utc": end.isoformat(),
                "energy_wh": CELL_WH,
                "duration_seconds": 3600.0,
                "valid": True,
                "exclusion_reason": None,
            }
        )
    return rows


def _seed(
    tmp_path: Path, days: Iterable[date], *, inadmissible: frozenset[date] = frozenset()
) -> tuple[SolarAnalyticsV2Store, str]:
    store = SolarAnalyticsV2Store(tmp_path / "catchup.sqlite")
    store.initialize()
    lineage_id = store.ensure_lineage(
        contract_key="catchup",
        metadata={
            "source_kind": "native",
            "native_entry_id": "entry",
            "model_fingerprint": "sha256:catchup",
            "actual_energy_entity": ENERGY_ENTITY,
            "actual_power_entity": POWER_ENTITY,
            "adapter_version": "test",
            "native_contract_version": "test",
            "normalization_version": NORMALIZATION_VERSION,
            "metric_version": METRIC_VERSION,
        },
        now=datetime(2026, 8, 1, tzinfo=UTC),
    )
    for day in sorted(days):
        admissible = day not in inadmissible
        scheduled = datetime.combine(day - timedelta(days=1), time(6), tzinfo=KYIV).astimezone(UTC)
        slot_id, _ = store.ensure_snapshot_slot(
            lineage_id=lineage_id,
            source_kind="native",
            snapshot_type="morning",
            scheduled_at_utc=scheduled,
            target_local_date=day,
            timezone_name=str(KYIV),
            observed_at_utc=scheduled - timedelta(minutes=5),
            native_updated_at_utc=scheduled - timedelta(minutes=5),
            observation_sequence=1,
            payload_sha256="sha256:profile",
            adapter_version="test",
            normalization_version=NORMALIZATION_VERSION,
            metric_version=METRIC_VERSION,
            status="admissible" if admissible else "blocked",
            admissible=admissible,
            exclusion_reason=None if admissible else "observed_after_schedule",
        )
        store.insert_snapshot_periods(slot_id, _cells(day))
        sample = datetime.combine(day, time(FIRST_CELL_HOUR), tzinfo=KYIV).astimezone(
            UTC
        ) - timedelta(minutes=10)
        last = sample + timedelta(hours=CELLS_PER_DAY, minutes=20)
        while sample <= last:
            store.add_power_sample(sample, ACTUAL_POWER_W)
            sample += timedelta(minutes=5)
    return store, lineage_id


def _local_noon(day: date) -> datetime:
    return datetime.combine(day, time(12), tzinfo=KYIV).astimezone(UTC)


def _marker(store: SolarAnalyticsV2Store) -> Any:
    return store.get_runtime(WATERMARK_RUNTIME_KEY)


def _days_touched(counter: _CountingStore) -> set[date]:
    return {start.astimezone(KYIV).date() for start, _ in counter.windows}


def test_an_absent_marker_rebuilds_every_retained_admissible_day_once(
    coordinator_shell, tmp_path: Path
) -> None:
    days = [TODAY - timedelta(days=offset) for offset in (4, 3, 2, 1)]
    store, lineage_id = _seed(tmp_path, days)
    counter = _CountingStore(store)
    shell = coordinator_shell(store=counter, time_zone=KYIV)

    shell._process_recent_intervals_sync(lineage_id, _local_noon(TODAY))

    assert _days_touched(counter) == set(days)
    assert len(counter.windows) == len(days) * CELLS_PER_DAY
    assert len(store.list_intervals(lineage_id=lineage_id)) == len(days) * CELLS_PER_DAY
    assert _marker(store)["finalized_through"] == (TODAY - timedelta(days=1)).isoformat()
    assert _marker(store)["revision"] == INTERVAL_BUILD_REVISION
    store.close()


def test_a_second_pass_integrates_nothing_for_days_already_final(
    coordinator_shell, tmp_path: Path
) -> None:
    days = [TODAY - timedelta(days=offset) for offset in (3, 2, 1)]
    store, lineage_id = _seed(tmp_path, days)
    counter = _CountingStore(store)
    shell = coordinator_shell(store=counter, time_zone=KYIV)
    now = _local_noon(TODAY)

    shell._process_recent_intervals_sync(lineage_id, now)
    first_rows = store.list_intervals(lineage_id=lineage_id)
    counter.windows.clear()
    shell._process_recent_intervals_sync(lineage_id, now)

    assert counter.windows == []
    assert store.list_intervals(lineage_id=lineage_id) == first_rows
    store.close()


def test_today_and_the_day_inside_the_finalization_margin_keep_reprocessing(
    coordinator_shell, tmp_path: Path
) -> None:
    """At 00:30 local, yesterday is not final yet, so it must still be rebuilt.

    Today is rebuilt too, but its forecast cells are still in the future at that
    hour, so the only day with elapsed cells to integrate is yesterday.
    """

    days = [TODAY - timedelta(days=offset) for offset in (3, 2, 1)] + [TODAY]
    store, lineage_id = _seed(tmp_path, days)
    counter = _CountingStore(store)
    shell = coordinator_shell(store=counter, time_zone=KYIV)
    just_after_midnight = datetime.combine(TODAY, time(0, 30), tzinfo=KYIV).astimezone(UTC)

    shell._process_recent_intervals_sync(lineage_id, just_after_midnight)
    assert _marker(store)["finalized_through"] == (TODAY - timedelta(days=2)).isoformat()

    counter.windows.clear()
    shell._process_recent_intervals_sync(lineage_id, just_after_midnight)

    assert _days_touched(counter) == {TODAY - timedelta(days=1)}
    assert len(counter.windows) == CELLS_PER_DAY
    assert _marker(store)["finalized_through"] == (TODAY - timedelta(days=2)).isoformat()
    store.close()


def test_the_marker_advances_once_the_margin_has_passed(coordinator_shell, tmp_path: Path) -> None:
    days = [TODAY - timedelta(days=1)]
    store, lineage_id = _seed(tmp_path, days)
    counter = _CountingStore(store)
    shell = coordinator_shell(store=counter, time_zone=KYIV)

    before_margin = datetime.combine(TODAY, time(0, 30), tzinfo=KYIV).astimezone(UTC)
    shell._process_recent_intervals_sync(lineage_id, before_margin)
    assert (
        _marker(store) is None
        or _marker(store)["finalized_through"] != (TODAY - timedelta(days=1)).isoformat()
    )

    after_margin = datetime.combine(TODAY, time(1, 0), tzinfo=KYIV).astimezone(UTC)
    shell._process_recent_intervals_sync(lineage_id, after_margin)

    assert _marker(store)["finalized_through"] == (TODAY - timedelta(days=1)).isoformat()
    store.close()


def test_a_day_with_no_admissible_slot_does_not_stall_the_marker(
    coordinator_shell, tmp_path: Path
) -> None:
    days = [TODAY - timedelta(days=offset) for offset in (3, 2, 1)]
    blocked = TODAY - timedelta(days=2)
    store, lineage_id = _seed(tmp_path, days, inadmissible=frozenset({blocked}))
    counter = _CountingStore(store)
    shell = coordinator_shell(store=counter, time_zone=KYIV)

    shell._process_recent_intervals_sync(lineage_id, _local_noon(TODAY))

    assert _days_touched(counter) == {TODAY - timedelta(days=3), TODAY - timedelta(days=1)}
    assert _marker(store)["finalized_through"] == (TODAY - timedelta(days=1)).isoformat()
    assert store.list_intervals(lineage_id=lineage_id, local_date=blocked.isoformat()) == []
    store.close()


def _analytics_fields(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return [
        (
            row["interval_start_utc"],
            row["interval_end_utc"],
            row["target_local_date"],
            row["forecast_energy_wh"],
            row["actual_energy_wh"],
            row["eligible_seconds"],
            row["actual_covered_seconds"],
            row["actual_valid"],
            row["paired_valid"],
            row["validity_reason"],
        )
        for row in rows
    ]


def test_a_direct_upgrade_replays_a_pre_clipping_day_and_clears_the_gate(
    coordinator_shell, tmp_path: Path
) -> None:
    """A database written before the day-boundary fix holds a day that cannot pass.

    That build dropped every forecast cell straddling local midnight, so a full
    day stored only its 15 daylight intervals and a daily row whose coverage was
    15 hours over 24. It left no finalization marker, so the day must replay once
    and come back as 17 intervals that clear the gate.
    """

    target = date(2026, 8, 10)
    seeded = seed_day(tmp_path / "upgrade.sqlite", target, KYIV)
    store, lineage_id = seeded.store, seeded.lineage_id
    old_coverage = DAYLIGHT_CELLS * 3600.0 / 86400.0
    assert old_coverage == 0.625

    for start, end in daylight_windows(target, KYIV):
        store.upsert_interval(
            {
                "lineage_id": lineage_id,
                "interval_start_utc": start.isoformat(),
                "interval_end_utc": end.isoformat(),
                "target_local_date": target.isoformat(),
                "forecast_energy_wh": DAYLIGHT_WH,
                "actual_energy_wh": DAYLIGHT_WH,
                "eligible_seconds": 3600.0,
                "actual_covered_seconds": 3600.0,
                "forecast_valid": True,
                "actual_valid": True,
                "paired_valid": True,
                "validity_reason": "paired",
                "reconciliation_status": "not_observed",
            }
        )
    store.upsert_daily(
        {
            "lineage_id": lineage_id,
            "local_date": target.isoformat(),
            "morning_slot_id": seeded.slot_id,
            "forecast_coverage": old_coverage,
            "actual_coverage": old_coverage,
            "paired_coverage": old_coverage,
            "valid_paired_day": False,
            "reason": "coverage_below_gate",
            "reconciliation_status": "not_observed",
        }
    )
    assert len(store.list_intervals(lineage_id=lineage_id)) == DAYLIGHT_CELLS
    assert _marker(store) is None

    counter = _CountingStore(store)
    shell = coordinator_shell(store=counter, time_zone=KYIV)
    now = seeded.day_end_utc + timedelta(hours=6)
    shell._process_recent_intervals_sync(lineage_id, now)
    daily = shell._process_daily_sync(lineage_id, now)

    intervals = store.list_intervals(lineage_id=lineage_id, local_date=target.isoformat())
    row = next(item for item in daily if item["local_date"] == target.isoformat())
    assert len(intervals) == DAYLIGHT_CELLS + 2
    assert sum(float(item["eligible_seconds"]) for item in intervals) == 86400
    assert row["forecast_coverage"] == pytest.approx(1.0)
    assert row["actual_coverage"] == pytest.approx(1.0)
    assert row["paired_coverage"] == pytest.approx(1.0)
    assert bool(row["valid_paired_day"]) is True
    assert row["reason"] == "valid_paired_day"
    assert _marker(store)["finalized_through"] == target.isoformat()

    counter.windows.clear()
    shell._process_recent_intervals_sync(lineage_id, now)

    assert counter.windows == []
    assert store.list_intervals(lineage_id=lineage_id, local_date=target.isoformat()) == intervals
    store.close()


def test_a_failure_partway_through_a_day_leaves_that_day_out_of_the_marker(
    coordinator_shell, tmp_path: Path
) -> None:
    """The marker may only cover a day whose rows are all written.

    The write that fails here happens after one interval row of that day has
    already been committed and before the marker is written, which is the
    ordering the pass depends on. The day must be absent from the marker, and the
    retry must converge on the same rows rather than add a second copy.
    """

    written_day = TODAY - timedelta(days=2)
    failing_day = TODAY - timedelta(days=1)
    store, lineage_id = _seed(tmp_path, [written_day, failing_day])
    proxy = _FailingStore(store, fail_on=failing_day, after_writes=1)
    shell = coordinator_shell(store=proxy, time_zone=KYIV)
    now = _local_noon(TODAY)

    with pytest.raises(RuntimeError):
        shell._process_recent_intervals_sync(lineage_id, now)

    partial = store.list_intervals(lineage_id=lineage_id, local_date=failing_day.isoformat())
    assert _marker(store)["finalized_through"] == written_day.isoformat()
    assert len(partial) == 1

    proxy.armed = False
    proxy.windows.clear()
    shell._process_recent_intervals_sync(lineage_id, now)

    assert _days_touched(proxy) == {failing_day}
    assert (
        len(store.list_intervals(lineage_id=lineage_id, local_date=failing_day.isoformat()))
        == CELLS_PER_DAY
    )
    assert len(store.list_intervals(lineage_id=lineage_id)) == 2 * CELLS_PER_DAY
    assert _marker(store)["finalized_through"] == failing_day.isoformat()
    store.close()


def test_a_lost_marker_replays_the_same_rows_without_duplicating_them(
    coordinator_shell, tmp_path: Path
) -> None:
    days = [TODAY - timedelta(days=offset) for offset in (2, 1)]
    store, lineage_id = _seed(tmp_path, days)
    shell = coordinator_shell(store=store, time_zone=KYIV)
    now = _local_noon(TODAY)

    shell._process_recent_intervals_sync(lineage_id, now)
    first = _analytics_fields(store.list_intervals(lineage_id=lineage_id))
    store.db.execute("DELETE FROM v2_runtime_state WHERE key=?", (WATERMARK_RUNTIME_KEY,))
    shell._process_recent_intervals_sync(lineage_id, now)

    assert _analytics_fields(store.list_intervals(lineage_id=lineage_id)) == first
    assert _marker(store)["finalized_through"] == (TODAY - timedelta(days=1)).isoformat()
    store.close()


@pytest.mark.parametrize("dst_day", [date(2026, 3, 29), date(2026, 10, 25)])
def test_a_dst_day_becomes_final_one_hour_after_its_own_midnight(
    coordinator_shell, tmp_path: Path, dst_day: date
) -> None:
    """The 23-hour and 25-hour days must not be finalized 24 hours after midnight."""

    store, lineage_id = _seed(tmp_path, [dst_day])
    shell = coordinator_shell(store=store, time_zone=KYIV)
    next_midnight = datetime.combine(dst_day + timedelta(days=1), time.min, tzinfo=KYIV)

    shell._process_recent_intervals_sync(
        lineage_id, (next_midnight + timedelta(minutes=45)).astimezone(UTC)
    )
    assert _marker(store) is None

    shell._process_recent_intervals_sync(
        lineage_id, (next_midnight + timedelta(hours=1)).astimezone(UTC)
    )
    assert _marker(store)["finalized_through"] == dst_day.isoformat()
    assert len(store.list_intervals(lineage_id=lineage_id)) == CELLS_PER_DAY
    store.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revision", "pre-clipping-build"),
        ("lineage_id", "some-other-lineage"),
        ("timezone", "Europe/Warsaw"),
        ("finalized_through", (TODAY + timedelta(days=30)).isoformat()),
    ],
)
def test_a_marker_that_does_not_describe_this_build_forces_a_rebuild(
    coordinator_shell, tmp_path: Path, field: str, value: str
) -> None:
    days = [TODAY - timedelta(days=offset) for offset in (2, 1)]
    store, lineage_id = _seed(tmp_path, days)
    stored = FinalizationWatermark(
        revision=INTERVAL_BUILD_REVISION,
        lineage_id=lineage_id,
        timezone=str(KYIV),
        finalized_through=TODAY - timedelta(days=1),
    ).as_runtime_value()
    stored[field] = value
    store.set_runtime(WATERMARK_RUNTIME_KEY, stored)
    counter = _CountingStore(store)
    shell = coordinator_shell(store=counter, time_zone=KYIV)

    shell._process_recent_intervals_sync(lineage_id, _local_noon(TODAY))

    assert _days_touched(counter) == set(days)
    assert _marker(store)["revision"] == INTERVAL_BUILD_REVISION
    assert _marker(store)["lineage_id"] == lineage_id
    store.close()
