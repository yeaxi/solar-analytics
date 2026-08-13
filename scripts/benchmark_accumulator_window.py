#!/usr/bin/env python3
"""Measure what one accumulator window read costs against a year of buckets.

Deterministic and self-contained. It fills a temporary store with 365 days of
30-minute accumulator buckets (17,520 rows), then reads one one-hour window
three ways and compares them:

- the legacy arm, an in-script copy of the unbounded ``interval_start_utc < end``
  read plus its prorating loop, kept here so the "before" number stays
  reproducible after the production query is bounded;
- the bounded arm, the same prorating over a read bounded on both sides;
- the shipping ``SolarAnalyticsV2Store.integrate_accumulators``.

Rows fetched and the returned window totals are exact and repeatable. Elapsed
milliseconds are reported as a median over 25 runs and will vary by machine.
The script fails only when the three arms disagree, so it is a differential
check first and a stopwatch second.

    python scripts/benchmark_accumulator_window.py
"""

from __future__ import annotations

import sqlite3
import statistics
import sys
import tempfile
import time
import types
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "solar_analytics"
_package = types.ModuleType("solar_analytics")
_package.__path__ = [str(_COMPONENT)]  # type: ignore[attr-defined]
sys.modules.setdefault("solar_analytics", _package)

from solar_analytics.storage_v2 import SolarAnalyticsV2Store  # noqa: E402

DAYS = 365
BUCKET = timedelta(minutes=30)
BUCKET_COUNT = DAYS * 48
BUCKET_WH = 100.0
BUCKET_SECONDS = 1800.0
BUCKET_SAMPLES = 6
RUNS = 25
FIRST_BUCKET = datetime(2025, 8, 3, tzinfo=UTC)
MALFORMED_START = "2025-02-30T25:61:00+00:00"

LEGACY_SQL = (
    "SELECT * FROM v2_accumulators WHERE interval_start_utc < ? ORDER BY interval_start_utc"
)
BOUNDED_SQL = (
    "SELECT * FROM v2_accumulators WHERE interval_start_utc >= ? AND interval_start_utc < ? "
    "ORDER BY interval_start_utc"
)


def _fill(store: SolarAnalyticsV2Store) -> None:
    store.db.executemany(
        "INSERT INTO v2_accumulators(interval_start_utc,energy_wh,covered_seconds,"
        "sample_count,last_power_w,quality) VALUES(?,?,?,?,?,?)",
        [
            (
                (FIRST_BUCKET + BUCKET * index).isoformat(),
                BUCKET_WH,
                BUCKET_SECONDS,
                BUCKET_SAMPLES,
                200.0,
                "good",
            )
            for index in range(BUCKET_COUNT)
        ],
    )


def _prorate(
    rows: Sequence[sqlite3.Row], start: datetime, end: datetime
) -> dict[str, float | int | str | None]:
    energy = 0.0
    covered = 0.0
    count = 0
    quality = "good"
    for row in rows:
        bucket_start = datetime.fromisoformat(
            row["interval_start_utc"].replace("Z", "+00:00")
        ).astimezone(UTC)
        bucket_end = bucket_start + BUCKET
        if bucket_end <= start:
            continue
        overlap = max(0.0, (min(bucket_end, end) - max(bucket_start, start)).total_seconds())
        if overlap <= 0:
            continue
        ratio = overlap / BUCKET.total_seconds()
        energy += float(row["energy_wh"] or 0.0) * ratio
        covered += float(row["covered_seconds"] or 0.0) * ratio
        count += int(row["sample_count"] or 0)
        if row["quality"] != "good":
            quality = "gap"
    return {
        "energy_wh": energy if covered > 0 else None,
        "covered_seconds": covered,
        "sample_count": count,
        "quality": quality if covered else "missing",
    }


def _legacy(db: sqlite3.Connection, start: datetime, end: datetime) -> dict[str, Any]:
    rows = db.execute(LEGACY_SQL, (end.isoformat(),)).fetchall()
    return {"rows": len(rows), "result": _prorate(rows, start, end)}


def _bounded(db: sqlite3.Connection, start: datetime, end: datetime) -> dict[str, Any]:
    rows = db.execute(BOUNDED_SQL, ((start - BUCKET).isoformat(), end.isoformat())).fetchall()
    return {"rows": len(rows), "result": _prorate(rows, start, end)}


