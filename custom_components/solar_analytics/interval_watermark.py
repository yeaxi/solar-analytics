"""How far interval rebuilding has finished, and when a local day is finished.

A morning snapshot is immutable and live power sampling only bridges gaps up to
15 minutes, so once a local day is over its interval rows can never change.
This module owns the two rules that make that safe to rely on: when a day is
final, and whether a stored marker still describes the work this build would
produce. It is pure, so both rules are RED/GREEN testable without Home
Assistant; the coordinator owns reading and writing the marker.

The marker is deliberately one row under one fixed runtime key. Per-day rows or
a JSON certificate per day would grow with retention and add a second thing to
prune.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .native import local_day_bounds_utc

WATERMARK_RUNTIME_KEY = "interval_finalization_watermark"

# This names the interval semantics a finished day is built with, so change it
# only when a finished day would now produce different rows. The current value
# is the day-boundary clipping that counts the zero-Wh overnight cell for both
# adjacent days. A change that preserves every row, such as reading fewer
# accumulator buckets for the same window, must not change it. The constant is
# deliberately separate from the manifest version, METRIC_VERSION and
# NORMALIZATION_VERSION: those three are part of the lineage contract key, so
# bumping one of them mints a fresh lineage and throws away accuracy history.
INTERVAL_BUILD_REVISION = "clipped-day-boundary-v1"

# A local day counts as final one hour after its own local midnight has passed,
# which leaves room for a late actual-power sample to land in the last bucket.
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
    """Return the newest local date that is over, plus the finalization margin.

    The boundary comes from ``local_day_bounds_utc``, so the 23-hour and 25-hour
    DST days end at their real UTC instant rather than 24 hours after the
    previous midnight.
    """

    today = now_utc.astimezone(tz).date()
    day_start, _ = local_day_bounds_utc(today, tz)
    return today - timedelta(days=1 if now_utc >= day_start + margin else 2)


def read_watermark(
    value: Any, *, lineage_id: str, timezone: str, final_through: date
) -> date | None:
    """Return the date a stored marker can be trusted through, or ``None``.

    ``None`` means "treat this as no marker at all and rebuild every retained
    day". A marker is refused when it is missing or malformed, when it was
    written by another build revision, lineage or timezone, or when it claims a
    day that is not final yet. Refusing costs one catch-up pass; trusting a
    stale marker leaves wrong rows on disk forever.
    """

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
