"""The two rules behind the interval finalization marker.

``last_final_local_date`` decides when a local day stops changing, and
``read_watermark`` decides whether a stored marker still describes the rows this
build produces. Both are pure, so they are tested here without a store.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from solar_analytics.interval_watermark import (
    INTERVAL_BUILD_REVISION,
    FinalizationWatermark,
    last_final_local_date,
    read_watermark,
)

KYIV = ZoneInfo("Europe/Kyiv")
LINEAGE = "lineage-1"


def _local(moment: datetime) -> datetime:
    return moment.astimezone(UTC)


def _marker(**overrides: Any) -> dict[str, str]:
    base = FinalizationWatermark(
        revision=INTERVAL_BUILD_REVISION,
        lineage_id=LINEAGE,
        timezone=str(KYIV),
        finalized_through=date(2026, 8, 1),
    ).as_runtime_value()
    base.update(overrides)
    return base


def test_yesterday_becomes_final_one_hour_after_local_midnight() -> None:
    midnight = datetime(2026, 8, 3, tzinfo=KYIV)

    assert last_final_local_date(_local(midnight), tz=KYIV) == date(2026, 8, 1)
    assert last_final_local_date(_local(midnight + timedelta(minutes=59)), tz=KYIV) == date(
        2026, 8, 1
    )
    assert last_final_local_date(_local(midnight + timedelta(hours=1)), tz=KYIV) == date(2026, 8, 2)
    assert last_final_local_date(_local(midnight + timedelta(hours=12)), tz=KYIV) == date(
        2026, 8, 2
    )


@pytest.mark.parametrize(
    ("day_after", "finished_day"),
    [(date(2026, 3, 30), date(2026, 3, 29)), (date(2026, 10, 26), date(2026, 10, 25))],
)
def test_dst_days_end_at_their_own_real_boundary(day_after: date, finished_day: date) -> None:
    """The 23-hour and 25-hour days must not be measured 24 hours from midnight."""

    midnight = datetime.combine(day_after, datetime.min.time(), tzinfo=KYIV)
    margin_passed = _local(midnight + timedelta(hours=1))

    assert last_final_local_date(margin_passed, tz=KYIV) == finished_day
    assert last_final_local_date(
        margin_passed - timedelta(minutes=1), tz=KYIV
    ) == finished_day - timedelta(days=1)
    assert margin_passed - midnight.astimezone(UTC) == timedelta(hours=1)


def test_a_marker_this_build_wrote_is_read_back_through_json() -> None:
    stored = json.loads(json.dumps(_marker()))

    assert read_watermark(
        stored, lineage_id=LINEAGE, timezone=str(KYIV), final_through=date(2026, 8, 2)
    ) == date(2026, 8, 1)


@pytest.mark.parametrize(
    "stored",
    [
        None,
        "interval_finalization_watermark",
        42,
        [],
        _marker(revision="some-older-build"),
        _marker(lineage_id="lineage-2"),
        _marker(timezone="Europe/Warsaw"),
        _marker(finalized_through="not-a-date"),
        _marker(finalized_through=""),
        _marker(finalized_through="2026-08-03"),
    ],
)
def test_a_marker_that_cannot_be_trusted_reads_as_absent(stored: Any) -> None:
    assert (
        read_watermark(
            stored, lineage_id=LINEAGE, timezone=str(KYIV), final_through=date(2026, 8, 2)
        )
        is None
    )


def test_a_marker_at_the_finality_boundary_is_still_trusted() -> None:
    stored = _marker(finalized_through="2026-08-02")

    assert read_watermark(
        stored, lineage_id=LINEAGE, timezone=str(KYIV), final_through=date(2026, 8, 2)
    ) == date(2026, 8, 2)
