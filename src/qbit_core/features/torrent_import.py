"""Import `.torrent` files into qBittorrent via the WebUI API.

Sources: a single `.torrent` file, a directory of `.torrent` files, or a
`.zip` archive of `.torrent` files. Never touches qBittorrent's own
filesystem -- discovery and validation happen locally, then candidates
are added through the client exactly like `trackers.py`'s plan/apply
mutations. A `.zip` failing any structural safety check (path traversal,
symlink/special entry, encryption, entry count, decompressed size,
compression ratio, path depth) is rejected whole, never partially
processed; an individual malformed `.torrent` inside an otherwise safe
source only invalidates that one entry.
"""

import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qbit_core.qbit.fields import get_field_as_string
from qbit_core.shared.bencode import BencodeError, decode_torrent

MAX_TORRENT_FILE_SIZE = 10 * 1024 * 1024
MAX_ZIP_ENTRIES = 5000
MAX_ZIP_TOTAL_UNCOMPRESSED = 200 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO = 100
MAX_ZIP_PATH_DEPTH = 12
IMPORT_BATCH_SIZE = 20

_WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")


class TorrentImportError(ValueError):
    """Report a source-level failure: bad path, or a rejected archive."""


@dataclass(frozen=True)
class TorrentImportCandidate:
    """One parsed `.torrent`, not yet classified against qBittorrent."""

    source_name: str
    torrent_bytes: bytes
    info_hash: str
    display_name: str | None


@dataclass(frozen=True)
class ImportIssue:
    """One source entry that could not be turned into a candidate."""

    source_name: str
    message: str


@dataclass(frozen=True)
class TorrentImportPlan:
    """A fully classified, side-effect-free import plan.

    `candidates` holds every successfully parsed `.torrent`, in
    deterministic discovery order; `ready` is the subset actually worth
    sending (new, and first occurrence of its infohash). `existing_hashes`
    and `duplicate_hashes` are informational only -- neither is ever sent.
    """

    discovered: int
    candidates: tuple[TorrentImportCandidate, ...]
    ready: tuple[TorrentImportCandidate, ...]
    existing_hashes: tuple[str, ...]
    duplicate_hashes: tuple[str, ...]
    invalid_entries: tuple[ImportIssue, ...]


@dataclass(frozen=True)
class ImportFailure:
    """One batch-add call that raised, covering every torrent in it."""

    info_hash: str
    source_name: str
    message: str


@dataclass(frozen=True)
class TorrentImportResult:
    imported_hashes: tuple[str, ...]
    failed: tuple[ImportFailure, ...]


@dataclass(frozen=True)
class _RawEntry:
    """A discovered source entry, either readable bytes or a reason it isn't."""

    name: str
    data: bytes | None
    error: str | None = None


def _discover_file(source: Path) -> list[_RawEntry]:
    if source.suffix.lower() != ".torrent":
        raise TorrentImportError(f"{source} is not a .torrent file")
    try:
        data = source.read_bytes()
    except OSError as error:
        raise TorrentImportError(f"Unable to read {source}: {error}") from error
    if len(data) > MAX_TORRENT_FILE_SIZE:
        return [
            _RawEntry(
                name=source.name,
                data=None,
                error="file exceeds the maximum torrent size",
            )
        ]
    return [_RawEntry(name=source.name, data=data)]


def _discover_directory(source: Path, *, recursive: bool) -> list[_RawEntry]:
    paths = source.rglob("*") if recursive else source.iterdir()
    torrent_paths = sorted(
        (p for p in paths if p.is_file() and p.suffix.lower() == ".torrent"),
        key=lambda p: p.as_posix(),
    )

    entries: list[_RawEntry] = []
    for path in torrent_paths:
        try:
            data = path.read_bytes()
        except OSError as error:
            entries.append(
                _RawEntry(name=path.name, data=None, error=str(error))
            )
            continue
        if len(data) > MAX_TORRENT_FILE_SIZE:
            entries.append(
                _RawEntry(
                    name=path.name,
                    data=None,
                    error="file exceeds the maximum torrent size",
                )
            )
            continue
        entries.append(_RawEntry(name=path.name, data=data))
    return entries


def _is_unsafe_zip_member(name: str) -> bool:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or _WINDOWS_DRIVE_PATTERN.match(normalized):
        return True
    return ".." in normalized.split("/")


def _is_symlink_or_special_zip_entry(info: zipfile.ZipInfo) -> bool:
    """Reject anything but a regular file or directory, unix-mode-wise."""
    if info.create_system != 3:  # not created on a unix system: no mode bits
        return False
    file_type = (info.external_attr >> 16) & 0o170000
    return file_type not in (0, 0o100000, 0o040000)


