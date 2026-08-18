"""Pure-function coverage for coordinator helpers.

The coordinator module is imported without instantiating any Home Assistant
runtime; only the module-level helpers are exercised. This test guards the
timezone-correct scheduler that replaced the buggy
``async_track_time_change(..., hour=6)`` pattern.
"""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def _read(binding_status="ok", binding_reason=None, status="ok", reason=None):
    """Build a NativeRead-shaped object for repair/log helper coverage."""

    binding = types.SimpleNamespace(status=binding_status, reason=binding_reason)
    return types.SimpleNamespace(binding=binding, status=status, reason=reason)


def test_next_local_hour_advances_to_today_when_hour_is_still_ahead(coordinator_module) -> None:
    tz = ZoneInfo("America/Los_Angeles")
    # 2026-08-11 03:30 in Los Angeles == 2026-08-11 10:30 UTC (PDT, UTC-7).
    now_utc = datetime(2026, 8, 11, 10, 30, tzinfo=UTC)

    result = coordinator_module._next_local_hour_utc(now_utc, tz, 6)

    # 2026-08-11 06:00 LA == 2026-08-11 13:00 UTC.
    assert result == datetime(2026, 8, 11, 13, 0, tzinfo=UTC)


def test_next_local_hour_advances_to_tomorrow_when_hour_is_already_past(coordinator_module) -> None:
    tz = ZoneInfo("America/Los_Angeles")
    # 2026-08-11 07:30 LA == 2026-08-11 14:30 UTC.
    now_utc = datetime(2026, 8, 11, 14, 30, tzinfo=UTC)

    result = coordinator_module._next_local_hour_utc(now_utc, tz, 6)

    # 2026-08-12 06:00 LA == 2026-08-12 13:00 UTC.
    assert result == datetime(2026, 8, 12, 13, 0, tzinfo=UTC)


def test_next_local_hour_respects_configured_timezone_not_utc(coordinator_module) -> None:
    """Scheduling must fire at the configured-TZ hour regardless of HA's own TZ."""

    kyiv = ZoneInfo("Europe/Kyiv")
    la = ZoneInfo("America/Los_Angeles")
    now_utc = datetime(2026, 8, 11, 0, 30, tzinfo=UTC)

    kyiv_next = coordinator_module._next_local_hour_utc(now_utc, kyiv, 6)
    la_next = coordinator_module._next_local_hour_utc(now_utc, la, 6)

    # 06:00 Kyiv (UTC+3, no DST in this period) == 03:00 UTC.
    assert kyiv_next.astimezone(kyiv).hour == 6
    # 06:00 Los Angeles (PDT, UTC-7) == 13:00 UTC.
    assert la_next.astimezone(la).hour == 6
    # Two different local hours therefore fire at two different UTC instants.
    assert kyiv_next != la_next


def test_next_local_hour_survives_dst_spring_forward(coordinator_module) -> None:
    """Scheduling for 06:00 local on a DST transition day still returns 06:00 local."""

    kyiv = ZoneInfo("Europe/Kyiv")
    # Kyiv 2026 spring-forward is Sunday 2026-03-29 03:00 local -> 04:00 local.
    # Ask for the next 06:00 local from Saturday evening.
    now_utc = datetime(2026, 3, 28, 20, 0, tzinfo=UTC)
    result = coordinator_module._next_local_hour_utc(now_utc, kyiv, 6)

    result_local = result.astimezone(kyiv)
    assert result_local.hour == 6
    assert result_local.date().isoformat() == "2026-03-29"


def test_default_time_zone_prefers_hass_config(coordinator_module) -> None:
    class _Hass:
        class config:  # type: ignore[no-redef]
            time_zone = "Europe/Berlin"

    assert coordinator_module._default_time_zone(_Hass()) == "Europe/Berlin"


def test_default_time_zone_falls_back_to_utc(coordinator_module) -> None:
    class _Hass:
        class config:  # type: ignore[no-redef]
            time_zone = None

    assert coordinator_module._default_time_zone(_Hass()) == "UTC"


def test_default_time_zone_falls_back_when_hass_has_no_config_attr(coordinator_module) -> None:
    class _Hass:
        pass

    assert coordinator_module._default_time_zone(_Hass()) == "UTC"


def test_maintain_repair_issues_creates_active_and_clears_the_rest(coordinator_module) -> None:
    """The coordinator raises exactly one issue at a time and clears the others."""

    created: list[tuple[str, dict]] = []
    deleted: list[str] = []

    def _create(hass, domain, issue_id, **kwargs):
        created.append((issue_id, kwargs))

    def _delete(hass, domain, issue_id):
        deleted.append(issue_id)

    ir = sys.modules["homeassistant.helpers.issue_registry"]
    ir.async_create_issue = _create
    ir.async_delete_issue = _delete

    shell = types.SimpleNamespace(hass=object())
    coordinator_module.SolarAnalyticsCoordinator._maintain_repair_issues(
        shell, _read(binding_status="canonical_actual_mismatch", binding_reason="power_missing")
    )

    assert len(created) == 1
    issue_id, kwargs = created[0]
    assert issue_id == "canonical_actual_mismatch"
    assert kwargs["is_fixable"] is True
    assert kwargs["translation_key"] == "canonical_actual_mismatch"
    # Every other managed issue is proactively cleared, not left dangling.
    assert set(deleted) >= {
        "binding_unavailable",
        "binding_ambiguous",
        "binding_changed",
        "native_entry_unavailable",
        "unsupported_native_contract",
        "unsupported_forecast_entity_contract",
    }


