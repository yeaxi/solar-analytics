"""Daily actual PV production reconstructed from long-term Recorder statistics.

This module owns one body of domain knowledge: what a day of imported actual
production is, and how to derive it from the hourly cumulative energy sums
Home Assistant keeps forever in ``statistics``. It is pure and has no Home
Assistant dependency, so the reconstruction rules are RED/GREEN testable
locally; the Home Assistant read boundary lives in :mod:`recorder_history`.

Imported days are *actuals only*. There is no recorded historical forecast
profile to pair them against, so they never reach ``v2_daily_comparisons``,
``valid_paired_day``, the rolling accuracy window, or WAPE.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from math import isfinite
from typing import Any, Literal
from zoneinfo import ZoneInfo

from .native import local_day_bounds_utc

IMPORT_PROVENANCE = "reconstructed_from_recorder_statistics"
DEFAULT_IMPORT_LOOKBACK_DAYS = 365

ImportStatus = Literal[
    "uninitialized",
    "imported",
    "no_statistics",
    "no_actual_energy_entity",
    "recorder_unavailable",
    "import_failed",
]


@dataclass(frozen=True)
class HourlySum:
    """One long-term statistics hour and the counter's cumulative kWh at its end."""

    start_utc: datetime
    cumulative_kwh: float


@dataclass(frozen=True)
class ImportedActualDay:
    """One local day of reconstructed actual production."""

    local_date: date
    energy_kwh: float
    observed_hours: int
    expected_hours: int
    counter_resets: int

    @property
    def coverage(self) -> float:
        if self.expected_hours <= 0:
            return 0.0
        return min(self.observed_hours / self.expected_hours, 1.0)


@dataclass(frozen=True)
class ImportedActualHistory:
    """The outcome of one import run for one actual PV energy entity."""

    status: ImportStatus
    source_entity_id: str | None = None
    days: tuple[ImportedActualDay, ...] = ()

    @property
    def total_kwh(self) -> float:
        return sum(day.energy_kwh for day in self.days)

    def as_storage_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "local_date": day.local_date.isoformat(),
                "energy_kwh": day.energy_kwh,
                "coverage": day.coverage,
                "observed_hours": day.observed_hours,
                "expected_hours": day.expected_hours,
                "counter_resets": day.counter_resets,
            }
            for day in self.days
        ]


def import_window_utc(
    today_local: date, *, tz: ZoneInfo, lookback_days: int = DEFAULT_IMPORT_LOOKBACK_DAYS
) -> tuple[datetime, datetime]:
    """Return the UTC statistics window covering ``lookback_days`` complete local days.

    The window opens one hour early so the first imported day has a preceding
    cumulative reading to subtract from, and closes at local midnight so a
    partially observed today is never presented as a full day.
    """

    window_end, _ = local_day_bounds_utc(today_local, tz)
    window_start, _ = local_day_bounds_utc(today_local - timedelta(days=max(lookback_days, 0)), tz)
    return window_start - timedelta(hours=1), window_end


def build_imported_history(
    rows: Iterable[Mapping[str, Any]], *, source_entity_id: str, tz: ZoneInfo
) -> ImportedActualHistory:
    """Turn raw ``statistics_during_period`` hourly rows into per-local-day totals."""

    days = _days_from_hourly_sums(_parse_hourly_sums(rows), tz=tz)
    return ImportedActualHistory(
        status="imported" if days else "no_statistics",
        source_entity_id=source_entity_id,
        days=days,
    )


def _parse_hourly_sums(rows: Iterable[Mapping[str, Any]]) -> list[HourlySum]:
    parsed: list[HourlySum] = []
    for row in rows:
        start_utc = _as_utc(row.get("start"))
        cumulative = _as_finite_float(row.get("sum"))
        if start_utc is None or cumulative is None:
            continue
        parsed.append(HourlySum(start_utc, cumulative))
    parsed.sort(key=lambda item: item.start_utc)
    return parsed


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not isfinite(float(value)):
        return None
    return datetime.fromtimestamp(float(value), UTC)


def _as_finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if isfinite(number) else None


def _days_from_hourly_sums(sums: list[HourlySum], *, tz: ZoneInfo) -> tuple[ImportedActualDay, ...]:
    energy: dict[date, float] = {}
    observed: dict[date, int] = {}
    resets: dict[date, int] = {}
    previous: HourlySum | None = None
    for current in sums:
        if previous is None:
            previous = current
            continue
        # Statistics hours are bucketed on the UTC clock, so a zone offset by a
        # fraction of an hour has one bucket straddling local midnight. It is
        # counted in the day its hour starts in rather than split, because
        # splitting would apportion energy that was never measured per-minute.
        local_day = current.start_utc.astimezone(tz).date()
        delta = current.cumulative_kwh - previous.cumulative_kwh
        previous = current
        if delta < 0:
            resets[local_day] = resets.get(local_day, 0) + 1
            continue
        energy[local_day] = energy.get(local_day, 0.0) + delta
        observed[local_day] = observed.get(local_day, 0) + 1
    return tuple(
        ImportedActualDay(
            local_date=local_day,
            energy_kwh=energy.get(local_day, 0.0),
            observed_hours=observed.get(local_day, 0),
            expected_hours=_hours_in_local_day(local_day, tz),
            counter_resets=resets.get(local_day, 0),
        )
        for local_day in sorted(energy.keys() | resets.keys())
    )


def _hours_in_local_day(local_day: date, tz: ZoneInfo) -> int:
    day_start, day_end = local_day_bounds_utc(local_day, tz)
    return round((day_end - day_start).total_seconds() / 3600.0)
