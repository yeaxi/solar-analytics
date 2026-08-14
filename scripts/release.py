#!/usr/bin/env python3
"""Check, extract, or prepare a Solar Analytics release.

The shipping version is the string in
``custom_components/solar_analytics/manifest.json``. ``pyproject.toml`` and
``CHANGELOG.md`` must agree with it. A GitHub tag may add a leading ``v``.

Read-only:

    python scripts/release.py check [--tag vX.Y.Z]
    python scripts/release.py notes [--tag vX.Y.Z]

Mutating (local, for a version-bump PR):

    python scripts/release.py prepare X.Y.Z
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_RELATIVE = Path("custom_components") / "solar_analytics" / "manifest.json"
CHANGELOG_NAME = "CHANGELOG.md"
PYPROJECT_NAME = "pyproject.toml"
UNRELEASED = "Unreleased"
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
HEADING = re.compile(r"^## \[([^\]]+)\](?: - (\S+))?[ \t]*$", re.MULTILINE)
PYPROJECT_VERSION_LINE = re.compile(r'^version\s*=\s*"[^"]+"', re.MULTILINE)
LIST_ITEM = re.compile(r"^-\s+\S")


class ReleaseError(Exception):
    """A release check or prepare step failed."""


@dataclass(frozen=True, kw_only=True)
class ChangelogSection:
    title: str
    date: str | None
    body: str


@dataclass(frozen=True, kw_only=True)
class Changelog:
    preamble: str
    sections: tuple[ChangelogSection, ...]


def version_from_tag(tag: str) -> str:
    """Strip a leading ``v`` when the rest is MAJOR.MINOR.PATCH."""

    if tag.startswith("v") and SEMVER.fullmatch(tag[1:]) is not None:
        return tag[1:]
    return tag


def parse_changelog(text: str) -> Changelog:
    """Split a Keep a Changelog file into preamble plus ``## [title]`` sections."""

    matches = list(HEADING.finditer(text))
    if not matches:
        raise ReleaseError("CHANGELOG.md has no ## [version] headings")
    preamble = text[: matches[0].start()]
    sections: list[ChangelogSection] = []
    for index, match in enumerate(matches):
        start = match.end()
        if start < len(text) and text[start] == "\n":
            start += 1
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append(
            ChangelogSection(title=match.group(1), date=match.group(2), body=text[start:end])
        )
    return Changelog(preamble=preamble, sections=tuple(sections))


def render_changelog(changelog: Changelog) -> str:
    """Serialize a parsed changelog, preserving each section body byte-for-byte."""

    parts = [changelog.preamble]
    for section in changelog.sections:
        heading = f"## [{section.title}]"
        if section.date is not None:
            heading = f"{heading} - {section.date}"
        parts.append(f"{heading}\n")
        parts.append(section.body)
    return "".join(parts)


def section_named(changelog: Changelog, title: str) -> ChangelogSection | None:
    for section in changelog.sections:
        if section.title == title:
            return section
    return None


def has_release_notes(body: str) -> bool:
    """True when the section contains at least one markdown list item."""

    return any(LIST_ITEM.match(line) for line in body.splitlines())


def manifest_version(root: Path) -> str:
    path = root / MANIFEST_RELATIVE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"cannot read {MANIFEST_RELATIVE}: {exc}") from exc
    if not isinstance(payload, dict) or "version" not in payload:
        raise ReleaseError(f"{MANIFEST_RELATIVE} has no version")
    return str(payload["version"])


def pyproject_version(root: Path) -> str:
    path = root / PYPROJECT_NAME
    try:
        payload: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseError(f"cannot read {PYPROJECT_NAME}: {exc}") from exc
    try:
        return str(payload["project"]["version"])
    except KeyError as exc:
        raise ReleaseError(f"{PYPROJECT_NAME} has no project.version") from exc


def load_changelog(root: Path) -> Changelog:
    path = root / CHANGELOG_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReleaseError(f"cannot read {CHANGELOG_NAME}: {exc}") from exc
    return parse_changelog(text)


def collect_check_errors(root: Path, *, tag: str | None = None) -> list[str]:
    """Return every version/changelog inconsistency; empty means the tree is releasable."""

    errors: list[str] = []
    try:
        shipped = manifest_version(root)
        packaged = pyproject_version(root)
        changelog = load_changelog(root)
    except ReleaseError as exc:
        return [str(exc)]

    if shipped != packaged:
        errors.append(
            f"{MANIFEST_RELATIVE} version {shipped!r} != {PYPROJECT_NAME} version {packaged!r}"
        )
    if section_named(changelog, UNRELEASED) is None:
        errors.append(f"{CHANGELOG_NAME} has no ## [{UNRELEASED}] heading")
    if section_named(changelog, shipped) is None:
        errors.append(f"{CHANGELOG_NAME} has no ## [{shipped}] heading")

    if tag is None:
        return errors

    expected = version_from_tag(tag)
    if expected != shipped:
        errors.append(f"tag {tag!r} does not match {MANIFEST_RELATIVE} version {shipped!r}")
    unreleased = section_named(changelog, UNRELEASED)
    if unreleased is not None and has_release_notes(unreleased.body):
        errors.append(
            f"{CHANGELOG_NAME} still has entries under ## [{UNRELEASED}]; "
            "run `python scripts/release.py prepare X.Y.Z` before tagging"
        )
    released = section_named(changelog, expected)
    if released is None:
        errors.append(f"{CHANGELOG_NAME} has no ## [{expected}] heading for tag {tag!r}")
    elif not released.body.strip():
        errors.append(f"{CHANGELOG_NAME} section ## [{expected}] has no entries")
    return errors


