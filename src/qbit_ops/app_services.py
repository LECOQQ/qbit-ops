"""Interface-independent application services shared by the CLI and TUI.

Kept free of Typer, Rich, and Textual so both interfaces can create a
qBittorrent client, classify its failures, and collect a one-fetch
refresh snapshot without importing each other's presentation layer. See
docs/TUI_ARCHITECTURE_REVIEW.md (Phase 9) for the architectural
reasoning behind this module's existence and its deliberately small
scope: it exists to remove two concrete duplications (client creation,
recoverable-failure classification), not to become a generic service
layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import qbittorrentapi

from qbit_ops.config import load_qbit_config
from qbit_ops.errors import QbitAuthenticationError, QbitConnectionError
from qbit_ops.status import StatusSnapshot, build_status_snapshot_from_data
from qbit_ops.torrents import (
    TorrentFilter,
    TorrentSelection,
    select_torrents_from_items,
)


def create_qbit_client() -> Any:
    """Create and authenticate a qBittorrent API client.

    Relocated verbatim from `qbit_ops.main._create_qbit_client` (Phase 9
    found its body had no Typer dependency despite living in the CLI
    module) -- `qbit_ops.main` re-exports this under the same private name so
    every existing call site and test seam
    (`tests/conftest.py::configure_qbit_backend` monkeypatches
    `qbit_ops.main._create_qbit_client` by string path) keeps working
    unchanged. Never prints or exits: raises `QbitAuthenticationError`/
    `QbitConnectionError` for the caller to render or classify.
    """
    config = load_qbit_config()
    client = qbittorrentapi.Client(
        host=config.host,
        username=config.username,
        password=config.password,
    )

    try:
        client.auth_log_in()
    except Exception as error:
        if _is_qbit_error(error, {"LoginFailed"}):
            raise QbitAuthenticationError(
                "Authentication to qBittorrent failed. Check QBIT_USER and "
                "QBIT_PASSWORD."
            ) from error
        if _is_qbit_error(error, {"APIConnectionError"}):
            raise QbitConnectionError(
                f"Unable to connect to qBittorrent at {config.host}."
            ) from error

        raise QbitConnectionError(
            f"Unable to initialize qBittorrent client: {error}"
        ) from error

    return client


def _is_qbit_error(error: Exception, class_names: set[str]) -> bool:
    """Return whether an exception matches expected qBittorrent errors."""
    return type(error).__name__ in class_names


@dataclass(frozen=True)
class RecoverableFailure:
    """One recognized, recoverable failure -- never a real programming defect.

    `code` matches the existing `StatusAlert`/unavailable-snapshot
    vocabulary (`authentication_failed`, `qbittorrent_unavailable`) so a
    caller can build a `build_unavailable_snapshot(code=..., message=...)`
    without re-deriving the mapping.
    """

    code: str
    message: str


def classify_recoverable_qbit_failure(
    error: Exception,
) -> RecoverableFailure | None:
    """Classify an exception as a recoverable connection/auth failure.

    Returns `None` when `error` is not one of the recognized recoverable
    categories (`QbitAuthenticationError`, `QbitConnectionError`, or a
    bare `OSError` from a post-login qBittorrent API call --
    `qbittorrentapi.APIConnectionError` and everything derived from it
    are themselves `OSError` subclasses). Callers must let a `None`
    result propagate as an unexpected internal error rather than
    silently degrading it to "unavailable" -- this is the single shared
    classification `qbit_ops.main`'s status/watch collection and the TUI's
    refresh worker both apply, so the recoverable/internal boundary
    cannot drift between the two interfaces.
    """
    if isinstance(error, QbitAuthenticationError):
        return RecoverableFailure("authentication_failed", str(error))
    if isinstance(error, QbitConnectionError | OSError):
        return RecoverableFailure("qbittorrent_unavailable", str(error))
    return None


@dataclass(frozen=True)
class TuiRefreshResult:
    """One TUI periodic refresh's collected data.

    Deliberately narrow, not a general `InstanceSnapshot`: it exists
    only to make the one-fetch invariant in `collect_tui_refresh`
    explicit and testable, per docs/TUI_ARCHITECTURE_REVIEW.md's
    decision against a broader snapshot abstraction. `raw_torrents` is
    the exact list `torrents_info()` returned this cycle -- kept
    alongside the already-built, unfiltered `torrents` selection so a
    caller (the TUI controller) can re-apply a `TorrentFilter` in
    memory without a second API call. It is intentionally the raw
    qBittorrent items, not `torrents.matched`: `SelectedTorrent.category`
    is already display-formatted (e.g. `(uncategorized)`), and
    re-filtering against the formatted value would silently break the
    `uncategorized` filter token -- see `qbit_ops.torrents._category_matches`.
    """

    status: StatusSnapshot
    torrents: TorrentSelection  # unfiltered (every torrent)
    raw_torrents: list[Any]


def collect_tui_refresh(
    client: Any,
    *,
    host: str | None = None,
) -> TuiRefreshResult:
    """Collect one refresh cycle's worth of data with exactly four API calls.

    Calls `app_version()`, `app_web_api_version()`, `transfer_info()`,
    and `torrents_info()` exactly once each -- the same bounded budget
    `qbit_ops.status.collect_status_snapshot` already uses -- and feeds the
    single `torrents_info()` result to both the status counters
    (`build_status_snapshot_from_data`) and the unfiltered torrent
    snapshot (`select_torrents_from_items`, `TorrentFilter()`, i.e. every
    torrent). Never calls `torrents_trackers()`; raises unchanged on any
    failure -- callers (the TUI refresh worker) apply
    `classify_recoverable_qbit_failure` themselves, since only they know
    whether to degrade or propagate.
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
    selection = select_torrents_from_items(torrents, TorrentFilter())

    return TuiRefreshResult(
        status=status, torrents=selection, raw_torrents=torrents
    )
