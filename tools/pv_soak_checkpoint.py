#!/usr/bin/env python3
"""Validate, freeze, and analyze a read-only Solar Analytics soak checkpoint.

This module deliberately performs no network access, Home Assistant calls, SSH,
SQLite access, or provider requests. The cron agent is the collector: it gathers
allowlisted evidence through an independently read-only method and passes a
small JSON envelope here. This module validates the envelope, writes an
immutable local snapshot, and produces a bounded PASS/BLOCKED analysis.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BASELINE_UTC = "2026-08-03T19:28:33Z"
SCHEMA_VERSION = 2

# The fixed status entities Solar Analytics always publishes. Any Solar
# Analytics install exposes exactly these five; every soak envelope must
# include them.
FIXED_STATUS_ENTITIES = frozenset(
    {
        "sensor.solar_analytics_native_forecast_solar_source_status",
        "sensor.solar_analytics_analysis_status",
        "sensor.solar_analytics_last_updated",
        "sensor.solar_analytics_solar_forecast_accuracy",
        "sensor.solar_analytics_solar_future_profile",
    }
)
REQUIRED_TABLES = frozenset(
    {
        "v2_lineages",
        "v2_current_profile_cache",
        "v2_snapshot_intervals",
        "v2_daily_comparisons",
        "v2_accuracy_results",
        "v2_runtime_state.last_actual_sample",
    }
)
REQUIRED_LOGS = frozenset({"solar_analytics", "victron_mqtt", "forecast_solar"})
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class CheckpointValidationError(ValueError):
    """Raised when a collector envelope violates the read-only contract."""


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CheckpointValidationError(f"{name} must be an object")
    return value


def _timestamp(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _TIMESTAMP_RE.fullmatch(value):
        raise CheckpointValidationError(f"{name} must be an explicit UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise CheckpointValidationError(f"{name} is not a valid UTC timestamp") from exc
    if parsed.tzinfo != UTC:
        raise CheckpointValidationError(f"{name} must be UTC")
    return value


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise CheckpointValidationError(f"{name} must be a sha256:<64 hex> digest")
    return value


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _payload_digest(payload: Mapping[str, Any]) -> str:
    unsigned = dict(payload)
    unsigned.pop("snapshot_digest", None)
    return "sha256:" + hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()


def validate_collector_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a JSON-compatible copy of a collector envelope."""
    root = _mapping(payload, "payload")
    if root.get("schema_version") != SCHEMA_VERSION:
        raise CheckpointValidationError(f"schema_version must be {SCHEMA_VERSION}")

    checkpoint_id = root.get("checkpoint_id")
    if not isinstance(checkpoint_id, str) or not checkpoint_id or len(checkpoint_id) > 128:
        raise CheckpointValidationError("checkpoint_id must be a non-empty short string")
    _timestamp(root.get("collected_at_utc"), "collected_at_utc")
    if root.get("baseline_utc") != BASELINE_UTC:
        raise CheckpointValidationError(f"baseline_utc must equal {BASELINE_UTC}")

    collection = _mapping(root.get("collection"), "collection")
    if collection.get("method") != "read_only_ssh":
        raise CheckpointValidationError("collection.method must be read_only_ssh")
    for key in ("physical_calls", "mutations", "network_writes"):
        value = collection.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise CheckpointValidationError(f"collection.{key} must be an integer")
        if value != 0:
            raise CheckpointValidationError(f"collection.{key} must be 0")
    forbidden_actions = collection.get("forbidden_actions")
    if forbidden_actions != []:
        raise CheckpointValidationError("collection.forbidden_actions must be an empty list")

    ha = _mapping(root.get("ha"), "ha")
    core_check = _mapping(ha.get("core_check"), "ha.core_check")
    if core_check.get("status") != "PASS":
        raise CheckpointValidationError("ha.core_check.status must be PASS")
    _timestamp(core_check.get("checked_at_utc"), "ha.core_check.checked_at_utc")
    _digest(core_check.get("output_digest"), "ha.core_check.output_digest")

    logs = _mapping(ha.get("logs"), "ha.logs")
    if set(logs) != REQUIRED_LOGS:
        raise CheckpointValidationError(
            "ha.logs must contain exactly the allowlisted fresh log streams"
        )
    for name in REQUIRED_LOGS:
        log = _mapping(logs[name], f"ha.logs.{name}")
        if not isinstance(log.get("fresh"), bool):
            raise CheckpointValidationError(f"ha.logs.{name}.fresh must be boolean")
        if not isinstance(log.get("mutation_mentions"), int) or log.get("mutation_mentions") != 0:
            raise CheckpointValidationError(f"ha.logs.{name}.mutation_mentions must be 0")
        _digest(log.get("excerpt_digest"), f"ha.logs.{name}.excerpt_digest")

    actual_pv = _mapping(root.get("actual_pv_entities"), "actual_pv_entities")
    for key in ("power", "energy"):
        value = actual_pv.get(key)
        if not isinstance(value, str) or not value:
            raise CheckpointValidationError(
                f"actual_pv_entities.{key} must be a non-empty entity id"
            )
    allowed_entities = FIXED_STATUS_ENTITIES | {actual_pv["power"], actual_pv["energy"]}

    entities = _mapping(root.get("entities"), "entities")
    unexpected_entities = set(entities) - allowed_entities
    missing_entities = allowed_entities - set(entities)
    if unexpected_entities:
        raise CheckpointValidationError("unallowlisted entity: " + sorted(unexpected_entities)[0])
    if missing_entities:
        raise CheckpointValidationError(
            "missing allowlisted entity: " + sorted(missing_entities)[0]
        )
    for entity_id, entity in entities.items():
        entity_obj = _mapping(entity, f"entities.{entity_id}")
        _timestamp(entity_obj.get("updated_at_utc"), f"entities.{entity_id}.updated_at_utc")

    sqlite = _mapping(root.get("sqlite"), "sqlite")
    if sqlite.get("mode") != "ro":
        raise CheckpointValidationError("sqlite.mode must be ro")
    if sqlite.get("integrity") != "ok":
        raise CheckpointValidationError("sqlite.integrity must be ok")
    _timestamp(sqlite.get("last_actual_sample"), "sqlite.last_actual_sample")
    tables = _mapping(sqlite.get("tables"), "sqlite.tables")
    if set(tables) != REQUIRED_TABLES:
        raise CheckpointValidationError("sqlite.tables must contain exactly the allowlisted tables")
    for table, table_obj in tables.items():
        obj = _mapping(table_obj, f"sqlite.tables.{table}")
        if obj.get("present") is not True:
            raise CheckpointValidationError(f"sqlite table not present: {table}")
        if not isinstance(obj.get("rows"), int) or obj.get("rows") < 0:
            raise CheckpointValidationError(
                f"sqlite.tables.{table}.rows must be a non-negative integer"
            )

    return json.loads(json.dumps(root, ensure_ascii=False))


