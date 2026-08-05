"""Pure deterministic analytics for the Solar Analytics Home Assistant integration.

The module intentionally has no Home Assistant imports.  It operates on normalized
30-minute records and compact daily records so that Hermes never has to inspect raw
Recorder history or thousands of state changes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from statistics import median
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

UTC = timezone.utc
KYIV = ZoneInfo("Europe/Kyiv")
UNKNOWN_STATES = {"", "unknown", "unavailable", "none", "null"}
VALIDITY_REASONS = (
    "valid_low_expected_power",
    "battery_full",
    "bms_charge_limit",
    "dvcc_limit",
    "export_limit",
    "mppt_error",
    "external_control",
    "thermal_derating",
    "clipping",
    "sensor_unavailable",
    "forecast_unavailable",
    "unknown_curtailment",
)
CURTAILMENT_REASONS = {
    "battery_full",
    "bms_charge_limit",
    "dvcc_limit",
    "export_limit",
    "clipping",
    "external_control",
    "thermal_derating",
    "unknown_curtailment",
}


def finite_float(value: Any) -> float | None:
    """Return a finite float, never silently converting missing input to zero."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().lower() in UNKNOWN_STATES:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def as_datetime(value: datetime | date | str, tz: ZoneInfo = KYIV) -> datetime:
    """Parse an ISO timestamp and make the timezone policy explicit."""

    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, time.min)
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        result = datetime.fromisoformat(text)
    if result.tzinfo is None:
        return result.replace(tzinfo=tz)
    return result.astimezone(tz)


def local_date(value: datetime | str) -> date:
    return as_datetime(value).date()


def interval_start(value: datetime, minutes: int = 30) -> datetime:
    """Round down on the UTC timeline, then expose the local timestamp.

    Flooring in UTC avoids creating nonexistent or duplicated wall-clock intervals
    at DST transitions. Europe/Kyiv changes by whole hours, so the resulting local
    boundaries remain aligned to the requested 30-minute grid.
    """

    local = as_datetime(value)
    utc = local.astimezone(UTC)
    epoch_seconds = int(utc.timestamp())
    bucket = minutes * 60
    floored = epoch_seconds - (epoch_seconds % bucket)
    return datetime.fromtimestamp(floored, UTC).astimezone(local.tzinfo or KYIV)


def to_w(value: Any, unit: str, duration_seconds: float = 3600.0) -> float | None:
    """Convert W, kW, Wh or kWh into average W for the interval."""

    number = finite_float(value)
    if number is None:
        return None
    normalized = (unit or "W").strip().lower().replace("·", "")
    if normalized in {"w", "watt", "watts"}:
        return number
    if normalized in {"kw", "kilowatt", "kilowatts"}:
        return number * 1000.0
    if normalized in {"wh", "watt-hour", "watt hours"}:
        return number * 3600.0 / max(duration_seconds, 1.0)
    if normalized in {"kwh", "kilowatt-hour", "kilowatt hours"}:
        return number * 3_600_000.0 / max(duration_seconds, 1.0)
    return None


def forecast_profile_analysis_allowed(
    native_contract: Mapping[str, Any],
    forecast_diagnostics: Mapping[str, Any],
) -> bool:
    """Allow interval analytics only after the native profile contract passes."""

    return (
        native_contract.get("status") == "ok"
        and forecast_diagnostics.get("model_status") == "aligned_to_native"
        and forecast_diagnostics.get("contract_status") in {"ok", "metadata_mismatch"}
        and not bool(forecast_diagnostics.get("normalization_blocked"))
    )


def forecast_snapshot_is_admissible(snapshot: Mapping[str, Any]) -> bool:
    """Allow a stored native Forecast.Solar profile after the same gates pass."""

    if snapshot.get("provider") != "forecast_solar":
        return False
    quality = snapshot.get("quality")
    if not isinstance(quality, Mapping):
        return False
    return (
        snapshot.get("profile_status") == "complete"
        and quality.get("model_status") == "aligned_to_native"
        and quality.get("normalization_blocked") is False
    )


@dataclass(frozen=True)
class ForecastPoint:
    timestamp: datetime
    power_w: float
    source_timestamp: datetime
    source_value: float
    source_unit: str
    contract_status: str = "ok"

    @property
    def energy_kwh_30m(self) -> float:
        return self.power_w * 0.5 / 1000.0