def test_maintain_repair_issues_uses_read_status_for_entity_contract(coordinator_module) -> None:
    """A resolved binding whose capture fails the entity contract raises its issue."""

    created: list[tuple[str, dict]] = []
    deleted: list[str] = []
    ir = sys.modules["homeassistant.helpers.issue_registry"]
    ir.async_create_issue = lambda hass, domain, issue_id, **kwargs: created.append(
        (issue_id, kwargs)
    )
    ir.async_delete_issue = lambda hass, domain, issue_id: deleted.append(issue_id)

    shell = types.SimpleNamespace(hass=object())
    coordinator_module.SolarAnalyticsCoordinator._maintain_repair_issues(
        shell,
        _read(status="unsupported_forecast_entity_contract", reason="non_wh_unit:kWh"),
    )

    assert len(created) == 1
    issue_id, kwargs = created[0]
    assert issue_id == "unsupported_forecast_entity_contract"
    assert kwargs["is_fixable"] is False
    assert kwargs["translation_placeholders"]["reason"] == "non_wh_unit:kWh"
    assert "unsupported_forecast_entity_contract" not in deleted


def test_maintain_repair_issues_marks_informational_issues_non_fixable(coordinator_module) -> None:
    created: list[tuple[str, dict]] = []
    ir = sys.modules["homeassistant.helpers.issue_registry"]
    ir.async_create_issue = lambda hass, domain, issue_id, **kwargs: created.append(
        (issue_id, kwargs)
    )
    ir.async_delete_issue = lambda *args, **kwargs: None

    shell = types.SimpleNamespace(hass=object())
    coordinator_module.SolarAnalyticsCoordinator._maintain_repair_issues(
        shell, _read(binding_status="binding_ambiguous", binding_reason="solar_source_count:0")
    )

    assert len(created) == 1
    issue_id, kwargs = created[0]
    assert issue_id == "binding_ambiguous"
    assert kwargs["is_fixable"] is False


def test_maintain_repair_issues_clears_everything_on_healthy_binding(coordinator_module) -> None:
    """When the binding is 'ok' every managed issue is deleted, none is created."""

    created: list[str] = []
    deleted: list[str] = []
    ir = sys.modules["homeassistant.helpers.issue_registry"]
    ir.async_create_issue = lambda hass, domain, issue_id, **kwargs: created.append(issue_id)
    ir.async_delete_issue = lambda hass, domain, issue_id: deleted.append(issue_id)

    shell = types.SimpleNamespace(hass=object())
    coordinator_module.SolarAnalyticsCoordinator._maintain_repair_issues(shell, _read())

    assert created == []
    assert set(deleted) == {
        "canonical_actual_mismatch",
        "binding_changed",
        "binding_unavailable",
        "binding_ambiguous",
        "native_entry_unavailable",
        "unsupported_native_contract",
        "unsupported_forecast_entity_contract",
    }


def test_log_native_status_transition_logs_once_per_transition(coordinator_module, caplog) -> None:
    """Repeated identical statuses log once; a recovery emits an info line."""

    import logging as _logging

    shell = types.SimpleNamespace(_logged_native_status=None)

    caplog.set_level(_logging.WARNING)
    caplog.clear()
    coordinator_module.SolarAnalyticsCoordinator._log_native_status_transition(
        shell, _read(status="native_source_unavailable", reason="native_update_not_observed")
    )
    coordinator_module.SolarAnalyticsCoordinator._log_native_status_transition(
        shell, _read(status="native_source_unavailable", reason="native_update_not_observed")
    )
    warnings = [r for r in caplog.records if r.levelno == _logging.WARNING]
    assert len(warnings) == 1
    assert "native_source_unavailable" in warnings[0].message

    caplog.set_level(_logging.INFO)
    caplog.clear()
    coordinator_module.SolarAnalyticsCoordinator._log_native_status_transition(shell, _read())
    coordinator_module.SolarAnalyticsCoordinator._log_native_status_transition(shell, _read())
    infos = [r for r in caplog.records if r.levelno == _logging.INFO]
    assert len(infos) == 1
    assert "recovered" in infos[0].message


def test_log_native_status_transition_stays_quiet_on_boot_when_healthy(
    coordinator_module, caplog
) -> None:
    """First observation of 'ok' does not log anything (no prior failure to recover from)."""

    import logging as _logging

    shell = types.SimpleNamespace(_logged_native_status=None)
    caplog.set_level(_logging.INFO)
    caplog.clear()
    coordinator_module.SolarAnalyticsCoordinator._log_native_status_transition(shell, _read())
    assert caplog.records == []
