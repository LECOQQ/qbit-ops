"""Pure(-ish) TUI state and refresh controller, independent of Textual.

Everything here is directly unit-testable without a terminal or a real
Textual `App` -- see `tests/test_tui_state.py`. Widgets in `app/tui/app.py`
only ever read `TuiState` and call `TuiController` methods; they never
reimplement filtering, refresh sequencing, or failure classification.

Security boundary (see docs/TUI_ARCHITECTURE_REVIEW.md §10): this module
only ever imports safe, structured domain outputs. It must never import
`app.torrents.list_torrents_with_trackers`, `app.torrents._get_tracker_details`,
any `plan_*`/`apply_*` mutation function, or `app.main` -- enforced by
`tests/test_tui_security.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.app_services import (
    TuiRefreshResult,
    classify_recoverable_qbit_failure,
    collect_tui_refresh,
    create_qbit_client,
)
from app.config import ConfigError
from app.errors import AppError, ErrorCategory
from app.status import StatusSnapshot
from app.torrents import (
    SelectedTorrent,
    TorrentFilter,
    TorrentSelection,
    get_safe_tracker_details,
    select_torrents_from_items,
)

DEFAULT_REFRESH_INTERVAL_SECONDS = 5.0


class ConnectionState(StrEnum):
    """The TUI's high-level connection/session state.

    Distinct from `app.status.Health`: this tracks whether the TUI *can
    talk to qBittorrent at all*, not the instance's own operational
    health (which is carried inside `TuiState.status` once connected).
    """

    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    AUTH_FAILED = "auth_failed"
    CONFIG_FAILED = "config_failed"


@dataclass
class TuiState:
    """The TUI's complete state, split into three kinds of field.

    Authoritative (replaced wholesale by a successful refresh):
        `status`, `torrent_snapshot`.

    Derived (recomputed locally from authoritative state + ephemeral UI
    state, never fetched):
        `visible`.

    Ephemeral UI state (never persisted, never part of a collected
    model):
        everything else.
    """

    connection: ConnectionState = ConnectionState.CONNECTING
    status: StatusSnapshot | None = None
    torrent_snapshot: TorrentSelection | None = None
    filters: TorrentFilter = field(default_factory=TorrentFilter)
    search: str = ""
    visible: TorrentSelection | None = None
    focused_hash: str | None = None
    focused_tracker_details: list[dict[str, Any]] | None = None
    focused_details_fetched_at: datetime | None = None
    refreshing: bool = False
    stale: bool = False
    last_successful_refresh: datetime | None = None
    last_error: AppError | None = None
    active_panel: str = "torrents"

    def focused_torrent(self) -> SelectedTorrent | None:
        """Look up the focused torrent's live fields from `visible`.

        Deliberately a lookup, not a stored copy: name/state/progress/
        category/rates always come from the latest periodic snapshot,
        per docs/TUI_ARCHITECTURE_REVIEW.md §10 -- only tracker details
        are focus-scoped and cached (`focused_tracker_details`).
        """
        if self.focused_hash is None or self.visible is None:
            return None
        for torrent in self.visible.matched:
            if torrent.hash == self.focused_hash:
                return torrent
        return None


class TuiController:
    """Own `TuiState` and every state-mutating operation the TUI needs.

    Not a generic state-management framework: one class, direct method
    calls, no event bus, no reducers. `client_factory` defaults to
    `app.app_services.create_qbit_client` but is injectable for tests
    (a `tests.support.FakeQbitClient` factory) and for reconnect
    (calling it again after a recoverable failure).
    """

    def __init__(
        self,
        *,
        client_factory: Callable[[], Any] = create_qbit_client,
        host: str | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._host = host
        self._client: Any | None = None
        self._raw_torrents: list[Any] = []
        self.state = TuiState()

    def _ensure_client(self) -> Any:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def refresh(self) -> None:
        """Perform one periodic refresh tick.

        A no-op once `connection` has reached a terminal blocking state
        (`AUTH_FAILED`/`CONFIG_FAILED`): neither can self-heal without a
        process restart, so retrying every tick would be a pointless
        loop -- see docs/TUI_ARCHITECTURE_REVIEW.md §4/§6.

        Otherwise calls `app.app_services.collect_tui_refresh` -- exactly
        four qBittorrent API calls, `torrents_info()` shared between the
        status counters and the torrent snapshot (see
        `docs/TUI_ARCHITECTURE_REVIEW.md` §5). On a recoverable failure,
        the previous `status`/`torrent_snapshot` are kept (never
        replaced with an empty result) and `stale` is set; on an
        unrecognized exception, this re-raises instead of degrading --
        callers must not swallow it as "unavailable" forever.
        """
        if self.state.connection in (
            ConnectionState.AUTH_FAILED,
            ConnectionState.CONFIG_FAILED,
        ):
            return

        self.state.refreshing = True
        try:
            client = self._ensure_client()
            result: TuiRefreshResult = collect_tui_refresh(
                client, host=self._host
            )
        except ConfigError as error:
            self.state.refreshing = False
            self.mark_config_failed(
                AppError(
                    category=ErrorCategory.CONFIGURATION,
                    code="configuration_invalid",
                    message=str(error),
                )
            )
            return
        except Exception as error:
            self.state.refreshing = False
            failure = classify_recoverable_qbit_failure(error)
            if failure is None:
                raise
            # Force reconnect (a fresh login) on the next tick rather than
            # reusing a client that may be in a bad session state.
            self._client = None
            self.state.connection = (
                ConnectionState.AUTH_FAILED
                if failure.code == "authentication_failed"
                else ConnectionState.RECONNECTING
            )
            self.state.stale = self.state.status is not None
            self.state.last_error = AppError(
                category=(
                    ErrorCategory.AUTHENTICATION
                    if failure.code == "authentication_failed"
                    else ErrorCategory.UNAVAILABLE
                ),
                code=failure.code,
                message=failure.message,
            )
            return

        self._raw_torrents = result.raw_torrents
        self.state.status = result.status
        self.state.torrent_snapshot = result.torrents
        self.state.connection = ConnectionState.CONNECTED
        self.state.stale = False
        self.state.last_error = None
        self.state.last_successful_refresh = result.status.generated_at
        self.state.refreshing = False
        self._recompute_visible()
        self._reconcile_focus()

    def mark_config_failed(self, error: AppError) -> None:
        """Enter the blocking configuration-failure state.

        Called once, before the refresh loop ever starts (mirrors
        `status`/`doctor`'s pre-loop configuration check) -- never
        retried, since invalid local configuration cannot self-heal.
        """
        self.state.connection = ConnectionState.CONFIG_FAILED
        self.state.last_error = error

    def set_filters(self, filters: TorrentFilter) -> None:
        """Apply a new `TorrentFilter`. Zero qBittorrent API calls."""
        self.state.filters = filters
        self._recompute_visible()
        self._reconcile_focus()

    def set_search(self, text: str) -> None:
        """Apply read-only substring search text. Zero qBittorrent API calls."""
        self.state.search = text
        self._recompute_visible()
        self._reconcile_focus()

    def set_focus(self, torrent_hash: str | None) -> None:
        """Focus a torrent by its complete hash, or clear focus with `None`.

        Fetches tracker details for the newly focused torrent only --
        at most one `torrents_trackers()` call, never a scan of every
        torrent. A no-op if the hash is already focused (does not
        re-fetch on redundant calls).
        """
        if torrent_hash == self.state.focused_hash:
            return
        self.state.focused_hash = torrent_hash
        self.state.focused_tracker_details = None
        self.state.focused_details_fetched_at = None
        if torrent_hash is not None:
            self._fetch_focused_tracker_details()

    def clear_focus(self) -> None:
        """Clear focus and any cached focused-torrent details."""
        self.state.focused_hash = None
        self.state.focused_tracker_details = None
        self.state.focused_details_fetched_at = None

    def refresh_focused_details(self) -> None:
        """Manually re-fetch tracker details for the currently focused torrent.

        No-op when nothing is focused. Never triggered automatically by
        the periodic refresh tick -- see docs/TUI_ARCHITECTURE_REVIEW.md
        §10.
        """
        if self.state.focused_hash is not None:
            self._fetch_focused_tracker_details()

    def _fetch_focused_tracker_details(self) -> None:
        torrent_hash = self.state.focused_hash
        if torrent_hash is None:
            return

        try:
            client = self._ensure_client()
            raw_trackers = client.torrents_trackers(torrent_hash)
        except Exception as error:
            failure = classify_recoverable_qbit_failure(error)
            if failure is None:
                raise
            self.state.last_error = AppError(
                category=(
                    ErrorCategory.AUTHENTICATION
                    if failure.code == "authentication_failed"
                    else ErrorCategory.UNAVAILABLE
                ),
                code=failure.code,
                message=failure.message,
            )
            return

        # Safe, structural fields only -- never a raw announce URL, path,
        # query value, or unsanitized message (see app.torrents).
        self.state.focused_tracker_details = get_safe_tracker_details(
            raw_trackers
        )
        self.state.focused_details_fetched_at = datetime.now(UTC)

    def _recompute_visible(self) -> None:
        if self.state.torrent_snapshot is None:
            self.state.visible = None
            return

        filtered = select_torrents_from_items(
            self._raw_torrents, self.state.filters
        )
        if self.state.search:
            needle = self.state.search.casefold()
            matched = tuple(
                torrent
                for torrent in filtered.matched
                if needle in torrent.name.casefold()
            )
        else:
            matched = filtered.matched

        self.state.visible = replace(filtered, matched=matched)

    def _reconcile_focus(self) -> None:
        if self.state.focused_hash is None:
            return
        if self.state.focused_torrent() is None:
            self.clear_focus()
