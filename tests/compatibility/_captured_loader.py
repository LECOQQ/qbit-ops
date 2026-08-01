"""Discover and load `captured-container` fixtures.

Distinct from `_fixture_loader.py`'s `category/name.json` layout:
captured fixtures live one level deeper, under
`fixtures/captured-container/<matrix-id>/<name>.json`, one directory
per matrix entry (see `tests/integration/_capture.py`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CAPTURED_FIXTURES_ROOT = (
    Path(__file__).parent / "fixtures" / "captured-container"
)


@dataclass(frozen=True)
class CapturedFixture:
    """One loaded `captured-container` fixture.

    `matrix_id` is derived from the *directory* the file lives in --
    the ground truth. `declared_matrix_id` is whatever the file's own
    `_meta.matrix_id` claims; a test must cross-check the two, since a
    fixture could otherwise claim one version while its content
    silently came from another.
    """

    matrix_id: str
    declared_matrix_id: str | None
    name: str
    path: Path
    trust: str
    payload: Any
    qbittorrent_version: str | None
    web_api_version: str | None
    architecture: str | None
    qbittorrent_api_version: str | None
    image_reference: str | None
    image_digest: str | None
    sanitization: str
    fields_removed: tuple[str, ...]
    fields_normalized: tuple[str, ...]
    limitations: str
    expected_qbittorrent_version: str | None
    expected_web_api_version: str | None
    expected_architecture: str | None


def discover_matrix_ids() -> list[str]:
    """Return every matrix id with at least one captured fixture directory."""
    if not CAPTURED_FIXTURES_ROOT.is_dir():
        return []
    return sorted(
        p.name for p in CAPTURED_FIXTURES_ROOT.iterdir() if p.is_dir()
    )


def load_captured_fixtures(matrix_id: str) -> list[CapturedFixture]:
    """Load every captured fixture for one matrix id."""
    directory = CAPTURED_FIXTURES_ROOT / matrix_id
    fixtures = []
    for path in sorted(directory.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        meta = raw["_meta"]
        fixtures.append(
            CapturedFixture(
                matrix_id=matrix_id,
                declared_matrix_id=meta.get("matrix_id"),
                name=path.stem,
                path=path,
                trust=meta["trust"],
                payload=raw["payload"],
                qbittorrent_version=meta.get("qbittorrent_version"),
                web_api_version=meta.get("web_api_version"),
                architecture=meta.get("architecture"),
                qbittorrent_api_version=meta.get("qbittorrent_api_version"),
                image_reference=meta.get("image_reference"),
                image_digest=meta.get("image_digest"),
                sanitization=meta.get("sanitization", ""),
                fields_removed=tuple(meta.get("fields_removed", ())),
                fields_normalized=tuple(meta.get("fields_normalized", ())),
                limitations=meta.get("limitations", ""),
                expected_qbittorrent_version=meta.get(
                    "expected_qbittorrent_version"
                ),
                expected_web_api_version=meta.get("expected_web_api_version"),
                expected_architecture=meta.get("expected_architecture"),
            )
        )
    return fixtures


def load_all_captured_fixtures() -> list[CapturedFixture]:
    """Load every captured fixture across every matrix id."""
    fixtures: list[CapturedFixture] = []
    for matrix_id in discover_matrix_ids():
        fixtures.extend(load_captured_fixtures(matrix_id))
    return fixtures
