from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from solar_analytics.native import (
    clip_period_to_local_date,
    local_day_bounds_utc,
    normalize_native_wh_hours,
)
from solar_analytics.storage_v2 import SolarAnalyticsV2Store
from solar_analytics.v2_metrics import (
    compute_accuracy,
    daily_schedule,
    previous_slots_to_finalize,
    underperformance_allowed,
    validate_actual_state,
)

# Test-only fixture values; the shipping integration resolves these
# entity IDs at runtime from the user's config-flow selection or from
# the Home Assistant Energy Dashboard, and the timezone from its own
# config entry.
POWER_ENTITY = "sensor.example_pv_power"
ENERGY_ENTITY = "sensor.example_pv_energy"
KYIV = ZoneInfo("Europe/Kyiv")


def _payload(*values: tuple[str, float]) -> dict[str, dict[str, float]]:
    return {"wh_hours": dict(values)}


def test_native_period_end_semantics_and_boundary_quarantine() -> None:
    profile = normalize_native_wh_hours(
        _payload(
            ("2026-08-03T00:00:00+00:00", 0),
            ("2026-08-03T01:00:00+00:00", 100),
            ("2026-08-03T02:00:00+00:00", 200),
        )
    )
    assert profile.status == "complete"
    assert profile.periods[0].valid is False
    assert profile.periods[0].exclusion_reason == "missing_previous_boundary"
    assert profile.periods[1].start_utc.isoformat() == "2026-08-03T00:00:00+00:00"
    assert profile.periods[1].end_utc.isoformat() == "2026-08-03T01:00:00+00:00"
    assert profile.periods[1].energy_wh == 100
    assert profile.periods[2].start_utc.isoformat() == "2026-08-03T01:00:00+00:00"
    assert profile.periods[2].end_utc.isoformat() == "2026-08-03T02:00:00+00:00"
    assert profile.periods[2].energy_wh == 200
    assert profile.periods[2].power_w == 200


def test_native_contract_accepts_explicit_zero_energy_overnight_period() -> None:
    """Forecast.Solar emits one sparse zero-Wh period across the night."""

    profile = normalize_native_wh_hours(
        _payload(
            ("2026-07-27T17:50:39+00:00", 56),
            ("2026-07-28T02:20:22+00:00", 0),
            ("2026-07-28T03:00:00+00:00", 18),
        )
    )

    assert profile.status == "complete"
    overnight = profile.periods[1]
    assert overnight.duration_seconds is not None
    assert overnight.duration_seconds > 2 * 60 * 60
    assert overnight.energy_wh == 0
    assert overnight.valid is True
    assert overnight.power_w == 0


def test_native_contract_rejects_gap_nonfinite_and_naive_timestamps() -> None:
    gap = normalize_native_wh_hours(
        _payload(
            ("2026-08-03T01:00:00+00:00", 10),
            ("2026-08-03T04:30:00+00:00", 20),
        )
    )
    assert gap.status == "blocked"
    assert gap.periods[-1].exclusion_reason == "internal_gap_or_period_too_long"

    malformed = normalize_native_wh_hours(
        {"wh_hours": {"2026-08-03T01:00:00": 10, "2026-08-03T02:00:00+00:00": float("nan")}}
    )
    assert malformed.status == "blocked"
    assert malformed.invalid_count >= 1


def test_local_day_bounds_span_the_real_length_of_a_dst_day() -> None:
    for day, seconds in (
        (date(2026, 3, 29), 82800),
        (date(2026, 10, 25), 90000),
        (date(2026, 8, 10), 86400),
    ):
        day_start, day_end = local_day_bounds_utc(day, KYIV)
        assert (day_end - day_start).total_seconds() == seconds


