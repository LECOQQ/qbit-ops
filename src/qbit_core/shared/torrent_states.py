"""Classify raw qBittorrent torrent states; expose `TorrentSnapshot`, the
qbittorrent-api-independent torrent model built from that classification.

Shared by `qbit_core.features.status` and `.torrents` to avoid an import
cycle between the two.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from qbit_core.qbit.fields import (
    UNSET_NUMERIC,
    UNSET_TIMESTAMP,
    get_field_as_float,
    get_field_as_int,
    get_field_as_string,
    get_optional_int,
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


def is_stalled_up_without_network_load(
    state: str, downloaded: int, progress: float
) -> bool:
    """Whether a `stalledUP` torrent was completed from local data alone.

    `downloaded == 0` and `progress == 1` is an arithmetic contradiction,
    not an inference: the torrent is complete but the network delivered
    nothing, so every byte was already on disk before the transfer
    started. Scoped to `stalledUP` only -- a `stoppedUP` torrent
    satisfying the same arithmetic reflects an operator decision, not a
    diagnostic, and keeps its own finding.

    Shared by `qbit_core.features.status`'s health computation and
    `qbit_core.features.explain`'s per-torrent findings so the two
    surfaces can never disagree about the same torrent (mirrors
    `qbit_core.features.torrents.compute_torrent_tracker_health`'s role
    for tracker health).

    Known false positive: a torrent whose transfer counters were reset
    presents the same arithmetic. Callers must surface this as a
    limitation, not silently trust the verdict.
    """
    return state == "stalledUP" and downloaded == 0 and progress >= 1.0


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

    `category` is the raw value (`""` when uncategorized); the
    `(uncategorized)` display label is applied at render time by
    `qbit_core.shared.selection.format_category_label`, never stored here.

    The canonical representation of a torrent's individual measures: any
    consumer -- an aggregate report, tomorrow a policy engine -- reads
    them here rather than off a raw API object, so two callers can never
    disagree about the same torrent. `ratio` is qBittorrent's own value,
    never derived from `downloaded`/`uploaded`, so a rule built on it
    always designates the same torrents as the `--ratio` filter.

    An optional measure is `None` when qBittorrent reported nothing
    usable -- absent, null, or one of its "unset" markers (see
    `qbit_core.qbit.fields.UNSET_NUMERIC`/`UNSET_TIMESTAMP`). Never `0`
    and never the epoch: `0` is a legitimate byte count or duration, so
    the unknown needs a value of its own -- and a marker read as a
    number would not merely inflate an aggregate, it would *subtract*
    from one.
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
    downloaded: int | None
    uploaded: int | None
    seeding_time: int | None
    added_at: datetime | None
    completed_at: datetime | None
    last_activity_at: datetime | None

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
        downloaded=get_optional_int(
            torrent, "downloaded", sentinels=UNSET_NUMERIC
        ),
        uploaded=get_optional_int(torrent, "uploaded", sentinels=UNSET_NUMERIC),
        seeding_time=get_optional_int(
            torrent, "seeding_time", sentinels=UNSET_NUMERIC
        ),
        added_at=_timestamp_field(torrent, "added_on"),
        completed_at=_timestamp_field(torrent, "completion_on"),
        last_activity_at=_timestamp_field(torrent, "last_activity"),
    )


def _timestamp_field(torrent: Any, field_name: str) -> datetime | None:
    """Read one epoch-second field as an aware UTC instant, or `None`.

    Same sentinel set the filtering layer compares against, so a moment
    the model reports is exactly a moment a bounded filter can match.
    """
    seconds = get_optional_int(torrent, field_name, sentinels=UNSET_TIMESTAMP)
    return None if seconds is None else datetime.fromtimestamp(seconds, tz=UTC)