@dataclass(frozen=True)
class NormalizedForecast:
    provider: str
    points: tuple[ForecastPoint, ...]
    declared_unit: str
    effective_unit: str
    value_semantics: str
    timestamp_semantics: str
    contract_status: str
    duplicate_timestamps: int = 0
    invalid_points: int = 0

    def as_compact_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "declared_unit": self.declared_unit,
            "effective_unit": self.effective_unit,
            "value_semantics": self.value_semantics,
            "timestamp_semantics": self.timestamp_semantics,
            "contract_status": self.contract_status,
            "duplicate_timestamps": self.duplicate_timestamps,
            "invalid_points": self.invalid_points,
            "points": [
                {
                    "ts": point.timestamp.isoformat(),
                    "w": round(point.power_w, 3),
                    "src_ts": point.source_timestamp.isoformat(),
                }
                for point in self.points
            ],
        }


def normalize_forecast_result(
    result: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    provider: str,
    declared_unit: str = "W",
    effective_unit: str | None = None,
    value_semantics: str = "power",
    timestamp_semantics: str = "not_applicable",
    default_step_seconds: int = 3600,
    tz: ZoneInfo = KYIV,
) -> NormalizedForecast:
    """Normalize a Forecast.Solar-style timestamp map without trusting metadata.

    ``value_semantics=power`` means the value is a power estimate and its unit is
    interpreted directly.  ``value_semantics=energy`` means the value is energy
    over an explicit source period.  ``timestamp_semantics`` must then be ``start`` or ``end``; unresolved
  period boundaries fail closed as ``blocked_timestamp_semantics`` instead of
  fabricating a power curve.  This is what lets the same deterministic code
  handle W, kW, Wh and kWh without silently guessing provider semantics.
    """

    if isinstance(result, Mapping) and "result" in result:
        raw = result.get("result")
    else:
        raw = result
    entries: list[tuple[datetime, Any]] = []
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            try:
                entries.append((as_datetime(str(key), tz), value))
            except (TypeError, ValueError):
                continue
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            ts = item.get("timestamp", item.get("ts", item.get("start")))
            if ts is None:
                continue
            try:
                entries.append((as_datetime(str(ts), tz), item.get("value", item.get("power"))))
            except (TypeError, ValueError):
                continue
    entries.sort(key=lambda item: item[0])
    deduped: list[tuple[datetime, Any]] = []
    duplicate_count = 0
    for timestamp, value in entries:
        if deduped and timestamp == deduped[-1][0]:
            duplicate_count += 1
            deduped[-1] = (timestamp, value)
        else:
            deduped.append((timestamp, value))

    effective = effective_unit or declared_unit
    unit_mismatch = declared_unit.lower() != effective.lower()
    contract_status = "metadata_mismatch" if unit_mismatch else "ok"
    if value_semantics == "energy" and timestamp_semantics not in {"start", "end"}:
        contract_status = "blocked_timestamp_semantics"
    points: list[ForecastPoint] = []
    invalid_points = 0
    for index, (timestamp, raw_value) in enumerate(deduped):
        period_timestamp = timestamp
        if value_semantics == "energy" and timestamp_semantics == "end":
            if index == 0:
                invalid_points += 1
                continue
            period_timestamp = deduped[index - 1][0]
            duration = (timestamp - period_timestamp).total_seconds()
        elif value_semantics == "energy" and timestamp_semantics == "start":
            if index + 1 >= len(deduped):
                invalid_points += 1
                continue
            duration = (deduped[index + 1][0] - timestamp).total_seconds()
        elif index + 1 < len(deduped):
            duration = (deduped[index + 1][0] - timestamp).total_seconds()
        else:
            duration = float(default_step_seconds)
        if value_semantics == "energy" and contract_status == "blocked_timestamp_semantics":
            invalid_points += 1
            continue
        if duration <= 0 or duration > 6 * 3600:
            duration = float(default_step_seconds)
        unit = effective if value_semantics == "power" else effective
        power = to_w(raw_value, unit, duration)
        if power is None or power < 0:
            invalid_points += 1
            continue
        points.append(
            ForecastPoint(
                timestamp=period_timestamp,
                power_w=power,
                source_timestamp=timestamp,
                source_value=float(raw_value),
                source_unit=declared_unit,
                contract_status=contract_status,
            )
        )
    return NormalizedForecast(
        provider=provider,
        points=tuple(points),
        declared_unit=declared_unit,
        effective_unit=effective,
        value_semantics=value_semantics,
        timestamp_semantics=timestamp_semantics,
        contract_status=contract_status,
        duplicate_timestamps=duplicate_count,
        invalid_points=invalid_points,
    )


