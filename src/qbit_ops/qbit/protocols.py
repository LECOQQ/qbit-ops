"""Minimal structural interfaces for the qBittorrent client qbit-ops needs.

Five focused protocols, derived from actual call sites (`grep -rn
"client\\." src/qbit_ops/`), not from `qbittorrentapi.Client`'s full
surface -- a method appears here only because some production module
calls it. This is deliberately not one large `QbitClient` interface:
`docs/audits/2026-07-codebase-architecture-inventory.md` constat A-6
calls for "un protocole" (singular) but the actual consumers split
cleanly along read/mutate and torrent/tracker/transfer/app-info lines,
and a single enormous interface would force every caller to declare
conformance to methods it never calls.

`QbitClient` composes all five for the rare caller that legitimately
needs the whole surface (`qbit_ops.app_services.collect_tui_refresh`,
which reads torrents, transfer info, and app info in one pass). Most
production functions still type their `client` parameter as `Any` --
this phase introduces the protocols and proves representative fakes
conform to them (see `tests/test_qbit_protocols.py`); it does not
rewrite every `client: Any` signature across the codebase, since that
touches ~37 call sites for no behavioral gain and is explicitly out of
scope for an extraction phase (see
`docs/audits/2026-07-package-refactor-plan.md` Phase 3 notes).

Every method here returns a broad, structural type (`Iterable[Any]`,
`Mapping[str, Any]`, `str`) wide enough for both a plain `dict`/`list`
test double and qbittorrentapi's own `TorrentDictionary`/`Tracker`/
`TransferInfoDictionary`/`TorrentInfoList`/`TrackersList` response
types (all of which are `Mapping`/`Sequence` subclasses) -- narrowing
further would require depending on qbittorrentapi's internal types,
which is exactly what a protocol is meant to avoid.

`torrents_start` is deliberately absent: production probes it with
`getattr(client, "torrents_start", None)` (see constat P-4), an
optional-attribute pattern a `Protocol` cannot express, and unrelated to
this phase's fix for that finding.
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
    """Read-only global transfer-rate introspection."""

    def transfer_info(self) -> Mapping[str, Any]:
        """Return current global download/upload speeds."""
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
    """LOW-risk torrent state mutations (see
    `qbit_ops.shared.execution.MutationRisk`).

    The only mutation surface the TUI is allowed to reach (see
    `tests/test_tui_security.py`).
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
    """The full structural surface qbit-ops calls across all commands.

    Composed from the five focused protocols above for the rare caller
    that legitimately spans all of them (e.g. the TUI's periodic
    refresh, which reads torrents, transfer info, and app info in one
    pass) -- most production functions still take `client: Any` and use
    only a subset.
    """
