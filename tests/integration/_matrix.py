"""Test-side adapter over the packaged qBittorrent compatibility manifest.

Re-exports `QbitMatrixEntry` as `MatrixEntry` and adds loader
convenience wrappers used by the Docker harness and CI, translating
`qbit_core.qbit.compatibility` errors into the exception types this
test suite expects.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

from qbit_core.qbit.compatibility import (
    CompatibilityManifestError,
    load_compatibility_evidence,
    parse_manifest_text,
)
from qbit_core.qbit.compatibility import (
    QbitMatrixEntry as MatrixEntry,
)

__all__ = [
    "MATRIX_MANIFEST_PATH",
    "MatrixEntry",
    "load_matrix",
    "load_matrix_entry",
    "latest_matrix_entry",
]

# Test-only convenience: the real, on-disk location of the packaged
# manifest -- used by tests that assert the file is untouched by a run
# (e.g. the freshness checker), never as an alternate loading path for
# production code.
MATRIX_MANIFEST_PATH = Path(
    str(
        importlib.resources.files("qbit_core.data").joinpath(
            "qbittorrent-matrix.toml"
        )
    )
)


def load_matrix(path: Path | None = None) -> list[MatrixEntry]:
    """Load every matrix entry, in file order.

    With no `path`, delegates entirely to the packaged loader (the
    ordinary case for every real consumer). `path` exists only so
    sabotage tests can exercise the same parsing/validation rules
    against arbitrary text (missing, empty, malformed, duplicate)
    without touching the packaged manifest.
    """
    try:
        if path is None:
            return list(load_compatibility_evidence().entries)
        text = Path(path).read_text(encoding="utf-8")
        return list(parse_manifest_text(text))
    except CompatibilityManifestError as error:
        raise ValueError(str(error)) from error


def latest_matrix_entry() -> MatrixEntry:
    """Return the matrix entry with the highest `expected_version`.

    Computed from the manifest, never a second hardcoded "latest" id.
    """
    try:
        return load_compatibility_evidence().latest()
    except CompatibilityManifestError as error:
        raise ValueError(str(error)) from error


def load_matrix_entry(matrix_id: str) -> MatrixEntry:
    """Load exactly one matrix entry by its stable id.

    Raises `KeyError` (not a silent `None`) when `matrix_id` is unknown --
    the harness must fail closed on a typo'd or removed matrix id, never
    fall back to "run against whatever is available".
    """
    try:
        evidence = load_compatibility_evidence()
    except CompatibilityManifestError as error:
        raise ValueError(str(error)) from error
    return evidence.entry(matrix_id)