def resample_forecast(
    forecast: NormalizedForecast,
    start: datetime,
    end: datetime,
    *,
    minutes: int = 30,
) -> tuple[ForecastPoint, ...]:
    """Piecewise-constant resampling onto the shared 30-minute local grid."""

    start_local = interval_start(as_datetime(start), minutes)
    end_local = as_datetime(end)
    start_utc = start_local.astimezone(UTC)
    end_utc = end_local.astimezone(UTC)
    source = list(forecast.points)
    result: list[ForecastPoint] = []
    cursor_utc = start_utc
    index = 0
    current: ForecastPoint | None = None
    while cursor_utc < end_utc:
        cursor = cursor_utc.astimezone(start_local.tzinfo or KYIV)
        while index < len(source) and source[index].timestamp.astimezone(UTC) <= cursor_utc:
            current = source[index]
            index += 1
        if current is not None:
            result.append(
                ForecastPoint(
                    timestamp=cursor,
                    power_w=current.power_w,
                    source_timestamp=current.source_timestamp,
                    source_value=current.source_value,
                    source_unit=current.source_unit,
                    contract_status=current.contract_status,
                )
            )
        cursor_utc += timedelta(minutes=minutes)
    return tuple(result)


@dataclass(frozen=True)
class AggregatedSample:
    timestamp: datetime
    power_w: float | None


@dataclass(frozen=True)
class ActualAggregate:
    interval_start: datetime
    actual_power_average_w: float | None
    actual_energy_kwh: float | None
    coverage_ratio: float
    sample_count: int
    duplicate_timestamps: int = 0
    data_quality: str = "good"


def aggregate_power_samples(
    samples: Iterable[tuple[datetime | str, Any] | AggregatedSample],
    start: datetime,
    end: datetime,
    *,
    minutes: int = 30,
    max_gap_seconds: int = 900,
) -> tuple[ActualAggregate, ...]:
    """Time-weighted aggregation without storing raw samples.

    The previous value is held until the next sample.  A gap above
    ``max_gap_seconds`` contributes no covered time and therefore cannot silently
    become a valid zero-production interval.
    """

    prepared: list[AggregatedSample] = []
    for item in samples:
        if isinstance(item, AggregatedSample):
            timestamp, value = item.timestamp, item.power_w
        else:
            timestamp, value = item
        prepared.append(AggregatedSample(as_datetime(timestamp), finite_float(value)))
    prepared.sort(key=lambda sample: sample.timestamp)
    deduped: list[AggregatedSample] = []
    duplicate_count = 0
    for sample in prepared:
        if deduped and sample.timestamp == deduped[-1].timestamp:
            duplicate_count += 1
            deduped[-1] = sample
        else:
            deduped.append(sample)
    start_local = interval_start(as_datetime(start), minutes)
    end_local = as_datetime(end)
    results: list[ActualAggregate] = []
    cursor = start_local
    while cursor < end_local:
        boundary = min(cursor + timedelta(minutes=minutes), end_local)
        seconds_total = (boundary - cursor).total_seconds()
        energy_wh = 0.0
        covered = 0.0
        count = 0
        previous: AggregatedSample | None = None
        for sample in deduped:
            if sample.timestamp <= cursor:
                previous = sample
                continue
            if sample.timestamp >= boundary:
                break
            count += 1
            if previous is not None and previous.power_w is not None:
                gap = (sample.timestamp - previous.timestamp).total_seconds()
                if 0 < gap <= max_gap_seconds:
                    segment_start = max(cursor, previous.timestamp)
                    segment_seconds = (sample.timestamp - segment_start).total_seconds()
                    if segment_seconds > 0:
                        energy_wh += previous.power_w * segment_seconds / 3600.0
                        covered += segment_seconds
            previous = sample
        if previous is not None and previous.power_w is not None:
            gap = (boundary - previous.timestamp).total_seconds()
            if 0 <= gap <= max_gap_seconds:
                energy_wh += previous.power_w * max(gap, 0.0) / 3600.0
                covered += max(gap, 0.0)
        ratio = min(max(covered / seconds_total, 0.0), 1.0) if seconds_total else 0.0
        avg = energy_wh / (covered / 3600.0) if covered > 0 else None
        quality = "good" if ratio >= 0.8 else "gap"
        results.append(
            ActualAggregate(
                interval_start=cursor,
                actual_power_average_w=avg,
                actual_energy_kwh=energy_wh / 1000.0 if covered > 0 else None,
                coverage_ratio=ratio,
                sample_count=count,
                duplicate_timestamps=duplicate_count,
                data_quality=quality,
            )
        )
        cursor = boundary
    return tuple(results)


@dataclass(frozen=True)
class ValidityContext:
    actual_power_w: float | None
    expected_power_w: float | None
    forecast_solar_available: bool = True
    vrm_forecast_available: bool = True
    solar_elevation_deg: float | None = None
    mppt_error: str | None = None
    mppt_external_control: bool = False
    battery_can_accept_charge: bool | None = True
    bms_charge_limit_active: bool = False
    dvcc_limit_active: bool = False
    battery_full: bool = False
    load_or_export_available: bool | None = True
    ess_limiting: bool = False
    export_allowed: bool | None = True
    clipping: bool = False
    thermal_derating: bool = False
    critical_data_available: bool = True
    unknown_curtailment: bool = False
    min_expected_power_w: float = 50.0
    min_solar_elevation_deg: float = 3.0


