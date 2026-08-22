"""Capture authentic `captured-container` payload fixtures.

Talks only to the disposable container the harness itself started,
never ordinary qbit-ops configuration discovery. Every payload is
scanned for secrets with the same rules
`tests/compatibility/test_payload_security.py` enforces before it is
written; a violation raises instead.
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

# Timestamp/duration fields that are not reproducible between two
# otherwise-identical captures. Normalized to a fixed sentinel so two
# captures of an unchanged instance are byte-identical; the field's
# presence and type are preserved, only the value is replaced.
VOLATILE_TORRENT_FIELDS = (
    "added_on",
    "completion_on",
    "last_activity",
    "seeding_time",
)
_VOLATILE_FIELD_SENTINEL = 0


class CaptureSecurityError(RuntimeError):
    """Report that a captured payload failed the pre-write security scan."""


@dataclass(frozen=True)
class CapturedFixtureSet:
    """Every fixture captured for one matrix entry, and where they landed."""

    matrix_id: str
    directory: Path
    written_files: tuple[Path, ...]


def _without_external_address(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Strip the instance's own public address from a payload.

    qBittorrent 5.1 added `last_external_address_v4`/`_v6`, and returns
    them from **both** `transfer_info()` and `sync_maindata()`. Empty on
    a disposable container; on a real instance they are the operator's
    IPv4/IPv6, and `_security_scan` cannot catch them -- a bare address
    is neither a URL, nor a path, nor an environment fragment, and
    `2.0.10.0` is a genuine Qt version string in `app_build_info`.
    Nothing in text separates the two.

    So they are dropped by **key**, which is exact, rather than matched
    by shape, which is not. Nothing in qbit-ops reads them.
    """
    stripped = dict(payload)
    removed = [
        key for key in stripped if key.startswith("last_external_address")
    ]
    for key in removed:
        del stripped[key]
    return stripped, removed


def _sanitize(value: Any) -> Any:
    """Recursively substitute the disposable tracker hostname."""
    if isinstance(value, str):
        return value.replace(*_TRACKER_HOSTNAME_SUBSTITUTION)
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize(item) for key, item in value.items()}
    return value