def check_release(root: Path, *, tag: str | None = None) -> None:
    errors = collect_check_errors(root, tag=tag)
    if errors:
        raise ReleaseError("\n".join(errors))


def extract_notes(root: Path, *, version: str) -> str:
    changelog = load_changelog(root)
    section = section_named(changelog, version)
    if section is None:
        raise ReleaseError(f"{CHANGELOG_NAME} has no ## [{version}] heading")
    if not section.body.strip():
        raise ReleaseError(f"{CHANGELOG_NAME} section ## [{version}] has no entries")
    return section.body.strip() + "\n"


def _write_manifest_version(root: Path, version: str) -> None:
    path = root / MANIFEST_RELATIVE
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["version"] = version
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_pyproject_version(root: Path, version: str) -> None:
    path = root / PYPROJECT_NAME
    text = path.read_text(encoding="utf-8")
    updated, count = PYPROJECT_VERSION_LINE.subn(f'version = "{version}"', text, count=1)
    if count != 1:
        raise ReleaseError(f"{PYPROJECT_NAME} must contain exactly one version assignment")
    path.write_text(updated, encoding="utf-8")


def prepare_release(root: Path, version: str, *, today: date | None = None) -> None:
    """Move Unreleased notes under ``version`` and bump the two version files."""

    if SEMVER.fullmatch(version) is None:
        raise ReleaseError(f"version {version!r} is not MAJOR.MINOR.PATCH")
    changelog = load_changelog(root)
    unreleased = section_named(changelog, UNRELEASED)
    if unreleased is None:
        raise ReleaseError(f"{CHANGELOG_NAME} has no ## [{UNRELEASED}] heading")
    if section_named(changelog, version) is not None:
        raise ReleaseError(f"{CHANGELOG_NAME} already has ## [{version}]")
    if not has_release_notes(unreleased.body):
        raise ReleaseError(f"## [{UNRELEASED}] has no entries to release")
    day = today if today is not None else datetime.now(UTC).date()
    emptied = ChangelogSection(title=UNRELEASED, date=None, body="\n")
    released = ChangelogSection(title=version, date=day.isoformat(), body=unreleased.body)
    rest = tuple(section for section in changelog.sections if section.title != UNRELEASED)
    updated = Changelog(preamble=changelog.preamble, sections=(emptied, released, *rest))
    (root / CHANGELOG_NAME).write_text(render_changelog(updated), encoding="utf-8")
    _write_manifest_version(root, version)
    _write_pyproject_version(root, version)


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--root", type=Path, default=ROOT, help="repository root (default: this repo)"
    )
    parser = argparse.ArgumentParser(
        description="Check, extract, or prepare a Solar Analytics release."
    )
    sub = parser.add_subparsers(dest="command")
    check = sub.add_parser("check", parents=[common], help="verify version files and changelog")
    check.add_argument("--tag", default=os.environ.get("RELEASE_TAG"), help="GitHub tag to compare")
    notes = sub.add_parser("notes", parents=[common], help="print changelog notes for a version")
    notes.add_argument(
        "--tag", default=os.environ.get("RELEASE_TAG"), help="GitHub tag whose notes to print"
    )
    notes.add_argument(
        "--version", default=None, help="explicit version; overrides --tag and the manifest"
    )
    prepare = sub.add_parser(
        "prepare", parents=[common], help="move Unreleased notes and bump version files"
    )
    prepare.add_argument("version", help="MAJOR.MINOR.PATCH to ship")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if not any(token in {"check", "notes", "prepare"} for token in raw):
        raw = ["check", *raw]
    args = _parser().parse_args(raw)
    root: Path = args.root
    try:
        if args.command == "check":
            check_release(root, tag=args.tag)
            return 0
        if args.command == "notes":
            if args.version is not None:
                version = args.version
            elif args.tag is not None:
                version = version_from_tag(args.tag)
            else:
                version = manifest_version(root)
            sys.stdout.write(extract_notes(root, version=version))
            return 0
        if args.command == "prepare":
            prepare_release(root, args.version)
            sys.stdout.write(
                f"moved Unreleased -> [{args.version}]\n"
                f"{MANIFEST_RELATIVE} version -> {args.version}\n"
                f"{PYPROJECT_NAME} version -> {args.version}\n"
            )
            return 0
    except ReleaseError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stderr.write(f"unknown command {args.command!r}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
