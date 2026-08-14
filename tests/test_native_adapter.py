from __future__ import annotations

import asyncio
import importlib
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "solar_analytics"


def _install_fake_ha(helper, *, root_version: bool = True):
    homeassistant = types.ModuleType("homeassistant")
    if root_version:
        homeassistant.__version__ = "2026.7.4"
    homeassistant_const = types.ModuleType("homeassistant.const")
    homeassistant_const.__version__ = "2026.7.4"
    config_entries = types.ModuleType("homeassistant.config_entries")
    core = types.ModuleType("homeassistant.core")
    components = types.ModuleType("homeassistant.components")
    energy_pkg = types.ModuleType("homeassistant.components.energy")
    energy_data = types.ModuleType("homeassistant.components.energy.data")
    forecast_pkg = types.ModuleType("homeassistant.components.forecast_solar")
    forecast_energy = types.ModuleType("homeassistant.components.forecast_solar.energy")
    config_entries.ConfigEntry = object
    core.HomeAssistant = object

    async def async_get_manager(hass):
        return hass.manager

    energy_data.async_get_manager = async_get_manager
    forecast_energy.async_get_solar_forecast = helper
    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.const": homeassistant_const,
            "homeassistant.config_entries": config_entries,
            "homeassistant.core": core,
            "homeassistant.components": components,
            "homeassistant.components.energy": energy_pkg,
            "homeassistant.components.energy.data": energy_data,
            "homeassistant.components.forecast_solar": forecast_pkg,
            "homeassistant.components.forecast_solar.energy": forecast_energy,
        }
    )
    parent = types.ModuleType("custom_components")
    parent.__path__ = [str(COMPONENT.parent)]
    package = types.ModuleType("custom_components.solar_analytics")
    package.__path__ = [str(COMPONENT)]
    sys.modules["custom_components"] = parent
    sys.modules["custom_components.solar_analytics"] = package


class FakePlane:
    subentry_id = "plane-1"
    data = {"declination": 33, "azimuth": 138, "modules_power": 5360}


class FakeNativeRuntime:
    def __init__(self) -> None:
        self.data = types.SimpleNamespace(
            wh_period={
                datetime(2026, 8, 3, 0, tzinfo=UTC): 0,
                datetime(2026, 8, 3, 1, tzinfo=UTC): 100,
                datetime(2026, 8, 3, 2, tzinfo=UTC): 200,
            }
        )
        self.last_update_success = True
        self.last_update_success_time = datetime.now(UTC)
        self.listeners = []

    def async_add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def emit_update(self) -> None:
        for listener in list(self.listeners):
            listener()


class PlainCoordinatorRuntime(FakeNativeRuntime):
    """Pinned Forecast.Solar runtime: DataUpdateCoordinator without a timestamp."""

    def __init__(self) -> None:
        super().__init__()
        self.last_update_success_time = None


class FakeNativeEntry:
    domain = "forecast_solar"
    data = {"latitude": 50.47, "longitude": 30.43}
    options = {"inverter_size": 5190, "damping_morning": 0, "damping_evening": 0}

    def __init__(self) -> None:
        self.runtime_data = FakeNativeRuntime()

    def get_subentries_of_type(self, kind):
        return [FakePlane()] if kind == "plane" else []


class PlainCoordinatorEntry(FakeNativeEntry):
    def __init__(self) -> None:
        self.runtime_data = PlainCoordinatorRuntime()


class DelayedRuntimeEntry(FakeNativeEntry):
    def __init__(self) -> None:
        self.runtime_data = None


class FakeEntries:
    def __init__(self, native_entry):
        self.native_entry = native_entry
        self.updated = None

    def async_get_entry(self, entry_id):
        return self.native_entry if entry_id == "native-1" else None

    def async_update_entry(self, entry, *, data):
        self.updated = data
        entry.data = data


class FakeConfig:
    version = "2026.7.4"


class FakeHass:
    def __init__(self, native_entry, manager):
        self.config_entries = FakeEntries(native_entry)
        self.manager = manager
        self.config = FakeConfig()

    def async_create_task(self, coro):
        return asyncio.create_task(coro)

    async def async_add_executor_job(self, target, *args):
        return target(*args)


class FakeEntry:
    entry_id = "analytics-1"
    data = {}
    options = {}
    domain = "solar_analytics"

    def __init__(self):
        self.unloads = []

    def async_on_unload(self, callback):
        self.unloads.append(callback)