def normalize_volatile_torrent_fields(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Replace known volatile torrent fields with a fixed sentinel value.

    Only replaces fields that are present, preserving payload shape.
    Returns a new dict; never mutates `payload` in place.
    """
    normalized = dict(payload)
    for field_name in VOLATILE_TORRENT_FIELDS:
        if field_name in normalized:
            normalized[field_name] = _VOLATILE_FIELD_SENTINEL
    return normalized


def _write_fixture(
    directory: Path,
    name: str,
    *,
    payload: Any,
    description: str,
    container: RunningQbitContainer,
    sanitization: str,
    fields_removed: list[str],
    fields_normalized: list[str],
    limitations: str,
) -> Path:
    matrix_entry = container.matrix_entry
    sanitized_payload = _sanitize(payload)
    meta = {
        "trust": "captured-container",
        "description": description,
        # Observed values -- read from the running container, never
        # copied from the manifest's expected_* fields.
        "qbittorrent_version": container.observed_version,
        "web_api_version": container.observed_web_api_version,
        "architecture": container.observed_architecture,
        "qbittorrent_api_version": _QBITTORRENT_API_VERSION,
        "sanitization": sanitization,
        "fields_removed": fields_removed,
        "fields_normalized": fields_normalized,
        "limitations": limitations,
        "matrix_id": matrix_entry.id,
        "image_reference": matrix_entry.image_reference,
        "image_digest": matrix_entry.image_digest,
        "capture_date": datetime.now(UTC).date().isoformat(),
        # Expected manifest values, included separately for
        # cross-reference -- never used as the verified value itself.
        "expected_qbittorrent_version": matrix_entry.expected_version,
        "expected_web_api_version": matrix_entry.expected_web_api_version,
        "expected_architecture": matrix_entry.expected_architecture,
    }
    document = {"_meta": meta, "payload": sanitized_payload}
    # Deterministic ordering and formatting: sorted keys, fixed indent,
    # trailing newline -- stable byte-for-byte across repeated captures
    # of the same observed state (once volatile fields are normalized).
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
    normalized_torrent = normalize_volatile_torrent_fields(complete_torrent)
    normalized_field_names = sorted(
        name for name in VOLATILE_TORRENT_FIELDS if name in complete_torrent
    )
    written.append(
        _write_fixture(
            directory,
            "torrent_complete",
            payload=normalized_torrent,
            description=(
                "A real `torrents_info()` entry captured from a "
                f"disposable linuxserver/qbittorrent:{matrix_entry.image_tag} "
                "container -- a fully seeded synthetic torrent."
            ),
            container=container,
            sanitization=(
                "Tracker hostname 'qbit-ops-tracker' replaced with the "
                "allowlisted placeholder 'tracker.example'. Torrent name, "
                "hash, and content are already synthetic (see "
                "tests/integration/_torrent_corpus.py)."
            ),
            fields_removed=[],
            fields_normalized=normalized_field_names,
            limitations=(
                "One captured instance; does not prove every field shape "
                "across all real-world usage. Timestamp/duration fields "
                f"({', '.join(VOLATILE_TORRENT_FIELDS)}) are normalized to "
                f"{_VOLATILE_FIELD_SENTINEL} -- present and typed "
                "correctly, but not evidence of real elapsed time."
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
            container=container,
            sanitization=(
                "Tracker hostname replaced with 'tracker.example'; no "
                "passkey was ever present."
            ),
            fields_removed=[],
            fields_normalized=[],
            limitations=(
                "The disposable tracker returns a minimal bencode "
                "response; real-world tracker messages may vary."
            ),
        )
    )

    transfer_info, transfer_addresses = _without_external_address(
        dict(client.transfer_info())
    )
    written.append(
        _write_fixture(
            directory,
            "transfer_info",
            payload=transfer_info,
            description=(
                "Real `transfer_info()` snapshot from the disposable instance."
            ),
            container=container,
            sanitization=(
                "The instance's own public address is dropped by key; no "
                "other host/credential field exists in this payload shape."
            ),
            fields_removed=transfer_addresses,
            fields_normalized=[],
            limitations=(
                "Rates reflect the disposable instance's idle synthetic "
                "corpus, not a real download/upload workload."
            ),
        )
    )

    # `server_state` is where the instance-wide totals live -- the only
    # source for all-time transfer, global ratio and peer count. It was
    # the one call `build_instance_stats_from_server_state` consumes
    # without a captured payload to check itself against, so what the
    # instance really returns there was never verified against a real
    # qBittorrent.
    # Annotated, not inferred: `client` is untyped, so the mapping
    # comes back as `dict[Unknown, Unknown]` and every read off it
    # widens into a type the sanitizer below cannot accept.
    sync_maindata: dict[str, Any] = dict(client.sync_maindata())
    server_state, external_address_fields = _without_external_address(
        dict(sync_maindata.get("server_state") or {})
    )
    written.append(
        _write_fixture(
            directory,
            "sync_maindata_server_state",
            payload=server_state,
            description=(
                "Real `sync_maindata()['server_state']` from the disposable "
                "instance. Only `server_state` is kept: the rest of the "
                "payload is a full torrent/category dump this project never "
                "reads, and it would carry paths and tracker URLs."
            ),
            container=container,
            sanitization=(
                "The `torrents`, `categories`, `tags` and `trackers` keys "
                "are dropped whole rather than scrubbed -- none is read by "
                "qbit-ops, and dropping beats redacting."
            ),
            fields_removed=[
                key for key in sync_maindata if key != "server_state"
            ]
            + external_address_fields,
            fields_normalized=[],
            limitations=(
                "Counters reflect a freshly started disposable instance: "
                "all-time totals are near zero and no ratio has been "
                "computed yet, which is itself the `None` case the model "
                "must handle."
            ),
        )
    )

    written.append(
        _write_fixture(
            directory,
            "app_version",
            payload=str(client.app_version()),
            description="Real `app_version()` string.",
            container=container,
            sanitization="n/a",
            fields_removed=[],
            fields_normalized=[],
            limitations="n/a",
        )
    )

    written.append(
        _write_fixture(
            directory,
            "app_web_api_version",
            payload=str(client.app_web_api_version()),
            description="Real `app_web_api_version()` string.",
            container=container,
            sanitization="n/a",
            fields_removed=[],
            fields_normalized=[],
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
                container=container,
                sanitization=(
                    "n/a -- build info carries no host/credential fields."
                ),
                fields_removed=[],
                fields_normalized=[],
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
