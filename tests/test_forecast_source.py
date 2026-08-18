"""Coverage for the forecast-entity provider and its attribute extractor.

The pure extractor is imported through the ``solar_analytics.native`` path
alias. The provider needs the Home Assistant config-entry/core stubs its
sibling adapter imports, installed here per the suite's usual pattern.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

from solar_analytics.native import extract_forecast_entity_wh_hours

COMPONENT = Path(__file__).parents[1] / "custom_components" / "solar_analytics"


def _install_ha_stub() -> None:
    ha = types.ModuleType("homeassistant")
    ha_const = types.ModuleType("homeassistant.const")
    ha_const.__version__ = "2026.7.4"
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    event = types.ModuleType("homeassistant.helpers.event")
    event.async_track_state_change_event = lambda hass, entity_ids, action: lambda: None
    sys.modules.update(
        {
            "homeassistant": ha,
            "homeassistant.const": ha_const,
            "homeassistant.config_entries": config_entries,
            "homeassistant.core": core,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.event": event,
        }
    )
    parent = types.ModuleType("custom_components")
    parent.__path__ = [str(COMPONENT.parent)]
    package = types.ModuleType("custom_components.solar_analytics")
    package.__path__ = [str(COMPONENT)]
    sys.modules["custom_components"] = parent
    sys.modules["custom_components.solar_analytics"] = package


class FakeState:
    def __init__(self, state, attributes, last_updated):
        self.state = state
        self.attributes = attributes
        self.last_updated = last_updated
        self.last_changed = last_updated


class FakeStates:
    def __init__(self, mapping):
        self._mapping = mapping

    def get(self, entity_id):
        return self._mapping.get(entity_id)


class FakeHass:
    def __init__(self, states):
        self.states = FakeStates(states)

    async def async_add_executor_job(self, target, *args):
        return target(*args)

    def async_create_task(self, coro):
        return asyncio.ensure_future(coro)


class FakeEntry:
    def __init__(self, data):
        self.entry_id = "analytics-1"
        self.data = data
        self.options = {}


def _entry_data(**overrides):
    data = {
        "forecast_source_type": "forecast_entity",
        "forecast_entity_id": "sensor.my_forecast",
        "actual_power_entity": "sensor.pv_power",
        "actual_energy_today_entity": "sensor.pv_energy",
    }
    data.update(overrides)
    return data


def _provider(states, **overrides):
    _install_ha_stub()
    module = importlib.import_module("custom_components.solar_analytics.forecast_source")
    return module.EntityForecastProvider(FakeHass(states), FakeEntry(_entry_data(**overrides)))


def _future_wh_hours(now: datetime) -> dict[str, int]:
    """A profile whose horizon still reaches beyond ``now``.

    The first entry is a boundary; the two following period-end timestamps are
    in the future, so the profile is live regardless of the entity's
    last_updated.
    """

    base = now.replace(minute=0, second=0, microsecond=0)
    return {
        base.isoformat(): 0,
        (base + timedelta(hours=1)).isoformat(): 100,
        (base + timedelta(hours=2)).isoformat(): 200,
    }


def test_extractor_accepts_known_wh_maps_and_rejects_scalars() -> None:
    assert extract_forecast_entity_wh_hours({"wh_hours": {"2026-08-03T01:00:00+00:00": 100}}) == {
        "wh_hours": {"2026-08-03T01:00:00+00:00": 100}
    }
    assert extract_forecast_entity_wh_hours(
        {"watt_hours_period": {"2026-08-03T01:00:00+00:00": 5}}
    ) == {"wh_hours": {"2026-08-03T01:00:00+00:00": 5}}
    assert extract_forecast_entity_wh_hours({"pv_estimate": 3.2}) is None
    assert extract_forecast_entity_wh_hours({"wh_hours": {}}) is None
    assert extract_forecast_entity_wh_hours({}) is None


def test_entity_provider_captures_timestamped_profile() -> None:
    now = datetime.now(UTC)
    state = FakeState(
        "on",
        {"wh_hours": _future_wh_hours(now), "unit_of_measurement": "Wh"},
        now,
    )
    provider = _provider({"sensor.my_forecast": state})

    async def run():
        binding = await provider.async_initialize()
        return binding, await provider.async_capture()

    binding, read = asyncio.run(run())
    assert binding.status == "ok"
    assert binding.forecast_entity_id == "sensor.my_forecast"
    assert provider.source_kind == "forecast_entity"
    assert read.status == "ok"
    assert read.observation is not None
    assert read.observation.profile.valid_periods[0].energy_wh == 100
    assert read.observation.model.fingerprint.startswith("sha256:")


def test_entity_provider_fails_closed_on_scalar_only_entity() -> None:
    state = FakeState("3.2", {"unit_of_measurement": "kW"}, datetime.now(UTC))
    provider = _provider({"sensor.my_forecast": state})
    read = asyncio.run(provider.async_capture())
    assert read.status == "unsupported_forecast_entity_contract"
    assert read.reason == "no_timestamped_profile"


def test_entity_provider_rejects_non_wh_unit() -> None:
    """A kWh map would be 1000x too small; fail closed rather than convert."""

    state = FakeState(
        "on",
        {
            "wh_hours": {
                "2026-08-03T00:00:00+00:00": 0,
                "2026-08-03T01:00:00+00:00": 1,
                "2026-08-03T02:00:00+00:00": 2,
            },
            "unit_of_measurement": "kWh",
        },
        datetime.now(UTC),
    )
    provider = _provider({"sensor.my_forecast": state})
    read = asyncio.run(provider.async_capture())
    assert read.status == "unsupported_forecast_entity_contract"
    assert read.reason == "non_wh_unit:kWh"


def test_entity_provider_accepts_unspecified_unit_as_wh() -> None:
    """A map with no unit is accepted (treated as Wh); the unit is unspecified."""

    now = datetime.now(UTC)
    state = FakeState("on", {"wh_hours": _future_wh_hours(now)}, now)
    provider = _provider({"sensor.my_forecast": state})
    read = asyncio.run(provider.async_capture())
    assert read.status == "ok"
    assert read.observation is not None
    assert read.observation.profile.valid_periods[0].energy_wh == 100


def test_entity_provider_rejects_restored_state() -> None:
    """A recorder-restored state is not live evidence and must be rejected."""

    now = datetime.now(UTC)
    state = FakeState(
        "on",
        {"wh_hours": _future_wh_hours(now), "unit_of_measurement": "Wh", "restored": True},
        now,
    )
    provider = _provider({"sensor.my_forecast": state})
    read = asyncio.run(provider.async_capture())
    assert read.status == "native_source_unavailable"
    assert read.reason == "forecast_entity_restored"


def test_entity_provider_admits_quiet_entity_with_future_horizon() -> None:
    """A day-ahead profile is live while it covers the future, even if quiet.

    The entity has not changed for 12 hours, which the old 2h last_updated gate
    would have marked stale, but the profile still reaches beyond now.
    """

    now = datetime.now(UTC)
    quiet_since = now - timedelta(hours=12)
    state = FakeState(
        "on",
        {"wh_hours": _future_wh_hours(now), "unit_of_measurement": "Wh"},
        quiet_since,
    )
    provider = _provider({"sensor.my_forecast": state})
    read = asyncio.run(provider.async_capture())
    assert read.status == "ok"
    assert read.observation is not None


def test_entity_provider_marks_stale_profile() -> None:
    stale = datetime.now(UTC) - timedelta(hours=3)
    state = FakeState(
        "on",
        {
            "wh_hours": {
                "2026-08-03T00:00:00+00:00": 0,
                "2026-08-03T01:00:00+00:00": 100,
                "2026-08-03T02:00:00+00:00": 200,
            }
        },
        stale,
    )
    provider = _provider({"sensor.my_forecast": state})
    read = asyncio.run(provider.async_capture())
    assert read.status == "native_source_stale"


def test_entity_provider_reports_missing_entity() -> None:
    provider = _provider({})
    read = asyncio.run(provider.async_capture())
    assert read.status == "native_source_unavailable"
    assert read.reason == "forecast_entity_missing"


def test_entity_provider_requires_forecast_entity_id() -> None:
    provider = _provider({}, forecast_entity_id="")
    binding = asyncio.run(provider.async_resolve_binding())
    assert binding.status == "binding_unavailable"
    assert binding.reason == "forecast_entity_id_missing"


def test_entity_provider_state_listener_records_and_reuses_observation() -> None:
    """A state change records an observation the later scheduled capture reuses.

    The recorded observation's ``observed_at_utc`` predates the later capture,
    so a scheduled morning snapshot can satisfy ``observed_at_utc <= scheduled``.
    """

    now = datetime.now(UTC)
    state = FakeState(
        "on",
        {"wh_hours": _future_wh_hours(now), "unit_of_measurement": "Wh"},
        now,
    )
    provider = _provider({"sensor.my_forecast": state})
    module = importlib.import_module("custom_components.solar_analytics.forecast_source")

    # Patch the tracker on the imported module so the assertion is robust to
    # cross-test module caching of the ``from ... import`` binding.
    registered: dict[str, object] = {}

    def _track(hass, entity_ids, action):
        for entity_id in entity_ids:
            registered[entity_id] = action

        def remove():
            for entity_id in entity_ids:
                registered.pop(entity_id, None)

        return remove

    original = module.async_track_state_change_event
    module.async_track_state_change_event = _track
    try:

        async def run():
            await provider.async_initialize()
            assert "sensor.my_forecast" in registered
            provider._handle_state_event(object())
            await provider._capture_task
            early_observation = provider._observation
            later = await provider.async_capture()
            await provider.async_unload()
            return early_observation, later

        early_observation, later = asyncio.run(run())
    finally:
        module.async_track_state_change_event = original

    assert early_observation is not None
    assert later.status == "ok"
    assert later.observation is not None
    assert later.observation.observation_sequence == early_observation.observation_sequence
    assert later.observation.observed_at_utc == early_observation.observed_at_utc
    # Unload removed the listener (read-only teardown).
    assert "sensor.my_forecast" not in registered


def test_migration_preserves_forecast_source_fields() -> None:
    migration = importlib.import_module("solar_analytics.migration")
    version, data = migration.migrate_entry_data(
        5,
        {
            "forecast_source_type": "forecast_entity",
            "forecast_entity_id": "sensor.my_forecast",
            "actual_power_entity": "sensor.pv_power",
        },
    )
    assert version == migration.CURRENT_ENTRY_VERSION == 6
    assert data["forecast_source_type"] == "forecast_entity"
    assert data["forecast_entity_id"] == "sensor.my_forecast"
