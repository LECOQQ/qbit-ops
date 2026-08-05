"""Minimal structural interfaces for the qBittorrent client qbit-ops needs.

Five focused protocols split along read/mutate and
torrent/tracker/transfer/app-info lines, rather than one large
`QbitClient` interface. `torrents_start` is deliberately absent:
production probes it with `getattr(client, "torrents_start", None)`,
an optional-attribute pattern a `Protocol` cannot express.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class QbitTorrentReader(Protocol):
    """Read-only torrent listing and per-torrent tracker inspection."""

    def torrents_info(self) -> Iterable[Any]:
        """Return every torrent qBittorrent currently tracks."""
        ...

    def torrents_trackers(self, torrent_hash: str) -> Iterable[Any]:
        """Return one torrent's tracker endpoints."""
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

    The only mutation surface the TUI is allowed to reach.
    """

    def torrents_pause(self, torrent_hashes: str | list[str]) -> None:
        """Pause the given torrents."""
        ...

    def torrents_resume(self, torrent_hashes: str | list[str]) -> None:
        """Resume the given torrents."""
        ...

    def torrents_reannounce(self, torrent_hashes: str | list[str]) -> None:
        """Reannounce the given torrents to their trackers."""
        ...


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
    QbitTrackerMutator,
    Protocol,
):
    """The full structural surface qbit-ops calls across all commands."""
