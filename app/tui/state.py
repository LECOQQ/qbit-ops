"""Pure(-ish) TUI state and refresh controller, independent of Textual.

Everything here is directly unit-testable without a terminal or a real
Textual `App` -- see `tests/test_tui_state.py`. Widgets in `app/tui/app.py`
only ever read `TuiState` and call `TuiController` methods; they never
reimplement filtering, refresh sequencing, or failure classification.

Worker-hardening phase (see docs/DECISIONS.md): every method that
performs a blocking qBittorrent call is split into two halves so
`app.tui.app` can run the blocking half on a Textual thread worker and
apply the result on the UI thread, never the reverse:

* `collect_refresh()` / `apply_refresh_success()` / `apply_refresh_failure()`
  -- periodic refresh.
* `collect_tracker_details()` / `apply_tracker_details_success()` /
  `apply_tracker_details_failure()` -- focused-torrent tracker details,
  guarded by a monotonic `_detail_request_id` so a stale background
  result (an old focus target, or a fetch superseded by a manual
  refresh) can never overwrite a newer one -- see
  `begin_focus_change()`/`begin_manual_detail_refresh()`.

`refresh()`/`set_focus()`/`clear_focus()`/`refresh_focused_details()`
remain as fully synchronous convenience wrappers composing the pieces
above, used by this module's own test suite (and any future
non-threaded caller); the real TUI never calls them directly -- it
calls the granular `collect_*`/`apply_*`/`begin_*` methods itself, from
the appropriate thread. Both paths share the exact same logic, so
behavior cannot drift between "convenience" and "threaded" use.

Security boundary (see docs/TUI_ARCHITECTURE_REVIEW.md §10): this module
only ever imports safe, structured domain outputs. It must never import
`app.torrents.list_torrents_with_trackers`, `app.torrents._get_tracker_details`,
any `plan_*`/`apply_*` mutation function, or `app.main` -- enforced by
`tests/test_tui_security.py`.
"""

from __future__ import annotations

