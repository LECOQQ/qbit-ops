"""Load and validate the packaged qBittorrent compatibility evidence manifest.

Read via `importlib.resources` (not a `Path(__file__)`-relative lookup)
so it works from an installed wheel with no repository checkout. See
docs/COMPATIBILITY.md. Importing this module never reads or parses
the manifest; parsing only happens when `load_compatibility_evidence()`
is called.
"""

from __future__ import annotations

import importlib.resources
import tomllib
from dataclasses import dataclass

from packaging.version import InvalidVersion, Version

_MANIFEST_PACKAGE = "qbit_core.data"
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
        """Return the entry whose `expected_version` exactly matches `version`.

        A literal string match (leading `v` stripped), not a
        `packaging.version.Version` equality: a patch release is never
        evidence for its whole line (docs/COMPATIBILITY.md).
        """
        normalized = version.removeprefix("v")
        for entry in self.entries:
            if entry.expected_version.removeprefix("v") == normalized:
                return entry
        return None


def load_compatibility_evidence() -> CompatibilityEvidence:
    """Load, validate, and return every packaged compatibility entry.

    Fails closed with `CompatibilityManifestError` on missing/unreadable
    data, malformed TOML, zero entries, duplicate entry ids, or two
    entries claiming the identical exact (application version, Web API
    version) pair -- redundant or contradictory evidence is never
    silently accepted, and a manifest proving nothing is never treated
    as "no evidence needed".
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
    except OSError as error:
        # Distinct from the "missing" branch above: the manifest exists
        # but couldn't be read (e.g. `PermissionError`) -- a different
        # fact, worth a message that doesn't claim the file is absent.
        raise CompatibilityManifestError(
            f"packaged compatibility manifest {_MANIFEST_FILENAME!r} in "
            f"{_MANIFEST_PACKAGE!r} could not be read: {error}"
        ) from error


def parse_manifest_text(text: str) -> tuple[QbitMatrixEntry, ...]:
    """Parse and validate manifest TOML text into immutable entries.

    Kept separate from `_read_packaged_manifest_text()` so tests can
    exercise parsing/validation against arbitrary text.
    """
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise CompatibilityManifestError(
            f"malformed compatibility manifest TOML: {error}"
        ) from error

    raw_entries = raw.get("entry", [])
    if not isinstance(raw_entries, list):
        raise CompatibilityManifestError(
            "compatibility manifest 'entry' must be an array of tables, "
            f"got {type(raw_entries).__name__}"
        )

    entries = tuple(_parse_entry(item) for item in raw_entries)

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


def _parse_entry(item: object) -> QbitMatrixEntry:
    if not isinstance(item, dict):
        raise CompatibilityManifestError(
            "compatibility manifest entry must be a table, got "
            f"{type(item).__name__}: {item!r}"
        )
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
    except (TypeError, ValueError) as error:
        # `webui_port=int(...)` and `capabilities=tuple(...)` coerce
        # rather than just look up a key, so a present-but-wrong-shaped
        # field (a non-numeric port, a non-iterable `capabilities`)
        # raises here instead of `KeyError` above.
        raise CompatibilityManifestError(
            f"compatibility manifest entry {entry_id!r} has a malformed "
            f"field: {error}"
        ) from error


def _validate_version(entry_id: str, field_name: str, value: str) -> None:
    """Validate a version string via `packaging.version.Version`."""
    try:
        Version(str(value).removeprefix("v"))
    except InvalidVersion as error:
        raise CompatibilityManifestError(
            f"compatibility manifest entry {entry_id!r} has an "
            f"unparseable {field_name}={value!r}: {error}"
        ) from error
