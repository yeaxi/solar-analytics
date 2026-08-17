"""Shared pytest fixtures for the Solar Analytics test suite.

The custom integration lives under ``custom_components/solar_analytics``.
Test modules import it either as ``custom_components.solar_analytics.<module>``
(with a fake ``homeassistant`` stack installed at test start) or, for the pure
helpers, as ``solar_analytics.<module>`` via the path shim below.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_COMPONENT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "solar_analytics"


def _register_solar_analytics_path_alias() -> None:
    """Expose the custom-component package under the historical ``solar_analytics`` name.

    Older tests (and any downstream consumer) import pure helpers as
    ``solar_analytics.native``/``solar_analytics.storage_v2``/``solar_analytics.v2_metrics``.
    The shipping code lives inside the custom component, so we register a
    ``solar_analytics`` alias that resolves to the same directory. This avoids
    keeping a byte-identical duplicate of every helper module at the repo root.
    """

    if "solar_analytics" in sys.modules:
        return

    package = types.ModuleType("solar_analytics")
    package.__path__ = [str(_COMPONENT_DIR)]
    sys.modules["solar_analytics"] = package

    for helper in (
        "native",
        "imported_actuals",
        "interval_watermark",
        "storage_v2",
        "v2_metrics",
        "entity_contract",
    ):
        module_path = _COMPONENT_DIR / f"{helper}.py"
        spec = importlib.util.spec_from_file_location(f"solar_analytics.{helper}", module_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"solar_analytics.{helper}"] = module
        spec.loader.exec_module(module)


def _install_homeassistant_stubs() -> None:
    """Install the minimal Home Assistant stack the component modules import.

    Home Assistant is not a test dependency, so the modules under test are
    imported against this stub. Anything a test needs to control (the recorder,
    the issue registry) is replaced per test through ``sys.modules``.
    """

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
    ha_helpers_ir = types.ModuleType("homeassistant.helpers.issue_registry")

    class _IssueSeverity:
        WARNING = "warning"
        ERROR = "error"

    ha_helpers_ir.IssueSeverity = _IssueSeverity
    ha_helpers_ir.async_create_issue = lambda *args, **kwargs: None
    ha_helpers_ir.async_delete_issue = lambda *args, **kwargs: None

    ha_helpers_event = types.ModuleType("homeassistant.helpers.event")
    ha_helpers_event.async_track_point_in_utc_time = lambda hass, action, when: lambda: None
    ha_helpers_event.async_track_state_change_event = (
        lambda hass, entity_ids, action: lambda: None
    )
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
            "homeassistant.helpers.issue_registry": ha_helpers_ir,
            "homeassistant.helpers.event": ha_helpers_event,
            "homeassistant.helpers.update_coordinator": ha_helpers_update,
        }
    )
    parent = types.ModuleType("custom_components")
    parent.__path__ = [str(_COMPONENT_DIR.parent)]
    package = types.ModuleType("custom_components.solar_analytics")
    package.__path__ = [str(_COMPONENT_DIR)]
    sys.modules["custom_components"] = parent
    sys.modules["custom_components.solar_analytics"] = package


def _load_component_module(name: str):
    """Import one component module by file path against the Home Assistant stub."""

    qualified = f"custom_components.solar_analytics.{name}"
    if qualified in sys.modules:
        return sys.modules[qualified]
    _install_homeassistant_stubs()
    spec = importlib.util.spec_from_file_location(qualified, _COMPONENT_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def coordinator_module():
    return _load_component_module("coordinator")


@pytest.fixture
def coordinator_shell(coordinator_module):
    """Build a coordinator that resolves its own methods but skips HA setup.

    The sync analytics passes need only the store and the analytics timezone.
    Bypassing ``__init__`` keeps them callable without a config entry, while
    real attribute lookup keeps helper-to-helper calls working.
    """

    def _make(*, store, time_zone):
        shell = object.__new__(coordinator_module.SolarAnalyticsCoordinator)
        shell.store = store
        shell.time_zone = time_zone
        return shell

    return _make


@pytest.fixture(scope="session")
def recorder_history_module():
    return _load_component_module("recorder_history")


_register_solar_analytics_path_alias()
