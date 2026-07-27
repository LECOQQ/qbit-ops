"""Load payload fixtures with their declared provenance metadata.

Not a test module itself (no `test_` prefix) -- the shared loader every
`test_*_payloads.py` file in this directory imports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).parent / "fixtures"

_VALID_TRUST_LEVELS = frozenset(
    {"synthetic", "official-example", "captured-container", "captured-instance"}
)


@dataclass(frozen=True)
class Fixture:
    """One loaded fixture: its payload plus declared provenance metadata."""

    name: str
    category: str
    payload: Any
    trust: str
    description: str
    qbittorrent_version: str | None
    web_api_version: str | None
    qbittorrent_api_version: str | None
    sanitization: str
    fields_removed: tuple[str, ...]
    limitations: str


def load_fixture(category: str, name: str) -> Fixture:
    """Load one fixture JSON file, validating its provenance metadata shape.

    `category` is the subdirectory (`torrents`, `trackers`, `transfer`,
    `application`); `name` is the filename without `.json`.
    """
    path = FIXTURES_DIR / category / f"{name}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))

    meta = raw["_meta"]
    trust = meta["trust"]
    if trust not in _VALID_TRUST_LEVELS:
        raise ValueError(
            f"{path}: unknown trust level {trust!r}, expected one of "
            f"{sorted(_VALID_TRUST_LEVELS)}"
        )

    return Fixture(
        name=name,
        category=category,
        payload=raw["payload"],
        trust=trust,
        description=meta["description"],
        qbittorrent_version=meta.get("qbittorrent_version"),
        web_api_version=meta.get("web_api_version"),
        qbittorrent_api_version=meta.get("qbittorrent_api_version"),
        sanitization=meta.get("sanitization", ""),
        fields_removed=tuple(meta.get("fields_removed", ())),
        limitations=meta.get("limitations", ""),
    )


def list_fixtures(category: str) -> list[str]:
    """Return every fixture name (without `.json`) in one category."""
    category_dir = FIXTURES_DIR / category
    return sorted(path.stem for path in category_dir.glob("*.json"))


def load_all_fixtures() -> list[Fixture]:
    """Load every fixture in every category.

    Used by the provenance and security scan tests.
    """
    fixtures: list[Fixture] = []
    for category_dir in sorted(FIXTURES_DIR.iterdir()):
        if not category_dir.is_dir():
            continue
        for name in list_fixtures(category_dir.name):
            fixtures.append(load_fixture(category_dir.name, name))
    return fixtures
