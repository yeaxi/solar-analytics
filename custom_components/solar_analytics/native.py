"""Pure validation and normalization for the pinned native Forecast.Solar profile.

The Home Assistant adapter lives in the custom component.  This module deliberately
has no Home Assistant dependency so the contract can be RED/GREEN tested locally.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

from .const import VERSION as _INTEGRATION_VERSION

# Convenient default timezone for pure-function tests only. Runtime callers
# always pass the user-configured ZoneInfo explicitly.
KYIV = ZoneInfo("Europe/Kyiv")
NATIVE_CONTRACT_VERSION = "ha_forecast_solar_energy_2026.7"
NATIVE_ADAPTER_VERSION = _INTEGRATION_VERSION
MAX_NATIVE_PERIOD_SECONDS = 2 * 60 * 60


@dataclass(frozen=True)
class NativePeriod:
    """One native period-end energy cell."""

    start_utc: datetime | None
    end_utc: datetime
    energy_wh: float | None
    valid: bool
    exclusion_reason: str | None = None

    @property
    def duration_seconds(self) -> float | None:
        if self.start_utc is None:
            return None
        return (self.end_utc - self.start_utc).total_seconds()

    @property
    def power_w(self) -> float | None:
        duration = self.duration_seconds
        if self.energy_wh is None or duration is None or duration <= 0:
            return None
        return self.energy_wh * 3600.0 / duration


@dataclass(frozen=True)
class NativeProfile:
    """Validated full native horizon, including quarantined boundary cells."""

    periods: tuple[NativePeriod, ...]
    raw_count: int
    payload_sha256: str | None
    status: str
    invalid_count: int
    duplicate_count: int = 0

    @property
    def valid_periods(self) -> tuple[NativePeriod, ...]:
        return tuple(period for period in self.periods if period.valid)

    @property
    def valid_energy_wh(self) -> float:
        return sum(period.energy_wh or 0.0 for period in self.valid_periods)

    def as_storage_rows(self) -> list[dict[str, Any]]:
        """Return all cells with UTC timestamps and explicit exclusion metadata."""

        return [
            {
                "interval_start_utc": period.start_utc.isoformat() if period.start_utc else None,
                "interval_end_utc": period.end_utc.isoformat(),
                "energy_wh": period.energy_wh,
                "duration_seconds": period.duration_seconds,
                "valid": period.valid,
                "exclusion_reason": period.exclusion_reason,
            }
            for period in self.periods
        ]


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _finite_non_negative(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    if not isfinite(number) or number < 0:
        return None
    return number


def payload_sha256(payload: Any) -> str | None:
    """Hash only canonical JSON; never include credentials or runtime objects."""

    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except TypeError, ValueError:
        return None
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def build_native_model_fingerprint(contract: Mapping[str, Any]) -> str | None:
    """Fingerprint model-shaping values without reconstructing provider URLs."""

    if contract.get("status") != "ok":
        return None
    required = (
        "latitude",
        "longitude",
        "declination",
        "azimuth",
        "modules_power_w",
        "inverter_size_w",
        "morning_damping",
        "evening_damping",
    )
    canonical: dict[str, Any] = {"schema": 1}
    for key in required:
        value = contract.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
        ):
            return None
        canonical[key] = float(value)
    canonical["plane_id"] = str(contract.get("plane_id") or "")
    canonical["auth_mode"] = str(contract.get("auth_mode") or "public")
    if canonical["inverter_size_w"] <= 0 or canonical["modules_power_w"] <= 0:
        return None
    if not 0 <= canonical["morning_damping"] <= 1 or not 0 <= canonical["evening_damping"] <= 1:
        return None
    digest = payload_sha256(canonical)
    return digest


def _canonical_wh_hours(raw: Mapping[str, Any]) -> dict[str, float] | None:
    canonical: dict[str, float] = {}
    for key, value in raw.items():
        timestamp = _parse_utc(key)
        number = _finite_non_negative(value)
        if timestamp is None or number is None:
            return None
        # The native helper removes exactly a zero point at midnight. Mirror that
        # contract without reconstructing the point in Solar Analytics.
        if number == 0.0 and (timestamp.hour, timestamp.minute, timestamp.second) == (0, 0, 0):
            continue
        canonical[timestamp.isoformat()] = number
    return canonical


def normalize_native_wh_hours(
    payload: Mapping[str, Any],
    *,
    max_period_seconds: int = MAX_NATIVE_PERIOD_SECONDS,
) -> NativeProfile:
    """Validate ``{"wh_hours": {ISO period_end: Wh}}`` fail-closed.

    Values remain energy per native period.  No instantaneous power curve is
    fabricated; power is derived only when a valid period duration is known.
    """

    if not isinstance(payload, Mapping) or not isinstance(payload.get("wh_hours"), Mapping):
        return NativeProfile((), 0, payload_sha256(payload), "blocked", 1)
    raw = payload["wh_hours"]
    if not raw:
        return NativeProfile((), 0, payload_sha256(payload), "blocked", 1)

    parsed: list[tuple[datetime, Any]] = []
    invalid = 0
    for key, value in raw.items():
        timestamp = _parse_utc(key)
        if timestamp is None:
            invalid += 1
            continue
        parsed.append((timestamp, value))
    parsed.sort(key=lambda item: item[0])

    periods: list[NativePeriod] = []
    seen: set[datetime] = set()
    durations: list[float] = []
    for index, (end_utc, raw_value) in enumerate(parsed):
        if end_utc in seen:
            invalid += 1
            periods.append(NativePeriod(None, end_utc, None, False, "duplicate_timestamp"))
            continue
        seen.add(end_utc)
        energy_wh = _finite_non_negative(raw_value)
        if index == 0:
            reason = "missing_previous_boundary"
            periods.append(NativePeriod(None, end_utc, energy_wh, False, reason))
            if energy_wh is None:
                invalid += 1
            continue
        start_utc = parsed[index - 1][0]
        duration = (end_utc - start_utc).total_seconds()
        durations.append(duration)
        if duration <= 0:
            invalid += 1
            periods.append(
                NativePeriod(
                    start_utc, end_utc, energy_wh, False, "overlap_or_non_positive_duration"
                )
            )
        # Forecast.Solar's native ``wh_period`` is sparse overnight. Core keeps
        # the non-midnight zero boundary, so a long zero-Wh cell is an explicit
        # native zero-energy period rather than evidence of a missing cell.
        elif duration > max_period_seconds and energy_wh != 0.0:
            invalid += 1
            periods.append(
                NativePeriod(
                    start_utc, end_utc, energy_wh, False, "internal_gap_or_period_too_long"
                )
            )
        elif energy_wh is None:
            invalid += 1
            periods.append(
                NativePeriod(start_utc, end_utc, None, False, "non_numeric_or_negative_energy")
            )
        else:
            periods.append(NativePeriod(start_utc, end_utc, energy_wh, True))

    # A first boundary is intentionally excluded, but all subsequent cells must be
    # valid for a complete native profile. A malformed timestamp/value blocks it.
    status = (
        "complete"
        if periods and any(item.valid for item in periods) and invalid == 0
        else "blocked"
    )
    return NativeProfile(
        tuple(periods),
        len(raw),
        payload_sha256(payload),
        status,
        invalid,
        duplicate_count=max(0, len(parsed) - len(seen)),
    )


def parse_native_profile(payload: Mapping[str, Any]) -> NativeProfile:
    """Compatibility alias used by the adapter and tests."""

    return normalize_native_wh_hours(payload)


def periods_for_local_date(
    profile: NativeProfile,
    local_date: date,
    *,
    tz: ZoneInfo = KYIV,
) -> tuple[NativePeriod, ...]:
    """Select whole UTC periods contained in one local day.

    A period crossing a local-day boundary is excluded instead of assigning its
    complete energy to one side. This prevents midnight/DST denominator leakage.
    """

    day_start = datetime.combine(local_date, time.min, tzinfo=tz).astimezone(UTC)
    day_end = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    return tuple(
        period
        for period in profile.valid_periods
        if period.start_utc is not None
        and period.start_utc >= day_start
        and period.end_utc <= day_end
    )


def period_coverage_seconds(period: NativePeriod, coverage_seconds: float) -> float:
    """Clamp actual coverage to a valid native period duration."""

    duration = period.duration_seconds or 0.0
    return min(max(float(coverage_seconds), 0.0), duration)
