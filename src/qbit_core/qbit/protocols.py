"""Minimal structural interfaces for the qBittorrent client qbit-ops needs.

Six focused protocols split along read/mutate and
torrent/tracker/transfer/app-info lines, rather than one large
`QbitClient` interface. Destructive removal gets its own protocol so a
consumer restricted to LOW-risk mutations cannot reach it.
`QbitBulkTorrentMutator` composes two of those six for the one function
that legitimately needs both -- it is not a seventh independent scope.
`QbitTorrentMutator` declares `torrents_start`, not `torrents_resume`:
production calls `torrents_start` directly for the resume/start action
(see `qbit_core.features.torrents._call_bulk_torrent_action`), and
`tests/test_qbit_library_http_boundary.py` proves the installed
qbittorrent-api client aliases the two to the same bound method, so
nothing is lost by typing the name actually called.
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

    def torrents_info(self) -> Iterable[Any]:
        """Return every torrent qBittorrent currently tracks."""
        ...

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

    def sync_maindata(self) -> Mapping[str, Any]:
        """Full sync snapshot; only `server_state` is used (see
        `qbit_core.features.status.collect_instance_stats`)."""
        ...


@runtime_checkable
class QbitAppInfoReader(Protocol):
    """Read-only application/Web API version introspection."""

    def app_version(self) -> str:
        """Return the qBittorrent application version string."""
        ...

    def app_web_api_version(self) -> str:
        """Return the qBittorrent Web API version string."""
        ...


@runtime_checkable
class QbitTorrentMutator(Protocol):
    """LOW-risk torrent state mutations (see `shared.execution.MutationRisk`).

    The only mutation surface the TUI is allowed to reach. Covers
    every action `apply_bulk_torrent_action` can apply except
    `torrents_delete` -- the one HIGH-risk exception, deliberately
    kept out; see `QbitDestructiveMutator`.
    """

    def torrents_pause(self, torrent_hashes: str | list[str]) -> None:
        """Pause the given torrents."""
        ...

    def torrents_start(self, torrent_hashes: str | list[str]) -> None:
        """Resume the given torrents.

        Named for the method production actually calls, not the
        `torrents_resume` alias -- see the module docstring.
        """
        ...

    def torrents_reannounce(self, torrent_hashes: str | list[str]) -> None:
        """Reannounce the given torrents to their trackers."""
        ...

    def torrents_create_category(self, name: str) -> None:
        """Create a new category, by name."""
        ...

    def torrents_set_category(
        self, torrent_hashes: str | list[str], category: str
    ) -> None:
        """Assign a category to the given torrents (empty string clears it)."""
        ...

    def torrents_add_tags(
        self, torrent_hashes: str | list[str], tags: str | list[str]
    ) -> None:
        """Add one or more tags to the given torrents."""
        ...

    def torrents_remove_tags(
        self, torrent_hashes: str | list[str], tags: str | list[str]
    ) -> None:
        """Remove one or more tags from the given torrents."""
        ...

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
    """Every action `apply_bulk_torrent_action` can reach, across both
    its callers -- every LOW-risk action plus `torrents_delete`.

    Not a TUI-safety boundary: `apply_bulk_torrent_action` is the
    engine shared by the CLI (which can request `delete`) and the TUI
    (which cannot). That restriction is not this type -- a value
    typed `QbitBulkTorrentMutator` can still be asked to delete. It is
    `qbit_ops.tui.state.TuiController.build_bulk_plan`, which never
    builds a plan whose action is `delete` (typed `TuiBulkAction`,
    refused at runtime otherwise; see `tests/test_tui_state.py`). Use
    `QbitTorrentMutator` instead, not this protocol, wherever a caller
    itself must stay provably restricted to LOW-risk mutations.
    """


@runtime_checkable
class QbitTrackerMutator(Protocol):
    """Tracker-list mutations -- never reachable from the TUI."""

    def torrents_add_trackers(self, torrent_hash: str, urls: str) -> None:
        """Add a tracker URL to one torrent."""
        ...

    def torrents_remove_trackers(
        self, torrent_hash: str, urls: list[str]
    ) -> None:
        """Remove one or more tracker URLs from one torrent."""
        ...

    def torrents_edit_tracker(
        self, torrent_hash: str, original_url: str, new_url: str
    ) -> None:
        """Replace one torrent's tracker URL with another."""
        ...


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
