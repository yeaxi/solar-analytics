"""Compact transactional SQLite storage for Solar Analytics.

Snapshots and prepared aggregates live here rather than in a Home Assistant state
attribute.  The schema is intentionally small: one row per 30-minute interval,
one row per completed local day, and compact JSON only for forecast profiles and
insight results.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

SCHEMA_VERSION = 1
UTC = timezone.utc


class SolarAnalyticsStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connections: dict[int, sqlite3.Connection] = {}
        self._connection_lock = threading.RLock()

    @property
    def connection(self) -> sqlite3.Connection:
        """Return a connection owned by the current executor thread.

        Home Assistant may hand the coordinator to different executor workers
        between refreshes. SQLite's default thread affinity rejects reusing one
        connection across those workers, so keep one connection per worker and
        close them all during shutdown.
        """
        thread_id = threading.get_ident()
        with self._connection_lock:
            db = self._connections.get(thread_id)
            if db is None:
                db = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA synchronous=NORMAL")
                db.execute("PRAGMA foreign_keys=ON")
                self._connections[thread_id] = db
                self._create_schema(db)
            return db

    def close(self) -> None:
        with self._connection_lock:
            connections = list(self._connections.values())
            self._connections.clear()
        for db in connections:
            db.close()

    def _create_schema(self, db: sqlite3.Connection) -> None:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS forecast_snapshots (
                provider TEXT NOT NULL,
                target_date TEXT NOT NULL,
                snapshot_type TEXT NOT NULL,
                snapshot_timestamp TEXT NOT NULL,
                daily_energy_kwh REAL,
                profile_json TEXT,
                parameters_json TEXT,
                source_id TEXT,
                profile_status TEXT NOT NULL,
                quality_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(provider, target_date, snapshot_type)
            );
            CREATE INDEX IF NOT EXISTS ix_snapshots_target_date
                ON forecast_snapshots(target_date, provider);
            CREATE TABLE IF NOT EXISTS interval_accumulators (
                interval_start TEXT PRIMARY KEY,
                energy_wh REAL NOT NULL DEFAULT 0,
                covered_seconds REAL NOT NULL DEFAULT 0,
                sample_count INTEGER NOT NULL DEFAULT 0,
                first_sample_ts TEXT,
                last_sample_ts TEXT,
                last_power_w REAL,
                duplicate_count INTEGER NOT NULL DEFAULT 0,
                quality TEXT NOT NULL DEFAULT 'good'
            );
            CREATE TABLE IF NOT EXISTS intervals (
                interval_start TEXT PRIMARY KEY,
                actual_power_average_w REAL,
                actual_energy_kwh REAL,
                forecast_solar_power_w REAL,
                forecast_solar_energy_kwh REAL,
                vrm_forecast_power_w REAL,
                vrm_forecast_energy_kwh REAL,
                consensus_expected_power_w REAL,
                consensus_expected_energy_kwh REAL,
                analysis_valid INTEGER NOT NULL DEFAULT 0,
                validity_reason TEXT NOT NULL,
                curtailment_reason TEXT,
                storm_context TEXT,
                data_quality TEXT NOT NULL,
                coverage_ratio REAL NOT NULL DEFAULT 0,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_intervals_start ON intervals(interval_start);
            CREATE TABLE IF NOT EXISTS daily_results (
                local_date TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS analysis_results (
                generated_at TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS recommendations (
                recommendation_id TEXT PRIMARY KEY,
                generated_at TEXT NOT NULL,
                parameter TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                applied_at TEXT
            );
            CREATE TABLE IF NOT EXISTS storm_events (
                event_id TEXT PRIMARY KEY,
                event_start TEXT NOT NULL,
                event_end TEXT,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runtime_state (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        db.execute(
            "INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        db = self.connection
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
        except Exception:
            db.execute("ROLLBACK")
            raise
        else:
            db.execute("COMMIT")

    @staticmethod
    def _json(payload: Mapping[str, Any] | Sequence[Any] | None) -> str:
        return json.dumps(payload if payload is not None else {}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def upsert_snapshot(
        self,
        *,
        provider: str,
        target_date: date | str,
        snapshot_type: str,
        snapshot_timestamp: datetime | str,
        daily_energy_kwh: float | None,
        profile: Mapping[str, Any] | Sequence[Any] | None,
        parameters: Mapping[str, Any] | None,
        source_id: str | None,
        profile_status: str,
        quality: Mapping[str, Any] | None = None,
    ) -> bool:
        """Insert the first snapshot for a semantic key; retries are no-ops."""

        target = target_date.isoformat() if isinstance(target_date, date) else str(target_date)
        timestamp = snapshot_timestamp.isoformat() if isinstance(snapshot_timestamp, datetime) else str(snapshot_timestamp)
        row = self.connection.execute(
            "SELECT 1 FROM forecast_snapshots WHERE provider=? AND target_date=? AND snapshot_type=?",
            (provider, target, snapshot_type),
        ).fetchone()
        if row:
            return False
        self.connection.execute(
            """
            INSERT INTO forecast_snapshots(
                provider,target_date,snapshot_type,snapshot_timestamp,daily_energy_kwh,
                profile_json,parameters_json,source_id,profile_status,quality_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                provider,
                target,
                snapshot_type,
                timestamp,
                daily_energy_kwh,
                self._json(profile),
                self._json(parameters),
                source_id,
                profile_status,
                self._json(quality),
                self._now(),
            ),
        )
        return True

    def get_snapshot(self, provider: str, target_date: date | str, snapshot_type: str) -> dict[str, Any] | None:
        target = target_date.isoformat() if isinstance(target_date, date) else str(target_date)
        row = self.connection.execute(
            "SELECT * FROM forecast_snapshots WHERE provider=? AND target_date=? AND snapshot_type=?",
            (provider, target, snapshot_type),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key in ("profile_json", "parameters_json", "quality_json"):
            result[key[:-5] if key.endswith("_json") else key] = json.loads(result.pop(key) or "{}")
        return result

    def list_snapshots(self, *, since: date | str | None = None, provider: str | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if since is not None:
            clauses.append("target_date >= ?")
            params.append(since.isoformat() if isinstance(since, date) else str(since))
        if provider:
            clauses.append("provider = ?")
            params.append(provider)
        query = "SELECT * FROM forecast_snapshots"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY target_date, provider, snapshot_type"
        rows = self.connection.execute(query, params).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for key in ("profile_json", "parameters_json", "quality_json"):
                item[key[:-5]] = json.loads(item.pop(key) or "{}")
            result.append(item)
        return result

    def upsert_interval(self, payload: Mapping[str, Any]) -> None:
        data = dict(payload)
        start = data["interval_start"]
        if isinstance(start, datetime):
            start = start.isoformat()
        data["interval_start"] = str(start)
        now = self._now()
        self.connection.execute(
            """
            INSERT INTO intervals(
                interval_start,actual_power_average_w,actual_energy_kwh,
                forecast_solar_power_w,forecast_solar_energy_kwh,
                vrm_forecast_power_w,vrm_forecast_energy_kwh,
                consensus_expected_power_w,consensus_expected_energy_kwh,
                analysis_valid,validity_reason,curtailment_reason,storm_context,
                data_quality,coverage_ratio,payload_json,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(interval_start) DO UPDATE SET
                actual_power_average_w=excluded.actual_power_average_w,
                actual_energy_kwh=excluded.actual_energy_kwh,
                forecast_solar_power_w=excluded.forecast_solar_power_w,
                forecast_solar_energy_kwh=excluded.forecast_solar_energy_kwh,
                vrm_forecast_power_w=excluded.vrm_forecast_power_w,
                vrm_forecast_energy_kwh=excluded.vrm_forecast_energy_kwh,
                consensus_expected_power_w=excluded.consensus_expected_power_w,
                consensus_expected_energy_kwh=excluded.consensus_expected_energy_kwh,
                analysis_valid=excluded.analysis_valid,
                validity_reason=excluded.validity_reason,
                curtailment_reason=excluded.curtailment_reason,
                storm_context=excluded.storm_context,
                data_quality=excluded.data_quality,
                coverage_ratio=excluded.coverage_ratio,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (
                data["interval_start"],
                data.get("actual_power_average_w"),
                data.get("actual_energy_kwh"),
                data.get("forecast_solar_power_w"),
                data.get("forecast_solar_energy_kwh"),
                data.get("vrm_forecast_power_w"),
                data.get("vrm_forecast_energy_kwh"),
                data.get("consensus_expected_power_w"),
                data.get("consensus_expected_energy_kwh"),
                int(bool(data.get("analysis_valid", False))),
                data.get("validity_reason", "forecast_unavailable"),
                data.get("curtailment_reason"),
                data.get("storm_context"),
                data.get("data_quality", "good"),
                data.get("coverage_ratio", 0.0),
                self._json(data),
                now,
            ),
        )

    def upsert_daily(self, local_date: date | str, payload: Mapping[str, Any]) -> None:
        key = local_date.isoformat() if isinstance(local_date, date) else str(local_date)
        self.connection.execute(
            """
            INSERT INTO daily_results(local_date,payload_json,updated_at) VALUES(?,?,?)
            ON CONFLICT(local_date) DO UPDATE SET payload_json=excluded.payload_json,updated_at=excluded.updated_at
            """,
            (key, self._json(payload), self._now()),
        )

    def list_daily(self, *, since: date | str | None = None) -> list[dict[str, Any]]:
        if since is None:
            rows = self.connection.execute("SELECT * FROM daily_results ORDER BY local_date").fetchall()
        else:
            key = since.isoformat() if isinstance(since, date) else str(since)
            rows = self.connection.execute("SELECT * FROM daily_results WHERE local_date>=? ORDER BY local_date", (key,)).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def save_analysis(self, generated_at: datetime | str, payload: Mapping[str, Any]) -> None:
        key = generated_at.isoformat() if isinstance(generated_at, datetime) else str(generated_at)
        self.connection.execute(
            "INSERT OR REPLACE INTO analysis_results(generated_at,payload_json) VALUES(?,?)",
            (key, self._json(payload)),
        )

    def latest_analysis(self) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT payload_json FROM analysis_results ORDER BY generated_at DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else None

    def save_recommendation(self, payload: Mapping[str, Any], generated_at: datetime | str) -> str:
        canonical = self._json(payload)
        recommendation_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        timestamp = generated_at.isoformat() if isinstance(generated_at, datetime) else str(generated_at)
        self.connection.execute(
            "INSERT OR IGNORE INTO recommendations(recommendation_id,generated_at,parameter,payload_json) VALUES(?,?,?,?)",
            (recommendation_id, timestamp, str(payload.get("parameter", "unknown")), canonical),
        )
        return recommendation_id

    def mark_recommendation_applied(self, recommendation_id: str, applied_at: datetime | str | None = None) -> bool:
        value = applied_at or datetime.now(UTC)
        timestamp = value.isoformat() if isinstance(value, datetime) else str(value)
        cursor = self.connection.execute(
            "UPDATE recommendations SET applied_at=? WHERE recommendation_id=?",
            (timestamp, recommendation_id),
        )
        return cursor.rowcount > 0

    def save_storm_event(self, event_id: str, event_start: datetime | str, event_type: str, payload: Mapping[str, Any], event_end: datetime | str | None = None) -> None:
        start = event_start.isoformat() if isinstance(event_start, datetime) else str(event_start)
        end = event_end.isoformat() if isinstance(event_end, datetime) else event_end
        self.connection.execute(
            "INSERT OR REPLACE INTO storm_events(event_id,event_start,event_end,event_type,payload_json) VALUES(?,?,?,?,?)",
            (event_id, start, end, event_type, self._json(payload)),
        )

    def set_runtime(self, key: str, value: Mapping[str, Any] | Sequence[Any] | str | float | int | None) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO runtime_state(key,value_json,updated_at) VALUES(?,?,?)",
            (key, self._json(value if isinstance(value, (Mapping, Sequence)) and not isinstance(value, (str, bytes)) else {"value": value}), self._now()),
        )

    def get_runtime(self, key: str) -> Any | None:
        row = self.connection.execute("SELECT value_json FROM runtime_state WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        payload = json.loads(row[0])
        return payload.get("value") if isinstance(payload, dict) and set(payload) == {"value"} else payload

    def add_power_sample(
        self,
        timestamp: datetime,
        power_w: float | None,
        *,
        minutes: int = 30,
        max_gap_seconds: int = 900,
    ) -> None:
        """Accumulate one new sample into 30-minute buckets.

        Only the previous sample and interval accumulators are persisted.  This
        avoids retaining a high-frequency raw stream while preserving elapsed-time
        weighting and restart continuity.
        """

        current_ts = timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=UTC)
        current_ts = current_ts.astimezone(UTC)
        previous = self.get_runtime("last_actual_sample")
        if previous and power_w is not None:
            try:
                previous_ts = datetime.fromisoformat(str(previous["timestamp"]).replace("Z", "+00:00")).astimezone(UTC)
                previous_power = float(previous["power_w"])
            except (KeyError, TypeError, ValueError):
                previous_ts = None
                previous_power = None
            if previous_ts is not None and previous_power is not None and previous_ts < current_ts:
                gap = (current_ts - previous_ts).total_seconds()
                if 0 < gap <= max_gap_seconds:
                    cursor = previous_ts
                    while cursor < current_ts:
                        epoch = int(cursor.timestamp())
                        bucket_seconds = minutes * 60
                        bucket_epoch = epoch - (epoch % bucket_seconds)
                        bucket_start = datetime.fromtimestamp(bucket_epoch, UTC)
                        bucket_end = bucket_start + timedelta(seconds=bucket_seconds)
                        segment_end = min(bucket_end, current_ts)
                        segment_seconds = (segment_end - cursor).total_seconds()
                        if segment_seconds > 0:
                            # Store the key on the UTC timeline. Local rendering is
                            # derived later with Europe/Kyiv, avoiding DST collisions.
                            local_key = bucket_start.isoformat()
                            self.connection.execute(
                                """
                                INSERT INTO interval_accumulators(
                                    interval_start,energy_wh,covered_seconds,sample_count,
                                    first_sample_ts,last_sample_ts,last_power_w,quality
                                ) VALUES(?,?,?,?,?,?,?,?)
                                ON CONFLICT(interval_start) DO UPDATE SET
                                    energy_wh=energy_wh+excluded.energy_wh,
                                    covered_seconds=covered_seconds+excluded.covered_seconds,
                                    sample_count=sample_count+excluded.sample_count,
                                    first_sample_ts=COALESCE(first_sample_ts,excluded.first_sample_ts),
                                    last_sample_ts=excluded.last_sample_ts,
                                    last_power_w=excluded.last_power_w,
                                    quality=CASE WHEN covered_seconds+excluded.covered_seconds >= ? THEN 'good' ELSE 'gap' END
                                """,
                                (
                                    local_key,
                                    previous_power * segment_seconds / 3600.0,
                                    segment_seconds,
                                    1,
                                    previous_ts.isoformat(),
                                    segment_end.isoformat(),
                                    previous_power,
                                    "good" if segment_seconds >= bucket_seconds * 0.8 else "gap",
                                    bucket_seconds * 0.8,
                                ),
                            )
                        cursor = segment_end
        if power_w is not None:
            self.set_runtime(
                "last_actual_sample",
                {"timestamp": current_ts.isoformat(), "power_w": float(power_w)},
            )
        elif previous is not None:
            # Keep the last good sample for continuity, but the next resumed sample
            # will fail the max-gap check and cannot fabricate energy.
            self.set_runtime("last_actual_sample", previous)

    def get_accumulator(self, interval_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM interval_accumulators WHERE interval_start=?",
            (interval_key,),
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        bucket_seconds = 1800.0
        covered = float(item.get("covered_seconds") or 0)
        item["actual_power_average_w"] = (
            float(item["energy_wh"]) / (covered / 3600.0) if covered > 0 else None
        )
        item["actual_energy_kwh"] = float(item["energy_wh"]) / 1000.0 if covered > 0 else None
        item["coverage_ratio"] = min(max(covered / bucket_seconds, 0.0), 1.0)
        return item

    def list_accumulators(self, *, since: str | None = None, limit: int = 10000) -> list[dict[str, Any]]:
        if since is None:
            rows = self.connection.execute(
                "SELECT interval_start FROM interval_accumulators ORDER BY interval_start DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT interval_start FROM interval_accumulators WHERE interval_start>=? ORDER BY interval_start LIMIT ?",
                (since, limit),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = self.get_accumulator(row[0])
            if item is not None:
                result.append(item)
        return result

    def list_intervals(self, *, since: str | None = None, limit: int = 10000) -> list[dict[str, Any]]:
        if since is None:
            rows = self.connection.execute(
                "SELECT payload_json FROM intervals ORDER BY interval_start DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT payload_json FROM intervals WHERE interval_start>=? ORDER BY interval_start LIMIT ?",
                (since, limit),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def list_storm_events(self, *, since: str | None = None) -> list[dict[str, Any]]:
        if since is None:
            rows = self.connection.execute("SELECT * FROM storm_events ORDER BY event_start").fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM storm_events WHERE event_start>=? ORDER BY event_start", (since,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            result.append(item)
        return result

    def prune(self, *, now: datetime | None = None, daily_days: int = 400, interval_days: int = 210, snapshot_days: int = 400) -> dict[str, int]:
        current = now or datetime.now(UTC)
        daily_cutoff = (current - timedelta(days=daily_days)).date().isoformat()
        interval_cutoff = (current - timedelta(days=interval_days)).isoformat()
        snapshot_cutoff = (current - timedelta(days=snapshot_days)).date().isoformat()
        counts: dict[str, int] = {}
        for table, column, cutoff in (
            ("daily_results", "local_date", daily_cutoff),
            ("intervals", "interval_start", interval_cutoff),
            ("interval_accumulators", "interval_start", interval_cutoff),
            ("forecast_snapshots", "target_date", snapshot_cutoff),
        ):
            cursor = self.connection.execute(f"DELETE FROM {table} WHERE {column} < ?", (cutoff,))
            counts[table] = cursor.rowcount
        return counts