@dataclass(frozen=True)
class ValidityResult:
    analysis_valid: bool
    reason: str
    curtailment_reason: str | None = None


def evaluate_validity(context: ValidityContext) -> ValidityResult:
    """Apply the centralized fail-closed validity mask in deterministic order."""

    if context.actual_power_w is None or not context.critical_data_available:
        return ValidityResult(False, "sensor_unavailable")
    if context.expected_power_w is None or not context.forecast_solar_available or not context.vrm_forecast_available:
        return ValidityResult(False, "forecast_unavailable")
    if context.expected_power_w <= context.min_expected_power_w:
        return ValidityResult(False, "valid_low_expected_power")
    if context.solar_elevation_deg is not None and context.solar_elevation_deg < context.min_solar_elevation_deg:
        return ValidityResult(False, "valid_low_expected_power")
    if context.mppt_error and context.mppt_error not in {"no_error", "-", "none"}:
        return ValidityResult(False, "mppt_error")
    if context.mppt_external_control:
        return ValidityResult(False, "external_control", "external_control")
    if context.battery_can_accept_charge is False:
        return ValidityResult(False, "bms_charge_limit", "bms_charge_limit")
    if context.bms_charge_limit_active:
        return ValidityResult(False, "bms_charge_limit", "bms_charge_limit")
    if context.dvcc_limit_active:
        return ValidityResult(False, "dvcc_limit", "dvcc_limit")
    if context.battery_full and context.load_or_export_available is False:
        return ValidityResult(False, "battery_full", "battery_full")
    if context.ess_limiting or context.export_allowed is False:
        return ValidityResult(False, "export_limit", "export_limit")
    if context.clipping:
        return ValidityResult(False, "clipping", "clipping")
    if context.thermal_derating:
        return ValidityResult(False, "thermal_derating", "thermal_derating")
    if context.unknown_curtailment:
        return ValidityResult(False, "unknown_curtailment", "unknown_curtailment")
    return ValidityResult(True, "valid")


@dataclass(frozen=True)
class IntervalMetric:
    interval_start: datetime
    actual_power_average_w: float | None
    actual_energy_kwh: float | None
    forecast_solar_power_w: float | None
    forecast_solar_energy_kwh: float | None
    vrm_forecast_power_w: float | None
    vrm_forecast_energy_kwh: float | None
    consensus_expected_power_w: float | None = None
    consensus_expected_energy_kwh: float | None = None
    analysis_valid: bool = False
    validity_reason: str = "forecast_unavailable"
    curtailment_reason: str | None = None
    storm_context: str | None = None
    data_quality: str = "good"
    coverage_ratio: float = 1.0
    peak_actual: bool = False

    def as_storage_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["interval_start"] = self.interval_start.isoformat()
        return result


@dataclass(frozen=True)
class DailyMetric:
    local_date: date
    actual_valid_energy_kwh: float
    actual_total_energy_kwh: float | None
    forecast_solar_kwh: float | None
    vrm_forecast_kwh: float | None
    valid_coverage: float
    valid_intervals: int
    expected_intervals: int
    curtailment_duration_minutes: int = 0
    storm_flag: bool = False
    data_quality: str = "good"
    actual_peak_power_w: float | None = None
    actual_peak_time: datetime | None = None
    forecast_solar_peak_power_w: float | None = None
    forecast_solar_peak_time: datetime | None = None
    vrm_peak_power_w: float | None = None
    vrm_peak_time: datetime | None = None
    snapshot_kind_solar: str | None = None
    snapshot_kind_vrm: str | None = None

    @property
    def consensus_expected_kwh(self) -> float | None:
        available = [value for value in (self.forecast_solar_kwh, self.vrm_forecast_kwh) if value is not None]
        return sum(available) / len(available) if available else None

    @property
    def consensus_realization(self) -> float | None:
        expected = self.consensus_expected_kwh
        if expected is None or expected <= 0:
            return None
        return self.actual_valid_energy_kwh / expected

    def as_storage_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["local_date"] = self.local_date.isoformat()
        for key in (
            "actual_peak_time",
            "forecast_solar_peak_time",
            "vrm_peak_time",
        ):
            if result[key] is not None:
                result[key] = result[key].isoformat()
        return result


@dataclass(frozen=True)
class AccuracyMetrics:
    provider: str
    window_days: int
    valid_days: int
    actual_kwh: float
    forecast_kwh: float
    bias: float | None
    wape: float | None
    mean_daily_error: float | None
    peak_time_error_minutes: float | None
    peak_power_error_w: float | None
    coverage: float
    confidence: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _window_days(days: Sequence[DailyMetric], window: int) -> list[DailyMetric]:
    ordered = sorted(days, key=lambda item: item.local_date)
    if not ordered:
        return []
    cutoff = ordered[-1].local_date - timedelta(days=window - 1)
    return [item for item in ordered if item.local_date >= cutoff]


