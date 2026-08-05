"""Classify raw qBittorrent torrent states; expose `TorrentSnapshot`, the
qbittorrent-api-independent torrent model built from that classification.

Shared by `qbit_core.features.status` and `.torrents` to avoid an import
cycle between the two.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from qbit_core.qbit.fields import (
    get_field_as_float,
    get_field_as_int,
    get_field_as_string,
)

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


def is_errored_state(state: str) -> bool:
    return state in _ERRORED_STATES


def is_checking_state(state: str) -> bool:
    return state in _CHECKING_STATES


def is_actively_downloading_state(state: str) -> bool:
    """True only while actually transferring -- narrower than
    `classify_torrent_state(state) == "downloading"`, which also groups
    a paused download (`pausedDL`/`stoppedDL`) under that direction."""
    return state in _DOWNLOADING_ACTIVE_STATES


def is_actively_uploading_state(state: str) -> bool:
    """See `is_actively_downloading_state`; same narrowing for uploads."""
    return state in _SEEDING_ACTIVE_STATES


@dataclass(frozen=True)
class TorrentSnapshot:
    """Stable, qbittorrent-api-independent representation of one torrent.

    `category` is the raw value (`""` when uncategorized) -- unlike
    `qbit_core.features.torrents.SelectedTorrent.category`, which is
    formatted for CLI/TUI display; the two models are not interchangeable.
    """

    hash: str
    name: str
    category: str
    state: str
    state_group: TorrentStateGroup
    size: int
    progress: float
    ratio: float
    download_rate: int
    upload_rate: int

    @property
    def is_paused(self) -> bool:
        return is_stopped_state(self.state)

    @property
    def is_active(self) -> bool:
        """Matches the CLI's `torrents list --active` semantics."""
        return not self.is_paused

    @property
    def is_downloading(self) -> bool:
        return is_actively_downloading_state(self.state)

    @property
    def is_uploading(self) -> bool:
        return is_actively_uploading_state(self.state)

    @property
    def is_checking(self) -> bool:
        return is_checking_state(self.state)

    @property
    def has_error(self) -> bool:
        return is_errored_state(self.state)


def build_torrent_snapshot(torrent: Any) -> TorrentSnapshot:
    state = get_field_as_string(torrent, "state")
    return TorrentSnapshot(
        hash=get_field_as_string(torrent, "hash"),
        name=get_field_as_string(torrent, "name"),
        category=get_field_as_string(torrent, "category"),
        state=state,
        state_group=classify_torrent_state(state),
        size=get_field_as_int(torrent, "size"),
        progress=get_field_as_float(torrent, "progress"),
        ratio=get_field_as_float(torrent, "ratio"),
        download_rate=get_field_as_int(torrent, "dlspeed"),
        upload_rate=get_field_as_int(torrent, "upspeed"),
    )
