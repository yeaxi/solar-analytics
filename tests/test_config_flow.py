"""Coverage for the config-flow forecast-source selector and validation.

The config flow imports Home Assistant selector helpers and voluptuous, neither
of which is installed in the test environment, so this module stubs just enough
of both to import and exercise the pure validation logic. The stubs record the
selector configuration so the schema can be asserted without a running HA.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "solar_analytics"


class _RecordingConfig:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


class _RecordingSelector:
    def __init__(self, config=None) -> None:
        self.config = config


class _Optional:
    def __init__(self, key, default=None) -> None:
        self.schema = key
        self.default = default


class _Schema:
    def __init__(self, schema) -> None:
        self.schema = schema


def _install_config_flow_stub(platforms, *, raise_import: bool = False) -> None:
    ha = types.ModuleType("homeassistant")
    ha_const = types.ModuleType("homeassistant.const")
    ha_const.__version__ = "2026.7.4"

    config_entries = types.ModuleType("homeassistant.config_entries")

    class _ConfigFlow:
        def __init_subclass__(cls, **kwargs) -> None:
            super().__init_subclass__()

    config_entries.ConfigFlow = _ConfigFlow
    config_entries.OptionsFlow = object
    config_entries.ConfigEntry = object

    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    core.callback = lambda func: func

    data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
    data_entry_flow.FlowResult = dict

    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    selector = types.ModuleType("homeassistant.helpers.selector")
    for name in (
        "ConfigEntrySelector",
        "EntitySelector",
        "NumberSelector",
        "SelectSelector",
        "TextSelector",
    ):
        setattr(selector, name, _RecordingSelector)
    for name in (
        "ConfigEntrySelectorConfig",
        "EntitySelectorConfig",
        "NumberSelectorConfig",
        "SelectSelectorConfig",
        "TextSelectorConfig",
    ):
        setattr(selector, name, _RecordingConfig)
    selector.NumberSelectorMode = types.SimpleNamespace(BOX="box")
    selector.SelectSelectorMode = types.SimpleNamespace(DROPDOWN="dropdown")
    selector.TextSelectorType = types.SimpleNamespace(TEXT="text")

    components = types.ModuleType("homeassistant.components")
    components.__path__ = []
    energy_pkg = types.ModuleType("homeassistant.components.energy")
    energy_pkg.__path__ = []
    energy_ws = types.ModuleType("homeassistant.components.energy.websocket_api")

    async def async_get_energy_platforms(hass):
        if raise_import:
            raise RuntimeError("registry unavailable")
        return dict(platforms)

    energy_ws.async_get_energy_platforms = async_get_energy_platforms

    vol = types.ModuleType("voluptuous")
    vol.Schema = _Schema
    vol.Optional = _Optional

    sys.modules.update(
        {
            "homeassistant": ha,
            "homeassistant.const": ha_const,
            "homeassistant.config_entries": config_entries,
            "homeassistant.core": core,
            "homeassistant.data_entry_flow": data_entry_flow,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.selector": selector,
            "homeassistant.components": components,
            "homeassistant.components.energy": energy_pkg,
            "homeassistant.components.energy.websocket_api": energy_ws,
            "voluptuous": vol,
        }
    )
    if raise_import:
        # Force the executor import to fail as well.
        sys.modules.pop("homeassistant.components.energy.websocket_api", None)

    parent = types.ModuleType("custom_components")
    parent.__path__ = [str(COMPONENT.parent)]
    package = types.ModuleType("custom_components.solar_analytics")
    package.__path__ = [str(COMPONENT)]
    sys.modules["custom_components"] = parent
    sys.modules["custom_components.solar_analytics"] = package


class FakeEntries:
    def __init__(self, entries) -> None:
        self._entries = entries

    def async_get_entry(self, entry_id):
        return self._entries.get(entry_id)


class FakeHass:
    def __init__(self, entries=None) -> None:
        self.config = types.SimpleNamespace(time_zone="UTC")
        self.config_entries = FakeEntries(entries or {})

    async def async_add_executor_job(self, target, *args):
        return target(*args)


def _load(platforms=None, *, raise_import: bool = False):
    _install_config_flow_stub(platforms or {}, raise_import=raise_import)
    return importlib.import_module("custom_components.solar_analytics.config_flow")


def _schema_selectors(module):
    schema = module._user_schema(FakeHass(), None)
    return {marker.schema: selector for marker, selector in schema.schema.items()}


def test_forecast_source_type_selector_is_translated() -> None:
    module = _load()
    selectors = _schema_selectors(module)
    config = selectors["forecast_source_type"].config
    assert config.kwargs["translation_key"] == "forecast_source_type"
    assert config.kwargs["options"] == ["energy_entry", "forecast_entity"]
    # No hardcoded English labels are passed for the option values.
    assert all(isinstance(option, str) for option in config.kwargs["options"])


def test_native_forecast_entry_selector_has_no_integration_filter() -> None:
    module = _load()
    selectors = _schema_selectors(module)
    config = selectors["native_forecast_entry_id"].config
    assert "integration" not in config.kwargs


def test_validate_native_entry_accepts_energy_forecast_domain() -> None:
    module = _load(platforms={"solcast_solar": lambda hass, entry_id: None})
    hass = FakeHass({"entry-1": types.SimpleNamespace(domain="solcast_solar")})
    assert asyncio.run(module._validate_native_entry(hass, "entry-1")) is True


def test_validate_native_entry_rejects_unrelated_domain() -> None:
    module = _load(platforms={"forecast_solar": lambda hass, entry_id: None})
    hass = FakeHass({"entry-1": types.SimpleNamespace(domain="hue")})
    assert asyncio.run(module._validate_native_entry(hass, "entry-1")) is False


def test_validate_native_entry_blank_is_autodetect() -> None:
    module = _load(platforms={"forecast_solar": lambda hass, entry_id: None})
    hass = FakeHass()
    assert asyncio.run(module._validate_native_entry(hass, None)) is True


def test_validate_native_entry_missing_entry_is_rejected() -> None:
    module = _load(platforms={"forecast_solar": lambda hass, entry_id: None})
    hass = FakeHass()
    assert asyncio.run(module._validate_native_entry(hass, "does-not-exist")) is False


def test_validate_native_entry_fails_open_when_registry_unavailable() -> None:
    module = _load(raise_import=True)
    hass = FakeHass({"entry-1": types.SimpleNamespace(domain="some_provider")})
    # The registry cannot be resolved, so an existing entry is accepted; the
    # read-only runtime adapter still fails closed on an unusable source.
    assert asyncio.run(module._validate_native_entry(hass, "entry-1")) is True