def test_native_adapter_binds_energy_dashboard_and_deduplicates_update() -> None:
    async def helper(hass, config_entry_id):
        return {
            "wh_hours": {
                "2026-08-03T00:00:00+00:00": 0,
                "2026-08-03T01:00:00+00:00": 100,
                "2026-08-03T02:00:00+00:00": 200,
            }
        }

    _install_fake_ha(helper)
    module = importlib.import_module("custom_components.solar_analytics.native_adapter")
    native_entry = FakeNativeEntry()
    manager = types.SimpleNamespace(
        data={
            "energy_sources": [
                {
                    "type": "solar",
                    "stat_energy_from": "sensor.garage_cerbo_gx_pv_energy",
                    "stat_rate": "sensor.garage_cerbo_gx_pv_power",
                    "config_entry_solar_forecast": ["native-1"],
                }
            ]
        }
    )
    hass = FakeHass(native_entry, manager)
    entry = FakeEntry()
    adapter = module.ForecastSolarNativeAdapter(hass, entry)

    async def run():
        binding = await adapter.async_initialize()
        native_entry.runtime_data.emit_update()
        if adapter._capture_task is not None:
            await adapter._capture_task
        first = await adapter.async_capture()
        second = await adapter.async_capture()
        native_entry.runtime_data.last_update_success_time += timedelta(seconds=1)
        native_entry.runtime_data.emit_update()
        if adapter._capture_task is not None:
            await adapter._capture_task
        third = await adapter.async_capture()
        return binding, first, second, third

    binding, first, second, third = asyncio.run(run())
    assert binding.status == "ok"
    assert entry.data["native_forecast_entry_id"] == "native-1"
    assert first.status == "ok"
    assert first.observation is not None
    assert first.observation.observation_sequence == 1
    assert second.observation is not None and second.observation.observation_sequence == 1
    assert third.observation is not None and third.observation.observation_sequence == 2
    assert third.observation.profile.valid_periods[0].energy_wh == 100


def test_native_adapter_listener_cleanup_is_idempotent() -> None:
    """Manual adapter unload must not double-remove HA's registered callback."""

    async def helper(hass, config_entry_id):
        return {"wh_hours": {"2026-08-03T00:00:00+00:00": 0, "2026-08-03T01:00:00+00:00": 100}}

    _install_fake_ha(helper)
    module = importlib.import_module("custom_components.solar_analytics.native_adapter")
    native_entry = FakeNativeEntry()
    manager = types.SimpleNamespace(
        data={
            "energy_sources": [
                {
                    "type": "solar",
                    "stat_energy_from": "sensor.garage_cerbo_gx_pv_energy",
                    "stat_rate": "sensor.garage_cerbo_gx_pv_power",
                    "config_entry_solar_forecast": ["native-1"],
                }
            ]
        }
    )
    entry = FakeEntry()
    adapter = module.ForecastSolarNativeAdapter(FakeHass(native_entry, manager), entry)

    async def run():
        await adapter.async_initialize()
        assert len(entry.unloads) == 1
        await adapter.async_unload()
        for callback in entry.unloads:
            callback()

    asyncio.run(run())


def test_native_adapter_reads_core_version_from_const_module() -> None:
    async def helper(hass, config_entry_id):
        return {"wh_hours": {"2026-08-03T01:00:00+00:00": 1}}

    _install_fake_ha(helper, root_version=False)
    module = importlib.import_module("custom_components.solar_analytics.native_adapter")
    adapter = module.ForecastSolarNativeAdapter(
        FakeHass(FakeNativeEntry(), types.SimpleNamespace(data={})), FakeEntry()
    )
    assert adapter._core_version_supported() is True


