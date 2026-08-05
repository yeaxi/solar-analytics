"""Pure, explicitly identified historical Recorder backfill helpers.

This module never labels historical data as live/native.  It parses the legacy
Forecast.Solar Recorder shape only for the amended ``historical_backfill`` path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from math import isfinite
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from .v2_metrics import MIN_ACTUAL_COVERAGE, MIN_FORECAST_COVERAGE

UTC = timezone.utc
DEFAULT_TIMEZONE = "Europe/Kyiv"
MAX_ACTUAL_GAP_SECONDS = 15 * 60
MAX_FORECAST_PERIOD_SECONDS = 2 * 60 * 60
BACKFILL_NORMALIZATION_VERSION = "historical-recorder-period-end-v1"
BACKFILL_METRIC_VERSION = "historical-backfill-morning-v1"


@dataclass(frozen=True)
class BackfillPeriod:
    interval_start_utc: datetime | None
    interval_end_utc: datetime
    energy_wh: float
    duration_seconds: float | None
    valid: bool
    exclusion_reason: str | None


@dataclass(frozen=True)
class ParsedForecastBackfill:
    source_entity: str
    source_kind: str
    observed_at_utc: datetime
    timezone_name: str
    payload_sha256: str
    periods: tuple[BackfillPeriod, ...]


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    else:
        raise ValueError("invalid_timestamp")
    if parsed.tzinfo is None:
        raise ValueError("naive_timestamp")
    return parsed.astimezone(UTC)


def _forecast_timestamp(value: Any, tz: ZoneInfo) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid_timestamp")
    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        # The legacy REST payload has no offset.  The source site timezone is
        # explicit input to the amended backfill path; do not use host timezone.
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(UTC)


def _number(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("invalid_forecast_value")
    try:
        number = float(value)
    except (TypeError, ValueError) as err:
        raise ValueError("invalid_forecast_value") from err
    if not isfinite(number) or number < 0:
        raise ValueError("invalid_forecast_value")
    return number


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def parse_legacy_forecast_result(
    result: Mapping[str, Any],
    *,
    source_entity: str,
    observed_at_utc: datetime,
    timezone_name: str = DEFAULT_TIMEZONE,
    max_period_seconds: int = MAX_FORECAST_PERIOD_SECONDS,
) -> ParsedForecastBackfill:
    """Parse legacy ``result`` without claiming native provenance.

    Values are treated as Wh per period ending at each timestamp.  The first
    boundary is deliberately invalid, and long positive-energy gaps remain
    invalid.  Explicit long zero-energy periods are accepted under the same
    sparse-zero rule as the native normalizer.
    """

    if not isinstance(result, Mapping) or not result:
        raise ValueError("empty_forecast_result")
    if not source_entity:
        raise ValueError("forecast_source_entity_missing")
    tz = ZoneInfo(timezone_name)
    observed = _utc(observed_at_utc)
    parsed: list[tuple[datetime, float]] = []
    seen: set[datetime] = set()
    for raw_timestamp, raw_value in result.items():
        timestamp = _forecast_timestamp(raw_timestamp, tz)
        if timestamp in seen:
            raise ValueError("duplicate_interval_end")
        seen.add(timestamp)
        parsed.append((timestamp, _number(raw_value)))
    parsed.sort(key=lambda item: item[0])
    periods: list[BackfillPeriod] = []
    previous: datetime | None = None
    for endpoint, energy_wh in parsed:
        if previous is None:
            periods.append(BackfillPeriod(None, endpoint, energy_wh, None, False, "missing_previous_boundary"))
            previous = endpoint
            continue
        duration = (endpoint - previous).total_seconds()
        if duration <= 0:
            raise ValueError("duplicate_interval_end")
        valid = duration <= max_period_seconds or energy_wh == 0.0
        reason = None if valid else "internal_gap_or_period_too_long"
        periods.append(BackfillPeriod(previous, endpoint, energy_wh, duration, valid, reason))
        previous = endpoint
    return ParsedForecastBackfill(
        source_entity=source_entity,
        source_kind="historical_legacy_rest",
        observed_at_utc=observed,
        timezone_name=timezone_name,
        payload_sha256=_digest(result),
        periods=tuple(periods),
    )


def _sample_timestamp(sample: Mapping[str, Any]) -> datetime:
    return _utc(sample.get("timestamp_utc"))


def _sample_power(sample: Mapping[str, Any]) -> float | None:
    raw = sample.get("power_w")
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if isfinite(value) and value >= 0 else None


def integrate_power_samples(
    samples: Sequence[Mapping[str, Any]],
    *,
    start_utc: datetime,
    end_utc: datetime,
    max_gap_seconds: int = MAX_ACTUAL_GAP_SECONDS,
) -> dict[str, float | int | str | None]:
    """Integrate Recorder power with no interpolation across invalid/long gaps."""

    start = _utc(start_utc)
    end = _utc(end_utc)
    if end <= start:
        return {"energy_wh": None, "covered_seconds": 0.0, "sample_count": 0, "quality": "invalid_window"}
    usable: list[tuple[datetime, float]] = []
    seen: set[datetime] = set()
    duplicate = False
    for sample in samples:
        try:
            timestamp = _sample_timestamp(sample)
        except (TypeError, ValueError):
            continue
        power = _sample_power(sample)
        if power is None:
            continue
        if timestamp in seen:
            duplicate = True
            continue
        seen.add(timestamp)
        usable.append((timestamp, power))
    usable.sort(key=lambda item: item[0])
    energy = 0.0
    covered = 0.0
    count = 0
    quality = "good"
    for (previous_ts, previous_power), (current_ts, _current_power) in zip(usable, usable[1:]):
        gap = (current_ts - previous_ts).total_seconds()
        if gap <= 0:
            duplicate = True
            continue
        overlap_start = max(previous_ts, start)
        overlap_end = min(current_ts, end)
        if overlap_end <= overlap_start:
            continue
        if gap > max_gap_seconds:
            quality = "gap"
            continue
        seconds = (overlap_end - overlap_start).total_seconds()
        energy += previous_power * seconds / 3600.0
        covered += seconds
        count += 1
    if duplicate:
        quality = "duplicate_timestamp"
    elif covered <= 0:
        quality = "missing"
    return {
        "energy_wh": energy if covered > 0 else None,
        "covered_seconds": covered,
        "sample_count": count,
        "quality": quality,
    }


def reconcile_energy_counter(
    samples: Sequence[Mapping[str, Any]],
    *,
    power_energy_wh: float | None,
) -> dict[str, float | str | None]:
    """Reconcile cumulative Recorder energy against integrated power."""

    usable: list[tuple[datetime, float]] = []
    for sample in samples:
        try:
            timestamp = _sample_timestamp(sample)
            raw = sample.get("energy_kwh")
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if isfinite(value) and value >= 0:
            usable.append((timestamp, value))
    usable.sort(key=lambda item: item[0])
    if len(usable) < 2:
        return {"status": "counter_insufficient_data", "counter_delta_kwh": None, "power_energy_kwh": (power_energy_wh / 1000.0 if power_energy_wh is not None else None), "tolerance_kwh": None}
    delta = usable[-1][1] - usable[0][1]
    power_kwh = power_energy_wh / 1000.0 if power_energy_wh is not None else None
    if delta < -0.05:
        return {"status": "counter_reset_detected", "counter_delta_kwh": delta, "power_energy_kwh": power_kwh, "tolerance_kwh": None}
    if power_kwh is None:
        return {"status": "counter_only_no_power_coverage", "counter_delta_kwh": delta, "power_energy_kwh": None, "tolerance_kwh": None}
    tolerance = max(0.1, 0.05 * max(abs(delta), abs(power_kwh), 1.0))
    status = "reconciled" if abs(delta - power_kwh) <= tolerance else "reconciliation_mismatch"
    return {"status": status, "counter_delta_kwh": delta, "power_energy_kwh": power_kwh, "tolerance_kwh": tolerance}


def _day_bounds(target_local_date: date, timezone_name: str) -> tuple[datetime, datetime, float]:
    tz = ZoneInfo(timezone_name)
    start_local = datetime.combine(target_local_date, time.min, tzinfo=tz)
    end_local = datetime.combine(target_local_date + timedelta(days=1), time.min, tzinfo=tz)
    start = start_local.astimezone(UTC)
    end = end_local.astimezone(UTC)
    return start, end, (end - start).total_seconds()


def build_backfill_intervals(
    periods: Sequence[BackfillPeriod],
    power_samples: Sequence[Mapping[str, Any]],
    *,
    target_local_date: date,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> list[dict[str, Any]]:
    day_start, day_end, _ = _day_bounds(target_local_date, timezone_name)
    rows: list[dict[str, Any]] = []
    for period in periods:
        if period.interval_start_utc is None:
            rows.append({
                "interval_start_utc": None,
                "interval_end_utc": period.interval_end_utc.isoformat(),
                "forecast_energy_wh": period.energy_wh,
                "actual_energy_wh": None,
                "eligible_seconds": 0.0,
                "actual_covered_seconds": 0.0,
                "forecast_valid": False,
                "actual_valid": False,
                "paired_valid": False,
                "validity_reason": period.exclusion_reason or "missing_previous_boundary",
            })
            continue
        if period.interval_start_utc < day_start or period.interval_end_utc > day_end:
            rows.append({
                "interval_start_utc": period.interval_start_utc.isoformat(),
                "interval_end_utc": period.interval_end_utc.isoformat(),
                "forecast_energy_wh": period.energy_wh,
                "actual_energy_wh": None,
                "eligible_seconds": 0.0,
                "actual_covered_seconds": 0.0,
                "forecast_valid": False,
                "actual_valid": False,
                "paired_valid": False,
                "validity_reason": "crosses_local_day_boundary",
            })
            continue
        actual = integrate_power_samples(
            power_samples,
            start_utc=period.interval_start_utc,
            end_utc=period.interval_end_utc,
        )
        duration = float(period.duration_seconds or 0.0)
        covered = float(actual.get("covered_seconds") or 0.0)
        actual_energy = actual.get("energy_wh")
        actual_valid = bool(period.valid and actual_energy is not None and covered >= duration * MIN_ACTUAL_COVERAGE)
        paired_valid = bool(period.valid and actual_valid)
        if not period.valid:
            reason = period.exclusion_reason or "forecast_invalid"
        elif paired_valid:
            reason = "paired"
        elif covered > 0:
            reason = "actual_gap"
        else:
            reason = "actual_missing"
        rows.append({
            "interval_start_utc": period.interval_start_utc.isoformat(),
            "interval_end_utc": period.interval_end_utc.isoformat(),
            "forecast_energy_wh": period.energy_wh,
            "actual_energy_wh": float(actual_energy) if actual_valid else None,
            "eligible_seconds": duration if period.valid else 0.0,
            "actual_covered_seconds": covered,
            "forecast_valid": bool(period.valid),
            "actual_valid": actual_valid,
            "paired_valid": paired_valid,
            "validity_reason": reason,
        })
    return rows


def build_backfill_daily(
    periods: Sequence[BackfillPeriod],
    power_samples: Sequence[Mapping[str, Any]],
    energy_samples: Sequence[Mapping[str, Any]],
    *,
    target_local_date: date,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    day_start, day_end, day_seconds = _day_bounds(target_local_date, timezone_name)
    rows = build_backfill_intervals(
        periods,
        power_samples,
        target_local_date=target_local_date,
        timezone_name=timezone_name,
    )
    forecast_seconds = sum(float(row["eligible_seconds"] or 0.0) for row in rows if row["forecast_valid"])
    actual_seconds = sum(float(row["actual_covered_seconds"] or 0.0) for row in rows)
    paired_seconds = sum(float(row["eligible_seconds"] or 0.0) for row in rows if row["paired_valid"])
    forecast_wh = sum(float(row["forecast_energy_wh"] or 0.0) for row in rows if row["forecast_valid"])
    actual_wh = sum(float(row["actual_energy_wh"] or 0.0) for row in rows if row["paired_valid"])
    all_power = integrate_power_samples(power_samples, start_utc=day_start, end_utc=day_end)
    day_energy_samples = []
    for sample in energy_samples:
        try:
            timestamp = _sample_timestamp(sample)
        except (TypeError, ValueError):
            continue
        if day_start <= timestamp <= day_end:
            day_energy_samples.append(sample)
    reconciliation = reconcile_energy_counter(day_energy_samples, power_energy_wh=all_power.get("energy_wh"))
    forecast_coverage = forecast_seconds / day_seconds if day_seconds else 0.0
    actual_coverage = actual_seconds / day_seconds if day_seconds else 0.0
    paired_coverage = paired_seconds / day_seconds if day_seconds else 0.0
    valid = bool(
        rows
        and forecast_coverage >= MIN_FORECAST_COVERAGE
        and actual_coverage >= MIN_ACTUAL_COVERAGE
        and paired_coverage >= MIN_ACTUAL_COVERAGE
    )
    signed = (actual_wh - forecast_wh) / 1000.0 if valid else None
    return {
        "local_date": target_local_date.isoformat(),
        "forecast_coverage": forecast_coverage,
        "actual_coverage": actual_coverage,
        "paired_coverage": paired_coverage,
        "valid_paired_day": valid,
        "reason": "valid_paired_day" if valid else "coverage_below_gate",
        "actual_kwh": actual_wh / 1000.0 if valid else None,
        "forecast_kwh": forecast_wh / 1000.0 if valid else None,
        "signed_error_kwh": signed,
        "absolute_error_kwh": abs(signed) if signed is not None else None,
        "reconciliation_status": reconciliation["status"],
        "reconciliation": reconciliation,
        "intervals": rows,
    }
