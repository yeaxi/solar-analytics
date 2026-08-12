"""Shared pytest fixtures for the Solar Analytics test suite.

The custom integration lives under ``custom_components/solar_analytics``.
Test modules import it either as ``custom_components.solar_analytics.<module>``
(with a fake ``homeassistant`` stack installed at test start) or, for the pure
helpers, as ``solar_analytics.<module>`` via the path shim below.
"""

from __future__ import annotations

import sys
from pathlib import Path

_COMPONENT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "solar_analytics"


def _register_solar_analytics_path_alias() -> None:
    """Expose the custom-component package under the historical ``solar_analytics`` name.

    Older tests (and any downstream consumer) import pure helpers as
    ``solar_analytics.native``/``solar_analytics.storage_v2``/``solar_analytics.v2_metrics``.
    The shipping code lives inside the custom component, so we register a
    ``solar_analytics`` alias that resolves to the same directory. This avoids
    keeping a byte-identical duplicate of every helper module at the repo root.
    """

    import importlib
    import types

    if "solar_analytics" in sys.modules:
        return

    package = types.ModuleType("solar_analytics")
    package.__path__ = [str(_COMPONENT_DIR)]
    sys.modules["solar_analytics"] = package

    for helper in ("native", "storage_v2", "v2_metrics", "entity_contract"):
        module_path = _COMPONENT_DIR / f"{helper}.py"
        spec = importlib.util.spec_from_file_location(f"solar_analytics.{helper}", module_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"solar_analytics.{helper}"] = module
        spec.loader.exec_module(module)


_register_solar_analytics_path_alias()
