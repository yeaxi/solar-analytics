"""Hermetic tests for scripts/release.py.

The live repository is one fixture: check without a tag must pass on the
tree CI would ship. Every mutating case runs against a temporary copy.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("solar_analytics_release", ROOT / "scripts" / "release.py")
assert _SPEC is not None and _SPEC.loader is not None
release = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(release)

MINIMAL_CHANGELOG = """# Changelog

## [Unreleased]

### Added
- A user-visible change.

## [1.0.0] - 2026-01

First release.
"""

MINIMAL_PYPROJECT = """[project]
name = "solar-analytics"
version = "1.0.0"
"""

MINIMAL_MANIFEST = {
    "domain": "solar_analytics",
    "name": "Solar Analytics",
    "version": "1.0.0",
}


def _write_tree(
    tmp_path: Path,
    *,
    changelog: str = MINIMAL_CHANGELOG,
    pyproject: str = MINIMAL_PYPROJECT,
    manifest: dict[str, object] | None = None,
) -> Path:
    component = tmp_path / "custom_components" / "solar_analytics"
    component.mkdir(parents=True)
    (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    payload = MINIMAL_MANIFEST if manifest is None else manifest
    (component / "manifest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return tmp_path


def test_live_tree_check_passes_without_tag() -> None:
    assert release.collect_check_errors(ROOT, tag=None) == []


def test_live_changelog_roundtrip() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert release.render_changelog(release.parse_changelog(text)) == text


def test_check_missing_unreleased(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        changelog="# Changelog\n\n## [1.0.0] - 2026-01\n\n- Shipped.\n",
    )
    errors = release.collect_check_errors(tmp_path)
    assert any("Unreleased" in error for error in errors)


def test_check_missing_version_heading(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        changelog="# Changelog\n\n## [Unreleased]\n\n- Pending.\n",
    )
    errors = release.collect_check_errors(tmp_path)
    assert any("## [1.0.0]" in error for error in errors)


def test_check_manifest_pyproject_mismatch(tmp_path: Path) -> None:
    _write_tree(tmp_path, pyproject='[project]\nversion = "9.9.9"\n')
    errors = release.collect_check_errors(tmp_path)
    assert any("9.9.9" in error for error in errors)


def test_check_tag_mismatch(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    errors = release.collect_check_errors(tmp_path, tag="v2.0.0")
    assert any("v2.0.0" in error for error in errors)


def test_check_tag_v_prefix_matches(tmp_path: Path) -> None:
    changelog = """# Changelog

## [Unreleased]

## [1.0.0] - 2026-01

- Shipped.
"""
    _write_tree(tmp_path, changelog=changelog)
    assert release.collect_check_errors(tmp_path, tag="v1.0.0") == []
    assert release.collect_check_errors(tmp_path, tag="1.0.0") == []


def test_check_tag_rejects_empty_release_section(tmp_path: Path) -> None:
    changelog = """# Changelog

## [Unreleased]

## [1.0.0] - 2026-01

"""
    _write_tree(tmp_path, changelog=changelog)
    errors = release.collect_check_errors(tmp_path, tag="v1.0.0")
    assert any("has no entries" in error for error in errors)


def test_check_tag_rejects_nonempty_unreleased(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    errors = release.collect_check_errors(tmp_path, tag="v1.0.0")
    assert any("Unreleased" in error for error in errors)


def test_notes_extracts_section_and_strips_v_prefix(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    notes = release.extract_notes(tmp_path, version=release.version_from_tag("v1.0.0"))
    assert "First release." in notes
    assert "## [1.0.0]" not in notes


def test_prepare_moves_unreleased_and_bumps(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    release.prepare_release(tmp_path, "1.1.0", today=date(2026, 8, 14))
    changelog = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [Unreleased]\n\n## [1.1.0] - 2026-08-14\n" in changelog
    assert "- A user-visible change." in changelog
    assert changelog.index("## [1.1.0]") < changelog.index("## [1.0.0]")
    unreleased = release.section_named(release.parse_changelog(changelog), "Unreleased")
    assert unreleased is not None
    assert not release.has_release_notes(unreleased.body)
    manifest = json.loads(
        (tmp_path / "custom_components" / "solar_analytics" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["version"] == "1.1.0"
    assert 'version = "1.1.0"' in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert release.collect_check_errors(tmp_path, tag="v1.1.0") == []


def test_prepare_rejects_empty_unreleased(tmp_path: Path) -> None:
    changelog = """# Changelog

## [Unreleased]

## [1.0.0] - 2026-01

- Shipped.
"""
    _write_tree(tmp_path, changelog=changelog)
    with pytest.raises(release.ReleaseError, match="no entries"):
        release.prepare_release(tmp_path, "1.1.0", today=date(2026, 8, 14))


def test_prepare_rejects_invalid_version(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    with pytest.raises(release.ReleaseError, match="MAJOR.MINOR.PATCH"):
        release.prepare_release(tmp_path, "1.1")
    with pytest.raises(release.ReleaseError, match="MAJOR.MINOR.PATCH"):
        release.prepare_release(tmp_path, "v1.1.0")


def test_prepare_rejects_existing_heading(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    with pytest.raises(release.ReleaseError, match="already has"):
        release.prepare_release(tmp_path, "1.0.0", today=date(2026, 8, 14))


def test_main_check_and_notes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    changelog = """# Changelog

## [Unreleased]

## [1.0.0] - 2026-01

- Shipped.
"""
    _write_tree(tmp_path, changelog=changelog)
    assert release.main(["check", "--root", str(tmp_path), "--tag", "v1.0.0"]) == 0
    assert release.main(["notes", "--root", str(tmp_path), "--tag", "v1.0.0"]) == 0
    captured = capsys.readouterr()
    assert "- Shipped." in captured.out
    assert release.main(["check", "--root", str(tmp_path), "--tag", "v9.9.9"]) == 1
    err = capsys.readouterr().err
    assert "v9.9.9" in err


def test_main_defaults_to_check(tmp_path: Path) -> None:
    changelog = """# Changelog

## [Unreleased]

## [1.0.0] - 2026-01

- Shipped.
"""
    _write_tree(tmp_path, changelog=changelog)
    assert release.main(["--root", str(tmp_path)]) == 0


def test_version_from_tag_leaves_non_semver_prefix() -> None:
    assert release.version_from_tag("v1.2.3") == "1.2.3"
    assert release.version_from_tag("1.2.3") == "1.2.3"
    assert release.version_from_tag("vnext") == "vnext"