def _median_ms(call: Any) -> float:
    call()
    samples = []
    for _ in range(RUNS):
        started = time.perf_counter()
        call()
        samples.append((time.perf_counter() - started) * 1000.0)
    return round(statistics.median(samples), 3)


def _plan(db: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> str:
    rows = db.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
    return " | ".join(str(row["detail"]) for row in rows)


def _comparable(result: dict[str, Any]) -> tuple[Any, ...]:
    energy = result["energy_wh"]
    return (
        None if energy is None else round(float(energy), 6),
        round(float(result["covered_seconds"]), 6),
        int(result["sample_count"] or 0),
        result["quality"],
    )


def _describe(result: dict[str, Any]) -> str:
    energy, covered, count, quality = _comparable(result)
    return f"energy_wh={energy} covered_seconds={covered} sample_count={count} quality={quality}"


def _poison_check(store: SolarAnalyticsV2Store, start: datetime, end: datetime) -> str:
    """Report whether one unreadable far-past bucket can break this window read."""

    store.db.execute(
        "INSERT INTO v2_accumulators(interval_start_utc,energy_wh,covered_seconds,"
        "sample_count,last_power_w,quality) VALUES(?,?,?,?,?,?)",
        (MALFORMED_START, BUCKET_WH, BUCKET_SECONDS, BUCKET_SAMPLES, 200.0, "good"),
    )
    try:
        return f"read succeeded ({_describe(store.integrate_accumulators(start, end))})"
    except ValueError as err:
        return f"read raised {type(err).__name__}: the row was fetched and parsed"


def main() -> int:
    window_start = FIRST_BUCKET + BUCKET * (BUCKET_COUNT - 2)
    window_end = window_start + timedelta(hours=1)

    with tempfile.TemporaryDirectory() as directory:
        store = SolarAnalyticsV2Store(Path(directory) / "accumulators.sqlite")
        store.initialize()
        _fill(store)

        legacy = _legacy(store.db, window_start, window_end)
        bounded = _bounded(store.db, window_start, window_end)
        production = store.integrate_accumulators(window_start, window_end)

        legacy_ms = _median_ms(lambda: _legacy(store.db, window_start, window_end))
        bounded_ms = _median_ms(lambda: _bounded(store.db, window_start, window_end))
        production_ms = _median_ms(lambda: store.integrate_accumulators(window_start, window_end))

        legacy_plan = _plan(store.db, LEGACY_SQL, (window_end.isoformat(),))
        bounded_plan = _plan(
            store.db,
            BOUNDED_SQL,
            ((window_start - BUCKET).isoformat(), window_end.isoformat()),
        )
        poison = _poison_check(store, window_start, window_end)
        store.close()

    reduction = legacy["rows"] / bounded["rows"] if bounded["rows"] else float("inf")
    print(f"buckets in store            : {BUCKET_COUNT} ({DAYS} days of 30-minute buckets)")
    print(f"window                      : {window_start.isoformat()} .. {window_end.isoformat()}")
    print(f"legacy arm rows fetched     : {legacy['rows']}")
    print(f"bounded arm rows fetched    : {bounded['rows']}")
    print(f"row reduction               : {reduction:.1f}x")
    print(f"legacy arm median ms        : {legacy_ms} over {RUNS} runs")
    print(f"bounded arm median ms       : {bounded_ms} over {RUNS} runs")
    print(f"production median ms        : {production_ms} over {RUNS} runs")
    print(f"legacy arm result           : {_describe(legacy['result'])}")
    print(f"bounded arm result          : {_describe(bounded['result'])}")
    print(f"production result           : {_describe(production)}")
    print(f"legacy query plan           : {legacy_plan}")
    print(f"bounded query plan          : {bounded_plan}")
    print(f"unreadable far-past bucket  : {poison}")

    agree = (
        _comparable(legacy["result"]) == _comparable(bounded["result"]) == _comparable(production)
    )
    print("PASS: all three arms report the same window" if agree else "FIX_REQUIRED: arms disagree")
    return 0 if agree else 1


if __name__ == "__main__":
    raise SystemExit(main())
