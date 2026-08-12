"""Pure v2 calendar, actual-state and accuracy gates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

# ``KYIV`` is retained as a convenient default for pure-function tests only;
# runtime code accepts any ``ZoneInfo`` chosen in the config flow.
KYIV = ZoneInfo("Europe/Kyiv")
MAX_ACTUAL_AGE_SECONDS = 15 * 60
MIN_FORECAST_COVERAGE = 0.95
MIN_ACTUAL_COVERAGE = 0.90
MIN_PAIRED_DAYS = 14
ROLLING_DAYS = 30


@dataclass(frozen=True)
class ScheduledSlot:
    snapshot_type: str
    scheduled_at_local: datetime
    scheduled_at_utc: datetime
    target_local_date: date


def local_datetime(day: date, hour: int, minute: int, *, tz: ZoneInfo = KYIV) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=tz)


def daily_schedule(
    day: date,
    *,
    tz: ZoneInfo = KYIV,
    morning_hour: int = 6,
    day_ahead_hour: int = 23,
) -> tuple[ScheduledSlot, ScheduledSlot]:
    """Return D-1 morning and day-ahead slots for target D=day+1.

    The two hours default to the historical 06:00 / 23:00 baseline but are
    configurable via the config flow (``morning_snapshot_hour`` and
    ``day_ahead_snapshot_hour``).
    """

    target = day + timedelta(days=1)
    morning = local_datetime(day, morning_hour, 0, tz=tz)
    day_ahead = local_datetime(day, day_ahead_hour, 0, tz=tz)
    return (
        ScheduledSlot("morning", morning, morning.astimezone(UTC), target),
        ScheduledSlot("day_ahead", day_ahead, day_ahead.astimezone(UTC), target),
    )


def previous_slots_to_finalize(
    now_utc: datetime,
    *,
    tz: ZoneInfo = KYIV,
    lookback_days: int = 2,
    morning_hour: int = 6,
    day_ahead_hour: int = 23,
) -> tuple[ScheduledSlot, ...]:
    """Slots whose scheduled instant is in the past; no backfill implied."""

    now = now_utc.astimezone(UTC)
    local_day = now.astimezone(tz).date()
    result: list[ScheduledSlot] = []
    for offset in range(lookback_days, -1, -1):
        day = local_day - timedelta(days=offset)
        for slot in daily_schedule(
            day, tz=tz, morning_hour=morning_hour, day_ahead_hour=day_ahead_hour
        ):
            if slot.scheduled_at_utc < now:
                result.append(slot)
    return tuple(result)


@dataclass(frozen=True)
class ActualState:
    entity_id: str
    value: float | None
    unit: str | None
    observed_at_utc: datetime | None
    status: str
    reason: str | None = None

    @property
    def valid(self) -> bool:
        return (
            self.status == "valid" and self.value is not None and self.observed_at_utc is not None
        )


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _number(state: Any) -> float | None:
    if isinstance(state, bool) or state is None:
        return None
    try:
        value = float(state)
    except (TypeError, ValueError):
        return None
    return value if isfinite(value) else None


def validate_actual_state(
    state: Mapping[str, Any] | None,
    *,
    expected_entity_id: str,
    kind: str,
    now_utc: datetime,
    max_age_seconds: int = MAX_ACTUAL_AGE_SECONDS,
) -> ActualState:
    """Validate one canonical HA state without converting missing to zero."""

    if not isinstance(state, Mapping):
        return ActualState(expected_entity_id, None, None, None, "invalid", "entity_missing")
    if state.get("entity_id", expected_entity_id) != expected_entity_id:
        return ActualState(expected_entity_id, None, None, None, "invalid", "entity_id_mismatch")
    raw_state = state.get("state")
    if str(raw_state).lower() in {"unknown", "unavailable", "none", ""}:
        return ActualState(
            expected_entity_id, None, None, None, "invalid", "unknown_or_unavailable"
        )
    attrs = state.get("attributes") if isinstance(state.get("attributes"), Mapping) else {}
    if attrs.get("restored") is True or state.get("restored") is True:
        return ActualState(expected_entity_id, None, None, None, "invalid", "restored_state")
    observed = _as_utc(state.get("last_updated"))
    if observed is None:
        return ActualState(
            expected_entity_id, None, None, None, "invalid", "observation_timestamp_missing"
        )
    age = (now_utc.astimezone(UTC) - observed).total_seconds()
    if age < -300:
        return ActualState(
            expected_entity_id, None, None, observed, "invalid", "observation_in_future"
        )
    if age > max_age_seconds:
        return ActualState(
            expected_entity_id, None, None, observed, "stale", f"age_seconds:{age:.1f}"
        )
    value = _number(raw_state)
    if value is None or value < 0:
        return ActualState(
            expected_entity_id, None, None, observed, "invalid", "non_numeric_or_negative"
        )
    unit = str(attrs.get("unit_of_measurement") or "")
    device_class = attrs.get("device_class")
    state_class = attrs.get("state_class")
    if kind == "power":
        if device_class != "power" or state_class != "measurement" or unit not in {"W", "kW"}:
            return ActualState(
                expected_entity_id, None, unit, observed, "invalid", "power_contract_mismatch"
            )
        normalized = value * 1000.0 if unit == "kW" else value
    elif kind == "energy":
        if (
            device_class != "energy"
            or state_class not in {"total", "total_increasing"}
            or unit not in {"kWh", "Wh"}
        ):
            return ActualState(
                expected_entity_id, None, unit, observed, "invalid", "energy_contract_mismatch"
            )
        normalized = value / 1000.0 if unit == "Wh" else value
    else:
        return ActualState(
            expected_entity_id, None, unit, observed, "invalid", "unknown_actual_kind"
        )
    return ActualState(expected_entity_id, normalized, unit, observed, "valid")


def compute_accuracy(
    daily_rows: list[Mapping[str, Any]], *, today_local: date, window_days: int = ROLLING_DAYS
) -> dict[str, Any]:
    """Compute paired-day accuracy only from valid morning-baseline days."""

    start = today_local - timedelta(days=window_days)
    eligible: list[Mapping[str, Any]] = []
    for row in daily_rows:
        try:
            row_date = date.fromisoformat(str(row["local_date"]))
        except (KeyError, TypeError, ValueError):
            continue
        if start <= row_date < today_local and bool(row.get("valid_paired_day")):
            eligible.append(row)
    actual_total = sum(float(row.get("actual_kwh") or 0.0) for row in eligible)
    forecast_total = sum(float(row.get("forecast_kwh") or 0.0) for row in eligible)
    signed_total = sum(float(row.get("signed_error_kwh") or 0.0) for row in eligible)
    absolute_total = sum(float(row.get("absolute_error_kwh") or 0.0) for row in eligible)
    valid_days = len(eligible)
    ready = valid_days >= MIN_PAIRED_DAYS
    return {
        "status": "ready" if ready else "insufficient_data",
        "accuracy_ready": ready,
        "valid_paired_days": valid_days,
        "required_paired_days": MIN_PAIRED_DAYS,
        "window_days": window_days,
        "actual_kwh": actual_total if eligible else None,
        "forecast_kwh": forecast_total if eligible else None,
        "bias_kwh": signed_total if eligible else None,
        "wape": absolute_total / actual_total if eligible and actual_total > 0 else None,
        "confidence": "high"
        if ready and valid_days >= 21
        else ("medium" if ready else "insufficient"),
    }


def underperformance_allowed(quality: Mapping[str, Any]) -> tuple[bool, str]:
    """Independent gate; forecast accuracy never becomes a fault claim."""

    blockers = (
        "curtailment",
        "external_control",
        "inverter_limitation",
        "stale_telemetry",
        "missing_actual",
        "forecast_unavailable",
        "reconciliation_mismatch",
    )
    for blocker in blockers:
        if bool(quality.get(blocker)):
            return False, blocker
    if not bool(quality.get("accuracy_ready")):
        return False, "accuracy_not_ready"
    return True, "allowed"
