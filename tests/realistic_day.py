"""One realistic Forecast.Solar day seeded on a real store.

The payload is the shape Forecast.Solar actually emits: an hourly cell for every
daylight hour, plus the single zero-Wh cell that spans the whole night and
straddles local midnight. Both DST transitions in Europe/Kyiv happen between
03:00 and 04:00 local, so a daylight window starting at 05:00 keeps every hourly
cell exactly one hour long on transition days.

Two test modules need this same day: the coverage gate drives it end to end, and
the interval catch-up replays it from rows an older build left behind.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from solar_analytics.native import normalize_native_wh_hours
from solar_analytics.storage_v2 import METRIC_VERSION, NORMALIZATION_VERSION, SolarAnalyticsV2Store

KYIV = ZoneInfo("Europe/Kyiv")
SUNRISE_HOUR = 5
SUNSET_HOUR = 20
DAYLIGHT_CELLS = SUNSET_HOUR - SUNRISE_HOUR
DAYLIGHT_WH = 900.0
ACTUAL_POWER_W = 3000.0

# Test-only fixture values; the shipping integration resolves these entity IDs
# at runtime from the user's config-flow selection.
POWER_ENTITY = "sensor.example_pv_power"
ENERGY_ENTITY = "sensor.example_pv_energy"


@dataclass(frozen=True)
class SeededDay:
    """A store holding one admissible morning snapshot and its actual power."""

    store: SolarAnalyticsV2Store
    lineage_id: str
    slot_id: int
    scheduled_at_utc: datetime
    day_start_utc: datetime
    day_end_utc: datetime


def native_payload(days: tuple[date, ...], tz: ZoneInfo) -> dict[str, dict[str, float]]:
    payload: dict[str, float] = {}
    for day in days:
        for hour in range(SUNRISE_HOUR + 1, SUNSET_HOUR + 1):
            end = datetime.combine(day, time(hour), tzinfo=tz).astimezone(UTC)
            payload[end.isoformat()] = DAYLIGHT_WH
        night_end = datetime.combine(
            day + timedelta(days=1), time(SUNRISE_HOUR), tzinfo=tz
        ).astimezone(UTC)
        payload[night_end.isoformat()] = 0.0
    return {"wh_hours": payload}


def daylight_windows(target: date, tz: ZoneInfo) -> list[tuple[datetime, datetime]]:
    """Return the UTC bounds of the daylight cells, which need no clipping."""

    windows = []
    for hour in range(SUNRISE_HOUR, SUNSET_HOUR):
        start = datetime.combine(target, time(hour), tzinfo=tz).astimezone(UTC)
        windows.append((start, start + timedelta(hours=1)))
    return windows


def seed_day(path: Path, target: date, tz: ZoneInfo) -> SeededDay:
    """Store the morning snapshot for ``target`` and sample actual power across it."""

    profile = normalize_native_wh_hours(native_payload((target - timedelta(days=1), target), tz))
    assert profile.status == "complete"

    day_start = datetime.combine(target, time.min, tzinfo=tz).astimezone(UTC)
    day_end = datetime.combine(target + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    scheduled = day_start + timedelta(hours=6)

    store = SolarAnalyticsV2Store(path)
    store.initialize()
    lineage_id = store.ensure_lineage(
        contract_key="realistic-day",
        metadata={
            "source_kind": "native",
            "native_entry_id": "entry",
            "model_fingerprint": "sha256:realistic-day",
            "model": {},
            "actual_energy_entity": ENERGY_ENTITY,
            "actual_power_entity": POWER_ENTITY,
            "adapter_version": "test",
            "native_contract_version": "test",
            "normalization_version": NORMALIZATION_VERSION,
            "metric_version": METRIC_VERSION,
        },
        now=scheduled,
    )
    slot_id, _ = store.ensure_snapshot_slot(
        lineage_id=lineage_id,
        source_kind="native",
        snapshot_type="morning",
        scheduled_at_utc=scheduled,
        target_local_date=target,
        timezone_name=str(tz),
        observed_at_utc=scheduled - timedelta(minutes=5),
        native_updated_at_utc=scheduled - timedelta(minutes=5),
        observation_sequence=1,
        payload_sha256=profile.payload_sha256,
        adapter_version="test",
        normalization_version=NORMALIZATION_VERSION,
        metric_version=METRIC_VERSION,
        status="admissible",
        admissible=True,
        exclusion_reason=None,
    )
    store.insert_snapshot_periods(slot_id, profile.as_storage_rows())

    sample = day_start - timedelta(hours=2)
    last_sample = day_end + timedelta(hours=2)
    while sample <= last_sample:
        local_hour = sample.astimezone(tz).hour
        daylight = SUNRISE_HOUR <= local_hour < SUNSET_HOUR
        store.add_power_sample(sample, ACTUAL_POWER_W if daylight else 0.0)
        sample += timedelta(minutes=5)

    return SeededDay(store, lineage_id, slot_id, scheduled, day_start, day_end)
