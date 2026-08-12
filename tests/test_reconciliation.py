"""Coverage for the reconciliation helpers extracted from the coordinator.

The three functions under test — ``reconcile_energy_counter``,
``reconcile_intervals``, ``rollup_daily`` — accept a store satisfying the
``ReconciliationStore`` protocol plus a ``ZoneInfo`` timezone and no Home
Assistant runtime. That lets us exercise them against a tiny in-memory
fake store instead of stubbing HA.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from solar_analytics.reconciliation import (  # type: ignore[import-not-found]
    reconcile_energy_counter,
    reconcile_intervals,
    rollup_daily,
)
from solar_analytics.v2_metrics import ActualState  # type: ignore[import-not-found]

KYIV = ZoneInfo("Europe/Kyiv")


@dataclass
class _FakeStore:
    """In-memory implementation of ``ReconciliationStore``."""

    runtime: dict[str, Any] = field(default_factory=dict)
    slots: list[dict[str, Any]] = field(default_factory=list)
    slot_periods: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    intervals: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    daily_writes: list[dict[str, Any]] = field(default_factory=list)
    upsert_interval_calls: list[dict[str, Any]] = field(default_factory=list)
    accumulator_result: Mapping[str, Any] = field(default_factory=dict)

    def get_runtime(self, key: str) -> Any:
        return self.runtime.get(key)

    def set_runtime(self, key: str, value: Any) -> None:
        self.runtime[key] = value

    def integrate_accumulators(self, start: datetime, end: datetime) -> Mapping[str, Any]:
        return self.accumulator_result

    def list_snapshot_slots(
        self,
        *,
        lineage_id: str | None = None,
        source_kind: str | None = None,
        snapshot_type: str | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        return [
            slot
            for slot in self.slots
            if (lineage_id is None or slot.get("lineage_id") == lineage_id)
            and (snapshot_type is None or slot.get("snapshot_type") == snapshot_type)
        ]

    def snapshot_periods(self, slot_id: int) -> Sequence[Mapping[str, Any]]:
        return self.slot_periods.get(slot_id, [])

    def list_intervals(self, *, lineage_id: str, local_date: str) -> Sequence[Mapping[str, Any]]:
        return self.intervals.get((lineage_id, local_date), [])

    def upsert_interval(self, row: Mapping[str, Any]) -> None:
        self.upsert_interval_calls.append(dict(row))

    def upsert_daily(self, row: Mapping[str, Any]) -> None:
        self.daily_writes.append(dict(row))

    def list_daily(self, *, lineage_id: str, since: str) -> Sequence[Mapping[str, Any]]:
        return [row for row in self.daily_writes if row.get("lineage_id") == lineage_id]


def _actual(value: float | None, *, status: str = "valid") -> ActualState:
    return ActualState(
        entity_id="sensor.pv_energy",
        value=value,
        unit="kWh",
        observed_at_utc=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
        status=status,
    )


# ---------------------------------------------------------------------------
# reconcile_energy_counter
# ---------------------------------------------------------------------------


def test_reconcile_energy_counter_returns_reason_when_state_invalid() -> None:
    store = _FakeStore()
    result = reconcile_energy_counter(
        store,
        actual_energy=_actual(None, status="stale"),
        now_utc=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
        tz=KYIV,
    )
    assert result == "stale"
    assert store.runtime == {}, "invalid observations must not touch the anchor"


def test_reconcile_energy_counter_creates_anchor_on_first_observation() -> None:
    store = _FakeStore()
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)

    result = reconcile_energy_counter(store, actual_energy=_actual(10.0), now_utc=now, tz=KYIV)

    assert result == "anchor_created"
    key = "energy_counter_anchor:2026-08-12"
    assert store.runtime[key]["value_kwh"] == 10.0


def test_reconcile_energy_counter_detects_counter_reset() -> None:
    store = _FakeStore()
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    reconcile_energy_counter(store, actual_energy=_actual(10.0), now_utc=now, tz=KYIV)

    # Counter drops by more than 50 Wh (0.05 kWh) -> treated as reset.
    result = reconcile_energy_counter(store, actual_energy=_actual(9.0), now_utc=now, tz=KYIV)

    assert result == "counter_reset_detected"
    assert store.runtime["reconciliation_status:2026-08-12"] == "counter_reset_detected"
    assert store.runtime["energy_counter_anchor:2026-08-12"]["value_kwh"] == 9.0


def test_reconcile_energy_counter_reports_reconciled_within_tolerance() -> None:
    store = _FakeStore(accumulator_result={"energy_wh": 1000.0, "covered_seconds": 3600.0})
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    reconcile_energy_counter(store, actual_energy=_actual(10.0), now_utc=now, tz=KYIV)

    # Counter grew 1 kWh; integrated power says 1.0 kWh -> match.
    result = reconcile_energy_counter(store, actual_energy=_actual(11.0), now_utc=now, tz=KYIV)

    assert result == "reconciled"
    assert store.runtime["reconciliation_status:2026-08-12"] == "reconciled"


def test_reconcile_energy_counter_reports_mismatch_when_delta_disagrees() -> None:
    store = _FakeStore(accumulator_result={"energy_wh": 500.0, "covered_seconds": 3600.0})
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    reconcile_energy_counter(store, actual_energy=_actual(10.0), now_utc=now, tz=KYIV)

    # Counter grew 5 kWh; integrated power says 0.5 kWh -> outside tolerance.
    result = reconcile_energy_counter(store, actual_energy=_actual(15.0), now_utc=now, tz=KYIV)

    assert result == "reconciliation_mismatch"
    assert store.runtime["reconciliation_status:2026-08-12"] == "reconciliation_mismatch"


def test_reconcile_energy_counter_falls_back_when_no_power_coverage() -> None:
    store = _FakeStore(accumulator_result={"energy_wh": None})
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    reconcile_energy_counter(store, actual_energy=_actual(10.0), now_utc=now, tz=KYIV)

    result = reconcile_energy_counter(store, actual_energy=_actual(10.5), now_utc=now, tz=KYIV)

    assert result == "counter_only_no_power_coverage"


def test_reconcile_energy_counter_resets_when_anchor_is_malformed() -> None:
    store = _FakeStore()
    store.runtime["energy_counter_anchor:2026-08-12"] = {"value_kwh": "not-a-number"}

    result = reconcile_energy_counter(
        store,
        actual_energy=_actual(10.0),
        now_utc=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
        tz=KYIV,
    )

    assert result == "anchor_reset_invalid"
    assert store.runtime["energy_counter_anchor:2026-08-12"]["value_kwh"] == 10.0


# ---------------------------------------------------------------------------
# reconcile_intervals
# ---------------------------------------------------------------------------


def test_reconcile_intervals_writes_paired_row_when_actual_covers_period() -> None:
    slot = {
        "snapshot_slot_id": 1,
        "lineage_id": "L1",
        "snapshot_type": "morning",
        "target_local_date": "2026-08-11",
        "admissible": True,
    }
    period = {
        "interval_start_utc": "2026-08-11T09:00:00+00:00",
        "interval_end_utc": "2026-08-11T10:00:00+00:00",
        "energy_wh": 500.0,
        "valid": True,
    }
    store = _FakeStore(
        slots=[slot],
        slot_periods={1: [period]},
        accumulator_result={"energy_wh": 490.0, "covered_seconds": 3600.0},
    )

    reconcile_intervals(
        store, lineage_id="L1", now_utc=datetime(2026, 8, 12, 12, 0, tzinfo=UTC), tz=KYIV
    )

    assert len(store.upsert_interval_calls) == 1
    row = store.upsert_interval_calls[0]
    assert row["paired_valid"] is True
    assert row["forecast_energy_wh"] == 500.0
    assert row["actual_energy_wh"] == 490.0
    assert row["validity_reason"] == "paired"
    assert row["target_local_date"] == "2026-08-11"


def test_reconcile_intervals_marks_actual_missing_when_no_coverage() -> None:
    slot = {
        "snapshot_slot_id": 2,
        "lineage_id": "L1",
        "snapshot_type": "morning",
        "target_local_date": "2026-08-11",
        "admissible": True,
    }
    period = {
        "interval_start_utc": "2026-08-11T09:00:00+00:00",
        "interval_end_utc": "2026-08-11T10:00:00+00:00",
        "energy_wh": 500.0,
        "valid": True,
    }
    store = _FakeStore(
        slots=[slot],
        slot_periods={2: [period]},
        accumulator_result={"energy_wh": None, "covered_seconds": 0.0},
    )

    reconcile_intervals(
        store, lineage_id="L1", now_utc=datetime(2026, 8, 12, 12, 0, tzinfo=UTC), tz=KYIV
    )

    row = store.upsert_interval_calls[0]
    assert row["paired_valid"] is False
    assert row["actual_energy_wh"] is None
    assert row["validity_reason"] == "actual_missing"


def test_reconcile_intervals_skips_period_crossing_local_day_boundary() -> None:
    slot = {
        "snapshot_slot_id": 3,
        "lineage_id": "L1",
        "snapshot_type": "morning",
        "target_local_date": "2026-08-11",
        "admissible": True,
    }
    # Kyiv 2026-08-11 starts at 2026-08-10T21:00:00Z, ends at 2026-08-11T21:00:00Z.
    # A period 20:30->22:00 UTC crosses the local-day boundary.
    period = {
        "interval_start_utc": "2026-08-11T20:30:00+00:00",
        "interval_end_utc": "2026-08-11T22:00:00+00:00",
        "energy_wh": 500.0,
        "valid": True,
    }
    store = _FakeStore(
        slots=[slot],
        slot_periods={3: [period]},
        accumulator_result={"energy_wh": 500.0, "covered_seconds": 5400.0},
    )

    reconcile_intervals(
        store, lineage_id="L1", now_utc=datetime(2026, 8, 12, 12, 0, tzinfo=UTC), tz=KYIV
    )

    assert store.upsert_interval_calls == [], (
        "cross-boundary periods must not be silently assigned to one side"
    )


def test_reconcile_intervals_ignores_non_admissible_and_future_days() -> None:
    non_admissible = {
        "snapshot_slot_id": 4,
        "lineage_id": "L1",
        "snapshot_type": "morning",
        "target_local_date": "2026-08-11",
        "admissible": False,
    }
    future = {
        "snapshot_slot_id": 5,
        "lineage_id": "L1",
        "snapshot_type": "morning",
        "target_local_date": "2026-12-31",
        "admissible": True,
    }
    period = {
        "interval_start_utc": "2026-08-11T09:00:00+00:00",
        "interval_end_utc": "2026-08-11T10:00:00+00:00",
        "energy_wh": 500.0,
        "valid": True,
    }
    store = _FakeStore(
        slots=[non_admissible, future],
        slot_periods={4: [period], 5: [period]},
        accumulator_result={"energy_wh": 500.0, "covered_seconds": 3600.0},
    )

    reconcile_intervals(
        store, lineage_id="L1", now_utc=datetime(2026, 8, 12, 12, 0, tzinfo=UTC), tz=KYIV
    )

    assert store.upsert_interval_calls == []


# ---------------------------------------------------------------------------
# rollup_daily
# ---------------------------------------------------------------------------


def test_rollup_daily_returns_empty_when_no_lineage() -> None:
    store = _FakeStore()
    result = rollup_daily(
        store, lineage_id=None, now_utc=datetime(2026, 8, 12, 12, 0, tzinfo=UTC), tz=KYIV
    )
    assert result == []
    assert store.daily_writes == []


def test_rollup_daily_records_non_admissible_reason() -> None:
    slot = {
        "snapshot_slot_id": 6,
        "lineage_id": "L1",
        "snapshot_type": "morning",
        "target_local_date": "2026-08-11",
        "admissible": False,
    }
    store = _FakeStore(slots=[slot])

    rollup_daily(store, lineage_id="L1", now_utc=datetime(2026, 8, 12, 12, 0, tzinfo=UTC), tz=KYIV)

    assert store.daily_writes[0]["reason"] == "morning_snapshot_not_admissible"


def test_rollup_daily_marks_valid_paired_day_when_all_gates_met() -> None:
    slot = {
        "snapshot_slot_id": 7,
        "lineage_id": "L1",
        "snapshot_type": "morning",
        "target_local_date": "2026-08-11",
        "admissible": True,
    }
    # Kyiv day is 86400 seconds. Load 24 intervals totaling 24 * 3600 = 86400.
    intervals = [
        {
            "interval_end_utc": f"2026-08-11T{hour:02d}:00:00+00:00",
            "eligible_seconds": 3600.0,
            "actual_covered_seconds": 3600.0,
            "forecast_valid": True,
            "paired_valid": True,
            "forecast_energy_wh": 1000.0,
            "actual_energy_wh": 950.0,
        }
        for hour in range(1, 25)
    ]
    store = _FakeStore(slots=[slot], intervals={("L1", "2026-08-11"): intervals})

    rollup_daily(store, lineage_id="L1", now_utc=datetime(2026, 8, 13, 12, 0, tzinfo=UTC), tz=KYIV)

    row = store.daily_writes[0]
    assert row["valid_paired_day"] is True
    assert row["reason"] == "valid_paired_day"
    assert row["actual_kwh"] == pytest.approx(22.8)
    assert row["forecast_kwh"] == pytest.approx(24.0)
    assert row["signed_error_kwh"] == pytest.approx(22.8 - 24.0)
    assert row["absolute_error_kwh"] == pytest.approx(abs(22.8 - 24.0))


def test_rollup_daily_marks_coverage_below_gate_when_actual_missing() -> None:
    slot = {
        "snapshot_slot_id": 8,
        "lineage_id": "L1",
        "snapshot_type": "morning",
        "target_local_date": "2026-08-11",
        "admissible": True,
    }
    intervals = [
        {
            "interval_end_utc": f"2026-08-11T{hour:02d}:00:00+00:00",
            "eligible_seconds": 3600.0,
            "actual_covered_seconds": 0.0,
            "forecast_valid": True,
            "paired_valid": False,
            "forecast_energy_wh": 1000.0,
            "actual_energy_wh": None,
        }
        for hour in range(1, 25)
    ]
    store = _FakeStore(slots=[slot], intervals={("L1", "2026-08-11"): intervals})

    rollup_daily(store, lineage_id="L1", now_utc=datetime(2026, 8, 13, 12, 0, tzinfo=UTC), tz=KYIV)

    row = store.daily_writes[0]
    assert row["valid_paired_day"] is False
    assert row["reason"] == "coverage_below_gate"
    assert row["signed_error_kwh"] is None
    assert row["actual_kwh"] is None