import threading
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

    Thread-safety: `_ensure_client()` is the only method more than one
    thread may call concurrently in practice (a periodic-refresh worker
    and a focused-detail worker can be in flight at the same time) --
    guarded by `_client_lock` so at most one of them ever constructs a
    new client. Every `apply_*` method mutates `self.state` and must
    only ever be called from the UI thread; every `collect_*` method
    performs blocking I/O only and touches no widget or reactive state,
    so it is safe to call from a background thread.
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
        self._client_lock = threading.Lock()
        self._raw_torrents: list[Any] = []
        self._detail_request_id = 0
        self.state = TuiState()

    def _ensure_client(self) -> Any:
        """Lazily create (or reuse) the qBittorrent client.

        Safe to call concurrently from more than one thread: the
        check-and-create sequence is guarded by `_client_lock` so a
        periodic-refresh worker and a focused-detail worker racing to
        reconnect after a failure can never both construct a client at
        once -- whichever acquires the lock first creates it, the other
        reuses that same instance.
        """
        with self._client_lock:
            if self._client is None:
                self._client = self._client_factory()
            return self._client

    # -- periodic refresh -------------------------------------------------

    def collect_refresh(self) -> TuiRefreshResult:
        """Perform one periodic refresh's blocking network work only.

        No state mutation -- safe to call from a background thread.
        Raises unchanged (`ConfigError`, or any qBittorrent/transport
        exception `app.app_services.classify_recoverable_qbit_failure`
        can classify, or a genuine internal defect); the caller applies
        the outcome via `apply_refresh_success`/`apply_refresh_failure`
        on the UI thread. Calling this while `state.connection` is
        already in a terminal blocking state is the caller's mistake to
        avoid (mirrors `refresh()`'s guard) -- this method itself has no
        opinion on when it is appropriate to call.
        """
        client = self._ensure_client()
        return collect_tui_refresh(client, host=self._host)

    def apply_refresh_success(self, result: TuiRefreshResult) -> None:
        """Apply a successful periodic refresh. UI-thread only."""
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

    def apply_refresh_failure(self, error: Exception) -> None:
        """Classify and apply a failed periodic refresh. UI-thread only.

        Re-raises unrecognized exceptions for the caller to treat as an
        unexpected internal error -- never silently degraded to
        "unavailable" forever, per
        `app.app_services.classify_recoverable_qbit_failure`'s contract.
        """
        self.state.refreshing = False
        if isinstance(error, ConfigError):
            self.mark_config_failed(
                AppError(
                    category=ErrorCategory.CONFIGURATION,
                    code="configuration_invalid",
                    message=str(error),
                )
            )
            return

        failure = classify_recoverable_qbit_failure(error)
        if failure is None:
            raise error
        # Force reconnect (a fresh login) on the next attempt rather than
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

    def refresh(self) -> None:
        """Perform one periodic refresh tick synchronously.

        A no-op once `connection` has reached a terminal blocking state
        (`AUTH_FAILED`/`CONFIG_FAILED`): neither can self-heal without a
        process restart, so retrying every tick would be a pointless
        loop -- see docs/TUI_ARCHITECTURE_REVIEW.md §4/§6.

        A synchronous convenience composing `collect_refresh()` and
        `apply_refresh_success()`/`apply_refresh_failure()`, kept for
        this module's own test suite and any non-threaded caller. The
        real TUI (`app.tui.app`) never calls this directly: it calls
        `collect_refresh()` from a Textual thread worker and applies the
        result on the UI thread instead, so qBittorrent I/O never blocks
        the event loop -- see docs/DECISIONS.md (worker hardening
        phase).
        """
        if self.state.connection in (
            ConnectionState.AUTH_FAILED,
            ConnectionState.CONFIG_FAILED,
        ):
            return

        self.state.refreshing = True
        try:
            result = self.collect_refresh()
        except Exception as error:
            self.apply_refresh_failure(error)
            return

        self.apply_refresh_success(result)

    def mark_config_failed(self, error: AppError) -> None:
        """Enter the blocking configuration-failure state.

        Called once, before the refresh loop ever starts (mirrors
        `status`/`doctor`'s pre-loop configuration check) -- never
        retried, since invalid local configuration cannot self-heal.
        """
        self.state.connection = ConnectionState.CONFIG_FAILED
        self.state.last_error = error

    # -- filters / search (always local, no I/O) ---------------------------

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

    # -- focused torrent / tracker details ----------------------------------

    def begin_focus_change(self, torrent_hash: str | None) -> int | None:
        """Update focus bookkeeping; return a request id for a background
        fetch, or `None` if no fetch is needed.

        A no-op (returns `None`, no state change at all) if
        `torrent_hash` already matches the current focus -- avoids a
        redundant re-fetch. Otherwise always bumps
        `_detail_request_id`, even when clearing focus
        (`torrent_hash=None`): this invalidates any already-in-flight
        background fetch for the torrent that was previously focused,
        so its result is discarded on arrival regardless of how it
        turns out -- see `apply_tracker_details_success`/`_failure`.
        """
        if torrent_hash == self.state.focused_hash:
            return None
        self.state.focused_hash = torrent_hash
        self.state.focused_tracker_details = None
        self.state.focused_details_fetched_at = None
        self._detail_request_id += 1
        if torrent_hash is None:
            return None
        return self._detail_request_id

    def begin_manual_detail_refresh(self) -> int | None:
        """Return a fresh request id for a manual (`r`) details refresh,
        or `None` if nothing is focused.

        Always bumps `_detail_request_id`, even though the focused hash
        is unchanged: this guarantees a slower, already-in-flight
        *automatic* fetch for the same torrent can never overwrite this
        newer, explicit request once both eventually complete -- only
        the result whose id matches the current one is ever applied.
        """
        if self.state.focused_hash is None:
            return None
        self._detail_request_id += 1
        return self._detail_request_id

    def clear_focus(self) -> None:
        """Clear focus and any cached focused-torrent details.

        Bumps `_detail_request_id` even though nothing is refetched, so
        any already-in-flight background fetch for the torrent that was
        focused is discarded when it eventually completes -- never
        calls `torrents_trackers()` itself.
        """
        self.state.focused_hash = None
        self.state.focused_tracker_details = None
        self.state.focused_details_fetched_at = None
        self._detail_request_id += 1

    def collect_tracker_details(self, torrent_hash: str) -> Any:
        """Perform the blocking `torrents_trackers()` call only.

        No state mutation -- safe to call from a background thread.
        Returns the raw qBittorrent tracker payload; the caller applies
        it via `apply_tracker_details_success`/`_failure` on the UI
        thread, which also enforces the stale-result guard.
        """
        client = self._ensure_client()
        return client.torrents_trackers(torrent_hash)

    def apply_tracker_details_success(
        self,
        request_id: int,
        torrent_hash: str,
        raw_trackers: Any,
    ) -> None:
        """Apply a successful tracker-details fetch. UI-thread only.

        Discarded (a no-op) if `request_id` no longer matches
        `_detail_request_id`: a newer focus change, manual refresh, or
        focus-clear happened after this fetch was started. This is the
        sole mechanism protecting against out-of-order completion for
        rapid focus movement (A -> B -> C: only C's result ever
        applies) -- it does not rely on cancelling the background
        thread, which cannot be done reliably once a blocking network
        call has started.
        """
        if request_id != self._detail_request_id:
            return
        # Safe, structural fields only -- never a raw announce URL, path,
        # query value, or unsanitized message (see app.torrents).
        self.state.focused_tracker_details = get_safe_tracker_details(
            raw_trackers
        )
        self.state.focused_details_fetched_at = datetime.now(UTC)

    def apply_tracker_details_failure(
        self,
        request_id: int,
        error: Exception,
    ) -> None:
        """Classify and apply a failed tracker-details fetch. UI-thread only.

        Also discarded if stale, per `apply_tracker_details_success`.
        Re-raises unrecognized exceptions for the caller to treat as an
        internal error.
        """
        if request_id != self._detail_request_id:
            return
        failure = classify_recoverable_qbit_failure(error)
        if failure is None:
            raise error
        self.state.last_error = AppError(
            category=(
                ErrorCategory.AUTHENTICATION
                if failure.code == "authentication_failed"
                else ErrorCategory.UNAVAILABLE
            ),
            code=failure.code,
            message=failure.message,
        )

    def set_focus(self, torrent_hash: str | None) -> None:
        """Focus a torrent by its complete hash, or clear focus with `None`.

        Synchronous convenience composing `begin_focus_change()`,
        `collect_tracker_details()`, and `apply_tracker_details_success()`/
        `_failure()` -- kept for this module's own test suite. The real
        TUI never calls this directly: it calls `begin_focus_change()`
        on the UI thread (fast, no I/O) and dispatches
        `collect_tracker_details()` to a background worker instead --
        see docs/DECISIONS.md (worker hardening phase).
        """
        request_id = self.begin_focus_change(torrent_hash)
        if request_id is None:
            return

        assert torrent_hash is not None  # begin_focus_change's own contract
        try:
            raw_trackers = self.collect_tracker_details(torrent_hash)
        except Exception as error:
            self.apply_tracker_details_failure(request_id, error)
            return

        self.apply_tracker_details_success(
            request_id, torrent_hash, raw_trackers
        )

    def refresh_focused_details(self) -> None:
        """Manually re-fetch tracker details for the currently focused torrent.

        No-op when nothing is focused. Never triggered automatically by
        the periodic refresh tick -- see docs/TUI_ARCHITECTURE_REVIEW.md
        §10. Synchronous convenience, same caveat as `set_focus()`.
        """
        request_id = self.begin_manual_detail_refresh()
        if request_id is None:
            return

        torrent_hash = self.state.focused_hash
        assert (
            torrent_hash is not None
        )  # begin_manual_detail_refresh's contract
        try:
            raw_trackers = self.collect_tracker_details(torrent_hash)
        except Exception as error:
            self.apply_tracker_details_failure(request_id, error)
            return

        self.apply_tracker_details_success(
            request_id, torrent_hash, raw_trackers
        )

    # -- internal -----------------------------------------------------------

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