def test_native_adapter_accepts_plain_coordinator_after_listener_observation() -> None:
    """Forecast.Solar has no native success timestamp; listener evidence is required."""

    async def helper(hass, config_entry_id):
        return {
            "wh_hours": {
                "2026-08-03T00:00:00+00:00": 0,
                "2026-08-03T01:00:00+00:00": 1,
                "2026-08-03T02:00:00+00:00": 2,
            }
        }

    _install_fake_ha(helper)
    module = importlib.import_module("custom_components.solar_analytics.native_adapter")
    native_entry = PlainCoordinatorEntry()
    manager = types.SimpleNamespace(
        data={
            "energy_sources": [
                {
                    "type": "solar",
                    "stat_energy_from": "sensor.garage_cerbo_gx_pv_energy",
                    "stat_rate": "sensor.garage_cerbo_gx_pv_power",
                    "config_entry_solar_forecast": ["native-1"],
                }
            ]
        }
    )
    hass = FakeHass(native_entry, manager)
    entry = FakeEntry()
    adapter = module.ForecastSolarNativeAdapter(hass, entry)

    async def run():
        await adapter.async_initialize()
        before_listener = await adapter.async_capture()
        native_entry.runtime_data.emit_update()
        if adapter._capture_task is not None:
            await adapter._capture_task
        after_listener = await adapter.async_capture()
        return before_listener, after_listener

    before_listener, after_listener = asyncio.run(run())
    assert before_listener.status == "native_source_unavailable"
    assert before_listener.reason == "native_update_not_observed"
    assert after_listener.status == "ok"
    assert after_listener.observation is not None


def test_native_adapter_retries_listener_after_native_entry_finishes_setup() -> None:
    async def helper(hass, config_entry_id):
        return {
            "wh_hours": {
                "2026-08-03T00:00:00+00:00": 0,
                "2026-08-03T01:00:00+00:00": 1,
                "2026-08-03T02:00:00+00:00": 2,
            }
        }

    _install_fake_ha(helper)
    module = importlib.import_module("custom_components.solar_analytics.native_adapter")
    native_entry = DelayedRuntimeEntry()
    manager = types.SimpleNamespace(
        data={
            "energy_sources": [
                {
                    "type": "solar",
                    "stat_energy_from": "sensor.garage_cerbo_gx_pv_energy",
                    "stat_rate": "sensor.garage_cerbo_gx_pv_power",
                    "config_entry_solar_forecast": ["native-1"],
                }
            ]
        }
    )
    hass = FakeHass(native_entry, manager)
    entry = FakeEntry()
    adapter = module.ForecastSolarNativeAdapter(hass, entry)

    async def run():
        await adapter.async_initialize()
        before_setup = await adapter.async_capture()
        native_entry.runtime_data = PlainCoordinatorRuntime()
        before_update = await adapter.async_capture()
        native_entry.runtime_data.emit_update()
        if adapter._capture_task is not None:
            await adapter._capture_task
        after_update = await adapter.async_capture()
        return before_setup, before_update, after_update

    before_setup, before_update, after_update = asyncio.run(run())
    assert before_setup.reason == "entry_unloaded"
    assert before_update.reason == "native_update_not_observed"
    assert after_update.status == "ok"


def test_native_adapter_rebinds_listener_when_native_runtime_is_replaced() -> None:
    async def helper(hass, config_entry_id):
        return {
            "wh_hours": {
                "2026-08-03T00:00:00+00:00": 0,
                "2026-08-03T01:00:00+00:00": 1,
                "2026-08-03T02:00:00+00:00": 2,
            }
        }

    _install_fake_ha(helper)
    module = importlib.import_module("custom_components.solar_analytics.native_adapter")
    native_entry = PlainCoordinatorEntry()
    manager = types.SimpleNamespace(
        data={
            "energy_sources": [
                {
                    "type": "solar",
                    "stat_energy_from": "sensor.garage_cerbo_gx_pv_energy",
                    "stat_rate": "sensor.garage_cerbo_gx_pv_power",
                    "config_entry_solar_forecast": ["native-1"],
                }
            ]
        }
    )
    adapter = module.ForecastSolarNativeAdapter(FakeHass(native_entry, manager), FakeEntry())
    runtime_a = native_entry.runtime_data

    async def run():
        await adapter.async_initialize()
        before = await adapter.async_capture()
        runtime_b = PlainCoordinatorRuntime()
        native_entry.runtime_data = runtime_b
        before_callback = await adapter.async_capture()
        runtime_b.emit_update()
        if adapter._capture_task is not None:
            await adapter._capture_task
        after = await adapter.async_capture()
        return before, before_callback, after, runtime_b

    before, before_callback, after, runtime_b = asyncio.run(run())
    assert before.reason == "native_update_not_observed"
    assert before_callback.reason == "native_update_not_observed"
    assert len(runtime_a.listeners) == 0
    assert len(runtime_b.listeners) == 1
    assert after.status == "ok"
    assert after.observation is not None


