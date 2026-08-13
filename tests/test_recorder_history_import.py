"""Behaviour of the Recorder read boundary and the coordinator's import step.

Home Assistant is not installed here, so the Recorder is a recording stub.
That makes these assertions about *what is asked for* rather than about live
Recorder behaviour: the stub proves the integration requests exactly one
statistic id through the read API and touches no write API, and it cannot
prove the real Recorder returns what we expect.
"""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from solar_analytics.imported_actuals import ImportedActualHistory, build_imported_history
from solar_analytics.storage_v2 import SolarAnalyticsV2Store

ENERGY_ENTITY = "sensor.example_pv_energy"
OTHER_ENTITY = "sensor.other_pv_energy"
KYIV = ZoneInfo("Europe/Kyiv")

_MUTATING_RECORDER_APIS = (
    "async_import_statistics",
    "async_add_external_statistics",
    "async_adjust_statistics",
    "async_clear_statistics",
)


class _RecordingModule:
    """A stand-in module that fails the test on any unexpected attribute access."""

    def __init__(self, **members) -> None:
        self._members = members
        self.accessed: list[str] = []

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        self.accessed.append(name)
        if name not in self._members:
            raise AssertionError(f"unexpected recorder attribute access: {name}")
        return self._members[name]


class _RecorderInstance:
    def __init__(self) -> None:
        self.jobs: list[object] = []

    async def async_add_executor_job(self, target, *args):
        self.jobs.append(target)
        return target(*args)


@pytest.fixture
def recorder_stub(monkeypatch):
    calls: list[dict] = []
    result: dict[str, list[dict]] = {}

    def _statistics_during_period(hass, start, end, statistic_ids, period, units, types_):
        calls.append(
            {
                "start": start,
                "end": end,
                "statistic_ids": statistic_ids,
                "period": period,
                "units": units,
                "types": types_,
            }
        )
        return result

    instance = _RecorderInstance()
    statistics = _RecordingModule(statistics_during_period=_statistics_during_period)
    recorder = _RecordingModule(get_instance=lambda hass: instance)
    monkeypatch.setitem(sys.modules, "homeassistant.components.recorder", recorder)
    monkeypatch.setitem(sys.modules, "homeassistant.components.recorder.statistics", statistics)
    return types.SimpleNamespace(
        calls=calls, result=result, recorder=recorder, statistics=statistics, instance=instance
    )


def test_import_requests_only_the_configured_entity(recorder_history_module, recorder_stub) -> None:
    recorder_stub.result[ENERGY_ENTITY] = [{"start": 0.0, "sum": 1.0}]

    rows = asyncio.run(
        recorder_history_module.async_hourly_energy_statistics(
            object(),
            statistic_id=ENERGY_ENTITY,
            start_utc=datetime(2026, 8, 1, tzinfo=UTC),
            end_utc=datetime(2026, 8, 3, tzinfo=UTC),
        )
    )

    assert rows == [{"start": 0.0, "sum": 1.0}]
    assert len(recorder_stub.calls) == 1
    call = recorder_stub.calls[0]
    assert call["statistic_ids"] == {ENERGY_ENTITY}
    assert call["period"] == "hour"
    assert call["units"] == {"energy": "kWh"}
    assert call["types"] == {"sum"}


def test_import_calls_no_mutating_recorder_api(recorder_history_module, recorder_stub) -> None:
    asyncio.run(
        recorder_history_module.async_hourly_energy_statistics(
            object(),
            statistic_id=ENERGY_ENTITY,
            start_utc=datetime(2026, 8, 1, tzinfo=UTC),
            end_utc=datetime(2026, 8, 3, tzinfo=UTC),
        )
    )

    assert recorder_stub.statistics.accessed == ["statistics_during_period"]
    assert recorder_stub.recorder.accessed == ["get_instance"]
    for forbidden in _MUTATING_RECORDER_APIS:
        assert forbidden not in recorder_stub.statistics.accessed
        assert forbidden not in recorder_stub.recorder.accessed


def test_import_runs_on_the_recorder_executor(recorder_history_module, recorder_stub) -> None:
    asyncio.run(
        recorder_history_module.async_hourly_energy_statistics(
            object(),
            statistic_id=ENERGY_ENTITY,
            start_utc=datetime(2026, 8, 1, tzinfo=UTC),
            end_utc=datetime(2026, 8, 3, tzinfo=UTC),
        )
    )

    assert len(recorder_stub.instance.jobs) == 1