def _provider_values(day: DailyMetric, provider: str) -> tuple[float, float] | None:
    forecast = day.forecast_solar_kwh if provider == "forecast_solar" else day.vrm_forecast_kwh
    if forecast is None or forecast <= 0 or day.actual_valid_energy_kwh < 0:
        return None
    return day.actual_valid_energy_kwh, forecast


def compute_accuracy(days: Sequence[DailyMetric], provider: str, window_days: int) -> AccuracyMetrics:
    """Compute bias/WAPE on completed valid days, never hourly MAPE."""

    selected = [item for item in _window_days(days, window_days) if item.valid_coverage >= 0.5]
    pairs = [pair for item in selected if (pair := _provider_values(item, provider)) is not None]
    actual_total = sum(pair[0] for pair in pairs)
    forecast_total = sum(pair[1] for pair in pairs)
    bias = (actual_total - forecast_total) / forecast_total if forecast_total > 0 else None
    wape = sum(abs(actual - forecast) for actual, forecast in pairs) / actual_total if actual_total > 0 else None
    daily_errors = [(actual - forecast) / forecast for actual, forecast in pairs if forecast > 0]
    coverage = sum(item.valid_coverage for item in selected) / len(selected) if selected else 0.0
    peak_time_errors: list[float] = []
    peak_power_errors: list[float] = []
    for item in selected:
        if provider == "forecast_solar":
            forecast_time, forecast_power = item.forecast_solar_peak_time, item.forecast_solar_peak_power_w
        else:
            forecast_time, forecast_power = item.vrm_peak_time, item.vrm_peak_power_w
        if item.actual_peak_time is not None and forecast_time is not None:
            peak_time_errors.append((forecast_time - item.actual_peak_time).total_seconds() / 60.0)
        if item.actual_peak_power_w is not None and forecast_power is not None:
            peak_power_errors.append(forecast_power - item.actual_peak_power_w)
    valid_days = len(pairs)
    if valid_days >= 30 and coverage >= 0.8:
        confidence = "high"
    elif valid_days >= 14 and coverage >= 0.6:
        confidence = "medium"
    elif valid_days >= 7:
        confidence = "low"
    else:
        confidence = "insufficient"
    return AccuracyMetrics(
        provider=provider,
        window_days=window_days,
        valid_days=valid_days,
        actual_kwh=actual_total,
        forecast_kwh=forecast_total,
        bias=bias,
        wape=wape,
        mean_daily_error=sum(daily_errors) / len(daily_errors) if daily_errors else None,
        peak_time_error_minutes=sum(peak_time_errors) / len(peak_time_errors) if peak_time_errors else None,
        peak_power_error_w=sum(peak_power_errors) / len(peak_power_errors) if peak_power_errors else None,
        coverage=coverage,
        confidence=confidence,
    )


def build_consensus(
    solar_kwh: float | None,
    vrm_kwh: float | None,
    *,
    solar_wape: float | None = None,
    vrm_wape: float | None = None,
    valid_days: int = 0,
    minimum_history_days: int = 14,
    minimum_weight: float = 0.2,
) -> dict[str, Any]:
    """Create bounded adaptive weights and a consensus expectation."""

    if valid_days < minimum_history_days or solar_wape is None or vrm_wape is None:
        weights = {"forecast_solar": 0.5, "vrm": 0.5}
        weight_mode = "equal_insufficient_history"
    else:
        solar_score = 1.0 / max(solar_wape, 0.001)
        vrm_score = 1.0 / max(vrm_wape, 0.001)
        total = solar_score + vrm_score
        weights = {
            "forecast_solar": solar_score / total,
            "vrm": vrm_score / total,
        }
        # A short or lucky streak must not make one provider authoritative.
        weights["forecast_solar"] = round(
            min(max(weights["forecast_solar"], minimum_weight), 1 - minimum_weight),
            6,
        )
        weights["vrm"] = round(1.0 - weights["forecast_solar"], 6)
        weight_mode = "rolling_wape_bounded"
    available: list[tuple[str, float, float]] = []
    if solar_kwh is not None and solar_kwh >= 0:
        available.append(("forecast_solar", solar_kwh, weights["forecast_solar"]))
    if vrm_kwh is not None and vrm_kwh >= 0:
        available.append(("vrm", vrm_kwh, weights["vrm"]))
    if not available:
        expected = None
        effective_weights: dict[str, float] = {}
    else:
        total_weight = sum(item[2] for item in available)
        effective_weights = {name: weight / total_weight for name, _, weight in available}
        expected = sum(value * weight / total_weight for _, value, weight in available)
    return {
        "expected_kwh": expected,
        "weights": weights,
        "effective_weights": effective_weights,
        "weight_mode": weight_mode,
        "providers_used": [item[0] for item in available],
    }


