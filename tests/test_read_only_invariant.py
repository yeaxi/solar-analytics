"""Static assertions that the shipping integration stays read-only.

AGENTS.md and the config flow both promise Solar Analytics never calls
Home Assistant services, never triggers refreshes on other integrations,
and never opens network sockets to a provider. These tests enforce that
statically by grepping the shipping source under
``custom_components/solar_analytics/``. They intentionally
run without importing Home Assistant so they cannot be silenced by a stub.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "solar_analytics"


def _source_files() -> list[Path]:
    return sorted(COMPONENT.glob("*.py"))


def _source_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("forbidden", "reason"),
    [
        (
            re.compile(r"\bhass\.services\.async_call\("),
            "Solar Analytics is read-only; it must not call Home Assistant services.",
        ),
        (
            re.compile(r"\basync_refresh\(\)"),
            "Solar Analytics must observe the native Forecast.Solar coordinator, not provoke it.",
        ),
        (
            re.compile(r"\bestimate\("),
            "Solar Analytics must never call the Forecast.Solar .estimate() network path.",
        ),
        (
            re.compile(r"\brequests\.(get|post|put|delete|patch|request)\("),
            "Solar Analytics must not perform blocking HTTP with `requests`.",
        ),
        (
            re.compile(r"\btime\.sleep\("),
            "Solar Analytics must not block the event loop with time.sleep().",
        ),
    ],
)
def test_read_only_invariant_forbids_pattern(forbidden: re.Pattern[str], reason: str) -> None:
    hits: list[str] = []
    for path in _source_files():
        for lineno, line in enumerate(_source_text(path).splitlines(), 1):
            if forbidden.search(line):
                hits.append(
                    f"{path.relative_to(COMPONENT.parent.parent.parent)}:{lineno}: {line.strip()}"
                )
    assert not hits, reason + "\n" + "\n".join(hits)


def test_no_service_registration() -> None:
    """The integration must not register any services (no services.yaml, no register calls)."""

    services_yaml = COMPONENT / "services.yaml"
    assert not services_yaml.exists(), "services.yaml would declare user-callable actions"

    register_pattern = re.compile(r"\bhass\.services\.async_register\(")
    hits = [path.name for path in _source_files() if register_pattern.search(_source_text(path))]
    assert not hits, f"unexpected async_register call sites: {hits}"


def test_manifest_declares_dependencies_and_platinum_scale() -> None:
    """Sanity-check manifest.json for a couple of platinum-relevant claims."""

    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert "energy" in manifest["dependencies"]
    assert "forecast_solar" in manifest["dependencies"]
    assert manifest["quality_scale"] == "platinum"
    assert manifest["config_flow"] is True
    assert manifest["iot_class"] == "local_push"
    assert manifest["issue_tracker"].startswith("https://")


def test_pyproject_version_matches_manifest() -> None:
    """Keep pyproject.toml version in lockstep with the shipping manifest."""

    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == manifest["version"]
