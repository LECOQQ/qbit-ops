"""Load and validate the packaged qBittorrent compatibility evidence manifest.

The manifest (`qbit_ops/data/qbittorrent-matrix.toml`) is the single
executable source of truth for which exact qBittorrent versions
`qbit-ops` has been container-integration tested against -- see
`docs/COMPATIBILITY.md` §10. This module is the only production-side
parser of that file: it reads it via `importlib.resources` so it works
from an installed wheel, with no repository checkout, no
`Path(__file__)`-relative lookup, and no working-directory dependency.

Test-only code (the Docker harness under `tests/integration/`) adapts
`QbitMatrixEntry`/`CompatibilityEvidence` into its own runtime objects,
but must never re-parse the manifest itself -- see
`tests/integration/_matrix.py`.

Importing this module never reads or parses the manifest; parsing only
happens when `load_compatibility_evidence()` is called.
"""

from __future__ import annotations

import importlib.resources
import tomllib
from dataclasses import dataclass

from packaging.version import InvalidVersion, Version

_MANIFEST_PACKAGE = "qbit_ops.data"
_MANIFEST_FILENAME = "qbittorrent-matrix.toml"


class CompatibilityManifestError(RuntimeError):
    """Report that the packaged compatibility manifest is missing, unreadable,
    or malformed -- the caller must fail closed, never fall back to "no
    evidence" or "assume compatible"."""


@dataclass(frozen=True)
class QbitMatrixEntry:
    """One exact qBittorrent version the compatibility evidence covers."""

    id: str
    image_repository: str
    image_tag: str
    image_digest: str
    expected_architecture: str
    release_line: str
    expected_version: str
    expected_web_api_version: str
    webui_port: int
    capabilities: tuple[str, ...]
    limitations: str
    provenance: str

    @property
    def image_reference(self) -> str:
        """Return the `repository:tag` reference (mutable, for display only)."""
        return f"{self.image_repository}:{self.image_tag}"

    @property
    def image_reference_with_digest(self) -> str:
        """Return the immutable `repository@sha256:...` reference to run."""
        return f"{self.image_repository}@{self.image_digest}"

    def has_capability(self, capability: str) -> bool:
        """Return whether this entry declares a given test capability."""
        return capability in self.capabilities


@dataclass(frozen=True)
class CompatibilityEvidence:
    """Every validated entry from the packaged compatibility manifest."""

    entries: tuple[QbitMatrixEntry, ...]

    @property
    def ids(self) -> tuple[str, ...]:
        """Return every entry id, in manifest order."""
        return tuple(entry.id for entry in self.entries)

    def entry(self, matrix_id: str) -> QbitMatrixEntry:
        """Return exactly one entry by its stable id.

        Raises `KeyError` (never a silent `None`) on an unknown id --
        callers must fail closed on a typo'd or removed matrix id.
        """
        for entry in self.entries:
            if entry.id == matrix_id:
                return entry
        raise KeyError(
            f"unknown qBittorrent matrix id {matrix_id!r}; known ids: "
            f"{', '.join(self.ids)}"
        )

    def latest(self) -> QbitMatrixEntry:
        """Return the entry with the highest `expected_version`."""
        return max(
            self.entries,
            key=lambda entry: Version(entry.expected_version.removeprefix("v")),
        )

    def oldest(self) -> QbitMatrixEntry:
        """Return the entry with the lowest `expected_version`."""
        return min(
            self.entries,
            key=lambda entry: Version(entry.expected_version.removeprefix("v")),
        )

    def entry_for_application_version(
        self, version: str
    ) -> QbitMatrixEntry | None:
        """Return the entry whose `expected_version` exactly matches
        `version`, or `None` if no entry does.

        Compares normalized version strings (leading `v` stripped from
        both sides), not `packaging.version.Version` equality -- an
        exact-evidence match must be a literal, exact release match,
        the same notion `docs/COMPATIBILITY.md` §10 rule 3 requires
        ("a patch release is never evidence for its whole line").
        """
        normalized = version.removeprefix("v")
        for entry in self.entries:
            if entry.expected_version.removeprefix("v") == normalized:
                return entry
        return None


