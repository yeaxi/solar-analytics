"""Pure reconciliation and daily-rollup helpers.

Extracted from ``coordinator.py`` so the store-backed pieces can be tested
without instantiating a ``DataUpdateCoordinator``. Each helper takes a
store object satisfying the :class:`ReconciliationStore` protocol plus a
``ZoneInfo`` for the configured analytics timezone; nothing here touches
Home Assistant, the network, or any global state.

Public API

- :func:`reconcile_energy_counter` — updates the per-local-day energy
  anchor and returns a status string describing how the observation
  reconciled against the integrated-power accumulator.
- :func:`reconcile_intervals` — joins the immutable morning snapshot's
  period cells to the integrated actual-power accumulator, upserting
  one paired interval row per period.
- :func:`rollup_daily` — computes per-day aggregates (forecast/actual
  coverage, valid-paired-day flag, signed error) and returns the recent
  daily rows the coordinator publishes to entities.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from .v2_metrics import MIN_ACTUAL_COVERAGE, MIN_FORECAST_COVERAGE, ROLLING_DAYS, ActualState


class ReconciliationStore(Protocol):
    """The subset of :class:`SolarAnalyticsV2Store` these helpers need."""

    def get_runtime(self, key: str) -> Any: ...
    def set_runtime(self, key: str, value: Any) -> None: ...
    def integrate_accumulators(self, start: datetime, end: datetime) -> Mapping[str, Any]: ...
    def list_snapshot_slots(
        self,
        *,
        lineage_id: str | None = ...,
        source_kind: str | None = ...,
        snapshot_type: str | None = ...,
    ) -> Sequence[Mapping[str, Any]]: ...
    def snapshot_periods(self, slot_id: int) -> Sequence[Mapping[str, Any]]: ...
    def list_intervals(
        self, *, lineage_id: str, local_date: str
    ) -> Sequence[Mapping[str, Any]]: ...
    def upsert_interval(self, row: Mapping[str, Any]) -> None: ...
    def upsert_daily(self, row: Mapping[str, Any]) -> None: ...
    def list_daily(self, *, lineage_id: str, since: str) -> Sequence[Mapping[str, Any]]: ...


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def reconcile_energy_counter(
    store: ReconciliationStore,
    *,
    actual_energy: ActualState,
    now_utc: datetime,
    tz: ZoneInfo,
) -> str:
    """Update the per-local-day energy anchor and return a reconciliation status.

    Return values:

    - ``anchor_created`` — first observation of the day; anchor stored.
    - ``anchor_reset_invalid`` — stored anchor was unparseable; reset.
    - ``counter_reset_detected`` — the counter went backwards by more than
      50 Wh; anchor reset and ``reconciliation_status:<local_date>`` set to
      ``counter_reset_detected``.
    - ``counter_only_no_power_coverage`` — no integrated power data to
      compare against.
    - ``reconciled`` — counter delta matches integrated power within the
      relative tolerance (10% of the larger of |delta|, |integral|, 1 kWh)
      or an absolute 0.1 kWh floor.
    - ``reconciliation_mismatch`` — the two values disagree beyond tolerance.
    - Any ``actual_energy.reason`` / ``actual_energy.status`` value if the
      observation itself is invalid; the anchor is not touched in that case.
    """

    if not actual_energy.valid:
        return actual_energy.reason or actual_energy.status
    local_date = now_utc.astimezone(tz).date().isoformat()
    key = f"energy_counter_anchor:{local_date}"
    anchor = store.get_runtime(key)
    anchor_payload = {
        "value_kwh": actual_energy.value,
        "observed_at_utc": _iso(actual_energy.observed_at_utc),
    }
    if not isinstance(anchor, Mapping):
        store.set_runtime(key, anchor_payload)
        return "anchor_created"
    try:
        anchor_value = float(anchor["value_kwh"])
        assert actual_energy.value is not None
        delta = float(actual_energy.value) - anchor_value
        anchor_time = _parse_iso(anchor.get("observed_at_utc")) or now_utc
    except (KeyError, TypeError, ValueError, AssertionError):
        store.set_runtime(key, anchor_payload)
        return "anchor_reset_invalid"
    if delta < -0.05:
        store.set_runtime(key, anchor_payload)
        store.set_runtime(f"reconciliation_status:{local_date}", "counter_reset_detected")
        return "counter_reset_detected"
    integral = store.integrate_accumulators(anchor_time, actual_energy.observed_at_utc or now_utc)
    energy_wh = integral.get("energy_wh")
    if energy_wh is None:
        status = "counter_only_no_power_coverage"
    else:
        power_kwh = float(energy_wh) / 1000.0
        tolerance = max(0.1, 0.10 * max(abs(delta), abs(power_kwh), 1.0))
        status = "reconciled" if abs(delta - power_kwh) <= tolerance else "reconciliation_mismatch"
    store.set_runtime(f"reconciliation_status:{local_date}", status)
    return status


def reconcile_intervals(
    store: ReconciliationStore,
    *,
    lineage_id: str,
    now_utc: datetime,
    tz: ZoneInfo,
) -> None:
    """Join morning-snapshot period cells to integrated actual power.

    For each admissible morning snapshot whose target-local-date is on or
    before today, walk the snapshot's period rows, integrate actual power
    over each period's UTC window, and upsert one interval row per period.
    A period straddling a local-day boundary is skipped to avoid silently
    assigning cross-boundary energy to one day.
    """

    today = now_utc.astimezone(tz).date()
    for slot in store.list_snapshot_slots(lineage_id=lineage_id, snapshot_type="morning"):
        try:
            local_day = date.fromisoformat(str(slot["target_local_date"]))
        except (ValueError, TypeError):
            continue
        if local_day > today or not bool(slot.get("admissible")):
            continue
        day_start = datetime.combine(local_day, time.min, tzinfo=tz).astimezone(UTC)
        day_end = datetime.combine(local_day + timedelta(days=1), time.min, tzinfo=tz).astimezone(
            UTC
        )
        reconciliation_status = (
            store.get_runtime(f"reconciliation_status:{local_day.isoformat()}") or "not_observed"
        )
        for row in store.snapshot_periods(int(slot["snapshot_slot_id"])):
            if not bool(row.get("valid")):
                continue
            start = _parse_iso(row.get("interval_start_utc"))
            end = _parse_iso(row.get("interval_end_utc"))
            if start is None or end is None or end > now_utc or end <= start:
                continue
            if start < day_start or end > day_end:
                continue
            duration = (end - start).total_seconds()
            actual = store.integrate_accumulators(start, end)
            covered = min(float(actual.get("covered_seconds") or 0.0), duration)
            actual_energy = actual.get("energy_wh")
            actual_valid = covered >= duration * MIN_ACTUAL_COVERAGE and actual_energy is not None
            paired_valid = actual_valid
            reason = (
                "paired" if paired_valid else ("actual_gap" if covered > 0 else "actual_missing")
            )
            store.upsert_interval(
                {
                    "lineage_id": lineage_id,
                    "interval_start_utc": start.isoformat(),
                    "interval_end_utc": end.isoformat(),
                    "target_local_date": local_day.isoformat(),
                    "forecast_energy_wh": float(row["energy_wh"]),
                    "actual_energy_wh": (
                        float(actual_energy) if actual_energy is not None and actual_valid else None
                    ),
                    "eligible_seconds": duration,
                    "actual_covered_seconds": covered,
                    "forecast_valid": True,
                    "actual_valid": actual_valid,
                    "paired_valid": paired_valid,
                    "validity_reason": reason,
                    "reconciliation_status": reconciliation_status,
                }
            )


def rollup_daily(
    store: ReconciliationStore,
    *,
    lineage_id: str | None,
    now_utc: datetime,
    tz: ZoneInfo,
) -> list[Mapping[str, Any]]:
    """Compute per-day aggregates and return the last ``ROLLING_DAYS + 2`` days.

    Days with a non-admissible morning snapshot are recorded with
    ``reason="morning_snapshot_not_admissible"`` so they are not silently
    dropped. Days that meet the coverage gates become ``valid_paired_day``
    rows carrying signed and absolute error in kWh.
    """

    if not lineage_id:
        return []
    today = now_utc.astimezone(tz).date()
    for slot in store.list_snapshot_slots(lineage_id=lineage_id, snapshot_type="morning"):
        try:
            local_day = date.fromisoformat(str(slot["target_local_date"]))
        except (ValueError, TypeError):
            continue
        if local_day >= today:
            continue
        if not bool(slot.get("admissible")):
            store.upsert_daily(
                {
                    "lineage_id": lineage_id,
                    "local_date": local_day.isoformat(),
                    "morning_slot_id": slot["snapshot_slot_id"],
                    "reason": "morning_snapshot_not_admissible",
                }
            )
            continue
        intervals = [
            row
            for row in store.list_intervals(lineage_id=lineage_id, local_date=local_day.isoformat())
            if row["interval_end_utc"] <= now_utc.isoformat()
        ]
        day_seconds = (
            datetime.combine(local_day + timedelta(days=1), time.min, tzinfo=tz)
            - datetime.combine(local_day, time.min, tzinfo=tz)
        ).total_seconds()
        forecast_seconds = sum(
            float(row["eligible_seconds"] or 0.0) for row in intervals if row["forecast_valid"]
        )
        actual_seconds = sum(float(row["actual_covered_seconds"] or 0.0) for row in intervals)
        paired_seconds = sum(
            float(row["eligible_seconds"] or 0.0) for row in intervals if row["paired_valid"]
        )
        forecast_kwh = (
            sum(
                float(row["forecast_energy_wh"] or 0.0)
                for row in intervals
                if row["forecast_valid"]
            )
            / 1000.0
        )
        actual_kwh = (
            sum(float(row["actual_energy_wh"] or 0.0) for row in intervals if row["paired_valid"])
            / 1000.0
        )
        forecast_coverage = forecast_seconds / day_seconds if day_seconds else 0.0
        actual_coverage = actual_seconds / day_seconds if day_seconds else 0.0
        paired_coverage = paired_seconds / day_seconds if day_seconds else 0.0
        valid = (
            forecast_coverage >= MIN_FORECAST_COVERAGE
            and actual_coverage >= MIN_ACTUAL_COVERAGE
            and paired_coverage >= MIN_ACTUAL_COVERAGE
            and bool(intervals)
        )
        reason = "valid_paired_day" if valid else "coverage_below_gate"
        signed = actual_kwh - forecast_kwh if valid else None
        reconciliation_status = (
            store.get_runtime(f"reconciliation_status:{local_day.isoformat()}") or "not_observed"
        )
        store.upsert_daily(
            {
                "lineage_id": lineage_id,
                "local_date": local_day.isoformat(),
                "morning_slot_id": slot["snapshot_slot_id"],
                "forecast_coverage": forecast_coverage,
                "actual_coverage": actual_coverage,
                "paired_coverage": paired_coverage,
                "valid_paired_day": valid,
                "reason": reason,
                "actual_kwh": actual_kwh if valid else None,
                "forecast_kwh": forecast_kwh if valid else None,
                "signed_error_kwh": signed,
                "absolute_error_kwh": abs(signed) if signed is not None else None,
                "reconciliation_status": reconciliation_status,
            }
        )
    return list(
        store.list_daily(
            lineage_id=lineage_id,
            since=(today - timedelta(days=ROLLING_DAYS + 2)).isoformat(),
        )
    )
