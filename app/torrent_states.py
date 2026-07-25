"""Classify raw qBittorrent torrent states into a stable public vocabulary.

Extracted from `app.torrents`/`app.status` (mirroring the Phase 2
extraction of `app.qbit_fields`) so both modules can depend on the same
state classification without an import cycle: `app.status` and the
torrent-filter pipeline in `app.torrents` both need it, and `app.torrents`
must not import `app.status` (which itself needs low-level torrent field
access). No Typer, no Rich.
"""

from __future__ import annotations

from typing import Any, Literal

from app.qbit_fields import get_field_as_float, get_field_as_string

TorrentStateGroup = Literal[
    "downloading",
    "seeding",
    "stalled",
    "checking",
    "errored",
    "unknown",
]

# States that are unambiguous regardless of qBittorrent 4 vs 5 naming.
_ERRORED_STATES = {"error", "missingFiles"}
_CHECKING_STATES = {
    "checkingDL",
    "checkingUP",
    "checkingResumeData",
    "allocating",
    "moving",
}
_STALLED_STATES = {"stalledDL", "stalledUP"}
_SEEDING_ACTIVE_STATES = {"uploading", "forcedUP", "queuedUP"}
_DOWNLOADING_ACTIVE_STATES = {"downloading", "metaDL", "forcedDL", "queuedDL"}


def is_stopped_state(state: str) -> bool:
    """Return whether qBittorrent reports a torrent as stopped.

    qBittorrent 5 renamed pause/resume to stop/start, and its states
    from `pausedDL`/`pausedUP` to `stoppedDL`/`stoppedUP`; both prefixes
    must be treated as "stopped" to behave correctly on either version.
    """
    normalized_state = state.casefold()
    return normalized_state.startswith("paused") or normalized_state.startswith(
        "stopped"
    )


def is_completed_torrent(torrent: Any) -> bool:
    """Return whether qBittorrent reports a torrent as completed."""
    return get_field_as_float(torrent, "progress") >= 1.0


def classify_torrent_state(state: str) -> TorrentStateGroup:
    """Classify a raw qBittorrent torrent state into a status group.

    Handles the qBittorrent 4 (`paused*`) vs 5 (`stopped*`) naming split
    by reusing `is_stopped_state` instead of re-deriving that rule, then
    disambiguating direction from the `DL`/`UP` suffix.
    """
    if state in _ERRORED_STATES:
        return "errored"
    if state in _CHECKING_STATES:
        return "checking"
    if state in _STALLED_STATES:
        return "stalled"
    if state in _SEEDING_ACTIVE_STATES:
        return "seeding"
    if state in _DOWNLOADING_ACTIVE_STATES:
        return "downloading"

    if is_stopped_state(state):
        if state.endswith("UP"):
            return "seeding"
        if state.endswith("DL"):
            return "downloading"

    return "unknown"


def get_torrent_state_group(torrent: Any) -> TorrentStateGroup:
    """Read a torrent's raw `state` field and classify it in one step."""
    return classify_torrent_state(get_field_as_string(torrent, "state"))
