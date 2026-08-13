"""Reconstruction rules for imported daily actual production.

Every case here is pure: raw long-term-statistics rows in, per-local-day
totals out. Home Assistant is never imported, so these assertions cannot be
weakened by a stub.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from solar_analytics.imported_actuals import (
    DEFAULT_IMPORT_LOOKBACK_DAYS,
    build_imported_history,
    import_window_utc,
)

KYIV = ZoneInfo("Europe/Kyiv")
ENERGY_ENTITY = "sensor.example_pv_energy"


def _rows(*pairs: tuple[datetime, float | None]) -> list[dict[str, object]]:
    return [{"start": start.timestamp(), "sum": value} for start, value in pairs]


def _hourly(start: datetime, *sums: float) -> list[dict[str, object]]:
    return _rows(*((start + timedelta(hours=index), value) for index, value in enumerate(sums)))


def test_full_day_of_hourly_sums_becomes_one_complete_day() -> None:
    baseline = datetime(2026, 8, 2, 23, tzinfo=UTC)
    cumulative = [100.0 + index * 0.5 for index in range(25)]

    history = build_imported_history(
        _hourly(baseline, *cumulative), source_entity_id=ENERGY_ENTITY, tz=UTC
    )

    assert history.status == "imported"
    assert history.source_entity_id == ENERGY_ENTITY
    assert len(history.days) == 1
    day = history.days[0]
    assert day.local_date == date(2026, 8, 3)
    assert day.energy_kwh == 12.0
    assert day.observed_hours == 24
    assert day.expected_hours == 24
    assert day.coverage == 1.0
    assert day.counter_resets == 0
    assert history.total_kwh == 12.0


def test_counter_reset_is_recorded_instead_of_producing_a_negative_day() -> None:
    history = build_imported_history(
        _hourly(datetime(2026, 8, 3, tzinfo=UTC), 10.0, 11.0, 0.5, 1.5),
        source_entity_id=ENERGY_ENTITY,
        tz=UTC,
    )

    day = history.days[0]
    assert day.energy_kwh == 2.0
    assert day.observed_hours == 2
    assert day.counter_resets == 1
    assert day.coverage == 2 / 24


def test_partial_day_reports_its_real_coverage() -> None:
    history = build_imported_history(
        _hourly(datetime(2026, 8, 3, 9, tzinfo=UTC), 5.0, 6.0, 7.0),
        source_entity_id=ENERGY_ENTITY,
        tz=UTC,
    )

    day = history.days[0]
    assert day.energy_kwh == 2.0
    assert day.observed_hours == 2
    assert day.coverage == 2 / 24


def test_days_are_bucketed_in_the_configured_zone_not_utc() -> None:
    history = build_imported_history(
        _hourly(datetime(2026, 8, 2, 19, tzinfo=UTC), 0.0, 1.0, 2.0, 3.0),
        source_entity_id=ENERGY_ENTITY,
        tz=KYIV,
    )

    assert [day.local_date for day in history.days] == [date(2026, 8, 2), date(2026, 8, 3)]
    assert [day.observed_hours for day in history.days] == [1, 2]


def test_spring_forward_day_expects_23_hours() -> None:
    history = build_imported_history(
        _hourly(datetime(2026, 3, 28, 22, tzinfo=UTC), 0.0, 1.0),
        source_entity_id=ENERGY_ENTITY,
        tz=KYIV,
    )

    assert history.days[0].local_date == date(2026, 3, 29)
    assert history.days[0].expected_hours == 23


def test_fall_back_day_expects_25_hours() -> None:
    history = build_imported_history(
        _hourly(datetime(2026, 10, 24, 21, tzinfo=UTC), 0.0, 1.0),
        source_entity_id=ENERGY_ENTITY,
        tz=KYIV,
    )

    assert history.days[0].local_date == date(2026, 10, 25)
    assert history.days[0].expected_hours == 25


def test_unusable_rows_are_dropped_without_poisoning_the_day() -> None:
    rows = _hourly(datetime(2026, 8, 3, tzinfo=UTC), 10.0, 11.0, 12.0)
    rows[1]["sum"] = None
    rows.append({"start": None, "sum": 99.0})
    rows.append({"start": float("nan"), "sum": 5.0})
    rows.append({"sum": 7.0})

    history = build_imported_history(rows, source_entity_id=ENERGY_ENTITY, tz=UTC)

    assert history.days[0].energy_kwh == 2.0
    assert history.days[0].observed_hours == 1


def test_rows_out_of_order_and_with_datetime_starts_are_normalized() -> None:
    rows = [
        {"start": datetime(2026, 8, 3, 2, tzinfo=UTC), "sum": 12.0},
        {"start": datetime(2026, 8, 3, 0, tzinfo=UTC), "sum": 10.0},
        {"start": datetime(2026, 8, 3, 1, tzinfo=UTC), "sum": 11.0},
    ]

    history = build_imported_history(rows, source_entity_id=ENERGY_ENTITY, tz=UTC)

    assert history.days[0].energy_kwh == 2.0
    assert history.days[0].observed_hours == 2


def test_no_rows_is_reported_as_no_statistics_not_as_a_zero_day() -> None:
    history = build_imported_history([], source_entity_id=ENERGY_ENTITY, tz=UTC)

    assert history.status == "no_statistics"
    assert history.days == ()
    assert history.total_kwh == 0.0


def test_a_single_row_yields_no_day_because_it_has_no_baseline() -> None:
    history = build_imported_history(
        _hourly(datetime(2026, 8, 3, tzinfo=UTC), 10.0), source_entity_id=ENERGY_ENTITY, tz=UTC
    )

    assert history.status == "no_statistics"


def test_import_window_covers_complete_local_days_with_one_baseline_hour() -> None:
    start, end = import_window_utc(date(2026, 8, 3), tz=KYIV, lookback_days=2)

    assert end == datetime(2026, 8, 2, 21, tzinfo=UTC)
    assert start == datetime(2026, 7, 31, 20, tzinfo=UTC)


def test_import_window_measures_the_lookback_across_a_dst_change() -> None:
    start, end = import_window_utc(date(2026, 3, 30), tz=KYIV, lookback_days=2)

    assert end == datetime(2026, 3, 29, 21, tzinfo=UTC)
    assert start == datetime(2026, 3, 27, 21, tzinfo=UTC)
    assert (end - start) == timedelta(days=2, hours=1) - timedelta(hours=1)


def test_default_lookback_is_bounded() -> None:
    start, end = import_window_utc(date(2026, 8, 3), tz=UTC)

    assert (end - start).days == DEFAULT_IMPORT_LOOKBACK_DAYS
