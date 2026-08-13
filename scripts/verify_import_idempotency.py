#!/usr/bin/env python3
"""Prove the historical-actuals import converges when it runs more than once.

Deterministic and self-contained. It builds a synthetic year of hourly
cumulative energy statistics (including a counter reset and both DST
transitions), runs the real reconstruction and the real store write twice
against a temporary database, and prints the row count and total kWh after
each run. Exits non-zero if the two runs disagree.

    python scripts/verify_import_idempotency.py
"""

from __future__ import annotations

import sys
import tempfile
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "solar_analytics"
_package = types.ModuleType("solar_analytics")
_package.__path__ = [str(_COMPONENT)]  # type: ignore[attr-defined]
sys.modules.setdefault("solar_analytics", _package)

from solar_analytics.imported_actuals import (  # noqa: E402
    IMPORT_PROVENANCE,
    build_imported_history,
    import_window_utc,
)
from solar_analytics.storage_v2 import SolarAnalyticsV2Store  # noqa: E402

ENTITY = "sensor.example_pv_energy"
TZ = ZoneInfo("Europe/Kyiv")
TODAY = datetime(2026, 8, 3, tzinfo=UTC).date()
RESET_AT_HOUR = 4000


def _synthetic_rows() -> list[dict[str, float]]:
    start_utc, end_utc = import_window_utc(TODAY, tz=TZ)
    rows: list[dict[str, float]] = []
    cumulative = 0.0
    hour = 0
    moment = start_utc
    while moment < end_utc:
        local_hour = moment.astimezone(TZ).hour
        cumulative += 1.5 if 8 <= local_hour <= 17 else 0.0
        rows.append(
            {"start": moment.timestamp(), "sum": 0.0 if hour == RESET_AT_HOUR else cumulative}
        )
        moment += timedelta(hours=1)
        hour += 1
    return rows


def _run_once(store: SolarAnalyticsV2Store, rows: list[dict[str, float]]) -> tuple[int, float]:
    history = build_imported_history(rows, source_entity_id=ENTITY, tz=TZ)
    store.replace_imported_actual_daily(
        source_entity_id=ENTITY,
        provenance=IMPORT_PROVENANCE,
        rows=history.as_storage_rows(),
        imported_at=datetime.now(UTC),
    )
    stored = store.list_imported_actual_daily(source_entity_id=ENTITY)
    return len(stored), round(sum(float(row["energy_kwh"]) for row in stored), 6)


def main() -> int:
    rows = _synthetic_rows()
    with tempfile.TemporaryDirectory() as directory:
        store = SolarAnalyticsV2Store(Path(directory) / "idempotency.sqlite")
        store.initialize()
        first = _run_once(store, rows)
        second = _run_once(store, rows)
        third = _run_once(store, rows)
        store.close()

    print(f"statistics rows fed to each run : {len(rows)}")
    print(f"run 1: rows={first[0]} total_kwh={first[1]}")
    print(f"run 2: rows={second[0]} total_kwh={second[1]}")
    print(f"run 3: rows={third[0]} total_kwh={third[1]}")
    if first == second == third:
        print("PASS: repeated imports converge to the same rows and the same total")
        return 0
    print("FIX_REQUIRED: repeated imports diverged")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