def test_day_boundary_clip_admits_the_zero_energy_night_and_rejects_energetic_crossings() -> None:
    target = date(2026, 8, 3)
    day_start, day_end = local_day_bounds_utc(target, KYIV)
    assert day_start == datetime(2026, 8, 2, 21, tzinfo=UTC)
    assert day_end == datetime(2026, 8, 3, 21, tzinfo=UTC)

    inside = (datetime(2026, 8, 3, 6, tzinfo=UTC), datetime(2026, 8, 3, 7, tzinfo=UTC))
    assert clip_period_to_local_date(*inside, 900.0, target, tz=KYIV) == inside

    night = (datetime(2026, 8, 2, 17, tzinfo=UTC), datetime(2026, 8, 3, 2, tzinfo=UTC))
    assert clip_period_to_local_date(*night, 0.0, target, tz=KYIV) == (day_start, night[1])
    assert clip_period_to_local_date(*night, 0.0, date(2026, 8, 2), tz=KYIV) == (
        night[0],
        day_start,
    )

    assert clip_period_to_local_date(*night, 30.0, target, tz=KYIV) is None
    assert clip_period_to_local_date(None, night[1], 0.0, target, tz=KYIV) is None
    assert clip_period_to_local_date(*night, None, target, tz=KYIV) is None
    assert clip_period_to_local_date(*inside, 0.0, date(2026, 8, 5), tz=KYIV) is None


def test_schedule_is_fixed_local_time_dst_aware_and_targets_the_next_day() -> None:
    anchor = date(2026, 3, 28)
    morning, day_ahead = daily_schedule(anchor, tz=KYIV)
    assert (morning.snapshot_type, day_ahead.snapshot_type) == ("morning", "day_ahead")
    assert morning.scheduled_at_local.hour == 6
    assert day_ahead.scheduled_at_local.hour == 23
    assert morning.scheduled_at_local.tzinfo is not None
    assert morning.scheduled_at_utc < day_ahead.scheduled_at_utc

    assert morning.scheduled_at_local.date() == anchor
    assert day_ahead.scheduled_at_local.date() == anchor
    assert morning.target_local_date == date(2026, 3, 29)
    assert day_ahead.target_local_date == morning.target_local_date

    now = datetime(2026, 8, 3, 12, tzinfo=UTC)
    slots = previous_slots_to_finalize(now, tz=KYIV)
    assert slots
    assert all(slot.scheduled_at_utc < now for slot in slots)


def test_actual_contract_rejects_wrong_source_stale_and_restored() -> None:
    now = datetime(2026, 8, 3, 12, tzinfo=UTC)
    valid_power = {
        "entity_id": POWER_ENTITY,
        "state": "1250",
        "last_updated": now - timedelta(minutes=1),
        "attributes": {
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
        },
    }
    result = validate_actual_state(
        valid_power, expected_entity_id=POWER_ENTITY, kind="power", now_utc=now
    )
    assert result.valid and result.value == 1250

    stale = {**valid_power, "last_updated": now - timedelta(minutes=16)}
    assert (
        validate_actual_state(
            stale, expected_entity_id=POWER_ENTITY, kind="power", now_utc=now
        ).status
        == "stale"
    )

    restored = {**valid_power, "attributes": {**valid_power["attributes"], "restored": True}}
    assert (
        validate_actual_state(
            restored, expected_entity_id=POWER_ENTITY, kind="power", now_utc=now
        ).reason
        == "restored_state"
    )

    wrong = {**valid_power, "entity_id": "sensor.other"}
    assert (
        validate_actual_state(
            wrong, expected_entity_id=POWER_ENTITY, kind="power", now_utc=now
        ).reason
        == "entity_id_mismatch"
    )


def test_energy_contract_normalizes_kwh_without_daily_sensor_substitution() -> None:
    now = datetime(2026, 8, 3, 12, tzinfo=UTC)
    state = {
        "entity_id": ENERGY_ENTITY,
        "state": "12.5",
        "last_updated": now - timedelta(seconds=30),
        "attributes": {
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total",
        },
    }
    result = validate_actual_state(
        state, expected_entity_id=ENERGY_ENTITY, kind="energy", now_utc=now
    )
    assert result.valid and result.value == 12.5


def test_accuracy_requires_fourteen_valid_paired_days_and_uses_wape() -> None:
    today = date(2026, 8, 3)
    rows = [
        {
            "local_date": (today - timedelta(days=day)).isoformat(),
            "valid_paired_day": True,
            "actual_kwh": 10,
            "forecast_kwh": 8,
            "signed_error_kwh": -2,
            "absolute_error_kwh": 2,
        }
        for day in range(1, 15)
    ]
    result = compute_accuracy(rows, today_local=today)
    assert result["accuracy_ready"] is True
    assert result["valid_paired_days"] == 14
    assert result["wape"] == pytest.approx(0.2)

    rows[-1] = {**rows[-1], "valid_paired_day": False}
    blocked = compute_accuracy(rows, today_local=today)
    assert blocked["status"] == "insufficient_data"
    assert blocked["accuracy_ready"] is False


