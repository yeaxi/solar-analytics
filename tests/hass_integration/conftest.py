"""Fixtures for Home-Assistant-integrated Solar Analytics tests.

These tests boot a real ``hass`` object provided by
``pytest-homeassistant-custom-component`` and set up the Solar Analytics
integration through the modern ``config_entries`` flow. That exercises the
same code paths a live HA install does — manifest loading, config-flow
selectors, entity registration, coordinator lifecycle, diagnostics — and
snapshots the resulting entity registry so a change in unique_id,
translation_key, device_class, entity_category, or enabled-by-default flag
fails the snapshot instead of silently drifting.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def enable_custom_integrations(enable_custom_integrations):
    """Route Home Assistant's integration loader to our custom_components/ tree.

    Re-exports the ``enable_custom_integrations`` fixture from
    ``pytest-homeassistant-custom-component`` as autouse so every test in this
    subdirectory sees Solar Analytics.
    """

    return enable_custom_integrations


@pytest.fixture(autouse=True)
def _bypass_core_version_check() -> Iterator[None]:
    """Force the native adapter to treat the running HA as supported.

    Solar Analytics targets HA Core 2026.7+, but
    ``pytest-homeassistant-custom-component`` pins an earlier release. We
    monkeypatch the adapter's version check for the duration of each test
    rather than lowering the shipping minimum.
    """

    from custom_components.solar_analytics import native_adapter

    with patch.object(
        native_adapter.ForecastSolarNativeAdapter,
        "_core_version_supported",
        return_value=True,
    ):
        yield


@pytest.fixture(autouse=True)
def _stub_hass_dependencies(hass) -> None:
    """Register stub 'energy' and 'forecast_solar' integrations for HA to load.

    Solar Analytics declares both in ``manifest.dependencies``. Installing the
    real components would pull SQLAlchemy, aiohttp, and the ``forecast_solar``
    PyPI client into the test environment; mocking them here is orders of
    magnitude cheaper and preserves the dependency-resolution contract our
    manifest declares.
    """

    from pytest_homeassistant_custom_component.common import MockModule, mock_integration

    async def _ok_async_setup(_hass, _config):
        return True

    mock_integration(hass, MockModule("energy", async_setup=_ok_async_setup))
    mock_integration(hass, MockModule("forecast_solar", async_setup=_ok_async_setup))


@pytest.fixture
def energy_manager_stub() -> types.SimpleNamespace:
    """Provide a minimal fake for ``homeassistant.components.energy.data``."""

    return types.SimpleNamespace(
        data={
            "energy_sources": [
                {
                    "type": "solar",
                    "stat_energy_from": "sensor.example_pv_energy",
                    "stat_rate": "sensor.example_pv_power",
                    "config_entry_solar_forecast": ["fake-forecast-solar-entry-id"],
                }
            ]
        }
    )


@pytest.fixture
def actual_pv_states(hass) -> None:
    """Register the two actual-PV sensor states the coordinator reads.

    The states carry the exact device_class / state_class / unit_of_measurement
    combinations ``validate_actual_state`` demands. HA stamps ``last_updated``
    on each set to ``now()``; the coordinator's staleness check accepts
    anything younger than 15 minutes.
    """

    hass.states.async_set(
        "sensor.example_pv_power",
        "1234.5",
        {
            "device_class": "power",
            "state_class": "measurement",
            "unit_of_measurement": "W",
        },
    )
    hass.states.async_set(
        "sensor.example_pv_energy",
        "12.3",
        {
            "device_class": "energy",
            "state_class": "total_increasing",
            "unit_of_measurement": "kWh",
        },
    )


@pytest.fixture
def energy_manager_installed(energy_manager_stub, monkeypatch) -> types.SimpleNamespace:
    """Install a stub ``homeassistant.components.energy.data`` module for import.

    The native adapter imports this module off the event loop; the stub
    replaces the real Energy component while the test runs so we do not need
    to configure the real Energy dashboard.
    """

    module = types.ModuleType("homeassistant.components.energy.data")

    async def async_get_manager(_hass):
        return energy_manager_stub

    module.async_get_manager = async_get_manager  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "homeassistant.components.energy.data", module)
    return energy_manager_stub


@pytest.fixture
def forecast_solar_entry(hass, monkeypatch) -> Any:
    """Install a fake Forecast.Solar config entry and helper the adapter can find."""

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain="forecast_solar",
        title="Fake Forecast.Solar",
        entry_id="fake-forecast-solar-entry-id",
        data={"latitude": 50.0, "longitude": 30.0},
        options={"inverter_size": 5000, "damping_morning": 0.0, "damping_evening": 0.0},
    )
    entry.add_to_hass(hass)

    class _FakePlane:
        subentry_id = "plane-1"
        data = {"declination": 33, "azimuth": 138, "modules_power": 5360}

    class _FakeRuntime:
        def __init__(self):
            self.data = types.SimpleNamespace(
                wh_period={
                    datetime(2026, 8, 12, hour, tzinfo=UTC): float(hour * 10) for hour in range(24)
                }
            )
            self.last_update_success = True
            self._listeners = []

        def async_add_listener(self, listener):
            self._listeners.append(listener)
            return lambda: self._listeners.remove(listener)

    runtime = _FakeRuntime()
    entry.runtime_data = runtime

    def _subentries_of_type(kind):
        return [_FakePlane()] if kind == "plane" else []

    entry.get_subentries_of_type = _subentries_of_type  # type: ignore[assignment]

    forecast_energy = types.ModuleType("homeassistant.components.forecast_solar.energy")

    async def _async_get_solar_forecast(_hass, _entry_id):
        return {"wh_hours": {dt.isoformat(): value for dt, value in runtime.data.wh_period.items()}}

    forecast_energy.async_get_solar_forecast = _async_get_solar_forecast  # type: ignore[attr-defined]
    monkeypatch.setitem(
        sys.modules, "homeassistant.components.forecast_solar.energy", forecast_energy
    )

    return entry


@pytest.fixture
def sa_config_entry(hass, forecast_solar_entry, energy_manager_installed, actual_pv_states):
    """Return a MockConfigEntry for Solar Analytics ready to add to hass."""

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    return MockConfigEntry(
        domain="solar_analytics",
        title="Solar Analytics",
        entry_id="sa-entry-1",
        unique_id="solar_analytics",
        version=5,
        data={
            "native_forecast_entry_id": forecast_solar_entry.entry_id,
            "actual_power_entity": "sensor.example_pv_power",
            "actual_energy_today_entity": "sensor.example_pv_energy",
            "time_zone": "Europe/Berlin",
            "morning_snapshot_hour": 6,
            "day_ahead_snapshot_hour": 23,
        },
    )