def compute_baseline(days: Sequence[DailyMetric], minimum_expected_kwh: float = 1.0) -> float | None:
    """Median consensus realization over the previous valid 30 days."""

    realizations = [
        item.consensus_realization
        for item in sorted(days, key=lambda item: item.local_date)[-30:]
        if item.valid_coverage >= 0.6
        and not item.curtailment_duration_minutes
        and not item.storm_flag
        and item.consensus_expected_kwh is not None
        and item.consensus_expected_kwh >= minimum_expected_kwh
        and item.consensus_realization is not None
    ]
    return median(realizations) if realizations else None


def _median_realization(days: Sequence[DailyMetric]) -> float | None:
    values = [item.consensus_realization for item in days if item.consensus_realization is not None]
    return median(values) if values else None


def _time_of_day_pattern(days: Sequence[DailyMetric]) -> str | None:
    """Use compact daily flags populated by the coordinator when available."""

    flags: list[str] = []
    for item in days[-14:]:
        value = getattr(item, "time_of_day_pattern", None)
        if value:
            flags.append(str(value))
    if not flags:
        return None
    counts: dict[str, int] = {}
    for flag in flags:
        counts[flag] = counts.get(flag, 0) + 1
    return max(counts, key=counts.get)


def detect_anomalies(
    current: Sequence[IntervalMetric],
    days: Sequence[DailyMetric],
    *,
    baseline: float | None,
    array_capacity_w: float | None,
    inverter_size_w: float | None = None,
) -> dict[str, Any]:
    """Return evidence-based anomaly flags; curtailment is never a PV fault."""

    if array_capacity_w is None or array_capacity_w <= 0:
        return {
            "near_zero_anomaly": False,
            "near_zero_intervals": 0,
            "persistent_underperformance": False,
            "step_change": False,
            "possible_gradual_decline": False,
            "time_of_day_pattern": None,
            "clipping_detected": False,
            "classification": "forecast_contract_unavailable",
            "evidence": {
                "baseline": baseline,
                "recent_median": None,
                "previous_median": None,
                "valid_days": 0,
                "capacity_contract": "unavailable",
            },
        }

    near_zero_intervals: list[IntervalMetric] = []
    for record in current:
        expected = record.consensus_expected_power_w
        if (
            expected is not None
            and expected > 0.20 * array_capacity_w
            and record.actual_power_average_w is not None
            and record.actual_power_average_w < 0.05 * array_capacity_w
            and record.analysis_valid
            and not record.curtailment_reason
        ):
            near_zero_intervals.append(record)
    near_zero = len(near_zero_intervals) >= 1

    valid_days = [
        item
        for item in days
        if item.consensus_realization is not None
        and item.valid_coverage >= 0.6
        and not item.curtailment_duration_minutes
        and item.consensus_expected_kwh is not None
        and item.consensus_expected_kwh >= array_capacity_w / 1000.0 * 0.25
    ]
    recent = valid_days[-5:]
    prior = valid_days[-19:-5]
    recent_median = _median_realization(recent)
    prior_median = _median_realization(prior)
    current_baseline = baseline if baseline is not None else _median_realization(valid_days[:-1])
    persistent = bool(
        current_baseline is not None
        and recent_median is not None
        and recent_median < current_baseline * 0.85
        and len(recent) >= 2
        and all(
            (item.forecast_solar_kwh is not None and item.vrm_forecast_kwh is not None)
            for item in recent
        )
    )
    step_change = bool(
        prior_median is not None
        and recent_median is not None
        and prior_median > 0
        and (prior_median - recent_median) / prior_median >= 0.12
        and len(recent) >= 2
        and len(prior) >= 5
    )
    gradual = False
    if len(valid_days) >= 21:
        weekly = [_median_realization(valid_days[index : index + 7]) for index in range(0, len(valid_days) - 6, 7)]
        weekly = [value for value in weekly if value is not None]
        gradual = len(weekly) >= 3 and all(a >= b for a, b in zip(weekly, weekly[1:])) and weekly[-1] < weekly[0] * 0.9
    clipping = False
    if inverter_size_w and len(current) >= 3:
        plateau = [
            item
            for item in current
            if item.actual_power_average_w is not None
            and item.actual_power_average_w >= inverter_size_w * 0.98
            and item.analysis_valid
        ]
        clipping = len(plateau) >= 3
    pattern = _time_of_day_pattern(days)
    return {
        "near_zero_anomaly": near_zero,
        "near_zero_intervals": len(near_zero_intervals),
        "persistent_underperformance": persistent,
        "step_change": step_change,
        "possible_gradual_decline": gradual,
        "time_of_day_pattern": pattern,
        "clipping_detected": clipping,
        "classification": (
            "persistent_underperformance"
            if persistent
            else "possible_underperformance"
            if step_change or near_zero
            else "possible_gradual_decline"
            if gradual
            else "clipping_detected"
            if clipping
            else "normal"
        ),
        "evidence": {
            "baseline": baseline,
            "recent_median": recent_median,
            "previous_median": prior_median,
            "valid_days": len(valid_days),
        },
    }