def test_underperformance_gate_is_independent_and_fail_closed() -> None:
    allowed, reason = underperformance_allowed({"accuracy_ready": True})
    assert allowed is True and reason == "allowed"
    allowed, reason = underperformance_allowed({"accuracy_ready": True, "curtailment": True})
    assert allowed is False and reason == "curtailment"


def test_v2_storage_migrates_additively_and_slot_is_idempotent(tmp_path) -> None:
    path = tmp_path / "solar.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("INSERT INTO meta VALUES('schema_version','1')")
        db.execute("CREATE TABLE legacy_rows(id INTEGER PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO legacy_rows VALUES(1,'preserve')")
        db.commit()

    store = SolarAnalyticsV2Store(path)
    store.initialize()
    assert store.schema_version() == 5
    assert store.integrity_check() == "ok"
    assert store.db.execute("SELECT value FROM legacy_rows WHERE id=1").fetchone()[0] == "preserve"

    now = datetime(2026, 8, 3, 12, tzinfo=UTC)
    metadata = {
        "native_entry_id": "native-entry",
        "model_fingerprint": "sha256:model",
        "actual_energy_entity": ENERGY_ENTITY,
        "actual_power_entity": POWER_ENTITY,
        "adapter_version": "2.0.0",
        "native_contract_version": "ha_forecast_solar_energy_2026.7.4",
    }
    lineage = store.ensure_lineage(contract_key="A", metadata=metadata, now=now)
    schedule = daily_schedule(date(2026, 8, 3), tz=KYIV)[0]
    first = store.ensure_snapshot_slot(
        lineage_id=lineage,
        source_kind="native",
        snapshot_type="morning",
        scheduled_at_utc=schedule.scheduled_at_utc,
        target_local_date=schedule.target_local_date,
        timezone_name="Europe/Kyiv",
        observed_at_utc=now,
        native_updated_at_utc=now,
        observation_sequence=1,
        payload_sha256="sha256:profile",
        adapter_version="2.0.0",
        normalization_version="native-period-end-v2",
        metric_version="morning-baseline-v2",
        status="admissible",
        admissible=True,
        exclusion_reason=None,
    )
    second = store.ensure_snapshot_slot(
        lineage_id=lineage,
        source_kind="native",
        snapshot_type="morning",
        scheduled_at_utc=schedule.scheduled_at_utc,
        target_local_date=schedule.target_local_date,
        timezone_name="Europe/Kyiv",
        observed_at_utc=now,
        native_updated_at_utc=now,
        observation_sequence=1,
        payload_sha256="sha256:profile",
        adapter_version="2.0.0",
        normalization_version="native-period-end-v2",
        metric_version="morning-baseline-v2",
        status="admissible",
        admissible=True,
        exclusion_reason=None,
    )
    assert first[0] == second[0] and first[1] is True and second[1] is False
    store.insert_snapshot_periods(
        first[0],
        [
            {
                "interval_start_utc": "2026-08-03T00:00:00+00:00",
                "interval_end_utc": "2026-08-03T01:00:00+00:00",
                "energy_wh": 100,
                "duration_seconds": 3600,
                "valid": True,
            }
        ],
    )
    assert len(store.snapshot_periods(first[0])) == 1
    store.close()


def test_v2_accuracy_cache_migrates_refresh_rows_and_overwrites_latest(tmp_path) -> None:
    path = tmp_path / "accuracy-cache.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("INSERT INTO meta VALUES('schema_version','3')")
        db.execute(
            "CREATE TABLE v2_accuracy_results ("
            "lineage_id TEXT NOT NULL, generated_at_utc TEXT NOT NULL, window_days INTEGER NOT NULL, "
            "valid_days INTEGER NOT NULL, accuracy_ready INTEGER NOT NULL, metric_version TEXT NOT NULL, "
            "payload_json TEXT NOT NULL, PRIMARY KEY(lineage_id, generated_at_utc))"
        )
        db.executemany(
            "INSERT INTO v2_accuracy_results VALUES(?,?,?,?,?,?,?)",
            [
                (
                    "lineage",
                    "2026-08-03T12:00:00+00:00",
                    30,
                    0,
                    0,
                    "morning-baseline-v2",
                    '{"generation": "old"}',
                ),
                (
                    "lineage",
                    "2026-08-03T12:05:00+00:00",
                    30,
                    1,
                    0,
                    "morning-baseline-v2",
                    '{"generation": "new"}',
                ),
            ],
        )
        db.commit()

    store = SolarAnalyticsV2Store(path)
    store.initialize()
    assert store.schema_version() == 5
    assert store.db.execute("SELECT count(*) FROM v2_accuracy_results").fetchone()[0] == 1
    assert store.latest_accuracy("lineage")["payload"] == {"generation": "new"}

    store.save_accuracy(
        lineage_id="lineage",
        generated_at=datetime(2026, 8, 3, 12, 10, tzinfo=UTC),
        window_days=30,
        valid_days=2,
        accuracy_ready=False,
        payload={"generation": "latest"},
    )
    assert store.db.execute("SELECT count(*) FROM v2_accuracy_results").fetchone()[0] == 1
    assert store.latest_accuracy("lineage")["payload"] == {"generation": "latest"}
    store.close()


