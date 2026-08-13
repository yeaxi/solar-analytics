"""Assert strings.json and locale files share the same key tree.

AGENTS.md requires every user-visible key in ``strings.json`` to have
matching entries in ``translations/en.json`` and ``translations/uk.json``.
These tests walk the JSON trees without importing Home Assistant.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "solar_analytics"


def _key_paths(value: Any, prefix: str = "") -> set[str]:
    """Return dotted paths for every leaf in a nested JSON object."""

    if isinstance(value, dict):
        paths: set[str] = set()
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths |= _key_paths(child, path)
        return paths
    if isinstance(value, list):
        paths = set()
        for index, child in enumerate(value):
            path = f"{prefix}[{index}]" if prefix else f"[{index}]"
            paths |= _key_paths(child, path)
        return paths
    return {prefix} if prefix else set()


def _load_keys(relative: str) -> set[str]:
    payload = json.loads((COMPONENT / relative).read_text(encoding="utf-8"))
    return _key_paths(payload)


def test_translation_key_sets_match_strings() -> None:
    """English and Ukrainian locale files must cover every strings.json key."""

    strings_keys = _load_keys("strings.json")
    en_keys = _load_keys("translations/en.json")
    uk_keys = _load_keys("translations/uk.json")

    assert strings_keys, "strings.json must not be empty"
    assert en_keys == strings_keys, (
        "translations/en.json key set differs from strings.json:\n"
        f"  missing: {sorted(strings_keys - en_keys)}\n"
        f"  extra: {sorted(en_keys - strings_keys)}"
    )
    assert uk_keys == strings_keys, (
        "translations/uk.json key set differs from strings.json:\n"
        f"  missing: {sorted(strings_keys - uk_keys)}\n"
        f"  extra: {sorted(uk_keys - strings_keys)}"
    )
