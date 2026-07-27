"""Load the qBittorrent Docker version matrix manifest.

The single source of truth for matrix entries is
`tests/integration/qbittorrent-matrix.toml`. Nothing under
`tests/integration/` or `Makefile`/CI may hardcode a second copy of an
image tag, digest, or expected version -- read it from here instead.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

MATRIX_MANIFEST_PATH = Path(__file__).parent / "qbittorrent-matrix.toml"


@dataclass(frozen=True)
class MatrixEntry:
    """One qBittorrent Docker matrix entry."""

    id: str
    image_repository: str
    image_tag: str
    image_digest: str
    release_line: str
    expected_version: str
    expected_web_api_version: str
    webui_port: int
    capabilities: tuple[str, ...]
    limitations: str
    provenance: str

    @property
    def image_reference(self) -> str:
        """Return the pinned `repository:tag` reference for `docker run`."""
        return f"{self.image_repository}:{self.image_tag}"

    @property
    def image_reference_with_digest(self) -> str:
        """Return the immutable `repository@sha256:...` reference."""
        return f"{self.image_repository}@{self.image_digest}"

    def has_capability(self, capability: str) -> bool:
        """Return whether this entry declares a given test capability."""
        return capability in self.capabilities


def load_matrix(path: Path = MATRIX_MANIFEST_PATH) -> list[MatrixEntry]:
    """Load every matrix entry from the manifest, in file order."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    entries = [_parse_entry(item) for item in raw.get("entry", [])]

    ids = [entry.id for entry in entries]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate matrix entry id in {path}: {ids}")

    return entries


def load_matrix_entry(
    matrix_id: str, path: Path = MATRIX_MANIFEST_PATH
) -> MatrixEntry:
    """Load exactly one matrix entry by its stable id.

    Raises `KeyError` (not a silent `None`) when `matrix_id` is unknown --
    the harness must fail closed on a typo'd or removed matrix id, never
    fall back to "run against whatever is available".
    """
    for entry in load_matrix(path):
        if entry.id == matrix_id:
            return entry

    known = ", ".join(entry.id for entry in load_matrix(path))
    raise KeyError(
        f"unknown qBittorrent matrix id {matrix_id!r}; known ids: {known}"
    )


def _parse_entry(item: dict) -> MatrixEntry:
    return MatrixEntry(
        id=item["id"],
        image_repository=item["image_repository"],
        image_tag=item["image_tag"],
        image_digest=item["image_digest"],
        release_line=item["release_line"],
        expected_version=item["expected_version"],
        expected_web_api_version=item["expected_web_api_version"],
        webui_port=int(item["webui_port"]),
        capabilities=tuple(item["capabilities"]),
        limitations=item["limitations"],
        provenance=item["provenance"],
    )