@dataclass(frozen=True)
class Recommendation:
    parameter: str
    current: float | None
    recommended: float | None
    expected_effect: str
    forecast_solar_bias: float | None
    vrm_bias: float | None
    confidence: str
    action: str = "review and apply manually"
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _part_bias(days: Sequence[DailyMetric], provider: str, part: str) -> float | None:
    values: list[tuple[float, float]] = []
    for item in days:
        profile = getattr(item, f"{provider}_{part}_actual_forecast", None)
        if isinstance(profile, Mapping):
            actual = finite_float(profile.get("actual"))
            forecast = finite_float(profile.get("forecast"))
            if actual is not None and forecast is not None and forecast > 0:
                values.append((actual, forecast))
    if not values:
        return None
    return sum(actual - forecast for actual, _ in values) / sum(forecast for _, forecast in values)


def generate_recommendations(
    days: Sequence[DailyMetric],
    *,
    current_parameters: Mapping[str, float | None],
    forecast_solar_accuracy: AccuracyMetrics,
    vrm_accuracy: AccuracyMetrics,
    valid_interval_count: int,
) -> tuple[Recommendation, ...]:
    """Generate manual Forecast.Solar tuning suggestions only with a real base."""

    valid_days = [
        item
        for item in days
        if item.valid_coverage >= 0.6
        and not item.curtailment_duration_minutes
        and item.consensus_expected_kwh is not None
    ]
    if len(valid_days) < 20 or valid_interval_count < 100:
        return ()
    first = valid_days[-28:-14]
    second = valid_days[-14:]
    if len(first) < 7 or len(second) < 7:
        return ()
    suggestions: list[Recommendation] = []
    # Daily bias uses actual - forecast; a negative value means Forecast.Solar is high.
    solar_bias = forecast_solar_accuracy.bias
    vrm_bias = vrm_accuracy.bias
    if solar_bias is not None and vrm_bias is not None and abs(solar_bias) >= 0.05:
        scale = 1.0 + solar_bias
        current = current_parameters.get("plane_capacity_w")
        recommended = current * scale if current is not None else None
        if recommended is not None and abs(recommended - current) / current >= 0.05 and abs(vrm_bias) < abs(solar_bias):
            suggestions.append(
                Recommendation(
                    parameter="plane_capacity",
                    current=current,
                    recommended=round(recommended, 1),
                    expected_effect=f"scale Forecast.Solar all-day prediction by {scale:.3f}",
                    forecast_solar_bias=solar_bias,
                    vrm_bias=vrm_bias,
                    confidence="high" if len(valid_days) >= 30 else "medium",
                    evidence={"valid_days": len(valid_days), "valid_intervals": valid_interval_count},
                )
            )
    for parameter, part in (("morning_damping", "morning"), ("evening_damping", "evening")):
        part_bias = _part_bias(second, "forecast_solar", part)
        first_bias = _part_bias(first, "forecast_solar", part)
        vrm_part_bias = _part_bias(second, "vrm", part)
        if part_bias is None or first_bias is None or vrm_part_bias is None:
            continue
        if part_bias < -0.05 and vrm_part_bias > part_bias and abs(part_bias - first_bias) < 0.08:
            current = current_parameters.get(parameter)
            recommended = current * (1.0 + part_bias) if current is not None else None
            if recommended is not None and abs(recommended - current) / max(abs(current), 0.001) >= 0.05:
                suggestions.append(
                    Recommendation(
                        parameter=parameter,
                        current=current,
                        recommended=round(recommended, 3),
                        expected_effect=f"reduce Forecast.Solar {part} prediction by {abs(part_bias) * 100:.1f}%",
                        forecast_solar_bias=part_bias,
                        vrm_bias=vrm_part_bias,
                        confidence="high" if len(valid_days) >= 30 else "medium",
                        evidence={"first_window_bias": first_bias, "second_window_bias": part_bias},
                    )
                )
    peak_errors = [
        abs((item.forecast_solar_peak_time - item.actual_peak_time).total_seconds() / 60.0)
        for item in valid_days
        if item.forecast_solar_peak_time is not None and item.actual_peak_time is not None
    ]
    vrm_peak_errors = [
        abs((item.vrm_peak_time - item.actual_peak_time).total_seconds() / 60.0)
        for item in valid_days
        if item.vrm_peak_time is not None and item.actual_peak_time is not None
    ]
    if peak_errors and vrm_peak_errors and median(peak_errors) - median(vrm_peak_errors) >= 30:
        suggestions.append(
            Recommendation(
                parameter="azimuth",
                current=current_parameters.get("azimuth"),
                recommended=None,
                expected_effect="review Forecast.Solar azimuth because its peak time is consistently shifted",
                forecast_solar_bias=forecast_solar_accuracy.bias,
                vrm_bias=vrm_accuracy.bias,
                confidence="medium" if len(valid_days) >= 20 else "low",
                evidence={"forecast_solar_peak_error_min": median(peak_errors), "vrm_peak_error_min": median(vrm_peak_errors)},
            )
        )
    return tuple(suggestions)


