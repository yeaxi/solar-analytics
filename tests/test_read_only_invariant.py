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
    return sorted(COMPONENT.rglob("*.py"))


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
        (
            re.compile(r"\basync_import_statistics\("),
            "Solar Analytics reads the Recorder; it must never write statistics into it.",
        ),
        (
            re.compile(r"\basync_add_external_statistics\("),
            "Solar Analytics must not publish external statistics to the Recorder.",
        ),
        (
            re.compile(r"\basync_adjust_statistics\("),
            "Solar Analytics must not adjust recorded statistics.",
        ),
        (
            re.compile(r"\bhass\.states\.async_set\("),
            "Solar Analytics publishes entities; it must not write states directly.",
        ),
        (
            re.compile(r"\bhass\.states\.async_remove\("),
            "Solar Analytics must not remove states belonging to other integrations.",
        ),
        (
            re.compile(r"home-assistant_v2\.db"),
            "A second connection to the live Recorder database is a WAL and locking hazard; "
            "read the Recorder through its own API instead.",
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


def test_sqlite3_stays_local_to_the_integrations_own_store() -> None:
    """Solar Analytics' own SQLite store is legitimate; a second Recorder connection is not."""

    import_pattern = re.compile(r"^\s*(import sqlite3|from sqlite3 import)\b", re.MULTILINE)
    importers = {path.name for path in _source_files() if import_pattern.search(_source_text(path))}
    assert importers == {"storage_v2.py"}, (
        f"only storage_v2.py may open SQLite databases; found: {sorted(importers)}"
    )


def test_manifest_declares_dependencies_and_platinum_scale() -> None:
    """Sanity-check manifest.json for a couple of platinum-relevant claims."""

    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert "energy" in manifest["dependencies"]
    # Forecast.Solar is only one of several possible forecast providers, so it
    # is an ordering hint (``after_dependencies``) rather than a hard
    # dependency; the integration must load for Solcast/entity users who never
    # configure Forecast.Solar.
    assert "forecast_solar" not in manifest["dependencies"]
    assert "forecast_solar" in manifest["after_dependencies"]
    # Recorder is default-enabled, so it is an ordering hint rather than a hard
    # dependency: the integration still loads (and says so) without it.
    assert "recorder" in manifest["after_dependencies"]
    assert "recorder" not in manifest["dependencies"]
    assert manifest["quality_scale"] == "platinum"
    assert manifest["config_flow"] is True
    assert manifest["iot_class"] == "local_push"
    assert manifest["issue_tracker"].startswith("https://")


def test_pyproject_version_matches_manifest() -> None:
    """Keep pyproject.toml version in lockstep with the shipping manifest."""

    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == manifest["version"]
