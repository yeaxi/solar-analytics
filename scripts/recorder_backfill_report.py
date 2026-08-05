#!/usr/bin/env python3
"""Read-only Recorder coverage/backfill report for Solar Analytics.

This script never writes Home Assistant config or the Recorder database. It emits
only candidate daily rows based on archived states/statistics and explicitly marks
reconstructed snapshots; current forecasts are never applied to history.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
KYIV = ZoneInfo("Europe/Kyiv")
ENTITIES = {
    "actual_power": "sensor.energy_solar_production_power",
    "forecast_solar_today": "sensor.energy_production_today",
    "forecast_solar_tomorrow": "sensor.energy_production_tomorrow",
    "vrm_today": "sensor.victron_remote_monitoring_estimated_energy_production_today",
    "vrm_tomorrow": "sensor.victron_remote_monitoring_estimated_energy_production_tomorrow",
}


def parse_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC).astimezone(KYIV)


def entity_metadata(con: sqlite3.Connection) -> dict[str, int]:
    wanted = tuple(ENTITIES.values())
    rows = con.execute(
        f"SELECT metadata_id,entity_id FROM states_meta WHERE entity_id IN ({','.join('?' for _ in wanted)})",
        wanted,
    )
    return {row[1]: row[0] for row in rows}


def statistic_metadata(con: sqlite3.Connection) -> dict[str, int]:
    wanted = tuple(ENTITIES.values())
    rows = con.execute(
        f"SELECT id,statistic_id FROM statistics_meta WHERE statistic_id IN ({','.join('?' for _ in wanted)})",
        wanted,
    )
    return {row[1]: row[0] for row in rows}


def actual_daily(con: sqlite3.Connection, metadata_id: int) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT start_ts,mean FROM statistics_short_term WHERE metadata_id=? AND mean IS NOT NULL ORDER BY start_ts",
        (metadata_id,),
    ).fetchall()
    grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {"energy_kwh": 0.0, "rows": 0, "gaps": 0})
    previous: float | None = None
    for start_ts, mean in rows:
        value = parse_number(mean)
        if value is None or value < 0:
            continue
        timestamp = local(float(start_ts))
        key = timestamp.date().isoformat()
        item = grouped[key]
        item["energy_kwh"] += value * 300.0 / 3_600_000.0
        item["rows"] += 1
        if previous is not None and float(start_ts) - previous > 900:
            item["gaps"] += 1
        previous = float(start_ts)
    result = []
    for key, item in sorted(grouped.items()):
        item["local_date"] = key
        item["coverage_ratio_5m"] = min(item["rows"] / 288.0, 1.0)
        item["classification"] = "actual_from_archived_short_term_statistics"
        result.append(item)
    return result


def state_history(con: sqlite3.Connection, metadata_id: int) -> list[tuple[datetime, str]]:
    rows = con.execute(
        "SELECT last_updated_ts,state FROM states WHERE metadata_id=? ORDER BY last_updated_ts",
        (metadata_id,),
    ).fetchall()
    return [(local(float(ts)), str(state)) for ts, state in rows]


def nearest_day_ahead(history: list[tuple[datetime, str]]) -> list[dict[str, Any]]:
    candidates: dict[str, list[tuple[float, datetime, str]]] = defaultdict(list)
    for timestamp, state in history:
        if timestamp.hour == 19 or timestamp.hour == 20:
            if timestamp.hour == 19 and timestamp.minute < 30:
                continue
            if timestamp.hour == 20 and timestamp.minute > 10:
                continue
            target = timestamp.date() + timedelta(days=1)
            distance = abs((timestamp - timestamp.replace(hour=20, minute=0, second=0, microsecond=0)).total_seconds())
            candidates[target.isoformat()].append((distance, timestamp, state))
    result = []
    for target, values in sorted(candidates.items()):
        _, timestamp, state = min(values, key=lambda item: item[0])
        value = parse_number(state)
        result.append({
            "target_date": target,
            "snapshot_timestamp": timestamp.isoformat(),
            "forecast_kwh": value,
            "snapshot_type": "day_ahead",
            "classification": "reconstructed_from_archived_state_at_snapshot_window",
            "state_was_archived": True,
        })
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="/config/home-assistant_v2.db")
    args = parser.parse_args()
    path = Path(args.db)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    metadata = entity_metadata(con)
    statistics_metadata = statistic_metadata(con)
    output: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "timezone": "Europe/Kyiv",
        "database": str(path),
        "forbidden_current_forecast_applied": False,
        "metadata": metadata,
        "statistics_metadata": statistics_metadata,
        "actual_daily": actual_daily(con, statistics_metadata[ENTITIES["actual_power"]]) if ENTITIES["actual_power"] in statistics_metadata else [],
        "reconstructed_snapshots": {},
        "missing": [],
        "notes": [
            "No Recorder row is treated as a true immutable snapshot unless its archived timestamp is in the day-ahead window.",
            "No current Forecast.Solar or current VRM value is applied to historical dates.",
            "Archived scalar snapshots do not reconstruct an hourly VRM profile.",
        ],
    }
    for provider_key in ("forecast_solar_tomorrow", "vrm_tomorrow"):
        entity = ENTITIES[provider_key]
        if entity not in metadata:
            output["missing"].append(entity)
            continue
        output["reconstructed_snapshots"][provider_key] = nearest_day_ahead(state_history(con, metadata[entity]))
    con.close()
    print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