def compact_insight(
    *,
    generated_at: datetime,
    accuracy_30d: Mapping[str, AccuracyMetrics],
    accuracy_90d: Mapping[str, AccuracyMetrics],
    actual_valid_kwh: float | None,
    solar_expected_kwh: float | None,
    vrm_expected_kwh: float | None,
    consensus_expected_kwh: float | None,
    baseline: float | None,
    valid_coverage: float,
    limitations: Mapping[str, Any],
    anomalies: Mapping[str, Any],
    storm: Mapping[str, Any],
    recommendations: Sequence[Recommendation],
) -> dict[str, Any]:
    """Build the bounded structured result handed to Hermes/dashboard text."""

    def realization(actual: float | None, expected: float | None) -> float | None:
        return actual / expected if actual is not None and expected and expected > 0 else None

    solar30 = accuracy_30d.get("forecast_solar")
    vrm30 = accuracy_30d.get("vrm")
    solar90 = accuracy_90d.get("forecast_solar")
    vrm90 = accuracy_90d.get("vrm")
    if limitations.get("curtailment_detected"):
        status = "curtailed_normally"
    elif limitations.get("data_quality") not in {None, "good"}:
        status = "data_quality_problem"
    elif anomalies.get("persistent_underperformance"):
        status = "persistent_underperformance"
    elif anomalies.get("possible_gradual_decline"):
        status = "possible_gradual_decline"
    elif anomalies.get("step_change") or anomalies.get("near_zero_anomaly"):
        status = "possible_underperformance"
    elif not solar30 or not vrm30 or valid_coverage < 0.5:
        status = "insufficient_data"
    elif solar30.wape is not None and vrm30.wape is not None and abs(solar30.wape - vrm30.wape) < 0.03:
        status = "normal"
    else:
        status = "forecasts_disagree"
    best30 = None
    if solar30 and vrm30 and solar30.wape is not None and vrm30.wape is not None:
        best30 = "forecast_solar" if solar30.wape < vrm30.wape else "vrm"
    best90 = None
    if solar90 and vrm90 and solar90.wape is not None and vrm90.wape is not None:
        best90 = "forecast_solar" if solar90.wape < vrm90.wape else "vrm"
    return {
        "generated_at": generated_at.astimezone(UTC).isoformat(),
        "overall_status": status,
        "forecast_accuracy": {
            "forecast_solar_bias_30d": solar30.bias if solar30 else None,
            "forecast_solar_wape_30d": solar30.wape if solar30 else None,
            "vrm_bias_30d": vrm30.bias if vrm30 else None,
            "vrm_wape_30d": vrm30.wape if vrm30 else None,
            "best_provider_30d": best30,
            "best_provider_90d": best90,
            "forecast_solar_confidence_30d": solar30.confidence if solar30 else "insufficient",
            "vrm_confidence_30d": vrm30.confidence if vrm30 else "insufficient",
        },
        "expected_production": {
            "actual_valid_kwh": actual_valid_kwh,
            "forecast_solar_expected_kwh": solar_expected_kwh,
            "vrm_expected_kwh": vrm_expected_kwh,
            "forecast_solar_realization": realization(actual_valid_kwh, solar_expected_kwh),
            "vrm_realization": realization(actual_valid_kwh, vrm_expected_kwh),
            "consensus_realization": realization(actual_valid_kwh, consensus_expected_kwh),
            "historical_baseline": baseline,
            "valid_coverage": valid_coverage,
        },
        "limitations": dict(limitations),
        "events": dict(storm),
        "anomalies": {
            key: value
            for key, value in anomalies.items()
            if key in {"near_zero_anomaly", "persistent_underperformance", "step_change", "possible_gradual_decline", "time_of_day_pattern", "clipping_detected", "classification", "evidence"}
        },
        "recommendations": [item.as_dict() for item in recommendations[:3]],
    }
