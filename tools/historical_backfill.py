#!/usr/bin/env python3
"""One-shot, provenance-preserving Home Assistant Recorder backfill.

The script is intentionally separate from the live coordinator.  It reads the
Recorder database read-only, writes only the additive v2 historical-backfill
tables, and never changes scheduled native slots/current native lineage.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any
from zoneinfo import ZoneInfo

# When executed on HA, this points at /config/custom_components.
def _load_component_modules(path: str):
    """Load pure modules without importing the HA-dependent component __init__."""
    import importlib.util
    import types

    component_dir = Path(path) / "solar_analytics"
    package_name = "_solar_analytics_backfill_pkg"
    package = types.ModuleType(package_name)
    package.__path__ = [str(component_dir)]
    sys.modules[package_name] = package

    def load(name: str):
        module_path = component_dir / f"{name}.py"
        spec = importlib.util.spec_from_file_location(f"{package_name}.{name}", module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot_load_module:{module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    metrics = load("v2_metrics")
    backfill = load("backfill")
    storage = load("storage_v2")
    return metrics, backfill, storage


def _utc_timestamp(value: float) -> datetime:
    return datetime.fromtimestamp(float(value), timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _metadata_id(db: sqlite3.Connection, entity_id: str) -> int:
    row = db.execute("SELECT metadata_id FROM states_meta WHERE entity_id=?", (entity_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"recorder_entity_missing:{entity_id}")
    return int(row[0])


def _attrs(db: sqlite3.Connection, attributes_id: int | None, cache: dict[int, dict[str, Any]]) -> dict[str, Any]:
    if not attributes_id:
        return {}
    if attributes_id not in cache:
        row = db.execute("SELECT shared_attrs FROM state_attributes WHERE attributes_id=?", (attributes_id,)).fetchone()
        try:
            value = json.loads(row[0]) if row and row[0] else {}
        except (TypeError, ValueError):
            value = {}
        cache[attributes_id] = value if isinstance(value, dict) else {}
    return cache[attributes_id]


def _rows_for_entity(
    db: sqlite3.Connection,
    metadata_id: int,
    *,
    start_ts: float,
    end_ts: float,
) -> list[dict[str, Any]]:
    """Read one entity through the Recorder metadata/timestamp index."""

    before = db.execute(
        "SELECT state,last_updated_ts,attributes_id FROM states WHERE metadata_id=? AND last_updated_ts < ? "
        "ORDER BY last_updated_ts DESC LIMIT 1",
        (metadata_id, start_ts),
    ).fetchall()
    inside = db.execute(
        "SELECT state,last_updated_ts,attributes_id FROM states WHERE metadata_id=? AND last_updated_ts >= ? AND last_updated_ts <= ? "
        "ORDER BY last_updated_ts",
        (metadata_id, start_ts, end_ts),
    ).fetchall()
    after = db.execute(
        "SELECT state,last_updated_ts,attributes_id FROM states WHERE metadata_id=? AND last_updated_ts > ? "
        "ORDER BY last_updated_ts LIMIT 1",
        (metadata_id, end_ts),
    ).fetchall()
    seen: set[tuple[float, str]] = set()
    result: list[dict[str, Any]] = []
    for row in [*before, *inside, *after]:
        key = (float(row["last_updated_ts"]), str(row["state"]))
        if key in seen:
            continue
        seen.add(key)
        result.append({"state": row["state"], "timestamp_utc": _utc_timestamp(row["last_updated_ts"]), "attributes_id": row["attributes_id"]})
    result.sort(key=lambda item: item["timestamp_utc"])
    return result


def _forecast_row(
    db: sqlite3.Connection,
    metadata_id: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = db.execute(
        "SELECT state,last_updated_ts,attributes_id FROM states WHERE metadata_id=? ORDER BY last_updated_ts DESC",
        (metadata_id,),
    ).fetchall()
    cache: dict[int, dict[str, Any]] = {}
    for row in rows:
        attrs = _attrs(db, row["attributes_id"], cache)
        result = attrs.get("result")
        if isinstance(result, dict) and result:
            return (
                {
                    "state": row["state"],
                    "timestamp_utc": _utc_timestamp(row["last_updated_ts"]),
                    "attributes": attrs,
                },
                {"recorder_rows": len(rows), "result_keys": len(result)},
            )
    raise RuntimeError("forecast_history_result_missing")


def _power_samples(rows: list[dict[str, Any]], db: sqlite3.Connection) -> list[dict[str, Any]]:
    cache: dict[int, dict[str, Any]] = {}
    result: list[dict[str, Any]] = []
    for row in rows:
        attrs = _attrs(db, row["attributes_id"], cache)
        raw = row["state"]
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = None
        unit = attrs.get("unit_of_measurement")
        if value is not None and unit == "kW":
            value *= 1000.0
        if attrs.get("device_class") != "power" or attrs.get("state_class") != "measurement" or unit not in {"W", "kW"}:
            value = None
        result.append({"timestamp_utc": row["timestamp_utc"], "power_w": value})
    return result


def _energy_samples(rows: list[dict[str, Any]], db: sqlite3.Connection) -> list[dict[str, Any]]:
    cache: dict[int, dict[str, Any]] = {}
    result: list[dict[str, Any]] = []
    for row in rows:
        attrs = _attrs(db, row["attributes_id"], cache)
        raw = row["state"]
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = None
        unit = attrs.get("unit_of_measurement")
        if value is not None and unit == "Wh":
            value /= 1000.0
        if attrs.get("device_class") != "energy" or attrs.get("state_class") not in {"total", "total_increasing"} or unit not in {"kWh", "Wh"}:
            value = None
        result.append({"timestamp_utc": row["timestamp_utc"], "energy_kwh": value})
    return result


def run(args: argparse.Namespace) -> dict[str, Any]:
    metrics, backfill, storage_module = _load_component_modules(args.component_parent)
    build_backfill_daily = backfill.build_backfill_daily
    parse_legacy_forecast_result = backfill.parse_legacy_forecast_result
    SolarAnalyticsV2Store = storage_module.SolarAnalyticsV2Store
    POWER_ENTITY = metrics.POWER_ENTITY
    ENERGY_ENTITY = metrics.ENERGY_ENTITY

    recorder_path = Path(args.recorder)
    if not recorder_path.exists():
        raise RuntimeError("recorder_db_missing")
    with sqlite3.connect(f"file:{recorder_path}?mode=ro", uri=True, timeout=20) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        forecast_entity = args.forecast_entity
        forecast_metadata = _metadata_id(db, forecast_entity)
        forecast_row, forecast_audit = _forecast_row(db, forecast_metadata)
        attrs = forecast_row["attributes"]
        result = attrs.get("result")
        observed_at = forecast_row["timestamp_utc"]
        parsed = parse_legacy_forecast_result(
            result,
            source_entity=forecast_entity,
            observed_at_utc=observed_at,
            timezone_name=args.timezone,
        )
        tz = ZoneInfo(args.timezone)
        local_dates = sorted({period.interval_end_utc.astimezone(tz).date() for period in parsed.periods})
        if not local_dates:
            raise RuntimeError("forecast_history_has_no_dates")
        day_start_local = datetime.combine(local_dates[0], datetime.min.time(), tzinfo=tz)
        day_end_local = datetime.combine(local_dates[-1] + timedelta(days=1), datetime.min.time(), tzinfo=tz)
        start_ts = day_start_local.astimezone(timezone.utc).timestamp()
        end_ts = day_end_local.astimezone(timezone.utc).timestamp()
        power_metadata = _metadata_id(db, POWER_ENTITY)
        energy_metadata = _metadata_id(db, ENERGY_ENTITY)
        power_rows = _rows_for_entity(db, power_metadata, start_ts=start_ts, end_ts=end_ts)
        energy_rows = _rows_for_entity(db, energy_metadata, start_ts=start_ts, end_ts=end_ts)
        power_samples = _power_samples(power_rows, db)
        energy_samples = _energy_samples(energy_rows, db)

    run_material = {
        "source_entity": forecast_entity,
        "observed_at_utc": observed_at.isoformat(),
        "forecast_payload_sha256": parsed.payload_sha256,
        "actual_power_entity": POWER_ENTITY,
        "actual_energy_entity": ENERGY_ENTITY,
        "source_start_utc": parsed.periods[0].interval_end_utc.isoformat(),
        "source_end_utc": parsed.periods[-1].interval_end_utc.isoformat(),
    }
    run_id = "recorder-" + hashlib.sha256(json.dumps(run_material, sort_keys=True).encode()).hexdigest()[:24]
    lineage_id = "backfill-" + run_id

    daily: list[dict[str, Any]] = []
    periods_by_date: dict[date, list[Any]] = defaultdict(list)
    for period in parsed.periods:
        periods_by_date[period.interval_end_utc.astimezone(tz).date()].append(period)
    for local_date in local_dates:
        daily.append(
            build_backfill_daily(
                periods_by_date[local_date],
                power_samples,
                energy_samples,
                target_local_date=local_date,
                timezone_name=args.timezone,
            )
        )
    valid_days = sum(1 for row in daily if row["valid_paired_day"])
    summary = {
        "run_id": run_id,
        "lineage_id": lineage_id,
        "capture_mode": "historical_backfill",
        "source_kind": "historical_legacy_rest",
        "forecast_source_entity": forecast_entity,
        "forecast_source_observed_at_utc": observed_at.isoformat(),
        "forecast_payload_sha256": parsed.payload_sha256,
        "forecast_period_count": len(parsed.periods),
        "forecast_valid_period_count": sum(1 for period in parsed.periods if period.valid),
        "forecast_invalid_period_count": sum(1 for period in parsed.periods if not period.valid),
        "actual_power_recorder_rows": len(power_rows),
        "actual_energy_recorder_rows": len(energy_rows),
        "target_local_dates": [item.isoformat() for item in local_dates],
        "valid_paired_days": valid_days,
        "daily": [
            {
                key: row[key]
                for key in (
                    "local_date",
                    "forecast_coverage",
                    "actual_coverage",
                    "paired_coverage",
                    "valid_paired_day",
                    "reason",
                    "actual_kwh",
                    "forecast_kwh",
                    "signed_error_kwh",
                    "reconciliation_status",
                )
            }
            for row in daily
        ],
        "forecast_recorder_audit": forecast_audit,
    }
    if args.dry_run:
        summary["write"] = "dry_run_no_database_write"
        return summary

    store = SolarAnalyticsV2Store(args.storage)
    store.initialize()
    now = datetime.now(timezone.utc)
    with store.transaction():
        lineage_id = store.create_backfill_lineage(
            run_id=run_id,
            source_kind="historical_legacy_rest",
            source_entity=forecast_entity,
            model_fingerprint=parsed.payload_sha256,
            now=now,
        )
        store.create_backfill_run(
            run_id=run_id,
            lineage_id=lineage_id,
            source_kind="historical_legacy_rest",
            forecast_source_entity=forecast_entity,
            actual_power_entity=POWER_ENTITY,
            actual_energy_entity=ENERGY_ENTITY,
            timezone_name=args.timezone,
            source_start_utc=parsed.periods[0].interval_end_utc,
            source_end_utc=parsed.periods[-1].interval_end_utc,
            forecast_row_count=len(parsed.periods),
            actual_power_row_count=len(power_rows),
            actual_energy_row_count=len(energy_rows),
            payload_sha256=parsed.payload_sha256,
            metadata={
                "capture_mode": "historical_backfill",
                "source_kind": "historical_legacy_rest",
                "source_observed_at_utc": observed_at.isoformat(),
                "source_timezone": args.timezone,
                "native_accuracy_eligible": False,
                "native_soak_eligible": False,
            },
            status="completed",
        )
        for local_date in local_dates:
            periods = periods_by_date[local_date]
            snapshot_id, _ = store.ensure_backfill_snapshot(
                run_id=run_id,
                lineage_id=lineage_id,
                snapshot_type="historical_backfill",
                target_local_date=local_date,
                source_observed_at_utc=observed_at,
                payload_sha256=parsed.payload_sha256,
                status="historical_backfill_admitted",
                admissible=True,
                exclusion_reason=None,
            )
            store.insert_backfill_snapshot_periods(
                snapshot_id,
                [
                    {
                        "interval_start_utc": period.interval_start_utc.isoformat() if period.interval_start_utc else None,
                        "interval_end_utc": period.interval_end_utc.isoformat(),
                        "energy_wh": period.energy_wh,
                        "duration_seconds": period.duration_seconds,
                        "valid": period.valid,
                        "exclusion_reason": period.exclusion_reason,
                    }
                    for period in periods
                ],
            )
        for row in daily:
            reconciliation_status = row["reconciliation_status"]
            for interval in row["intervals"]:
                if interval.get("interval_start_utc") is None:
                    continue
                interval_payload = {**interval, "reconciliation_status": reconciliation_status}
                store.upsert_backfill_interval(
                    run_id=run_id,
                    lineage_id=lineage_id,
                    local_date=row["local_date"],
                    payload=interval_payload,
                )
            daily_payload = {key: value for key, value in row.items() if key != "intervals"}
            store.upsert_backfill_daily(run_id=run_id, lineage_id=lineage_id, payload=daily_payload)
        accuracy = {
            "status": "ready" if valid_days >= 14 else "insufficient_data",
            "capture_mode": "historical_backfill",
            "source_kind": "historical_legacy_rest",
            "accuracy_ready": False,
            "native_accuracy_ready": False,
            "valid_paired_days": valid_days,
            "required_paired_days": 14,
            "native_soak_completed": False,
            "daily": summary["daily"],
        }
        store.save_backfill_accuracy(
            run_id=run_id,
            lineage_id=lineage_id,
            generated_at=now,
            window_days=30,
            valid_days=valid_days,
            accuracy_ready=False,
            payload=accuracy,
        )
    summary["write"] = "historical_backfill_committed"
    summary["lineage_id"] = lineage_id
    summary["storage_integrity"] = store.integrity_check()
    store.close()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recorder", default="/config/home-assistant_v2.db")
    parser.add_argument("--storage", default="/config/solar_analytics/solar_analytics.sqlite")
    parser.add_argument("--component-parent", default="/config/custom_components")
    parser.add_argument("--forecast-entity", default="sensor.forecast_solar_hourly_api")
    parser.add_argument("--timezone", default="Europe/Kyiv")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        result = run(args)
    except Exception as err:  # explicit redacted boundary for operator output
        print(json.dumps({"status": "failed", "reason": f"{type(err).__name__}:{err}"}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