def test_native_adapter_ignores_callback_from_replaced_runtime() -> None:
    async def helper(hass, config_entry_id):
        return {
            "wh_hours": {
                "2026-08-03T00:00:00+00:00": 0,
                "2026-08-03T01:00:00+00:00": 1,
                "2026-08-03T02:00:00+00:00": 2,
            }
        }

    _install_fake_ha(helper)
    module = importlib.import_module("custom_components.solar_analytics.native_adapter")
    native_entry = PlainCoordinatorEntry()
    manager = types.SimpleNamespace(
        data={
            "energy_sources": [
                {
                    "type": "solar",
                    "stat_energy_from": "sensor.garage_cerbo_gx_pv_energy",
                    "stat_rate": "sensor.garage_cerbo_gx_pv_power",
                    "config_entry_solar_forecast": ["native-1"],
                }
            ]
        }
    )
    adapter = module.ForecastSolarNativeAdapter(FakeHass(native_entry, manager), FakeEntry())

    async def run():
        await adapter.async_initialize()
        runtime_a = native_entry.runtime_data
        stale_listener = runtime_a.listeners[0]
        native_entry.runtime_data = PlainCoordinatorRuntime()
        before = await adapter.async_capture()
        stale_listener()
        if adapter._capture_task is not None:
            await adapter._capture_task
        after = await adapter.async_capture()
        return before, after

    before, after = asyncio.run(run())
    assert before.reason == "native_update_not_observed"
    assert after.status == "native_source_unavailable"
    assert after.reason == "native_update_not_observed"
    assert after.observation is None


def test_native_adapter_does_not_admit_failed_listener_callback() -> None:
    async def helper(hass, config_entry_id):
        return {
            "wh_hours": {
                "2026-08-03T00:00:00+00:00": 0,
                "2026-08-03T01:00:00+00:00": 1,
                "2026-08-03T02:00:00+00:00": 2,
            }
        }

    _install_fake_ha(helper)
    module = importlib.import_module("custom_components.solar_analytics.native_adapter")
    native_entry = PlainCoordinatorEntry()
    manager = types.SimpleNamespace(
        data={
            "energy_sources": [
                {
                    "type": "solar",
                    "stat_energy_from": "sensor.garage_cerbo_gx_pv_energy",
                    "stat_rate": "sensor.garage_cerbo_gx_pv_power",
                    "config_entry_solar_forecast": ["native-1"],
                }
            ]
        }
    )
    adapter = module.ForecastSolarNativeAdapter(FakeHass(native_entry, manager), FakeEntry())

    async def run():
        await adapter.async_initialize()
        runtime = native_entry.runtime_data
        runtime.last_update_success = False
        runtime.emit_update()
        if adapter._capture_task is not None:
            await adapter._capture_task
        runtime.last_update_success = True
        return await adapter.async_capture()

    result = asyncio.run(run())
    assert result.status == "native_source_unavailable"
    assert result.reason == "native_update_not_observed"
    assert result.observation is None


def test_native_adapter_fails_closed_on_ambiguous_energy_binding() -> None:
    async def helper(hass, config_entry_id):
        return {"wh_hours": {"2026-08-03T01:00:00+00:00": 1}}

    _install_fake_ha(helper)
    module = importlib.import_module("custom_components.solar_analytics.native_adapter")
    native_entry = FakeNativeEntry()
    manager = types.SimpleNamespace(data={"energy_sources": [{"type": "solar"}, {"type": "solar"}]})
    hass = FakeHass(native_entry, manager)
    adapter = module.ForecastSolarNativeAdapter(hass, FakeEntry())
    result = asyncio.run(adapter.async_resolve_binding())
    assert result.status == "binding_ambiguous"


def test_native_adapter_reports_redacted_profile_validation_reason() -> None:
    """Expose only structural validation classes when the native profile is blocked."""

    async def helper(hass, config_entry_id):
        return {
            "wh_hours": {
                "2026-08-03T01:00:00+00:00": 10,
                "2026-08-03T04:30:00+00:00": 20,
            }
        }

    _install_fake_ha(helper)
    module = importlib.import_module("custom_components.solar_analytics.native_adapter")
    native_entry = PlainCoordinatorEntry()
    manager = types.SimpleNamespace(
        data={
            "energy_sources": [
                {
                    "type": "solar",
                    "stat_energy_from": "sensor.garage_cerbo_gx_pv_energy",
                    "stat_rate": "sensor.garage_cerbo_gx_pv_power",
                    "config_entry_solar_forecast": ["native-1"],
                }
            ]
        }
    )
    hass = FakeHass(native_entry, manager)
    entry = FakeEntry()
    adapter = module.ForecastSolarNativeAdapter(hass, entry)

    async def run():
        await adapter.async_initialize()
        native_entry.runtime_data.emit_update()
        if adapter._capture_task is not None:
            await adapter._capture_task
        return await adapter.async_capture()

    result = asyncio.run(run())
    assert result.status == "unsupported_native_contract"
    assert result.reason == (
        "wh_hours_validation_failed:raw_count=2:invalid_count=1:"
        "reasons=internal_gap_or_period_too_long"
    )