def load_compatibility_evidence() -> CompatibilityEvidence:
    """Load, validate, and return every packaged compatibility entry.

    Fails closed:
    - missing or unreadable package data -> `CompatibilityManifestError`;
    - malformed TOML -> `CompatibilityManifestError`;
    - zero entries -> `CompatibilityManifestError` (a manifest that
      proves nothing must never be treated as "no evidence needed");
    - duplicate entry ids -> `CompatibilityManifestError`;
    - two entries claiming the identical exact (application version,
      Web API version) pair -> `CompatibilityManifestError` (redundant
      or contradictory evidence must be resolved in the manifest, never
      silently accepted).
    """
    return CompatibilityEvidence(
        entries=parse_manifest_text(_read_packaged_manifest_text())
    )


def _read_packaged_manifest_text() -> str:
    try:
        traversable = importlib.resources.files(_MANIFEST_PACKAGE).joinpath(
            _MANIFEST_FILENAME
        )
        return traversable.read_text(encoding="utf-8")
    except (
        FileNotFoundError,
        ModuleNotFoundError,
        NotADirectoryError,
    ) as error:
        raise CompatibilityManifestError(
            f"packaged compatibility manifest {_MANIFEST_FILENAME!r} is "
            f"missing from {_MANIFEST_PACKAGE!r}: {error}"
        ) from error


def parse_manifest_text(text: str) -> tuple[QbitMatrixEntry, ...]:
    """Parse and validate manifest TOML text into immutable entries.

    Kept separate from `_read_packaged_manifest_text()` so tests can
    exercise parsing/validation against arbitrary text (missing,
    empty, malformed, duplicate) without needing a second file on disk.
    Not part of the ordinary production path -- `load_compatibility_evidence()`
    is the one production entry point.
    """
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise CompatibilityManifestError(
            f"malformed compatibility manifest TOML: {error}"
        ) from error

    entries = tuple(_parse_entry(item) for item in raw.get("entry", []))

    if not entries:
        raise CompatibilityManifestError(
            "compatibility manifest declares zero matrix entries; "
            "refusing to report evidence for a matrix that tests nothing"
        )

    ids = [entry.id for entry in entries]
    if len(ids) != len(set(ids)):
        raise CompatibilityManifestError(
            f"duplicate matrix entry id in compatibility manifest: {ids}"
        )

    seen_exact_evidence: dict[tuple[str, str], str] = {}
    for entry in entries:
        exact_evidence = (
            entry.expected_version,
            entry.expected_web_api_version,
        )
        if exact_evidence in seen_exact_evidence:
            raise CompatibilityManifestError(
                "duplicate exact application/Web API evidence "
                f"{exact_evidence} in compatibility manifest entries "
                f"{seen_exact_evidence[exact_evidence]!r} and {entry.id!r}"
            )
        seen_exact_evidence[exact_evidence] = entry.id

    return entries


def _parse_entry(item: dict) -> QbitMatrixEntry:
    try:
        entry_id = item["id"]
        expected_version = item["expected_version"]
        expected_web_api_version = item["expected_web_api_version"]
    except KeyError as error:
        raise CompatibilityManifestError(
            f"compatibility manifest entry missing required field: {error}"
        ) from error

    _validate_version(entry_id, "expected_version", expected_version)
    _validate_version(
        entry_id, "expected_web_api_version", expected_web_api_version
    )

    try:
        return QbitMatrixEntry(
            id=entry_id,
            image_repository=item["image_repository"],
            image_tag=item["image_tag"],
            image_digest=item["image_digest"],
            expected_architecture=item["expected_architecture"],
            release_line=item["release_line"],
            expected_version=expected_version,
            expected_web_api_version=expected_web_api_version,
            webui_port=int(item["webui_port"]),
            capabilities=tuple(item["capabilities"]),
            limitations=item["limitations"],
            provenance=item["provenance"],
        )
    except KeyError as error:
        raise CompatibilityManifestError(
            f"compatibility manifest entry {entry_id!r} missing required "
            f"field: {error}"
        ) from error


def _validate_version(entry_id: str, field_name: str, value: str) -> None:
    """Validate a version string via the project's version-parsing policy.

    `packaging.version.Version` is the same parser
    `tests/integration/_matrix.py` and
    `scripts/check_qbit_matrix_freshness.py` already use to compare
    manifest versions (e.g. the Web API 2.11.0 stop/start threshold in
    `docs/COMPATIBILITY.md` §2) -- reused here rather than a second
    ad-hoc check.
    """
    try:
        Version(str(value).removeprefix("v"))
    except InvalidVersion as error:
        raise CompatibilityManifestError(
            f"compatibility manifest entry {entry_id!r} has an "
            f"unparseable {field_name}={value!r}: {error}"
        ) from error
