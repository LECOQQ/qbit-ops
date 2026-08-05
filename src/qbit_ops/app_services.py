"""Interface-independent application services shared by the CLI and TUI.

Kept free of Typer, Rich, and Textual so both interfaces can create a
qBittorrent client, classify its failures, and collect a one-fetch
refresh snapshot without importing each other's presentation layer.
See docs/ARCHITECTURE.md for why this stays at the package root
instead of moving into `features/` or `shared/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qbit_core.errors import QbitAuthenticationError, QbitConnectionError
from qbit_core.features.status import (
    InstanceStats,
    StatusSnapshot,
    build_status_snapshot_from_data,
    collect_instance_stats,
)
from qbit_core.features.torrents import (
    TorrentFilter,
    TorrentSelection,
    select_torrents_from_items,
)
from qbit_core.qbit.client import create_qbit_client as _create_qbit_client
from qbit_ops.config import load_qbit_config

__all__ = [
    "create_qbit_client",
    "RecoverableFailure",
    "classify_recoverable_qbit_failure",
    "TuiRefreshResult",
    "collect_tui_refresh",
]


def create_qbit_client() -> Any:
    """Zero-arg CLI/TUI entry point: load `.env` config, then delegate to
    `qbit_core.qbit.client.create_qbit_client(config)`."""
    return _create_qbit_client(load_qbit_config())


@dataclass(frozen=True)
class RecoverableFailure:
    """One recognized, recoverable failure -- never a real programming defect.

    `code` matches the `StatusAlert`/unavailable-snapshot vocabulary
    (`authentication_failed`, `qbittorrent_unavailable`).
    """

    code: str
    message: str


def classify_recoverable_qbit_failure(
    error: Exception,
) -> RecoverableFailure | None:
    """Classify an exception as a recoverable connection/auth failure.

    Returns `None` when `error` is not `QbitAuthenticationError`,
    `QbitConnectionError`, or a bare `OSError` (which covers
    `qbittorrentapi.APIConnectionError` and its subclasses). Callers
    must let a `None` result propagate as an internal error rather than
    silently degrading it to "unavailable".
    """
    if isinstance(error, QbitAuthenticationError):
        return RecoverableFailure("authentication_failed", str(error))
    if isinstance(error, QbitConnectionError | OSError):
        return RecoverableFailure("qbittorrent_unavailable", str(error))
    return None


@dataclass(frozen=True)
class TuiRefreshResult:
    """One TUI periodic refresh's collected data.

    `raw_torrents` is the raw `torrents_info()` result, kept alongside
    the unfiltered `torrents` selection so the TUI controller can
    re-apply a `TorrentFilter` in memory without a second API call.
    Re-filtering must use `raw_torrents`, not `torrents.matched`:
    `SelectedTorrent.category` is display-formatted (e.g.
    `(uncategorized)`) and would silently break the `uncategorized`
    filter token -- see `qbit_core.features.torrents._category_matches`.
    """

    status: StatusSnapshot
    instance_stats: InstanceStats
    torrents: TorrentSelection  # unfiltered (every torrent)
    raw_torrents: list[Any]


def collect_tui_refresh(
    client: Any,
    *,
    host: str | None = None,
) -> TuiRefreshResult:
    """Collect one refresh cycle: exactly five API calls (`app_version`,
    `app_web_api_version`, `transfer_info`, `torrents_info`,
    `sync_maindata`, each once), the single `torrents_info()` result
    feeding both the status counters and the unfiltered selection.
    Raises unchanged; callers classify via
    `classify_recoverable_qbit_failure`.
    """
    qbittorrent_version = getattr(client, "app_version", lambda: None)()
    api_version = getattr(client, "app_web_api_version", lambda: None)()
    transfer_info_data = client.transfer_info()
    torrents = list(client.torrents_info())

    status = build_status_snapshot_from_data(
        qbittorrent_version=(
            str(qbittorrent_version)
            if qbittorrent_version is not None
            else None
        ),
        api_version=str(api_version) if api_version is not None else None,
        transfer_info_data=transfer_info_data,
        torrents=torrents,
        host=host,
    )
    # status.host is already credential-redacted; never pass the raw host.
    instance_stats = collect_instance_stats(client, instance_id=status.host)
    selection = select_torrents_from_items(torrents, TorrentFilter())

    return TuiRefreshResult(
        status=status,
        instance_stats=instance_stats,
        torrents=selection,
        raw_torrents=torrents,
    )
