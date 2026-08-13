"""When a local day is final, and whether a stored finalization marker is usable."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .native import local_day_bounds_utc

WATERMARK_RUNTIME_KEY = "interval_finalization_watermark"

INTERVAL_BUILD_REVISION = "clipped-day-boundary-v1"
FINALIZATION_MARGIN = timedelta(hours=1)


@dataclass(frozen=True)
class FinalizationWatermark:
    """The newest local date whose interval rows are built and final."""

    revision: str
    lineage_id: str
    timezone: str
    finalized_through: date

    def as_runtime_value(self) -> dict[str, str]:
        return {
            "revision": self.revision,
            "lineage_id": self.lineage_id,
            "timezone": self.timezone,
            "finalized_through": self.finalized_through.isoformat(),
        }


def last_final_local_date(
    now_utc: datetime, *, tz: ZoneInfo, margin: timedelta = FINALIZATION_MARGIN
) -> date:
    """Return the newest local date whose end, plus ``margin``, is in the past."""

    today = now_utc.astimezone(tz).date()
    day_start, _ = local_day_bounds_utc(today, tz)
    return today - timedelta(days=1 if now_utc >= day_start + margin else 2)


def read_watermark(
    value: Any, *, lineage_id: str, timezone: str, final_through: date
) -> date | None:
    """Return the date this marker covers, or ``None`` when it does not apply here."""

    if not isinstance(value, Mapping):
        return None
    if str(value.get("revision")) != INTERVAL_BUILD_REVISION:
        return None
    if str(value.get("lineage_id")) != lineage_id:
        return None
    if str(value.get("timezone")) != timezone:
        return None
    try:
        finalized_through = date.fromisoformat(str(value.get("finalized_through")))
    except ValueError:
        return None
    return finalized_through if finalized_through <= final_through else None