_BACKFILL_TABLES = (
    "v2_backfill_runs",
    "v2_backfill_snapshots",
    "v2_backfill_snapshot_intervals",
    "v2_backfill_intervals",
    "v2_backfill_daily_comparisons",
    "v2_backfill_accuracy_results",
)


def _write_v4_database_with_backfill_tables(path) -> None:
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("INSERT INTO meta VALUES('schema_version','4')")
        db.execute("CREATE TABLE v2_backfill_runs(run_id TEXT PRIMARY KEY, status TEXT NOT NULL)")
        db.execute(
            "CREATE TABLE v2_backfill_snapshots(snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "run_id TEXT NOT NULL, FOREIGN KEY(run_id) REFERENCES v2_backfill_runs(run_id))"
        )
        db.execute(
            "CREATE TABLE v2_backfill_snapshot_intervals(snapshot_id INTEGER NOT NULL, "
            "interval_end_utc TEXT NOT NULL, PRIMARY KEY(snapshot_id, interval_end_utc), "
            "FOREIGN KEY(snapshot_id) REFERENCES v2_backfill_snapshots(snapshot_id))"
        )
        for child in (
            "v2_backfill_intervals",
            "v2_backfill_daily_comparisons",
            "v2_backfill_accuracy_results",
        ):
            db.execute(
                f"CREATE TABLE {child}(run_id TEXT NOT NULL, local_date TEXT NOT NULL, "
                f"PRIMARY KEY(run_id, local_date), "
                f"FOREIGN KEY(run_id) REFERENCES v2_backfill_runs(run_id))"
            )
        db.execute("INSERT INTO v2_backfill_runs VALUES('run-1','complete')")
        db.execute("INSERT INTO v2_backfill_snapshots(run_id) VALUES('run-1')")
        db.execute("INSERT INTO v2_backfill_snapshot_intervals VALUES(1,'2026-08-03T01:00:00Z')")
        for child in (
            "v2_backfill_intervals",
            "v2_backfill_daily_comparisons",
            "v2_backfill_accuracy_results",
        ):
            db.execute(f"INSERT INTO {child} VALUES('run-1','2026-08-03')")
        db.commit()


