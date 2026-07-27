"""Capture authentic `captured-container` payload fixtures.

Talks only to the disposable container the harness itself started
(`RunningQbitContainer`), never ordinary qbit-ops configuration
discovery. Every captured payload is sanitized and scanned with the
exact same rules `tests/compatibility/test_payload_security.py`
enforces (`_security_scan.scan_text`) *before* it is accepted -- a
violation raises, it never gets written.
"""

from __future__ import annotations

import importlib.metadata
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import qbittorrentapi

from tests.compatibility._security_scan import scan_text
from tests.integration._harness import RunningQbitContainer
from tests.integration._matrix import MatrixEntry
from tests.integration._seed import SeededCorpus

_QBITTORRENT_API_VERSION = importlib.metadata.version("qbittorrent-api")

CAPTURED_FIXTURES_ROOT = (
    Path(__file__).parent.parent
    / "compatibility"
    / "fixtures"
    / "captured-container"
)

# The disposable in-network tracker's real hostname is not on the
# fixture security allowlist (`tracker.example` is) -- substitute it
# before writing, and record the substitution in `sanitization`.
_TRACKER_HOSTNAME_SUBSTITUTION = ("qbit-ops-tracker", "tracker.example")


class CaptureSecurityError(RuntimeError):
    """Report that a captured payload failed the pre-write security scan."""


@dataclass(frozen=True)
class CapturedFixtureSet:
    """Every fixture captured for one matrix entry, and where they landed."""

    matrix_id: str
    directory: Path
    written_files: tuple[Path, ...]


def _sanitize(value: Any) -> Any:
    """Recursively substitute the disposable tracker hostname."""
    if isinstance(value, str):
        return value.replace(*_TRACKER_HOSTNAME_SUBSTITUTION)
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize(item) for key, item in value.items()}
    return value


def _write_fixture(
    directory: Path,
    name: str,
    *,
    payload: Any,
    description: str,
    matrix_entry: MatrixEntry,
    sanitization: str,
    fields_removed: list[str],
    limitations: str,
) -> Path:
    sanitized_payload = _sanitize(payload)
    meta = {
        "trust": "captured-container",
        "description": description,
        "qbittorrent_version": matrix_entry.expected_version,
        "web_api_version": matrix_entry.expected_web_api_version,
        "qbittorrent_api_version": _QBITTORRENT_API_VERSION,
        "sanitization": sanitization,
        "fields_removed": fields_removed,
        "limitations": limitations,
        "matrix_id": matrix_entry.id,
        "image_reference": matrix_entry.image_reference,
        "image_digest": matrix_entry.image_digest,
        "capture_date": datetime.now(UTC).date().isoformat(),
    }
    document = {"_meta": meta, "payload": sanitized_payload}
    # Deterministic ordering and formatting: sorted keys, fixed indent,
    # trailing newline -- stable byte-for-byte across repeated captures
    # of the same observed state.
    text = (
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    )

    violations = scan_text(text)
    if violations:
        raise CaptureSecurityError(
            f"refusing to write {name}.json: {violations}"
        )

    path = directory / f"{name}.json"
    path.write_text(text, encoding="utf-8")
    return path


def capture_matrix_fixtures(
    container: RunningQbitContainer,
    corpus: SeededCorpus,
) -> CapturedFixtureSet:
    """Capture torrents/tracker/transfer/application fixtures for one entry."""
    matrix_entry = container.matrix_entry
    directory = CAPTURED_FIXTURES_ROOT / matrix_entry.id
    directory.mkdir(parents=True, exist_ok=True)

    client = qbittorrentapi.Client(
        host=container.host,
        username=container.username,
        password=container.password,
    )
    client.auth_log_in()

    written: list[Path] = []

    complete_torrent = next(
        dict(t)
        for t in client.torrents_info()
        if t.hash == corpus.complete.info_hash_hex
    )
    written.append(
        _write_fixture(
            directory,
            "torrent_complete",
            payload=complete_torrent,
            description=(
                "A real `torrents_info()` entry captured from a "
                f"disposable linuxserver/qbittorrent:{matrix_entry.image_tag} "
                "container -- a fully seeded synthetic torrent."
            ),
            matrix_entry=matrix_entry,
            sanitization=(
                "Tracker hostname 'qbit-ops-tracker' replaced with the "
                "allowlisted placeholder 'tracker.example'. Torrent name, "
                "hash, and content are already synthetic (see "
                "tests/integration/_torrent_corpus.py)."
            ),
            fields_removed=[],
            limitations=(
                "One captured instance; does not prove every field shape "
                "across all real-world usage."
            ),
        )
    )

    tracked_trackers = [
        dict(t)
        for t in client.torrents_trackers(
            torrent_hash=corpus.tracked.info_hash_hex
        )
    ]
    written.append(
        _write_fixture(
            directory,
            "trackers_tracked_torrent",
            payload=tracked_trackers,
            description=(
                "Real `torrents_trackers()` entries (DHT/PeX/LSD "
                "pseudo-trackers plus the disposable in-network tracker) "
                "for the tracked synthetic torrent."
            ),
            matrix_entry=matrix_entry,
            sanitization=(
                "Tracker hostname replaced with 'tracker.example'; no "
                "passkey was ever present."
            ),
            fields_removed=[],
            limitations=(
                "The disposable tracker returns a minimal bencode "
                "response; real-world tracker messages may vary."
            ),
        )
    )

    transfer_info = dict(client.transfer_info())
    written.append(
        _write_fixture(
            directory,
            "transfer_info",
            payload=transfer_info,
            description=(
                "Real `transfer_info()` snapshot from the disposable instance."
            ),
            matrix_entry=matrix_entry,
            sanitization=(
                "n/a -- no host/credential fields present in this payload "
                "shape."
            ),
            fields_removed=[],
            limitations=(
                "Rates reflect the disposable instance's idle synthetic "
                "corpus, not a real download/upload workload."
            ),
        )
    )

    written.append(
        _write_fixture(
            directory,
            "app_version",
            payload=str(client.app_version()),
            description="Real `app_version()` string.",
            matrix_entry=matrix_entry,
            sanitization="n/a",
            fields_removed=[],
            limitations="n/a",
        )
    )

    written.append(
        _write_fixture(
            directory,
            "app_web_api_version",
            payload=str(client.app_web_api_version()),
            description="Real `app_web_api_version()` string.",
            matrix_entry=matrix_entry,
            sanitization="n/a",
            fields_removed=[],
            limitations="n/a",
        )
    )

    try:
        build_info = dict(client.app_build_info())
        written.append(
            _write_fixture(
                directory,
                "app_build_info",
                payload=build_info,
                description=(
                    "Real `app_build_info()` snapshot, where the endpoint "
                    "exists."
                ),
                matrix_entry=matrix_entry,
                sanitization=(
                    "n/a -- build info carries no host/credential fields."
                ),
                fields_removed=[],
                limitations=(
                    "qbit-ops never calls this endpoint itself (see "
                    "docs/COMPATIBILITY.md #3); captured for reference only."
                ),
            )
        )
    except (
        Exception
    ) as error:  # noqa: BLE001 -- endpoint absence must not fail the capture
        (directory / "app_build_info.unavailable.txt").write_text(
            f"app/buildInfo unavailable for {matrix_entry.id}: "
            f"{type(error).__name__}\n",
            encoding="utf-8",
        )

    return CapturedFixtureSet(
        matrix_id=matrix_entry.id,
        directory=directory,
        written_files=tuple(written),
    )
