"""Interface-independent application services shared by the CLI and TUI.

Kept free of Typer, Rich, and Textual so both interfaces can create a
qBittorrent client, classify its failures, and collect a one-fetch
refresh snapshot without importing each other's presentation layer.
See docs/ARCHITECTURE.md for why this stays at the package root
instead of moving into `features/` or `shared/`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from qbit_core.errors import QbitAuthenticationError, QbitConnectionError
from qbit_core.features.status import (
    InstanceStats,
    ServerStateCache,
    StatusSnapshot,
    build_status_snapshot_from_data,
    collect_instance_stats_delta,
)
from qbit_core.qbit.client import create_qbit_client as _create_qbit_client
from qbit_ops.config import load_qbit_config

__all__ = [
    "create_qbit_client",
    "RecoverableFailure",
    "classify_recoverable_qbit_failure",
    "TuiRefreshCache",
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
class TuiRefreshCache:
    """Cross-cycle state `collect_tui_refresh` reuses on the same
    connection instead of re-fetching it every cycle.

    `qbittorrent_version`/`api_version` cannot change for the life of a
    connection, so once fetched they are carried verbatim rather than
    paid for again every cycle. `server_state_cache` is the
    `sync_maindata` delta cursor -- see `ServerStateCache`.

    Empty (`TuiRefreshCache()`, the default) is the only correct value
    on the first refresh after a (re)connection: `None` versions force a
    real fetch, and `server_state_cache.rid == 0` forces a full
    `sync_maindata` snapshot. A caller must throw this away -- not reuse
    it -- across a reconnection, since it may target a different
    instance; see `qbit_ops.tui.state.TuiController.reset_connection`
    and `.apply_refresh_failure`.
    """

    qbittorrent_version: str | None = None
    api_version: str | None = None
    server_state_cache: ServerStateCache = field(
        default_factory=ServerStateCache
    )


@dataclass(frozen=True)
class TuiRefreshResult:
    """One TUI periodic refresh's collected data.

    `raw_torrents` is the raw `torrents_info()` result. It is the only
    torrent payload carried, and it is carried raw on purpose: the
    controller re-applies a `TorrentFilter` in memory without a second
    API call, and the composable filters read raw fields (`save_path`,
    `private`, and the raw-named `added_on`) that `TorrentSnapshot`
    does not expose under that name or type. It also feeds
    `build_bulk_action_plan_from_snapshot`'s zero-API planning path.

    `total_torrents` is the instance-wide count the Overview shows --
    kept as a plain integer rather than an unfiltered `Selection`,
    which would rebuild a snapshot per torrent on every refresh cycle
    for a number and a null check.

    `cache` is this cycle's `TuiRefreshCache`, to pass into the next
    `collect_tui_refresh` call on the same connection.
    """

    status: StatusSnapshot
    instance_stats: InstanceStats
    total_torrents: int
    raw_torrents: list[Any]
    cache: TuiRefreshCache


def collect_tui_refresh(
    client: Any,
    *,
    host: str | None = None,
    cache: TuiRefreshCache | None = None,
) -> TuiRefreshResult:
    """Collect one refresh cycle.

    Five API calls (`app_version`, `app_web_api_version`,
    `transfer_info`, `torrents_info`, `sync_maindata`, each once) on the
    first call after a (re)connection -- i.e. when `cache` is `None` or
    empty. Three calls on every later cycle on the same connection:
    `app_version`/`app_web_api_version` are served from `cache` instead
    of re-fetched, and `sync_maindata` requests a delta via
    `cache.server_state_cache.rid` instead of the whole library -- see
    `TuiRefreshCache`. The single `torrents_info()` result feeds both
    the status counters and the unfiltered selection. Raises unchanged;
    callers classify via `classify_recoverable_qbit_failure`.
    """
    cache = cache if cache is not None else TuiRefreshCache()

    qbittorrent_version = cache.qbittorrent_version
    if qbittorrent_version is None:
        raw_version = getattr(client, "app_version", lambda: None)()
        qbittorrent_version = (
            str(raw_version) if raw_version is not None else None
        )

    api_version = cache.api_version
    if api_version is None:
        raw_api_version = getattr(client, "app_web_api_version", lambda: None)()
        api_version = (
            str(raw_api_version) if raw_api_version is not None else None
        )

    transfer_info_data = client.transfer_info()
    torrents = list(client.torrents_info())

    status = build_status_snapshot_from_data(
        qbittorrent_version=qbittorrent_version,
        api_version=api_version,
        transfer_info_data=transfer_info_data,
        torrents=torrents,
        host=host,
    )
    # status.host is already credential-redacted; never pass the raw host.
    instance_stats, server_state_cache = collect_instance_stats_delta(
        client, instance_id=status.host, cache=cache.server_state_cache
    )

    return TuiRefreshResult(
        status=status,
        instance_stats=instance_stats,
        total_torrents=len(torrents),
        raw_torrents=torrents,
        cache=TuiRefreshCache(
            qbittorrent_version=qbittorrent_version,
            api_version=api_version,
            server_state_cache=server_state_cache,
        ),
    )
