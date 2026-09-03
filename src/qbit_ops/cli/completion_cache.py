"""Disk-backed cache of live vocabularies for shell completion.

See `.agents/specs/completion-cache.md`. This module owns *storage*
only: where the cache file lives, how it is read and merge-written, and
the cold-start failure witness. Building the Typer `autocompletion`
callback that decides *when* to read, probe or skip is
`qbit_ops.cli.completion_live`.

The cache lives next to whichever `.env` file
`qbit_ops.config.get_active_env_file_path()` currently resolves to --
same precedence `QBIT_OPS_ENV_FILE` already governs everywhere else, so
two instances never share a cache file. Inside that file, entries are
additionally indexed by the redacted instance host (`status.host`'s own
form, via `qbit_core.features.status.redact_host`): a second guard for
the case two differently-located `.env` files still name the same
instance, or vice versa.

Every read is exception-safe by construction: a missing, truncated or
otherwise unreadable file reads back exactly like an empty cache, never
raising. A shell-completion callback that raises breaks the user's
shell, not just the command -- see the module docstring of
`completion_live.py`.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qbit_core.features.status import redact_host as _redact_status_host
from qbit_ops.config import get_active_env_file_path

__all__ = [
    "InstanceVocabulary",
    "cache_file_path",
    "redact_host",
    "read_instance_vocabulary",
    "merge_instance_vocabulary",
    "mark_cold_start_probe_failed",
]

CACHE_FILENAME = ".qbit-ops-completion-cache.json"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class InstanceVocabulary:
    """One instance's cached completion values, plus its probe witness."""

    categories: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    tracker_hosts: tuple[str, ...] = ()
    cold_start_probe_failed: bool = False


def redact_host(host: str) -> str:
    """Reduce `host` to the credential-free form that indexes the cache.

    Reuses `status.host`'s own redaction (`qbit_core.features.status.
    redact_host`) rather than a second implementation, so the two can
    never disagree about what counts as safe. That function was
    module-private (`_redact_host`) until this change promoted it to
    `qbit_core`'s declared public surface -- required by
    `tests/test_qbit_core_public_surface.py`, which refuses any
    `qbit_ops` import of a name `qbit_core` has not declared in
    `__all__`.
    """
    return _redact_status_host(host)


def cache_file_path() -> Path:
    """Return the completion cache file for the currently active `.env`."""
    return get_active_env_file_path().parent / CACHE_FILENAME


def read_instance_vocabulary(host: str) -> InstanceVocabulary:
    """Read one instance's cached vocabulary, indexed by its redacted host.

    Never raises: a missing file, an unreadable file, invalid JSON, or a
    payload shaped unexpectedly all read back as an empty, non-failed
    entry -- the same thing a cache that was never written would report.
    """
    data = _read_cache_document()
    entry = data.get("instances", {}).get(redact_host(host))
    if not isinstance(entry, dict):
        return InstanceVocabulary()

    return InstanceVocabulary(
        categories=_string_tuple(entry.get("categories")),
        tags=_string_tuple(entry.get("tags")),
        tracker_hosts=_string_tuple(entry.get("tracker_hosts")),
        cold_start_probe_failed=bool(
            entry.get("cold_start_probe_failed", False)
        ),
    )


def merge_instance_vocabulary(
    host: str,
    *,
    categories: Iterable[str] | None = None,
    tags: Iterable[str] | None = None,
    tracker_hosts: Iterable[str] | None = None,
) -> None:
    """Union newly observed values into `host`'s cached entry, atomically.

    A merge, not a replace: a command that only saw a filtered subset of
    the library must never erase names a broader run already learned.
    Also clears the cold-start probe witness for `host` -- writing fresh
    data *is* "a command that reached the instance", the signal the
    witness exists to react to.
    """
    _update_entry(
        host,
        categories=categories,
        tags=tags,
        tracker_hosts=tracker_hosts,
        cold_start_probe_failed=False,
    )


def mark_cold_start_probe_failed(host: str) -> None:
    """Record that the one-shot cold-start probe for `host` failed.

    Leaves any existing cached lists untouched -- only the witness
    changes -- so a probe failure can never regress data a prior
    successful command already wrote.
    """
    _update_entry(host, cold_start_probe_failed=True)


def _update_entry(
    host: str,
    *,
    categories: Iterable[str] | None = None,
    tags: Iterable[str] | None = None,
    tracker_hosts: Iterable[str] | None = None,
    cold_start_probe_failed: bool | None = None,
) -> None:
    key = redact_host(host)
    data = _read_cache_document()
    instances = data.get("instances")
    if not isinstance(instances, dict):
        instances = {}
    existing = instances.get(key)
    entry: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}

    if categories is not None:
        entry["categories"] = _merged(entry.get("categories"), categories)
    if tags is not None:
        entry["tags"] = _merged(entry.get("tags"), tags)
    if tracker_hosts is not None:
        entry["tracker_hosts"] = _merged(
            entry.get("tracker_hosts"), tracker_hosts
        )
    if cold_start_probe_failed is not None:
        entry["cold_start_probe_failed"] = cold_start_probe_failed

    instances[key] = entry
    _write_cache_document(
        {"schema_version": SCHEMA_VERSION, "instances": instances}
    )


def _merged(existing: Any, new_values: Iterable[str]) -> list[str]:
    return sorted(
        set(_string_tuple(existing)) | {value for value in new_values}
    )


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _read_cache_document() -> dict[str, Any]:
    try:
        raw = cache_file_path().read_text(encoding="utf-8")
        document = json.loads(raw)
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return document if isinstance(document, dict) else {}


def _write_cache_document(document: dict[str, Any]) -> None:
    """Write `document` to the cache file, tolerating a write failure.

    Atomic: written to a temporary file in the same directory, then
    `os.replace()`d over the real path -- the rename is what keeps a
    concurrent reader from ever observing a half-written file (POSIX
    `rename(2)` is atomic within one filesystem, which is why the
    temporary file is created *in* the cache's own directory rather
    than the platform temp dir). Writing the cache is a side effect of
    a command that already succeeded at its real purpose, so any I/O
    failure here (a full disk, a read-only directory) is swallowed
    rather than surfaced as a command failure.
    """
    path = cache_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=".completion-cache-", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temp_name, path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
    except OSError:
        return