def test_native_adapter_imports_helper_off_event_loop() -> None:
    """Native helper import must not execute synchronously in HA's event loop."""

    async def helper(hass, config_entry_id):
        return {
            "wh_hours": {
                "2026-08-03T00:00:00+00:00": 0,
                "2026-08-03T01:00:00+00:00": 1,
                "2026-08-03T02:00:00+00:00": 2,
            }
        }

    _install_fake_ha(helper)
    module = importlib.import_module("custom_components.solar_analytics.native_adapter")
    native_entry = PlainCoordinatorEntry()
    manager = types.SimpleNamespace(
        data={
            "energy_sources": [
                {
                    "type": "solar",
                    "stat_energy_from": "sensor.garage_cerbo_gx_pv_energy",
                    "stat_rate": "sensor.garage_cerbo_gx_pv_power",
                    "config_entry_solar_forecast": ["native-1"],
                }
            ]
        }
    )
    hass = FakeHass(native_entry, manager)
    executor_calls = []

    async def async_add_executor_job(target, *args):
        executor_calls.append((target.__name__, args))
        return target(*args)

    hass.async_add_executor_job = async_add_executor_job
    adapter = module.ForecastSolarNativeAdapter(hass, FakeEntry())

    async def run():
        await adapter.async_initialize()
        native_entry.runtime_data.emit_update()
        if adapter._capture_task is not None:
            await adapter._capture_task
        return await adapter.async_capture()

    result = asyncio.run(run())
    assert result.status == "ok"
    assert all(name == "import_module" for name, _args in executor_calls)
    imported_modules = [args[0] for _name, args in executor_calls]
    assert imported_modules.count("homeassistant.components.energy.data") == 3
    assert imported_modules.count("homeassistant.components.forecast_solar.energy") == 1


def test_native_adapter_prefers_user_configured_entities_over_energy_dashboard() -> None:
    """User overrides in entry.data must beat auto-detection from the Energy Dashboard."""

    async def helper(hass, config_entry_id):
        return {"wh_hours": {"2026-08-03T00:00:00+00:00": 0, "2026-08-03T01:00:00+00:00": 1}}

    _install_fake_ha(helper)
    module = importlib.import_module("custom_components.solar_analytics.native_adapter")
    native_entry = FakeNativeEntry()
    manager = types.SimpleNamespace(
        data={
            "energy_sources": [
                {
                    "type": "solar",
                    "stat_energy_from": "sensor.dashboard_energy",
                    "stat_rate": "sensor.dashboard_power",
                    "config_entry_solar_forecast": ["dashboard-entry"],
                }
            ]
        }
    )
    hass = FakeHass(native_entry, manager)
    entry = FakeEntry()
    entry.data = {
        "native_forecast_entry_id": "native-1",
        "actual_power_entity": "sensor.user_power",
        "actual_energy_today_entity": "sensor.user_energy",
    }
    adapter = module.ForecastSolarNativeAdapter(hass, entry)

    async def run():
        return await adapter.async_resolve_binding()

    binding = asyncio.run(run())
    assert binding.status == "ok"
    assert binding.native_entry_id == "native-1"
    assert binding.actual_power_entity == "sensor.user_power"
    assert binding.actual_energy_entity == "sensor.user_energy"


def test_native_adapter_falls_back_to_energy_dashboard_when_user_omits_fields() -> None:
    """Empty entry.data must trigger Energy-Dashboard auto-detection for every field."""

    async def helper(hass, config_entry_id):
        return {"wh_hours": {"2026-08-03T00:00:00+00:00": 0, "2026-08-03T01:00:00+00:00": 1}}

    _install_fake_ha(helper)
    module = importlib.import_module("custom_components.solar_analytics.native_adapter")
    native_entry = FakeNativeEntry()
    manager = types.SimpleNamespace(
        data={
            "energy_sources": [
                {
                    "type": "solar",
                    "stat_energy_from": "sensor.autodetect_energy",
                    "stat_rate": "sensor.autodetect_power",
                    "config_entry_solar_forecast": ["native-1"],
                }
            ]
        }
    )
    hass = FakeHass(native_entry, manager)
    adapter = module.ForecastSolarNativeAdapter(hass, FakeEntry())

    async def run():
        return await adapter.async_resolve_binding()

    binding = asyncio.run(run())
    assert binding.status == "ok"
    assert binding.native_entry_id == "native-1"
    assert binding.actual_power_entity == "sensor.autodetect_power"
    assert binding.actual_energy_entity == "sensor.autodetect_energy"


