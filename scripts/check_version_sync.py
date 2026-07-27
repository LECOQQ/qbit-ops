#!/usr/bin/env python3
"""Verify that the declared release version is consistent across the repo.

Compares the canonical version in ``pyproject.toml`` against the Release
Please manifest (``.release-please-manifest.json``), and optionally against
a supplied Git tag. Standard library only: no network access, no installed
package metadata (``importlib.metadata`` reflects whatever was last
installed, which can predate the current Release PR -- see
``docs/RELEASE.md``).

Usage:
    python scripts/check_version_sync.py
    python scripts/check_version_sync.py --tag v0.3.0
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
MANIFEST_PATH = REPO_ROOT / ".release-please-manifest.json"
MANIFEST_ROOT_KEY = "."

TAG_PATTERN = re.compile(r"^v(\d+\.\d+\.\d+)$")


class VersionSyncError(Exception):
    """Raised for any actionable version-consistency failure."""


def _load_pyproject_version(path: Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise VersionSyncError(f"{path} not found.") from exc

    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise VersionSyncError(f"{path} is not valid TOML: {exc}") from exc

    project_version = data.get("project", {}).get("version")
    poetry_version = data.get("tool", {}).get("poetry", {}).get("version")

    if project_version and poetry_version and project_version != poetry_version:
        raise VersionSyncError(
            "Conflicting version declarations in pyproject.toml: "
            f"[project].version={project_version!r} != "
            f"[tool.poetry].version={poetry_version!r}. "
            "Exactly one canonical version declaration is allowed."
        )

    canonical = project_version or poetry_version
    if not canonical:
        raise VersionSyncError(
            f"{path} declares no version under [project] or [tool.poetry]."
        )

    commitizen_version = (
        data.get("tool", {}).get("commitizen", {}).get("version")
    )
    if commitizen_version and commitizen_version != canonical:
        raise VersionSyncError(
            "pyproject.toml [tool.commitizen].version "
            f"({commitizen_version!r}) disagrees with the canonical "
            f"version ({canonical!r}). The Release Please extra-files "
            "TOML updater for $.tool.commitizen.version may have failed "
            "or gone stale."
        )

    return canonical


def _load_manifest_version(path: Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise VersionSyncError(f"{path} not found.") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VersionSyncError(f"{path} is not valid JSON: {exc}") from exc

    if MANIFEST_ROOT_KEY not in data:
        raise VersionSyncError(
            f"{path} has no {MANIFEST_ROOT_KEY!r} root package entry."
        )

    return data[MANIFEST_ROOT_KEY]


def _parse_tag_version(tag: str) -> str:
    match = TAG_PATTERN.match(tag)
    if not match:
        raise VersionSyncError(
            f"Tag {tag!r} does not match the expected vX.Y.Z format."
        )
    return match.group(1)


def check(pyproject_path: Path, manifest_path: Path, tag: str | None) -> str:
    """Run all consistency checks and return a success message.

    Raises VersionSyncError on the first failing check.
    """
    pyproject_version = _load_pyproject_version(pyproject_path)
    manifest_version = _load_manifest_version(manifest_path)

    if pyproject_version != manifest_version:
        raise VersionSyncError(
            f"pyproject.toml version ({pyproject_version!r}) does not "
            f"match {manifest_path.name}[{MANIFEST_ROOT_KEY!r}] "
            f"({manifest_version!r})."
        )

    message = (
        f"OK: pyproject.toml and {manifest_path.name} agree on "
        f"version {pyproject_version}."
    )

    if tag is not None:
        tag_version = _parse_tag_version(tag)
        if tag_version != pyproject_version:
            raise VersionSyncError(
                f"Tag {tag!r} (version {tag_version!r}) does not match "
                f"the declared version ({pyproject_version!r})."
            )
        message += f" Tag {tag} matches."

    return message


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tag",
        default=None,
        help="Optional Git tag (vX.Y.Z) to check against the declared version.",
    )
    args = parser.parse_args(argv)

    try:
        message = check(PYPROJECT_PATH, MANIFEST_PATH, args.tag)
    except VersionSyncError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