def _table_names(store: SolarAnalyticsV2Store) -> set[str]:
    return {
        str(row[0])
        for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def test_v4_database_drops_populated_backfill_tables_on_migration(tmp_path) -> None:
    """Child rows must not veto the drop: foreign_keys=ON makes DROP an implicit DELETE."""

    path = tmp_path / "backfill-v4.sqlite"
    _write_v4_database_with_backfill_tables(path)

    store = SolarAnalyticsV2Store(path)
    store.initialize()

    assert store.schema_version() == 5
    assert _table_names(store) & set(_BACKFILL_TABLES) == set()
    assert store.integrity_check() == "ok"
    store.close()


def test_fresh_database_initializes_at_5_without_backfill_tables(tmp_path) -> None:
    store = SolarAnalyticsV2Store(tmp_path / "fresh.sqlite")
    store.initialize()

    assert store.schema_version() == 5
    assert _table_names(store) & set(_BACKFILL_TABLES) == set()
    store.close()


def test_v2_storage_lineage_a_to_b_to_a_is_three_epochs(tmp_path) -> None:
    store = SolarAnalyticsV2Store(tmp_path / "lineage.sqlite")
    store.initialize()
    now = datetime(2026, 8, 3, tzinfo=UTC)
    metadata = {
        "native_entry_id": "e",
        "model_fingerprint": "f",
        "actual_energy_entity": ENERGY_ENTITY,
        "actual_power_entity": POWER_ENTITY,
        "adapter_version": "2",
        "native_contract_version": "n",
    }
    a1 = store.ensure_lineage(contract_key="A", metadata=metadata, now=now)
    b = store.ensure_lineage(
        contract_key="B",
        metadata={**metadata, "model_fingerprint": "f2"},
        now=now + timedelta(minutes=1),
    )
    a2 = store.ensure_lineage(contract_key="A", metadata=metadata, now=now + timedelta(minutes=2))
    assert len({a1, b, a2}) == 3
    assert store.current_lineage_id() == a2


def test_v2_current_lineage_id_is_scoped_to_configured_source(tmp_path) -> None:
    """A reconfigured source must not inherit the previous source's lineage."""

    store = SolarAnalyticsV2Store(tmp_path / "lineage_source.sqlite")
    store.initialize()
    now = datetime(2026, 8, 3, tzinfo=UTC)
    native_metadata = {
        "source_kind": "native",
        "native_entry_id": "entry-x",
        "forecast_entity_id": None,
        "model_fingerprint": "f",
        "actual_energy_entity": ENERGY_ENTITY,
        "actual_power_entity": POWER_ENTITY,
        "adapter_version": "2",
        "native_contract_version": "n",
    }
    native_id = store.ensure_lineage(contract_key="NATIVE", metadata=native_metadata, now=now)

    # No-arg reuse is unchanged.
    assert store.current_lineage_id() == native_id
    # Same source reuses the lineage.
    assert (
        store.current_lineage_id(source_kind="native", source_id="entry-x") == native_id
    )
    # A different Energy entry does not inherit the lineage.
    assert store.current_lineage_id(source_kind="native", source_id="entry-y") is None
    # Switching to a forecast entity does not inherit the lineage either.
    assert (
        store.current_lineage_id(source_kind="forecast_entity", source_id="sensor.f")
        is None
    )

    # Once the entity source produces its own lineage, it is reused for itself
    # and the old native source no longer matches.
    entity_metadata = {
        "source_kind": "forecast_entity",
        "native_entry_id": "",
        "forecast_entity_id": "sensor.f",
        "model_fingerprint": "g",
        "actual_energy_entity": ENERGY_ENTITY,
        "actual_power_entity": POWER_ENTITY,
        "adapter_version": "2",
        "native_contract_version": "n",
    }
    entity_id = store.ensure_lineage(
        contract_key="ENTITY", metadata=entity_metadata, now=now + timedelta(minutes=1)
    )
    assert entity_id != native_id
    assert (
        store.current_lineage_id(source_kind="forecast_entity", source_id="sensor.f")
        == entity_id
    )
    assert store.current_lineage_id(source_kind="native", source_id="entry-x") is None
    store.close()


def _import_rows(*pairs: tuple[str, float]) -> list[dict[str, object]]:
    return [
        {
            "local_date": local_date,
            "energy_kwh": energy_kwh,
            "coverage": 1.0,
            "observed_hours": 24,
            "expected_hours": 24,
            "counter_resets": 0,
        }
        for local_date, energy_kwh in pairs
    ]


def test_imported_actual_daily_reimport_replaces_instead_of_accumulating(tmp_path) -> None:
    store = SolarAnalyticsV2Store(tmp_path / "imported.sqlite")
    store.initialize()
    imported_at = datetime(2026, 8, 3, 5, tzinfo=UTC)

    for _ in range(2):
        store.replace_imported_actual_daily(
            source_entity_id=ENERGY_ENTITY,
            provenance="reconstructed_from_recorder_statistics",
            rows=_import_rows(("2026-08-01", 12.5), ("2026-08-02", 9.25)),
            imported_at=imported_at,
        )

    rows = store.list_imported_actual_daily(source_entity_id=ENERGY_ENTITY)
    assert [row["local_date"] for row in rows] == ["2026-08-01", "2026-08-02"]
    assert sum(float(row["energy_kwh"]) for row in rows) == 21.75
    assert {row["provenance"] for row in rows} == {"reconstructed_from_recorder_statistics"}

    store.replace_imported_actual_daily(
        source_entity_id=ENERGY_ENTITY,
        provenance="reconstructed_from_recorder_statistics",
        rows=_import_rows(("2026-08-02", 10.0)),
        imported_at=imported_at + timedelta(days=1),
    )
    corrected = store.list_imported_actual_daily(source_entity_id=ENERGY_ENTITY)
    assert [float(row["energy_kwh"]) for row in corrected] == [12.5, 10.0]
    store.close()


def test_imported_actual_daily_is_keyed_per_source_entity_and_pruned(tmp_path) -> None:
    store = SolarAnalyticsV2Store(tmp_path / "imported-prune.sqlite")
    store.initialize()
    prune_now = datetime(2026, 8, 3, tzinfo=UTC)
    cutoff = (prune_now - timedelta(days=30)).date()

    for entity in (ENERGY_ENTITY, "sensor.other_pv_energy"):
        store.replace_imported_actual_daily(
            source_entity_id=entity,
            provenance="reconstructed_from_recorder_statistics",
            rows=_import_rows(
                ((cutoff - timedelta(days=1)).isoformat(), 1.0),
                (cutoff.isoformat(), 2.0),
            ),
            imported_at=prune_now,
        )

    assert len(store.list_imported_actual_daily()) == 4
    assert len(store.list_imported_actual_daily(source_entity_id=ENERGY_ENTITY)) == 2

    pruned = store.prune(now=prune_now, retention_days=30)
    assert pruned["v2_imported_actual_daily"] == 2
    assert [row["local_date"] for row in store.list_imported_actual_daily()] == [
        cutoff.isoformat(),
        cutoff.isoformat(),
    ]
    store.close()


def test_v2_storage_backup_restore_and_exact_retention_boundary(tmp_path) -> None:
    path = tmp_path / "retention.sqlite"
    backup = tmp_path / "restore.sqlite"
    store = SolarAnalyticsV2Store(path)
    store.initialize()
    metadata = {
        "native_entry_id": "native-entry",
        "model_fingerprint": "sha256:model",
        "actual_energy_entity": ENERGY_ENTITY,
        "actual_power_entity": POWER_ENTITY,
        "adapter_version": "2.0.0",
        "native_contract_version": "ha_forecast_solar_energy_2026.7.4",
    }
    prune_now = datetime(2026, 1, 7, tzinfo=UTC)
    cutoff = (prune_now - timedelta(days=3650)).date()
    lineage = store.ensure_lineage(
        contract_key="retention",
        metadata=metadata,
        now=prune_now,
    )

    for target in (cutoff - timedelta(days=1), cutoff, cutoff + timedelta(days=1)):
        scheduled = datetime(target.year, target.month, target.day, 3, tzinfo=UTC)
        slot_id, inserted = store.ensure_snapshot_slot(
            lineage_id=lineage,
            source_kind="native",
            snapshot_type="morning",
            scheduled_at_utc=scheduled,
            target_local_date=target,
            timezone_name="Europe/Kyiv",
            observed_at_utc=scheduled,
            native_updated_at_utc=scheduled,
            observation_sequence=1,
            payload_sha256="sha256:profile",
            adapter_version="2.0.0",
            normalization_version="native-period-end-v2",
            metric_version="morning-baseline-v2",
            status="admissible",
            admissible=True,
            exclusion_reason=None,
        )
        assert inserted is True
        store.insert_snapshot_periods(
            slot_id,
            [
                {
                    "interval_start_utc": scheduled.isoformat(),
                    "interval_end_utc": (scheduled + timedelta(hours=1)).isoformat(),
                    "energy_wh": 100,
                    "duration_seconds": 3600,
                    "valid": True,
                }
            ],
        )

    store.backup_to(backup)
    restored = SolarAnalyticsV2Store(backup)
    restored.initialize()
    assert restored.integrity_check() == "ok"
    assert len(restored.list_snapshot_slots()) == 3
    assert len(restored.snapshot_periods(1)) == 1
    restored.close()

    pruned = store.prune(now=prune_now, retention_days=3650)
    assert pruned["v2_snapshot_slots"] == 1
    remaining = store.list_snapshot_slots()
    assert [row["target_local_date"] for row in remaining] == [
        cutoff.isoformat(),
        (cutoff + timedelta(days=1)).isoformat(),
    ]
    assert len(store.snapshot_periods(1)) == 0
    assert store.integrity_check() == "ok"
    store.close()