def test_unreadable_recorder_is_reported_as_none_not_as_empty(
    recorder_history_module, monkeypatch
) -> None:
    def _boom(hass):
        raise RuntimeError("recorder not running")

    monkeypatch.setitem(
        sys.modules, "homeassistant.components.recorder", _RecordingModule(get_instance=_boom)
    )
    monkeypatch.setitem(
        sys.modules, "homeassistant.components.recorder.statistics", _RecordingModule()
    )

    rows = asyncio.run(
        recorder_history_module.async_hourly_energy_statistics(
            object(),
            statistic_id=ENERGY_ENTITY,
            start_utc=datetime(2026, 8, 1, tzinfo=UTC),
            end_utc=datetime(2026, 8, 3, tzinfo=UTC),
        )
    )

    assert rows is None


def test_missing_entity_in_the_result_is_an_empty_read(
    recorder_history_module, recorder_stub
) -> None:
    recorder_stub.result[OTHER_ENTITY] = [{"start": 0.0, "sum": 1.0}]

    rows = asyncio.run(
        recorder_history_module.async_hourly_energy_statistics(
            object(),
            statistic_id=ENERGY_ENTITY,
            start_utc=datetime(2026, 8, 1, tzinfo=UTC),
            end_utc=datetime(2026, 8, 3, tzinfo=UTC),
        )
    )

    assert rows == []


def _shell(coordinator_module, store):
    return types.SimpleNamespace(store=store, time_zone=KYIV)


def _history(days: int) -> ImportedActualHistory:
    baseline = datetime(2026, 7, 31, 21, tzinfo=UTC)
    rows = [
        {"start": (baseline + timedelta(hours=hour)).timestamp(), "sum": float(hour)}
        for hour in range(24 * days + 1)
    ]
    return build_imported_history(rows, source_entity_id=ENERGY_ENTITY, tz=KYIV)


def test_import_is_idempotent_across_repeated_runs(coordinator_module, tmp_path) -> None:
    store = SolarAnalyticsV2Store(tmp_path / "import.sqlite")
    store.initialize()
    shell = _shell(coordinator_module, store)
    today = date(2026, 8, 3)
    now = datetime(2026, 8, 3, 5, tzinfo=UTC)
    history = _history(2)

    first = coordinator_module.SolarAnalyticsCoordinator._store_imported_history_sync(
        shell, history, ENERGY_ENTITY, today, now
    )
    second = coordinator_module.SolarAnalyticsCoordinator._store_imported_history_sync(
        shell, history, ENERGY_ENTITY, today, now + timedelta(hours=1)
    )

    assert first["status"] == "imported"
    assert first["day_count"] == second["day_count"]
    assert first["total_kwh"] == second["total_kwh"]
    assert first["points"] == second["points"]
    assert (
        len(store.list_imported_actual_daily(source_entity_id=ENERGY_ENTITY)) == first["day_count"]
    )
    store.close()


def test_stored_run_is_not_repeated_on_the_same_local_day(coordinator_module, tmp_path) -> None:
    store = SolarAnalyticsV2Store(tmp_path / "watermark.sqlite")
    store.initialize()
    shell = _shell(coordinator_module, store)
    today = date(2026, 8, 3)
    now = datetime(2026, 8, 3, 5, tzinfo=UTC)
    imported = coordinator_module.SolarAnalyticsCoordinator._store_imported_history_sync(
        shell, _history(2), ENERGY_ENTITY, today, now
    )

    class _Hass:
        @staticmethod
        async def async_add_executor_job(target, *args):
            return target(*args)

    shell.hass = _Hass()

    same_day = asyncio.run(
        coordinator_module.SolarAnalyticsCoordinator._async_read_actual_history(
            shell, ENERGY_ENTITY, today, now
        )
    )
    assert same_day is None

    replayed = coordinator_module.SolarAnalyticsCoordinator._store_imported_history_sync(
        shell, None, ENERGY_ENTITY, today, now
    )
    assert replayed["status"] == "imported"
    assert replayed["day_count"] == imported["day_count"]
    assert replayed["points"] == imported["points"]
    store.close()


def test_recorder_unavailable_keeps_the_entry_alive_with_a_status(
    coordinator_module, tmp_path
) -> None:
    store = SolarAnalyticsV2Store(tmp_path / "unavailable.sqlite")
    store.initialize()
    shell = _shell(coordinator_module, store)

    block = coordinator_module.SolarAnalyticsCoordinator._store_imported_history_sync(
        shell,
        ImportedActualHistory("recorder_unavailable", ENERGY_ENTITY),
        ENERGY_ENTITY,
        date(2026, 8, 3),
        datetime(2026, 8, 3, 5, tzinfo=UTC),
    )

    assert block["status"] == "recorder_unavailable"
    assert block["day_count"] == 0
    assert block["points"] == []
    store.close()