def _blockers(payload: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    try:
        validated = validate_collector_payload(payload)
    except CheckpointValidationError as exc:
        return [str(exc)]

    collection = validated["collection"]
    ha = validated["ha"]
    if any(collection[key] != 0 for key in ("physical_calls", "mutations", "network_writes")):
        blockers.append("collection contains a non-zero write or physical-call count")
    if ha["core_check"]["status"] != "PASS":
        blockers.append("ha.core_check is not PASS")
    for name, log in ha["logs"].items():
        if not log["fresh"]:
            blockers.append(f"stale log stream: {name}")
        if log["mutation_mentions"] != 0:
            blockers.append(f"mutation mention in log stream: {name}")
    for entity_id, entity in validated["entities"].items():
        if entity.get("state") in (None, "", "unknown", "unavailable"):
            blockers.append(f"unknown or unavailable PV evidence: {entity_id}")
    return blockers


def analyze_snapshot(snapshot: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    """Return bounded analysis without emitting raw HA/log/SQLite content."""
    source_path: Path | None = None
    if isinstance(snapshot, (str, Path)):
        source_path = Path(snapshot)
        try:
            payload = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "status": "BLOCKED",
                "blockers": [f"cannot read snapshot: {exc.__class__.__name__}"],
                "physical_calls": None,
                "scope": "PV-only",
            }
    else:
        payload = snapshot

    blockers = _blockers(payload)
    result: dict[str, Any] = {
        "status": "PASS" if not blockers else "BLOCKED",
        "scope": "PV-only",
        "checkpoint_id": payload.get("checkpoint_id") if isinstance(payload, Mapping) else None,
        "collected_at_utc": payload.get("collected_at_utc")
        if isinstance(payload, Mapping)
        else None,
        "baseline_utc": payload.get("baseline_utc") if isinstance(payload, Mapping) else None,
        "physical_calls": None,
        "mutations": None,
        "network_writes": None,
        "blockers": blockers,
    }
    if isinstance(payload, Mapping) and isinstance(payload.get("collection"), Mapping):
        for key in ("physical_calls", "mutations", "network_writes"):
            result[key] = payload["collection"].get(key)
    if source_path is not None and source_path.is_file():
        stored_digest = payload.get("snapshot_digest") if isinstance(payload, Mapping) else None
        if not isinstance(stored_digest, str) or not _DIGEST_RE.fullmatch(stored_digest):
            result["blockers"].append("snapshot_digest is missing or malformed")
        else:
            result["snapshot_digest"] = stored_digest
            try:
                expected_digest = _payload_digest(payload)
            except (TypeError, ValueError):
                expected_digest = None
            if stored_digest != expected_digest:
                result["blockers"].append(
                    "snapshot_digest does not match canonical snapshot content"
                )
        result["status"] = "PASS" if not result["blockers"] else "BLOCKED"
    return result


def write_immutable_snapshot(payload: Mapping[str, Any], output_dir: str | Path) -> Path:
    """Validate and write a content-addressed, no-overwrite JSON snapshot."""
    validated = validate_collector_payload(payload)
    digest = _payload_digest(validated)
    stamped = datetime.fromisoformat(validated["collected_at_utc"][:-1] + "+00:00").strftime(
        "%Y%m%dT%H%M%SZ"
    )
    filename = f"checkpoint_{stamped}_{digest[7:19]}.json"
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    stored = dict(validated)
    stored["snapshot_digest"] = digest
    data = _canonical_bytes(stored) + b"\n"

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o444)
    except FileExistsError as err:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("snapshot_digest") != digest:
            raise CheckpointValidationError("immutable snapshot filename collision") from err
        return path
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _template() -> dict[str, Any]:
    example_power = "sensor.example_pv_power"
    example_energy = "sensor.example_pv_energy"
    entities = sorted(FIXED_STATUS_ENTITIES | {example_power, example_energy})
    return {
        "schema_version": SCHEMA_VERSION,
        "checkpoint_id": "replace-me",
        "collected_at_utc": "YYYY-MM-DDTHH:MM:SSZ",
        "baseline_utc": BASELINE_UTC,
        "collection": {
            "method": "read_only_ssh",
            "physical_calls": 0,
            "mutations": 0,
            "network_writes": 0,
            "forbidden_actions": [],
        },
        "actual_pv_entities": {"power": example_power, "energy": example_energy},
        "ha": {
            "core_check": {
                "status": "PASS",
                "checked_at_utc": "YYYY-MM-DDTHH:MM:SSZ",
                "output_digest": "sha256:" + "0" * 64,
            },
            "logs": {
                name: {
                    "fresh": True,
                    "mutation_mentions": 0,
                    "excerpt_digest": "sha256:" + "0" * 64,
                }
                for name in sorted(REQUIRED_LOGS)
            },
        },
        "entities": {
            entity_id: {"state": None, "updated_at_utc": "YYYY-MM-DDTHH:MM:SSZ"}
            for entity_id in entities
        },
        "sqlite": {
            "path": "/config/solar_analytics/solar_analytics.sqlite",
            "mode": "ro",
            "integrity": "ok",
            "tables": {table: {"present": True, "rows": 0} for table in sorted(REQUIRED_TABLES)},
            "last_actual_sample": "YYYY-MM-DDTHH:MM:SSZ",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    template = sub.add_parser("template", help="write the collector envelope template")
    template.add_argument("--output", required=True, type=Path)
    snapshot = sub.add_parser("snapshot", help="validate input and write an immutable snapshot")
    snapshot.add_argument("--input", required=True, type=Path)
    snapshot.add_argument("--output-dir", required=True, type=Path)
    analyze = sub.add_parser("analyze", help="analyze a snapshot without emitting raw evidence")
    analyze.add_argument("--snapshot", required=True, type=Path)
    args = parser.parse_args(argv)

    if args.command == "template":
        args.output.write_text(
            json.dumps(_template(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps({"status": "TEMPLATE_WRITTEN", "path": str(args.output)}, ensure_ascii=False)
        )
        return 0

    if args.command == "snapshot":
        try:
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            path = write_immutable_snapshot(payload, args.output_dir)
        except (OSError, json.JSONDecodeError, CheckpointValidationError) as exc:
            print(json.dumps({"status": "BLOCKED", "blocker": str(exc)}, ensure_ascii=False))
            return 2
        print(json.dumps({"status": "SNAPSHOT_WRITTEN", "path": str(path)}, ensure_ascii=False))
        return 0

    result = analyze_snapshot(args.snapshot)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
