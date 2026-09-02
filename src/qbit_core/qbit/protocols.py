"""The structural surface of a real qBittorrent client, split into
narrow read/mutate protocols composed into `QbitClient`. Destructive
removal gets its own protocol so a consumer restricted to LOW-risk
mutations cannot reach it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "QbitClient",
]


@runtime_checkable
class QbitTorrentReader(Protocol):
    """Read-only torrent, category and tag introspection."""

    def torrents_info(self) -> Iterable[Any]: ...

    def torrents_trackers(self, torrent_hash: str) -> Iterable[Any]:
        """Return one torrent's tracker endpoints."""
        ...

    def torrents_categories(self) -> Mapping[str, Any]:
        """Return every category currently registered, keyed by name."""
        ...

    def torrents_tags(self) -> Iterable[str]:
        """Return every tag currently declared on the instance, including
        one no torrent carries."""
        ...


@runtime_checkable
class QbitTransferReader(Protocol):
    """Read-only global transfer/instance-statistics introspection."""

    def transfer_info(self) -> Mapping[str, Any]:
        """Return current global download/upload speeds."""
        ...

    def sync_maindata(self, rid: str | int = 0) -> Mapping[str, Any]:
        """Sync snapshot; only `server_state` is used (see
        `qbit_core.features.status.collect_instance_stats`/
        `collect_instance_stats_delta`). `rid=0` (the default) returns a
        full snapshot; echoing back a nonzero `rid` from a prior call
        returns only the `server_state` keys that changed since."""
        ...


@runtime_checkable
class QbitAppInfoReader(Protocol):
    """Read-only application/Web API version introspection."""

    def app_version(self) -> str: ...

    def app_web_api_version(self) -> str: ...


@runtime_checkable
class QbitTorrentMutator(Protocol):
    """LOW-risk torrent state mutations (see `shared.execution.MutationRisk`).

    The only mutation surface the TUI is allowed to reach. Excludes
    `torrents_delete`, the one HIGH-risk exception -- see
    `QbitDestructiveMutator`.
    """

    def torrents_pause(self, torrent_hashes: str | list[str]) -> None: ...

    def torrents_start(self, torrent_hashes: str | list[str]) -> None:
        """Resume the given torrents.

        Named for the method production actually calls, not the
        `torrents_resume` alias -- see
        `qbit_core.features.torrents._call_bulk_torrent_action`.
        """
        ...

    def torrents_reannounce(self, torrent_hashes: str | list[str]) -> None: ...

    def torrents_create_category(self, name: str) -> None: ...

    def torrents_set_category(
        self, torrent_hashes: str | list[str], category: str
    ) -> None:
        """Assign a category to the given torrents (empty string clears it)."""
        ...

    def torrents_add_tags(
        self, torrent_hashes: str | list[str], tags: str | list[str]
    ) -> None: ...

    def torrents_remove_tags(
        self, torrent_hashes: str | list[str], tags: str | list[str]
    ) -> None: ...

    def torrents_set_download_limit(
        self, torrent_hashes: str | list[str], limit: int
    ) -> None:
        """Set the download rate limit for the given torrents, in bytes/s."""
        ...

    def torrents_set_upload_limit(
        self, torrent_hashes: str | list[str], limit: int
    ) -> None:
        """Set the upload rate limit for the given torrents, in bytes/s."""
        ...


@runtime_checkable
class QbitDestructiveMutator(Protocol):
    """Irreversible torrent removal -- deliberately its own protocol.

    Kept out of `QbitTorrentMutator` so the TUI's mutation surface stays
    provably limited to LOW-risk state changes: a consumer typed against
    that protocol cannot reach this method at all.
    """

    def torrents_delete(
        self, delete_files: bool, torrent_hashes: str | list[str]
    ) -> None:
        """Delete the given torrents, optionally with their files."""
        ...


@runtime_checkable
class QbitBulkTorrentMutator(
    QbitTorrentMutator, QbitDestructiveMutator, Protocol
):
    """Every action `apply_bulk_torrent_action` can reach; not itself a
    TUI-safety boundary (see
    `qbit_ops.tui.state.TuiController.build_bulk_plan`). Use
    `QbitTorrentMutator` instead wherever a caller itself must stay
    provably restricted to LOW-risk mutations.
    """


@runtime_checkable
class QbitTrackerMutator(Protocol):
    """Tracker-list mutations -- never reachable from the TUI."""

    def torrents_add_trackers(self, torrent_hash: str, urls: str) -> None: ...

    def torrents_remove_trackers(
        self, torrent_hash: str, urls: list[str]
    ) -> None: ...

    def torrents_edit_tracker(
        self, torrent_hash: str, original_url: str, new_url: str
    ) -> None: ...


@runtime_checkable
class QbitClient(
    QbitTorrentReader,
    QbitTransferReader,
    QbitAppInfoReader,
    QbitTorrentMutator,
    QbitDestructiveMutator,
    QbitTrackerMutator,
    Protocol,
):
    """The full structural surface qbit-ops calls across all commands."""