def _discover_zip(source: Path) -> list[_RawEntry]:
    try:
        archive = zipfile.ZipFile(source)
    except (OSError, zipfile.BadZipFile) as error:
        raise TorrentImportError(
            f"Unable to open {source} as a zip archive: {error}"
        ) from error

    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise TorrentImportError(
                f"{source} has more than {MAX_ZIP_ENTRIES} entries"
            )

        torrent_infos: list[zipfile.ZipInfo] = []
        total_uncompressed = 0
        for info in infos:
            if info.is_dir():
                continue
            if info.flag_bits & 0x1:
                raise TorrentImportError(f"{source} is encrypted")
            if _is_unsafe_zip_member(info.filename):
                raise TorrentImportError(
                    f"{source} contains an unsafe path: {info.filename}"
                )
            if _is_symlink_or_special_zip_entry(info):
                raise TorrentImportError(
                    f"{source} contains a symlink or special entry: "
                    f"{info.filename}"
                )
            if info.filename.count("/") > MAX_ZIP_PATH_DEPTH:
                raise TorrentImportError(
                    f"{source} contains a path deeper than "
                    f"{MAX_ZIP_PATH_DEPTH} levels: {info.filename}"
                )

            total_uncompressed += info.file_size
            if total_uncompressed > MAX_ZIP_TOTAL_UNCOMPRESSED:
                raise TorrentImportError(
                    f"{source} exceeds the maximum total decompressed size"
                )
            if (
                info.compress_size > 0
                and info.file_size / info.compress_size
                > MAX_ZIP_COMPRESSION_RATIO
            ):
                raise TorrentImportError(
                    f"{source} has a suspicious compression ratio: "
                    f"{info.filename}"
                )

            if info.filename.lower().endswith(".torrent"):
                torrent_infos.append(info)

        torrent_infos.sort(key=lambda i: i.filename)

        entries: list[_RawEntry] = []
        for info in torrent_infos:
            name = info.filename
            if info.file_size > MAX_TORRENT_FILE_SIZE:
                entries.append(
                    _RawEntry(
                        name=name,
                        data=None,
                        error="file exceeds the maximum torrent size",
                    )
                )
                continue
            try:
                with archive.open(info) as handle:
                    # Read one byte past the declared size: catches a
                    # declared-vs-actual mismatch without trusting the
                    # zip's own metadata.
                    data = handle.read(MAX_TORRENT_FILE_SIZE + 1)
            except (OSError, zipfile.BadZipFile, RuntimeError) as error:
                entries.append(
                    _RawEntry(name=name, data=None, error=str(error))
                )
                continue
            if len(data) > MAX_TORRENT_FILE_SIZE:
                entries.append(
                    _RawEntry(
                        name=name,
                        data=None,
                        error="file exceeds the maximum torrent size",
                    )
                )
                continue
            entries.append(_RawEntry(name=name, data=data))

    return entries


def _extract_display_name(root: dict[bytes, Any]) -> str | None:
    info = root.get(b"info")
    if not isinstance(info, dict):
        return None
    name = info.get(b"name")
    if not isinstance(name, bytes):
        return None
    return name.decode("utf-8", errors="replace")


def _fetch_existing_hashes(client: Any) -> set[str]:
    return {
        get_field_as_string(torrent, "hash").lower()
        for torrent in client.torrents_info()
    }


def plan_torrent_import(
    client: Any,
    source: Path,
    *,
    recursive: bool = False,
) -> TorrentImportPlan:
    """Discover, validate, and classify `.torrent`s from `source`.

    No mutation.
    """
    if not source.exists():
        raise TorrentImportError(f"{source} does not exist")

    if source.is_dir():
        raw_entries = _discover_directory(source, recursive=recursive)
    elif source.suffix.lower() == ".zip":
        raw_entries = _discover_zip(source)
    elif source.suffix.lower() == ".torrent":
        raw_entries = _discover_file(source)
    else:
        raise TorrentImportError(
            f"{source} is neither a .torrent file, a directory, nor a "
            ".zip archive"
        )

    candidates: list[TorrentImportCandidate] = []
    invalid_entries: list[ImportIssue] = []
    for entry in raw_entries:
        if entry.error is not None:
            invalid_entries.append(
                ImportIssue(source_name=entry.name, message=entry.error)
            )
            continue
        assert entry.data is not None
        try:
            decoded = decode_torrent(entry.data)
        except BencodeError as error:
            invalid_entries.append(
                ImportIssue(source_name=entry.name, message=str(error))
            )
            continue
        candidates.append(
            TorrentImportCandidate(
                source_name=entry.name,
                torrent_bytes=entry.data,
                info_hash=decoded.info_hash,
                display_name=_extract_display_name(decoded.root),
            )
        )

    existing_hashes = _fetch_existing_hashes(client)

    ready: list[TorrentImportCandidate] = []
    existing: list[str] = []
    duplicates: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.info_hash in existing_hashes:
            existing.append(candidate.info_hash)
        elif candidate.info_hash in seen:
            duplicates.append(candidate.info_hash)
        else:
            seen.add(candidate.info_hash)
            ready.append(candidate)

    return TorrentImportPlan(
        discovered=len(raw_entries),
        candidates=tuple(candidates),
        ready=tuple(ready),
        existing_hashes=tuple(existing),
        duplicate_hashes=tuple(duplicates),
        invalid_entries=tuple(invalid_entries),
    )


def apply_torrent_import(
    client: Any,
    plan: TorrentImportPlan,
    *,
    save_path: str | None = None,
    category: str | None = None,
    tags: Sequence[str] = (),
    paused: bool = True,
) -> TorrentImportResult:
    """Add `plan.ready` via the WebUI API, chunked into grouped calls.

    A batch that raises marks every torrent in it as failed but never
    aborts the remaining batches, so one bad batch can't hide the rest
    of the outcome. "Imported" means the add call succeeded, not that a
    follow-up read confirmed the torrent's presence.
    """
    imported: list[str] = []
    failed: list[ImportFailure] = []

    for start in range(0, len(plan.ready), IMPORT_BATCH_SIZE):
        batch = plan.ready[start : start + IMPORT_BATCH_SIZE]
        try:
            client.torrents_add(
                torrent_files=[candidate.torrent_bytes for candidate in batch],
                save_path=save_path,
                category=category,
                tags=list(tags) or None,
                is_stopped=paused,
                use_auto_torrent_management=False,
            )
        except Exception as error:
            message = str(error)
            failed.extend(
                ImportFailure(
                    info_hash=candidate.info_hash,
                    source_name=candidate.source_name,
                    message=message,
                )
                for candidate in batch
            )
            continue
        imported.extend(candidate.info_hash for candidate in batch)

    return TorrentImportResult(
        imported_hashes=tuple(imported), failed=tuple(failed)
    )