class FakeGenericRuntime:
    """A non-Forecast.Solar provider coordinator: no ``wh_period`` on runtime."""

    def __init__(self) -> None:
        self.data = types.SimpleNamespace()
        self.last_update_success = True
        self.listeners: list = []

    def async_add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def emit_update(self) -> None:
        for listener in list(self.listeners):
            listener()


class FakeSolcastEntry:
    domain = "solcast_solar"
    entry_id = "native-1"
    data = {"api_key": "SECRET-should-never-leak"}
    options = {"resource_id": "abc-123", "hard_limit": 5000}

    def __init__(self) -> None:
        self.runtime_data = FakeGenericRuntime()


def test_native_adapter_generalizes_to_non_forecast_solar_provider() -> None:
    """Any integration exposing the Energy solar-forecast platform is accepted.

    Solcast has no ``wh_period`` runtime and different config than
    Forecast.Solar, so this exercises the generic liveness gate and the generic
    model fingerprint. Secret config must never enter the model values.
    """

    async def helper(hass, config_entry_id):
        return {
            "wh_hours": {
                "2026-08-03T00:00:00+00:00": 0,
                "2026-08-03T01:00:00+00:00": 100,
                "2026-08-03T02:00:00+00:00": 200,
            }
        }

    _install_fake_ha(helper)
    solcast_pkg = types.ModuleType("homeassistant.components.solcast_solar")
    solcast_energy = types.ModuleType("homeassistant.components.solcast_solar.energy")
    solcast_energy.async_get_solar_forecast = helper
    sys.modules["homeassistant.components.solcast_solar"] = solcast_pkg
    sys.modules["homeassistant.components.solcast_solar.energy"] = solcast_energy
    module = importlib.import_module("custom_components.solar_analytics.native_adapter")
    native_entry = FakeSolcastEntry()
    manager = types.SimpleNamespace(
        data={
            "energy_sources": [
                {
                    "type": "solar",
                    "stat_energy_from": "sensor.pv_energy",
                    "stat_rate": "sensor.pv_power",
                    "config_entry_solar_forecast": ["native-1"],
                }
            ]
        }
    )
    hass = FakeHass(native_entry, manager)
    adapter = module.ForecastSolarNativeAdapter(hass, FakeEntry())

    async def run():
        await adapter.async_initialize()
        native_entry.runtime_data.emit_update()
        if adapter._capture_task is not None:
            await adapter._capture_task
        return await adapter.async_capture()

    result = asyncio.run(run())
    assert result.status == "ok"
    assert result.observation is not None
    assert result.observation.profile.valid_periods[0].energy_wh == 100
    fingerprint = result.observation.model.fingerprint
    assert isinstance(fingerprint, str) and fingerprint.startswith("sha256:")
    serialized = repr(result.observation.model.values)
    assert "api_key" not in serialized and "SECRET" not in serialized
    assert isinstance(adapter, module.ForecastProfileProvider)
    assert module.EnergyForecastProvider is module.ForecastSolarNativeAdapter


def test_native_adapter_accepts_supported_minimum_and_rejects_older_core() -> None:
    """Version check is now a >= minimum, not an exact string match."""

    async def helper(hass, config_entry_id):
        return {"wh_hours": {"2026-08-03T01:00:00+00:00": 1}}

    _install_fake_ha(helper)
    module = importlib.import_module("custom_components.solar_analytics.native_adapter")
    adapter = module.ForecastSolarNativeAdapter(
        FakeHass(FakeNativeEntry(), types.SimpleNamespace(data={})), FakeEntry()
    )

    import homeassistant.const as ha_const

    ha_const.__version__ = "2026.7.9"
    assert adapter._core_version_supported() is True

    ha_const.__version__ = "2026.8.0"
    assert adapter._core_version_supported() is True

    ha_const.__version__ = "2026.6.5"
    assert adapter._core_version_supported() is False

    ha_const.__version__ = "2026.7.4"
