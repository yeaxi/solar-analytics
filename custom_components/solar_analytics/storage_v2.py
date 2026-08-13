"""Versioned, additive SQLite storage for Solar Analytics v2."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from math import isfinite
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 5
NORMALIZATION_VERSION = "native-period-end-v2.1"
METRIC_VERSION = "morning-baseline-v2"
ACCUMULATOR_BUCKET_SECONDS = 30 * 60

_DROPPED_BACKFILL_TABLES = (
    "v2_backfill_snapshot_intervals",
    "v2_backfill_intervals",
    "v2_backfill_daily_comparisons",
    "v2_backfill_accuracy_results",
    "v2_backfill_snapshots",
    "v2_backfill_runs",
)


class StorageError(RuntimeError):
    """A storage failure that must keep analytics fail-closed."""


class NewerSchemaError(StorageError):
    """The database was created by a newer integration version."""


class SolarAnalyticsV2Store:
    """One serialized writer with additive migration from the legacy schema."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    @property
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            raise StorageError("storage_not_initialized")
        return self._db

    def initialize(self) -> None:
        with self._lock:
            if self._db is not None:
                return
            try:
                self._db = sqlite3.connect(
                    self.path,
                    timeout=20,
                    isolation_level=None,
                    check_same_thread=False,
                )
                self._db.row_factory = sqlite3.Row
                self._db.execute("PRAGMA journal_mode=WAL")
                self._db.execute("PRAGMA synchronous=NORMAL")
                self._db.execute("PRAGMA foreign_keys=ON")
                self._migrate()
            except sqlite3.Error as err:
                if self._db is not None:
                    self._db.close()
                    self._db = None
                raise StorageError(f"sqlite_initialize_failed:{type(err).__name__}") from err

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    def _migrate(self) -> None:
        db = self.db
        db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        row = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        current = int(row[0]) if row else 0
        if current > SCHEMA_VERSION:
            raise NewerSchemaError(f"schema_version:{current}")
        try:
            db.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS v2_lineages (
                    lineage_id TEXT PRIMARY KEY,
                    contract_key TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    native_entry_id TEXT NOT NULL,
                    model_fingerprint TEXT NOT NULL,
                    actual_energy_entity TEXT NOT NULL,
                    actual_power_entity TEXT NOT NULL,
                    adapter_version TEXT NOT NULL,
                    native_contract_version TEXT NOT NULL,
                    normalization_version TEXT NOT NULL,
                    metric_version TEXT NOT NULL,
                    started_at_utc TEXT NOT NULL,
                    ended_at_utc TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_v2_lineages_contract
                    ON v2_lineages(source_kind, native_entry_id, started_at_utc);
                CREATE TABLE IF NOT EXISTS v2_snapshot_slots (
                    snapshot_slot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lineage_id TEXT,
                    source_kind TEXT NOT NULL,
                    snapshot_type TEXT NOT NULL,
                    scheduled_at_utc TEXT NOT NULL,
                    target_local_date TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    observed_at_utc TEXT,
                    native_updated_at_utc TEXT,
                    observation_sequence INTEGER,
                    payload_sha256 TEXT,
                    adapter_version TEXT NOT NULL,
                    normalization_version TEXT NOT NULL,
                    metric_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    admissible INTEGER NOT NULL DEFAULT 0,
                    exclusion_reason TEXT,
                    created_at_utc TEXT NOT NULL,
                    UNIQUE(lineage_id, snapshot_type, scheduled_at_utc)
                );
                CREATE INDEX IF NOT EXISTS ix_v2_slots_date
                    ON v2_snapshot_slots(target_local_date, source_kind, snapshot_type);
                CREATE TABLE IF NOT EXISTS v2_snapshot_intervals (
                    snapshot_slot_id INTEGER NOT NULL,
                    interval_start_utc TEXT,
                    interval_end_utc TEXT NOT NULL,
                    energy_wh REAL,
                    duration_seconds REAL,
                    valid INTEGER NOT NULL,
                    exclusion_reason TEXT,
                    PRIMARY KEY(snapshot_slot_id, interval_end_utc),
                    FOREIGN KEY(snapshot_slot_id) REFERENCES v2_snapshot_slots(snapshot_slot_id)
                );
                CREATE INDEX IF NOT EXISTS ix_v2_snapshot_intervals_end
                    ON v2_snapshot_intervals(interval_end_utc);
                CREATE TABLE IF NOT EXISTS v2_current_profile_cache (
                    source_kind TEXT PRIMARY KEY,
                    lineage_id TEXT,
                    observed_at_utc TEXT NOT NULL,
                    native_updated_at_utc TEXT,
                    observation_sequence INTEGER,
                    payload_sha256 TEXT,
                    profile_json TEXT NOT NULL,
                    quality_json TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS v2_intervals (
                    lineage_id TEXT NOT NULL,
                    interval_start_utc TEXT NOT NULL,
                    interval_end_utc TEXT NOT NULL,
                    target_local_date TEXT NOT NULL,
                    forecast_energy_wh REAL,
                    actual_energy_wh REAL,
                    eligible_seconds REAL NOT NULL DEFAULT 0,
                    actual_covered_seconds REAL NOT NULL DEFAULT 0,
                    forecast_valid INTEGER NOT NULL DEFAULT 0,
                    actual_valid INTEGER NOT NULL DEFAULT 0,
                    paired_valid INTEGER NOT NULL DEFAULT 0,
                    validity_reason TEXT NOT NULL,
                    reconciliation_status TEXT,
                    payload_json TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY(lineage_id, interval_start_utc, interval_end_utc)
                );
                CREATE INDEX IF NOT EXISTS ix_v2_intervals_date
                    ON v2_intervals(lineage_id, target_local_date, interval_end_utc);
                CREATE TABLE IF NOT EXISTS v2_daily_comparisons (
                    lineage_id TEXT NOT NULL,
                    local_date TEXT NOT NULL,
                    morning_slot_id INTEGER,
                    forecast_coverage REAL NOT NULL,
                    actual_coverage REAL NOT NULL,
                    paired_coverage REAL NOT NULL,
                    valid_paired_day INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    actual_kwh REAL,
                    forecast_kwh REAL,
                    signed_error_kwh REAL,
                    absolute_error_kwh REAL,
                    reconciliation_status TEXT,
                    payload_json TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY(lineage_id, local_date)
                );
                CREATE INDEX IF NOT EXISTS ix_v2_daily_date
                    ON v2_daily_comparisons(local_date, lineage_id);
                CREATE TABLE IF NOT EXISTS v2_accuracy_results (
                    lineage_id TEXT NOT NULL,
                    generated_at_utc TEXT NOT NULL,
                    window_days INTEGER NOT NULL,
                    valid_days INTEGER NOT NULL,
                    accuracy_ready INTEGER NOT NULL,
                    metric_version TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(lineage_id, window_days, metric_version)
                );
                CREATE TABLE IF NOT EXISTS v2_runtime_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS v2_accumulators (
                    interval_start_utc TEXT PRIMARY KEY,
                    energy_wh REAL NOT NULL,
                    covered_seconds REAL NOT NULL,
                    sample_count INTEGER NOT NULL,
                    last_power_w REAL,
                    quality TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS v2_imported_actual_daily (
                    source_entity_id TEXT NOT NULL,
                    local_date TEXT NOT NULL,
                    energy_kwh REAL NOT NULL,
                    coverage REAL NOT NULL,
                    observed_hours INTEGER NOT NULL,
                    expected_hours INTEGER NOT NULL,
                    counter_resets INTEGER NOT NULL DEFAULT 0,
                    provenance TEXT NOT NULL,
                    imported_at_utc TEXT NOT NULL,
                    PRIMARY KEY(source_entity_id, local_date)
                );
                """
            )
            if current < 4:
                db.execute("ALTER TABLE v2_accuracy_results RENAME TO v2_accuracy_results_v3")
                db.execute(
                    "CREATE TABLE v2_accuracy_results ("
                    "lineage_id TEXT NOT NULL, generated_at_utc TEXT NOT NULL, window_days INTEGER NOT NULL, "
                    "valid_days INTEGER NOT NULL, accuracy_ready INTEGER NOT NULL, metric_version TEXT NOT NULL, "
                    "payload_json TEXT NOT NULL, PRIMARY KEY(lineage_id, window_days, metric_version))"
                )
                db.execute(
                    "INSERT INTO v2_accuracy_results(lineage_id,generated_at_utc,window_days,valid_days,"
                    "accuracy_ready,metric_version,payload_json) "
                    "SELECT lineage_id,generated_at_utc,window_days,valid_days,accuracy_ready,metric_version,payload_json "
                    "FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY lineage_id,window_days,metric_version "
                    "ORDER BY generated_at_utc DESC) AS row_number FROM v2_accuracy_results_v3) "
                    "WHERE row_number=1"
                )
                db.execute("DROP TABLE v2_accuracy_results_v3")
            if current < 5:
                # Children first: foreign_keys=ON turns DROP TABLE into an
                # implicit DELETE FROM, which a surviving child row would veto.
                for table in _DROPPED_BACKFILL_TABLES:
                    db.execute(f"DROP TABLE IF EXISTS {table}")
            db.execute(
                "INSERT INTO meta(key,value) VALUES('schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
            except Exception:
                self.db.execute("ROLLBACK")
                raise
            else:
                self.db.execute("COMMIT")

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(
            value if value is not None else {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def schema_version(self) -> int:
        row = self.db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(row[0]) if row else 0

    def set_runtime(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO v2_runtime_state(key,value_json,updated_at_utc) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at_utc=excluded.updated_at_utc",
            (key, self._json(value), self._now()),
        )

    def get_runtime(self, key: str) -> Any | None:
        row = self.db.execute(
            "SELECT value_json FROM v2_runtime_state WHERE key=?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def ensure_lineage(
        self, *, contract_key: str, metadata: Mapping[str, Any], now: datetime
    ) -> str:
        current_id = self.get_runtime("current_lineage_id")
        current_key = self.get_runtime("current_lineage_key")
        if current_id and current_key == contract_key:
            row = self.db.execute(
                "SELECT lineage_id FROM v2_lineages WHERE lineage_id=?", (current_id,)
            ).fetchone()
            if row:
                return str(current_id)
        lineage_id = uuid.uuid4().hex
        values = {
            "lineage_id": lineage_id,
            "contract_key": contract_key,
            "source_kind": str(metadata.get("source_kind", "native")),
            "native_entry_id": str(metadata["native_entry_id"]),
            "model_fingerprint": str(metadata["model_fingerprint"]),
            "actual_energy_entity": str(metadata["actual_energy_entity"]),
            "actual_power_entity": str(metadata["actual_power_entity"]),
            "adapter_version": str(metadata["adapter_version"]),
            "native_contract_version": str(metadata["native_contract_version"]),
            "normalization_version": str(
                metadata.get("normalization_version", NORMALIZATION_VERSION)
            ),
            "metric_version": str(metadata.get("metric_version", METRIC_VERSION)),
            "started_at_utc": now.astimezone(UTC).isoformat(),
            "payload_json": self._json(metadata),
        }
        self.db.execute(
            "INSERT INTO v2_lineages(lineage_id,contract_key,source_kind,native_entry_id,model_fingerprint,"
            "actual_energy_entity,actual_power_entity,adapter_version,native_contract_version,normalization_version,"
            "metric_version,started_at_utc,payload_json) VALUES(:lineage_id,:contract_key,:source_kind,:native_entry_id,"
            ":model_fingerprint,:actual_energy_entity,:actual_power_entity,:adapter_version,:native_contract_version,"
            ":normalization_version,:metric_version,:started_at_utc,:payload_json)",
            values,
        )
        if current_id and current_id != lineage_id:
            self.db.execute(
                "UPDATE v2_lineages SET ended_at_utc=? WHERE lineage_id=? AND ended_at_utc IS NULL",
                (values["started_at_utc"], current_id),
            )
        self.set_runtime("current_lineage_id", lineage_id)
        self.set_runtime("current_lineage_key", contract_key)
        return lineage_id

    def current_lineage_id(self) -> str | None:
        value = self.get_runtime("current_lineage_id")
        return str(value) if value else None

    def ensure_snapshot_slot(
        self,
        *,
        lineage_id: str | None,
        source_kind: str,
        snapshot_type: str,
        scheduled_at_utc: datetime,
        target_local_date: date | str,
        timezone_name: str,
        observed_at_utc: datetime | None,
        native_updated_at_utc: datetime | None,
        observation_sequence: int | None,
        payload_sha256: str | None,
        adapter_version: str,
        normalization_version: str,
        metric_version: str,
        status: str,
        admissible: bool,
        exclusion_reason: str | None,
    ) -> tuple[int, bool]:
        target = (
            target_local_date.isoformat()
            if isinstance(target_local_date, date)
            else str(target_local_date)
        )
        # Terminal blocked/missing slots still need a stable identity. A sentinel
        # lineage is never eligible for analytics and prevents duplicate retries.
        lineage_key = lineage_id or "__unavailable__"
        values = (
            lineage_key,
            source_kind,
            snapshot_type,
            scheduled_at_utc.astimezone(UTC).isoformat(),
            target,
            timezone_name,
            observed_at_utc.astimezone(UTC).isoformat() if observed_at_utc else None,
            native_updated_at_utc.astimezone(UTC).isoformat() if native_updated_at_utc else None,
            observation_sequence,
            payload_sha256,
            adapter_version,
            normalization_version,
            metric_version,
            status,
            int(admissible),
            exclusion_reason,
            self._now(),
        )
        cursor = self.db.execute(
            "INSERT INTO v2_snapshot_slots(lineage_id,source_kind,snapshot_type,scheduled_at_utc,target_local_date,timezone,"
            "observed_at_utc,native_updated_at_utc,observation_sequence,payload_sha256,adapter_version,normalization_version,"
            "metric_version,status,admissible,exclusion_reason,created_at_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(lineage_id,snapshot_type,scheduled_at_utc) DO NOTHING",
            values,
        )
        row = self.db.execute(
            "SELECT snapshot_slot_id FROM v2_snapshot_slots WHERE lineage_id=? AND snapshot_type=? AND scheduled_at_utc=?",
            (lineage_key, snapshot_type, scheduled_at_utc.astimezone(UTC).isoformat()),
        ).fetchone()
        if row is None:
            raise StorageError("snapshot_slot_insert_failed")
        return int(row[0]), cursor.rowcount == 1

    def insert_snapshot_periods(self, snapshot_slot_id: int, rows: list[Mapping[str, Any]]) -> None:
        self.db.executemany(
            "INSERT OR IGNORE INTO v2_snapshot_intervals(snapshot_slot_id,interval_start_utc,interval_end_utc,energy_wh,"
            "duration_seconds,valid,exclusion_reason) VALUES(?,?,?,?,?,?,?)",
            [
                (
                    snapshot_slot_id,
                    row.get("interval_start_utc"),
                    row["interval_end_utc"],
                    row.get("energy_wh"),
                    row.get("duration_seconds"),
                    int(bool(row.get("valid"))),
                    row.get("exclusion_reason"),
                )
                for row in rows
            ],
        )

    def list_snapshot_slots(
        self,
        *,
        lineage_id: str | None = None,
        target_local_date: str | None = None,
        snapshot_type: str | None = None,
        source_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("lineage_id", lineage_id),
            ("target_local_date", target_local_date),
            ("snapshot_type", snapshot_type),
            ("source_kind", source_kind),
        ):
            if value is not None:
                clauses.append(f"{column}=?")
                params.append(value)
        query = (
            "SELECT * FROM v2_snapshot_slots"
            + (" WHERE " + " AND ".join(clauses) if clauses else "")
            + " ORDER BY scheduled_at_utc"
        )
        return [dict(row) for row in self.db.execute(query, params).fetchall()]

    def snapshot_periods(self, snapshot_slot_id: int) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM v2_snapshot_intervals WHERE snapshot_slot_id=? ORDER BY interval_end_utc",
                (snapshot_slot_id,),
            ).fetchall()
        ]

    def upsert_current_profile(
        self,
        *,
        source_kind: str,
        lineage_id: str | None,
        observed_at_utc: datetime,
        native_updated_at_utc: datetime | None,
        observation_sequence: int | None,
        payload_sha256: str | None,
        profile: Any,
        quality: Mapping[str, Any],
    ) -> None:
        self.db.execute(
            "INSERT INTO v2_current_profile_cache(source_kind,lineage_id,observed_at_utc,native_updated_at_utc,observation_sequence,payload_sha256,profile_json,quality_json,updated_at_utc) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(source_kind) DO UPDATE SET lineage_id=excluded.lineage_id,observed_at_utc=excluded.observed_at_utc,native_updated_at_utc=excluded.native_updated_at_utc,observation_sequence=excluded.observation_sequence,payload_sha256=excluded.payload_sha256,profile_json=excluded.profile_json,quality_json=excluded.quality_json,updated_at_utc=excluded.updated_at_utc",
            (
                source_kind,
                lineage_id,
                observed_at_utc.astimezone(UTC).isoformat(),
                native_updated_at_utc.astimezone(UTC).isoformat()
                if native_updated_at_utc
                else None,
                observation_sequence,
                payload_sha256,
                self._json(profile),
                self._json(quality),
                self._now(),
            ),
        )

    def current_profile(self, source_kind: str = "native") -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM v2_current_profile_cache WHERE source_kind=?", (source_kind,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["profile"] = json.loads(result.pop("profile_json"))
        result["quality"] = json.loads(result.pop("quality_json"))
        return result

    def add_power_sample(
        self,
        timestamp: datetime,
        power_w: float | None,
        *,
        minutes: int = ACCUMULATOR_BUCKET_SECONDS // 60,
        max_gap_seconds: int = 900,
    ) -> None:
        current_ts = (
            timestamp.astimezone(UTC) if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)
        )
        if power_w is not None and (not isfinite(float(power_w)) or float(power_w) < 0):
            power_w = None
        previous = self.get_runtime("last_actual_sample")
        if isinstance(previous, Mapping) and power_w is not None:
            try:
                previous_ts = datetime.fromisoformat(
                    str(previous["timestamp"]).replace("Z", "+00:00")
                ).astimezone(UTC)
                previous_power = float(previous["power_w"])
            except KeyError, TypeError, ValueError:
                previous_ts = None
                previous_power = None
            if previous_ts is not None and previous_power is not None and previous_ts < current_ts:
                gap = (current_ts - previous_ts).total_seconds()
                if 0 < gap <= max_gap_seconds:
                    cursor = previous_ts
                    bucket_seconds = minutes * 60
                    while cursor < current_ts:
                        bucket_epoch = int(cursor.timestamp()) - (
                            int(cursor.timestamp()) % bucket_seconds
                        )
                        bucket_start = datetime.fromtimestamp(bucket_epoch, UTC)
                        bucket_end = bucket_start + timedelta(seconds=bucket_seconds)
                        segment_end = min(bucket_end, current_ts)
                        segment_seconds = (segment_end - cursor).total_seconds()
                        if segment_seconds > 0:
                            key = bucket_start.isoformat()
                            self.db.execute(
                                "INSERT INTO v2_accumulators(interval_start_utc,energy_wh,covered_seconds,sample_count,last_power_w,quality) VALUES(?,?,?,?,?,?) ON CONFLICT(interval_start_utc) DO UPDATE SET energy_wh=energy_wh+excluded.energy_wh,covered_seconds=covered_seconds+excluded.covered_seconds,sample_count=sample_count+excluded.sample_count,last_power_w=excluded.last_power_w,quality=CASE WHEN covered_seconds+excluded.covered_seconds>=? THEN 'good' ELSE 'gap' END",
                                (
                                    key,
                                    previous_power * segment_seconds / 3600.0,
                                    segment_seconds,
                                    1,
                                    previous_power,
                                    "good",
                                    bucket_seconds * 0.8,
                                ),
                            )
                        cursor = segment_end
        if power_w is not None:
            self.set_runtime(
                "last_actual_sample",
                {"timestamp": current_ts.isoformat(), "power_w": float(power_w)},
            )

    def integrate_accumulators(
        self, start_utc: datetime, end_utc: datetime
    ) -> dict[str, float | int | str | None]:
        start = start_utc.astimezone(UTC) if start_utc.tzinfo else start_utc.replace(tzinfo=UTC)
        end = end_utc.astimezone(UTC) if end_utc.tzinfo else end_utc.replace(tzinfo=UTC)
        bucket = timedelta(seconds=ACCUMULATOR_BUCKET_SECONDS)
        try:
            rows = self.db.execute(
                "SELECT * FROM v2_accumulators WHERE interval_start_utc >= ? AND interval_start_utc < ? "
                "ORDER BY interval_start_utc",
                ((start - bucket).isoformat(), end.isoformat()),
            ).fetchall()
        except sqlite3.OperationalError:
            return {
                "energy_wh": None,
                "covered_seconds": 0.0,
                "sample_count": 0,
                "quality": "missing",
            }
        energy = 0.0
        covered = 0.0
        count = 0
        quality = "good"
        for row in rows:
            bucket_start = datetime.fromisoformat(
                row["interval_start_utc"].replace("Z", "+00:00")
            ).astimezone(UTC)
            bucket_end = bucket_start + bucket
            if bucket_end <= start:
                continue
            overlap = max(0.0, (min(bucket_end, end) - max(bucket_start, start)).total_seconds())
            if overlap <= 0:
                continue
            ratio = overlap / ACCUMULATOR_BUCKET_SECONDS
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

    def upsert_interval(self, payload: Mapping[str, Any]) -> None:
        data = dict(payload)
        self.db.execute(
            "INSERT INTO v2_intervals(lineage_id,interval_start_utc,interval_end_utc,target_local_date,forecast_energy_wh,actual_energy_wh,eligible_seconds,actual_covered_seconds,forecast_valid,actual_valid,paired_valid,validity_reason,reconciliation_status,payload_json,updated_at_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(lineage_id,interval_start_utc,interval_end_utc) DO UPDATE SET forecast_energy_wh=excluded.forecast_energy_wh,actual_energy_wh=excluded.actual_energy_wh,eligible_seconds=excluded.eligible_seconds,actual_covered_seconds=excluded.actual_covered_seconds,forecast_valid=excluded.forecast_valid,actual_valid=excluded.actual_valid,paired_valid=excluded.paired_valid,validity_reason=excluded.validity_reason,reconciliation_status=excluded.reconciliation_status,payload_json=excluded.payload_json,updated_at_utc=excluded.updated_at_utc",
            (
                data["lineage_id"],
                data["interval_start_utc"],
                data["interval_end_utc"],
                data["target_local_date"],
                data.get("forecast_energy_wh"),
                data.get("actual_energy_wh"),
                data.get("eligible_seconds", 0.0),
                data.get("actual_covered_seconds", 0.0),
                int(bool(data.get("forecast_valid"))),
                int(bool(data.get("actual_valid"))),
                int(bool(data.get("paired_valid"))),
                data.get("validity_reason", "unknown"),
                data.get("reconciliation_status"),
                self._json(data),
                self._now(),
            ),
        )

    def list_intervals(
        self,
        *,
        lineage_id: str | None = None,
        local_date: str | None = None,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = []
        params = []
        for column, value in (
            ("lineage_id", lineage_id),
            ("target_local_date", local_date),
            ("interval_end_utc", since),
        ):
            if value is not None:
                clauses.append(f"{column}>=?" if column == "interval_end_utc" else f"{column}=?")
                params.append(value)
        query = (
            "SELECT * FROM v2_intervals"
            + (" WHERE " + " AND ".join(clauses) if clauses else "")
            + " ORDER BY interval_start_utc"
        )
        rows = []
        for row in self.db.execute(query, params).fetchall():
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            rows.append(item)
        return rows

    def upsert_daily(self, payload: Mapping[str, Any]) -> None:
        data = dict(payload)
        self.db.execute(
            "INSERT INTO v2_daily_comparisons(lineage_id,local_date,morning_slot_id,forecast_coverage,actual_coverage,paired_coverage,valid_paired_day,reason,actual_kwh,forecast_kwh,signed_error_kwh,absolute_error_kwh,reconciliation_status,payload_json,updated_at_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(lineage_id,local_date) DO UPDATE SET morning_slot_id=excluded.morning_slot_id,forecast_coverage=excluded.forecast_coverage,actual_coverage=excluded.actual_coverage,paired_coverage=excluded.paired_coverage,valid_paired_day=excluded.valid_paired_day,reason=excluded.reason,actual_kwh=excluded.actual_kwh,forecast_kwh=excluded.forecast_kwh,signed_error_kwh=excluded.signed_error_kwh,absolute_error_kwh=excluded.absolute_error_kwh,reconciliation_status=excluded.reconciliation_status,payload_json=excluded.payload_json,updated_at_utc=excluded.updated_at_utc",
            (
                data["lineage_id"],
                data["local_date"],
                data.get("morning_slot_id"),
                data.get("forecast_coverage", 0.0),
                data.get("actual_coverage", 0.0),
                data.get("paired_coverage", 0.0),
                int(bool(data.get("valid_paired_day"))),
                data.get("reason", "insufficient_data"),
                data.get("actual_kwh"),
                data.get("forecast_kwh"),
                data.get("signed_error_kwh"),
                data.get("absolute_error_kwh"),
                data.get("reconciliation_status"),
                self._json(data),
                self._now(),
            ),
        )

    def list_daily(
        self, *, lineage_id: str | None = None, since: str | None = None
    ) -> list[dict[str, Any]]:
        clauses = []
        params = []
        if lineage_id is not None:
            clauses.append("lineage_id=?")
            params.append(lineage_id)
        if since is not None:
            clauses.append("local_date>=?")
            params.append(since)
        query = (
            "SELECT * FROM v2_daily_comparisons"
            + (" WHERE " + " AND ".join(clauses) if clauses else "")
            + " ORDER BY local_date"
        )
        return [dict(row) for row in self.db.execute(query, params).fetchall()]

    def save_accuracy(
        self,
        *,
        lineage_id: str,
        generated_at: datetime,
        window_days: int,
        valid_days: int,
        accuracy_ready: bool,
        payload: Mapping[str, Any],
    ) -> None:
        self.db.execute(
            "INSERT INTO v2_accuracy_results(lineage_id,generated_at_utc,window_days,valid_days,accuracy_ready,metric_version,payload_json) "
            "VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(lineage_id,window_days,metric_version) DO UPDATE SET "
            "generated_at_utc=excluded.generated_at_utc,valid_days=excluded.valid_days,"
            "accuracy_ready=excluded.accuracy_ready,payload_json=excluded.payload_json",
            (
                lineage_id,
                generated_at.astimezone(UTC).isoformat(),
                window_days,
                valid_days,
                int(accuracy_ready),
                METRIC_VERSION,
                self._json(payload),
            ),
        )

    def latest_accuracy(self, lineage_id: str | None = None) -> dict[str, Any] | None:
        query = "SELECT * FROM v2_accuracy_results"
        params = []
        if lineage_id is not None:
            query += " WHERE lineage_id=?"
            params.append(lineage_id)
        query += " ORDER BY generated_at_utc DESC LIMIT 1"
        row = self.db.execute(query, params).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def replace_imported_actual_daily(
        self,
        *,
        source_entity_id: str,
        provenance: str,
        rows: Sequence[Mapping[str, Any]],
        imported_at: datetime,
    ) -> int:
        """Write one import run's days atomically, replacing any earlier values.

        Never additive: a re-run of the same window converges to the same rows
        instead of accumulating them.
        """

        stamp = imported_at.astimezone(UTC).isoformat()
        with self.transaction():
            self.db.executemany(
                "INSERT INTO v2_imported_actual_daily(source_entity_id,local_date,energy_kwh,coverage,"
                "observed_hours,expected_hours,counter_resets,provenance,imported_at_utc) "
                "VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(source_entity_id,local_date) DO UPDATE SET energy_kwh=excluded.energy_kwh,"
                "coverage=excluded.coverage,observed_hours=excluded.observed_hours,"
                "expected_hours=excluded.expected_hours,counter_resets=excluded.counter_resets,"
                "provenance=excluded.provenance,imported_at_utc=excluded.imported_at_utc",
                [
                    (
                        source_entity_id,
                        str(row["local_date"]),
                        float(row["energy_kwh"]),
                        float(row["coverage"]),
                        int(row["observed_hours"]),
                        int(row["expected_hours"]),
                        int(row["counter_resets"]),
                        provenance,
                        stamp,
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def list_imported_actual_daily(
        self, *, source_entity_id: str | None = None, since: str | None = None
    ) -> list[dict[str, Any]]:
        clauses = []
        params = []
        if source_entity_id is not None:
            clauses.append("source_entity_id=?")
            params.append(source_entity_id)
        if since is not None:
            clauses.append("local_date>=?")
            params.append(since)
        query = (
            "SELECT * FROM v2_imported_actual_daily"
            + (" WHERE " + " AND ".join(clauses) if clauses else "")
            + " ORDER BY local_date"
        )
        return [dict(row) for row in self.db.execute(query, params).fetchall()]

    def integrity_check(self) -> str:
        row = self.db.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row else "unknown"

    def backup_to(self, destination: str | Path) -> None:
        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(destination_path) as backup_db:
            self.db.backup(backup_db)

    def prune(self, *, now: datetime | None = None, retention_days: int = 3650) -> dict[str, int]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        cutoff = (current - timedelta(days=retention_days)).date().isoformat()
        counts: dict[str, int] = {}
        with self.transaction():
            # Child rows must be removed before their parent snapshot slots while
            # foreign_keys=ON. Keep the whole retention operation atomic so a
            # locked/corrupt failure cannot leave a partially pruned archive.
            cursor = self.db.execute(
                "DELETE FROM v2_snapshot_intervals WHERE snapshot_slot_id IN "
                "(SELECT snapshot_slot_id FROM v2_snapshot_slots WHERE target_local_date < ?)",
                (cutoff,),
            )
            counts["v2_snapshot_intervals"] = cursor.rowcount
            for table, column in (
                ("v2_snapshot_slots", "target_local_date"),
                ("v2_intervals", "target_local_date"),
                ("v2_daily_comparisons", "local_date"),
                ("v2_imported_actual_daily", "local_date"),
            ):
                cursor = self.db.execute(f"DELETE FROM {table} WHERE {column} < ?", (cutoff,))
                counts[table] = cursor.rowcount
            try:
                cursor = self.db.execute(
                    "DELETE FROM v2_accumulators WHERE interval_start_utc < ?",
                    (
                        datetime.combine(
                            current.date() - timedelta(days=retention_days), time.min, tzinfo=UTC
                        ).isoformat(),
                    ),
                )
                counts["v2_accumulators"] = cursor.rowcount
            except sqlite3.OperationalError:
                counts["v2_accumulators"] = 0
            self.db.execute(
                "DELETE FROM v2_snapshot_intervals WHERE snapshot_slot_id NOT IN "
                "(SELECT snapshot_slot_id FROM v2_snapshot_slots)"
            )
        return counts
