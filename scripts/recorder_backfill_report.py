#!/usr/bin/env python3
"""Read-only Recorder coverage/backfill report for Solar Analytics.

This script inspects an archived Home Assistant Recorder database
(``home-assistant_v2.db``) in read-only mode and emits candidate daily rows
based on archived states/statistics for whichever sensors an operator
declares on the command line. It never writes to the Recorder, never
constructs a new native profile, and never applies a *current* forecast to a
*historical* date; reconstructed snapshots are marked as such.

The integration proper is fully reusable, so this operator utility is
parameterised too. Callers supply their own actual-PV power sensor and,
optionally, their own Forecast.Solar and VRM day-ahead sensors.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from math import isfinite
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def parse_number(value: Any) -> float | None:
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return number if isfinite(number) else None


def _local(ts: float, tz: ZoneInfo) -> datetime:
    return datetime.fromtimestamp(ts, UTC).astimezone(tz)


def entity_metadata(con: sqlite3.Connection, entity_ids: tuple[str, ...]) -> dict[str, int]:
    if not entity_ids:
        return {}
    placeholders = ",".join("?" for _ in entity_ids)
    rows = con.execute(
        f"SELECT metadata_id,entity_id FROM states_meta WHERE entity_id IN ({placeholders})",
        entity_ids,
    )
    return {row[1]: row[0] for row in rows}


def statistic_metadata(con: sqlite3.Connection, entity_ids: tuple[str, ...]) -> dict[str, int]:
    if not entity_ids:
        return {}
    placeholders = ",".join("?" for _ in entity_ids)
    rows = con.execute(
        f"SELECT id,statistic_id FROM statistics_meta WHERE statistic_id IN ({placeholders})",
        entity_ids,
    )
    return {row[1]: row[0] for row in rows}


def actual_daily(con: sqlite3.Connection, metadata_id: int, tz: ZoneInfo) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT start_ts,mean FROM statistics_short_term "
        "WHERE metadata_id=? AND mean IS NOT NULL ORDER BY start_ts",
        (metadata_id,),
    ).fetchall()
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"energy_kwh": 0.0, "rows": 0, "gaps": 0}
    )
    previous: float | None = None
    for start_ts, mean in rows:
        value = parse_number(mean)
        if value is None or value < 0:
            continue
        timestamp = _local(float(start_ts), tz)
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


def state_history(
    con: sqlite3.Connection, metadata_id: int, tz: ZoneInfo
) -> list[tuple[datetime, str]]:
    rows = con.execute(
        "SELECT last_updated_ts,state FROM states WHERE metadata_id=? ORDER BY last_updated_ts",
        (metadata_id,),
    ).fetchall()
    return [(_local(float(ts), tz), str(state)) for ts, state in rows]


def nearest_day_ahead(
    history: list[tuple[datetime, str]], day_ahead_hour: int
) -> list[dict[str, Any]]:
    """Group state observations around ``day_ahead_hour`` into per-day candidates."""

    candidates: dict[str, list[tuple[float, datetime, str]]] = defaultdict(list)
    for timestamp, state in history:
        if timestamp.hour not in {day_ahead_hour - 1, day_ahead_hour}:
            continue
        if timestamp.hour == day_ahead_hour - 1 and timestamp.minute < 30:
            continue
        if timestamp.hour == day_ahead_hour and timestamp.minute > 10:
            continue
        target = timestamp.date() + timedelta(days=1)
        distance = abs(
            (
                timestamp
                - timestamp.replace(hour=day_ahead_hour, minute=0, second=0, microsecond=0)
            ).total_seconds()
        )
        candidates[target.isoformat()].append((distance, timestamp, state))
    result = []
    for target, values in sorted(candidates.items()):
        _, timestamp, state = min(values, key=lambda item: item[0])
        value = parse_number(state)
        result.append(
            {
                "target_date": target,
                "snapshot_timestamp": timestamp.isoformat(),
                "forecast_kwh": value,
                "snapshot_type": "day_ahead",
                "classification": "reconstructed_from_archived_state_at_snapshot_window",
                "state_was_archived": True,
            }
        )
    return result


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default="/config/home-assistant_v2.db",
        help="Path to the Home Assistant Recorder SQLite file (opened read-only).",
    )
    parser.add_argument(
        "--actual-power-entity",
        required=True,
        help="entity_id of the actual PV power sensor (statistic in the Recorder).",
    )
    parser.add_argument(
        "--forecast-solar-tomorrow-entity",
        default=None,
        help="Optional entity_id of the Forecast.Solar 'production tomorrow' sensor.",
    )
    parser.add_argument(
        "--vrm-tomorrow-entity",
        default=None,
        help="Optional entity_id of the VRM 'estimated production tomorrow' sensor.",
    )
    parser.add_argument(
        "--time-zone",
        default="UTC",
        help="IANA timezone the report labels local dates and snapshot windows in.",
    )
    parser.add_argument(
        "--day-ahead-hour",
        type=int,
        default=20,
        help=(
            "Local hour (0-23) around which archived day-ahead scalar snapshots "
            "should be found. Defaults to 20:00 local."
        ),
    )
    return parser


def main() -> None:
    args = _build_arg_parser().parse_args()
    if not (0 <= args.day_ahead_hour <= 23):
        raise SystemExit("--day-ahead-hour must be an integer between 0 and 23")
    try:
        tz = ZoneInfo(args.time_zone)
    except Exception as err:
        raise SystemExit(f"invalid --time-zone: {err}") from err

    entities: dict[str, str] = {"actual_power": args.actual_power_entity}
    if args.forecast_solar_tomorrow_entity:
        entities["forecast_solar_tomorrow"] = args.forecast_solar_tomorrow_entity
    if args.vrm_tomorrow_entity:
        entities["vrm_tomorrow"] = args.vrm_tomorrow_entity
    all_entity_ids = tuple(entities.values())

    path = Path(args.db)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    try:
        metadata = entity_metadata(con, all_entity_ids)
        statistics_metadata = statistic_metadata(con, all_entity_ids)
        output: dict[str, Any] = {
            "generated_at": datetime.now(UTC).isoformat(),
            "timezone": str(tz),
            "database": str(path),
            "day_ahead_hour": args.day_ahead_hour,
            "forbidden_current_forecast_applied": False,
            "metadata": metadata,
            "statistics_metadata": statistics_metadata,
            "actual_daily": (
                actual_daily(con, statistics_metadata[entities["actual_power"]], tz)
                if entities["actual_power"] in statistics_metadata
                else []
            ),
            "reconstructed_snapshots": {},
            "missing": [],
            "notes": [
                "No Recorder row is treated as a true immutable snapshot unless its archived timestamp is in the day-ahead window.",
                "No current Forecast.Solar or current VRM value is applied to historical dates.",
                "Archived scalar snapshots do not reconstruct an hourly VRM profile.",
            ],
        }
        for provider_key in ("forecast_solar_tomorrow", "vrm_tomorrow"):
            entity = entities.get(provider_key)
            if entity is None:
                continue
            if entity not in metadata:
                output["missing"].append(entity)
                continue
            output["reconstructed_snapshots"][provider_key] = nearest_day_ahead(
                state_history(con, metadata[entity], tz), args.day_ahead_hour
            )
    finally:
        con.close()
    print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
