"""Pure-function coverage for coordinator helpers.

The coordinator module is imported without instantiating any Home Assistant
runtime; only the module-level helpers are exercised. This test guards the
timezone-correct scheduler that replaced the buggy
``async_track_time_change(..., hour=6)`` pattern.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

UTC = timezone.utc
COMPONENT = (
    Path(__file__).resolve().parents[1]
    / "home_assistant"
    / "custom_components"
    / "solar_analytics"
)


def _load_coordinator_helpers():
    """Import coordinator.py with a minimal HA stub, then hand back the helpers.

    Only the pure functions ``_next_local_hour_utc`` and ``_default_time_zone``
    are exercised; no ``SolarAnalyticsCoordinator`` instance is constructed.
    """

    if "custom_components.solar_analytics.coordinator" in sys.modules:
        return sys.modules["custom_components.solar_analytics.coordinator"]

    ha = types.ModuleType("homeassistant")
    ha_const = types.ModuleType("homeassistant.const")
    ha_config = types.ModuleType("homeassistant.config_entries")
    ha_config.ConfigEntry = object
    ha_core = types.ModuleType("homeassistant.core")
    ha_core.HomeAssistant = object

    def _passthrough_callback(func):
        return func

    ha_core.callback = _passthrough_callback
    ha_helpers = types.ModuleType("homeassistant.helpers")
    ha_helpers_event = types.ModuleType("homeassistant.helpers.event")
    ha_helpers_event.async_track_point_in_utc_time = lambda hass, action, when: lambda: None
    ha_helpers_update = types.ModuleType("homeassistant.helpers.update_coordinator")

    from typing import Generic, TypeVar

    _T = TypeVar("_T")

    class _StubCoordinator(Generic[_T]):
        def __init__(self, *args, **kwargs):
            pass

    class _StubUpdateFailed(Exception):
        pass

    ha_helpers_update.DataUpdateCoordinator = _StubCoordinator
    ha_helpers_update.UpdateFailed = _StubUpdateFailed
    sys.modules.update(
        {
            "homeassistant": ha,
            "homeassistant.const": ha_const,
            "homeassistant.config_entries": ha_config,
            "homeassistant.core": ha_core,
            "homeassistant.helpers": ha_helpers,
            "homeassistant.helpers.event": ha_helpers_event,
            "homeassistant.helpers.update_coordinator": ha_helpers_update,
        }
    )
    parent = types.ModuleType("custom_components")
    parent.__path__ = [str(COMPONENT.parent)]
    package = types.ModuleType("custom_components.solar_analytics")
    package.__path__ = [str(COMPONENT)]
    sys.modules["custom_components"] = parent
    sys.modules["custom_components.solar_analytics"] = package

    spec = importlib.util.spec_from_file_location(
        "custom_components.solar_analytics.coordinator",
        COMPONENT / "coordinator.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["custom_components.solar_analytics.coordinator"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def helpers():
    return _load_coordinator_helpers()


def test_next_local_hour_advances_to_today_when_hour_is_still_ahead(helpers) -> None:
    tz = ZoneInfo("America/Los_Angeles")
    # 2026-08-11 03:30 in Los Angeles == 2026-08-11 10:30 UTC (PDT, UTC-7).
    now_utc = datetime(2026, 8, 11, 10, 30, tzinfo=UTC)

    result = helpers._next_local_hour_utc(now_utc, tz, 6)

    # 2026-08-11 06:00 LA == 2026-08-11 13:00 UTC.
    assert result == datetime(2026, 8, 11, 13, 0, tzinfo=UTC)


def test_next_local_hour_advances_to_tomorrow_when_hour_is_already_past(helpers) -> None:
    tz = ZoneInfo("America/Los_Angeles")
    # 2026-08-11 07:30 LA == 2026-08-11 14:30 UTC.
    now_utc = datetime(2026, 8, 11, 14, 30, tzinfo=UTC)

    result = helpers._next_local_hour_utc(now_utc, tz, 6)

    # 2026-08-12 06:00 LA == 2026-08-12 13:00 UTC.
    assert result == datetime(2026, 8, 12, 13, 0, tzinfo=UTC)


def test_next_local_hour_respects_configured_timezone_not_utc(helpers) -> None:
    """Scheduling must fire at the configured-TZ hour regardless of HA's own TZ."""

    kyiv = ZoneInfo("Europe/Kyiv")
    la = ZoneInfo("America/Los_Angeles")
    now_utc = datetime(2026, 8, 11, 0, 30, tzinfo=UTC)

    kyiv_next = helpers._next_local_hour_utc(now_utc, kyiv, 6)
    la_next = helpers._next_local_hour_utc(now_utc, la, 6)

    # 06:00 Kyiv (UTC+3, no DST in this period) == 03:00 UTC.
    assert kyiv_next.astimezone(kyiv).hour == 6
    # 06:00 Los Angeles (PDT, UTC-7) == 13:00 UTC.
    assert la_next.astimezone(la).hour == 6
    # Two different local hours therefore fire at two different UTC instants.
    assert kyiv_next != la_next


def test_next_local_hour_survives_dst_spring_forward(helpers) -> None:
    """Scheduling for 06:00 local on a DST transition day still returns 06:00 local."""

    kyiv = ZoneInfo("Europe/Kyiv")
    # Kyiv 2026 spring-forward is Sunday 2026-03-29 03:00 local -> 04:00 local.
    # Ask for the next 06:00 local from Saturday evening.
    now_utc = datetime(2026, 3, 28, 20, 0, tzinfo=UTC)
    result = helpers._next_local_hour_utc(now_utc, kyiv, 6)

    result_local = result.astimezone(kyiv)
    assert result_local.hour == 6
    assert result_local.date().isoformat() == "2026-03-29"


def test_default_time_zone_prefers_hass_config(helpers) -> None:
    class _Hass:
        class config:  # type: ignore[no-redef]
            time_zone = "Europe/Berlin"

    assert helpers._default_time_zone(_Hass()) == "Europe/Berlin"


def test_default_time_zone_falls_back_to_utc(helpers) -> None:
    class _Hass:
        class config:  # type: ignore[no-redef]
            time_zone = None

    assert helpers._default_time_zone(_Hass()) == "UTC"


def test_default_time_zone_falls_back_when_hass_has_no_config_attr(helpers) -> None:
    class _Hass:
        pass

    assert helpers._default_time_zone(_Hass()) == "UTC"
