"""Pure(-ish) TUI state and refresh controller, independent of Textual.

Everything here is directly unit-testable without a terminal or a real
Textual `App` -- see `tests/test_tui_state.py`. Widgets in `qbit_ops/tui/app.py`
only ever read `TuiState` and call `TuiController` methods; they never
reimplement filtering, refresh sequencing, or failure classification.

Worker-hardening phase (see docs/DECISIONS.md): every method that
performs a blocking qBittorrent call is split into two halves so
`qbit_ops.tui.app` can run the blocking half on a Textual thread worker and
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

Security boundary (see docs/TUI_ARCHITECTURE_REVIEW.md §10, revised for
TUI 2 -- see docs/DECISIONS.md, "multi-selection + LOW-risk bulk
actions"): this module only ever imports safe, structured domain
outputs, plus exactly the two LOW-risk bulk torrent mutation functions
TUI 2 needs (`qbit_ops.torrents.apply_bulk_torrent_action`,
`build_bulk_action_plan_from_snapshot`) -- both act only on
Pause/Resume/Reannounce, take an already-frozen plan or an explicit
hash list, and never rescan or accept an unbounded selector. It must
never import `qbit_ops.torrents.list_torrents_with_trackers`,
`qbit_ops.torrents._get_tracker_details`,
`qbit_ops.torrents.plan_bulk_torrent_action`
(it always rescans and accepts `--all`, neither of which fits the
frozen-plan model here), any tracker mutation `plan_*`/`apply_*`
function, any deletion function, or `qbit_ops.main` -- enforced by
`tests/test_tui_security.py`.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from qbit_ops.app_services import (
    TuiRefreshResult,
    classify_recoverable_qbit_failure,
    collect_tui_refresh,
    create_qbit_client,
)
from qbit_ops.config import ConfigError
from qbit_ops.errors import AppError, ErrorCategory
from qbit_ops.execution import MutationStatus
from qbit_ops.explain import ExplanationReport, build_torrent_explanation
from qbit_ops.qbit_fields import get_field_as_string
from qbit_ops.status import StatusSnapshot
from qbit_ops.torrent_states import is_stopped_state
from qbit_ops.torrents import (
    BulkTorrentActionPlan,
    SelectedTorrent,
    TorrentBulkAction,
    TorrentFilter,
    TorrentSelection,
    apply_bulk_torrent_action,
    build_bulk_action_plan_from_snapshot,
    get_safe_tracker_details,
    select_torrents_from_items,
)

DEFAULT_REFRESH_INTERVAL_SECONDS = 5.0


class ConnectionState(StrEnum):
    """The TUI's high-level connection/session state.

    Distinct from `qbit_ops.status.Health`: this tracks whether the TUI *can
    talk to qBittorrent at all*, not the instance's own operational
    health (which is carried inside `TuiState.status` once connected).
    """

    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    AUTH_FAILED = "auth_failed"
    CONFIG_FAILED = "config_failed"


class Workspace(StrEnum):
    """The TUI's two top-level operator workspaces (Overview-first redesign).

    Purely a UI-navigation concern -- switching never triggers a
    qBittorrent call and never touches `filters`/`search`/`focused_hash`,
    all of which are preserved across a switch by construction (nothing
    in `set_workspace` resets them). See docs/DECISIONS.md (Overview
    redesign phase).
    """

    OVERVIEW = "overview"
    TORRENTS = "torrents"


@dataclass
class TuiState:
    """The TUI's complete state, split into three kinds of field.

    Authoritative (replaced wholesale by a successful refresh):
        `status`, `torrent_snapshot`, `stopped_count`.

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
    stopped_count: int = 0
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
    workspace: Workspace = Workspace.OVERVIEW
    selected_hashes: set[str] = field(default_factory=set)
    """Explicit multi-selection for bulk actions (TUI 2) -- full
    canonical hashes only. Deliberately distinct from `focused_hash`
    (never implied by focus alone), `visible` (the current filter/
    search result), and any CLI `--all` concept: `set()` always means
    "nothing selected", never "every torrent" -- see
    `TuiController.select_all_visible`/`reconcile_selection`.
    """

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
    `qbit_ops.app_services.create_qbit_client` but is injectable for tests
    (a `tests.support.FakeQbitClient` factory) and for reconnect
    (calling it again after a recoverable failure).

    Thread-safety -- **one remote-client coordinator** (audit finding
    F-2, see docs/audits/2026-07-26-tui-bulk-mutation-remediation.md):
    `_remote_lock` serializes *every* blocking qBittorrent operation the
    TUI can reach. There are exactly three such entry points, and all
    three take it for the whole duration of their call:

    * `collect_refresh()`          -- periodic refresh
    * `collect_tracker_details()`  -- focused/manual tracker details
    * `apply_bulk_plan()`          -- LOW-risk bulk mutation Apply

    This replaces the pre-TUI-2 posture (documented on `_ensure_client`
    until this remediation) that a periodic-refresh worker and a
    focused-detail worker could use the same client concurrently:
    `qbittorrentapi` is backed by a `requests.Session` whose
    multithreaded safety is not guaranteed, and a mutation overlapping a
    read was accepted limitation L-3 plus finding F-2. At most one
    remote operation now touches the client at a time -- provable via
    `max_concurrent_remote_operations`.

    The lock is only ever held on a worker thread, never on the UI
    thread, and never across widget rendering or user input. It is a
    plain `Lock`, not an `RLock`: no coordinated method calls another,
    and the post-Apply refresh is dispatched as a *separate* worker from
    the UI thread only after the mutation worker has already returned
    (and therefore released the lock), so nesting -- and deadlock --
    cannot occur. `_client_lock` remains a distinct, narrower lock
    guarding only lazy client *construction*; the acquisition order is
    always `_remote_lock` then `_client_lock`, never the reverse.

    Every `apply_*` method mutates `self.state` and must only ever be
    called from the UI thread; every `collect_*` method performs
    blocking I/O only and touches no widget or reactive state, so it is
    safe to call from a background thread.
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
        self._remote_lock = threading.Lock()
        self._remote_in_flight = 0
        self._max_concurrent_remote = 0
        self._raw_torrents: list[Any] = []
        self._detail_request_id = 0
        self.state = TuiState()

    @property
    def max_concurrent_remote_operations(self) -> int:
        """High-water mark of simultaneous remote client operations.

        Instrumentation for the F-2 regression tests: must never exceed
        `1`. Incremented/decremented inside `_remote_operation()`, so it
        measures actual overlap rather than merely asserting the lock
        exists.
        """
        return self._max_concurrent_remote

    @contextmanager
    def _remote_operation(self) -> Iterator[Any]:
        """Own the qBittorrent client exclusively for one blocking call.

        Yields the (lazily created) client. Worker threads only -- see
        the class docstring. The in-flight accounting lives inside the
        lock, so `max_concurrent_remote_operations` is an honest
        measurement of overlap, not a restatement of the lock's
        existence.
        """
        with self._remote_lock:
            self._remote_in_flight += 1
            self._max_concurrent_remote = max(
                self._max_concurrent_remote, self._remote_in_flight
            )
            try:
                yield self._ensure_client()
            finally:
                self._remote_in_flight -= 1

    def _ensure_client(self) -> Any:
        """Lazily create (or reuse) the qBittorrent client.

        Guarded by `_client_lock` so two callers racing to reconnect
        after a failure can never both construct a client at once.
        Callers performing a *blocking* call must go through
        `_remote_operation()` instead of calling this directly -- that
        is what serializes the call itself (see the class docstring);
        this method only makes construction safe.
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
        exception `qbit_ops.app_services.classify_recoverable_qbit_failure`
        can classify, or a genuine internal defect); the caller applies
        the outcome via `apply_refresh_success`/`apply_refresh_failure`
        on the UI thread. Calling this while `state.connection` is
        already in a terminal blocking state is the caller's mistake to
        avoid (mirrors `refresh()`'s guard) -- this method itself has no
        opinion on when it is appropriate to call.
        """
        with self._remote_operation() as client:
            return collect_tui_refresh(client, host=self._host)

    def apply_refresh_success(
        self, result: TuiRefreshResult, *, reconcile: bool = True
    ) -> None:
        """Apply a successful periodic refresh. UI-thread only.

        `reconcile=False` suppresses only the *selection* reconciliation
        (audit finding F-1): the raw snapshot, status, counts, visible
        set, and focus are always updated, since a refresh must never
        show stale data merely because a modal is open. The caller
        passes `False` while a `FiltersScreen` holds an uncommitted
        draft -- a category filter is exact-match, so a half-typed
        "films" transiently matches nothing and would silently destroy
        a selection the operator patiently built, which is exactly what
        `set_filters`'s documented deferral exists to prevent. The
        existing commit points (Apply/Clear/Cancel) reconcile instead;
        this knowledge lives in `qbit_ops.tui.app`, which owns the screen
        stack, rather than teaching the controller what a modal is.

        Precisely what is and is not deferred (closure review D-1 --
        the earlier wording here described the mechanism backwards):
        `set_filters()` **is** called on every keystroke, by
        `qbit_ops.tui.app._apply_filters_from_panel` via `on_input_changed`,
        so `state.filters` genuinely holds the partial draft and
        `_recompute_visible()` genuinely uses it -- with only "f" typed,
        `visible` is empty. What the `reconcile` flag defers is *only*
        the selection reconciliation, never the filter itself. Do not
        "simplify" this parameter away as redundant: without it, a tick
        landing mid-typing would drop the whole selection against a
        filter the operator has not finished writing.
        """
        self._raw_torrents = result.raw_torrents
        self.state.status = result.status
        self.state.torrent_snapshot = result.torrents
        self.state.stopped_count = _count_stopped_torrents(result.raw_torrents)
        self.state.connection = ConnectionState.CONNECTED
        self.state.stale = False
        self.state.last_error = None
        self.state.last_successful_refresh = result.status.generated_at
        self.state.refreshing = False
        self._recompute_visible()
        self._reconcile_focus()
        if reconcile:
            self.reconcile_selection()

    def apply_refresh_failure(self, error: Exception) -> None:
        """Classify and apply a failed periodic refresh. UI-thread only.

        Re-raises unrecognized exceptions for the caller to treat as an
        unexpected internal error -- never silently degraded to
        "unavailable" forever, per
        `qbit_ops.app_services.classify_recoverable_qbit_failure`'s contract.
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
        real TUI (`qbit_ops.tui.app`) never calls this directly: it calls
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

    # -- workspace navigation (always local, no I/O) ------------------------

    def set_workspace(self, workspace: Workspace) -> None:
        """Switch the active workspace. Zero qBittorrent API calls.

        Deliberately touches nothing else: `filters`, `search`,
        `focused_hash`, and `focused_tracker_details` all survive a
        workspace switch untouched, per the Overview redesign's explicit
        "preserves search and filter state" / "preserves the last
        focused torrent" requirements. `qbit_ops.tui.app` is responsible for
        not leaving a widget from the now-hidden workspace focused.
        """
        self.state.workspace = workspace

    # -- filters / search (always local, no I/O) ---------------------------

    def set_filters(self, filters: TorrentFilter) -> None:
        """Apply a new `TorrentFilter`. Zero qBittorrent API calls.

        Deliberately does *not* reconcile the selection itself: the
        Filters modal applies live, on every keystroke (see
        `qbit_ops.tui.app._apply_filters_from_panel`), and a category filter
        is an *exact*-match field -- typing "films" one letter at a
        time transiently matches nothing (`"f"`, `"fi"`, ... never
        equal "films"), which would otherwise wipe out a selection
        before the user finishes typing the very filter that would
        have kept it visible. The caller reconciles explicitly, once,
        at each real commit point instead (`reconcile_selection()` --
        see `qbit_ops.tui.app.action_activate`'s `FiltersScreen` branch and
        `FiltersScreen.action_clear`/`QbitOpsTuiApp.action_dismiss_overlay`'s
        cancel-revert branch).
        """
        self.state.filters = filters
        self._recompute_visible()
        self._reconcile_focus()

    def set_search(self, text: str) -> int:
        """Apply read-only search text against name and hash.

        Matches a torrent whose name contains `text` as a case-
        insensitive substring, OR whose hash starts with `text`
        case-insensitively (covers both a full hash and a leading
        prefix) -- no fuzzy scoring, no ambiguity error, since this is a
        read-only list filter, not a target-selection lookup. Zero
        qBittorrent API calls. If the currently focused torrent no
        longer matches, `_reconcile_focus()` clears focus (and any
        pending detail fetch) for it -- see `clear_focus()`. Returns
        the number of selected hashes hidden by the new search and
        dropped from the selection (see `reconcile_selection()`).

        Unlike `set_filters()`, search reconciles live on every
        keystroke: a substring match only ever *narrows* while typing
        forward (every prefix of a matching string still matches), so
        the same transient-wipe risk does not apply the same way, and
        there is no separate "commit" step in the UI to hook instead.
        """
        self.state.search = text
        self._recompute_visible()
        self._reconcile_focus()
        return self.reconcile_selection()

    # -- multi-selection (always local, no I/O) -----------------------------

    def visible_hashes(self) -> frozenset[str]:
        """The set of full hashes currently visible (post filter/search)."""
        if self.state.visible is None:
            return frozenset()
        return frozenset(torrent.hash for torrent in self.state.visible.matched)

    def toggle_selection(self, torrent_hash: str) -> None:
        """Toggle one torrent's membership in the selection.

        Never implied by focus -- this is the only way a torrent enters
        `selected_hashes`. Selecting a torrent that is not currently
        visible is the caller's mistake to avoid; this method itself
        does not check visibility (a toggle always targets one exact,
        already-known hash, e.g. the currently focused row).
        """
        if torrent_hash in self.state.selected_hashes:
            self.state.selected_hashes.discard(torrent_hash)
        else:
            self.state.selected_hashes.add(torrent_hash)

    def select_all_visible(self) -> None:
        """Select every currently visible torrent -- never a hidden one
        and never "the whole instance". Zero qBittorrent API calls."""
        self.state.selected_hashes = set(self.visible_hashes())

    def clear_selection(self) -> None:
        """Clear the selection entirely. Zero qBittorrent API calls."""
        self.state.selected_hashes = set()

    def reconcile_selection(self) -> int:
        """Drop any selected hash no longer visible. UI-thread only.

        The safety-first policy TUI 2 is built on: "the actionable
        selection is always a subset of the currently visible
        torrents." Called after every filter/search change and every
        successful periodic refresh (see `set_filters`/`set_search`/
        `apply_refresh_success`) -- a torrent that becomes hidden or
        disappears is never left selectable-but-invisible. Returns the
        number of hashes removed, so the caller can show a concise
        "N hidden selections cleared" notification; `0` when nothing
        changed (the common case), never itself a reason to notify.
        """
        visible = self.visible_hashes()
        hidden = self.state.selected_hashes - visible
        if not hidden:
            return 0
        self.state.selected_hashes -= hidden
        return len(hidden)

    def clear_selection_for(self, hashes: Iterable[str]) -> None:
        """Remove exactly `hashes` from the selection.

        Used after a result modal is dismissed to clear only the
        torrents an applied plan actually acted on (`plan.changes`),
        per TUI 2's documented dismissal policy -- a skipped or
        unavailable torrent's selection is left untouched so the
        operator can reconsider it, rather than being silently cleared
        alongside the ones that did change.
        """
        self.state.selected_hashes -= set(hashes)

    # -- bulk actions (LOW-risk only: pause / resume / reannounce) ----------

    def build_bulk_plan(
        self,
        action: TorrentBulkAction,
        torrent_hashes: tuple[str, ...],
    ) -> BulkTorrentActionPlan:
        """Build a frozen bulk-action plan -- pure, zero API calls.

        `torrent_hashes` must already be an immutable, sorted snapshot
        (see `QbitOpsTuiApp.action_open_actions`, which captures
        `tuple(sorted(selected_hashes))` at the moment an action is
        chosen) -- this method never reads `self.state.selected_hashes`
        itself, so a selection change after this call can never affect
        an already-built plan. Delegates every skip-reason decision to
        `qbit_ops.torrents.build_bulk_action_plan_from_snapshot` (the same
        rule `plan_bulk_torrent_action`/the CLI uses) against the
        current `_raw_torrents` snapshot -- no second rule catalogue,
        no rescan.
        """
        return build_bulk_action_plan_from_snapshot(
            self._raw_torrents, action, torrent_hashes
        )

    def classify_plan_status(
        self, plan: BulkTorrentActionPlan
    ) -> MutationStatus:
        """Classify a plan before Apply -- pure.

        Follows `qbit_ops.main`'s shared `_run_mutation` mapping for the two
        cases the CLI can actually produce, with one TUI-specific
        refinement (audit finding F-3): a selection whose torrents all
        *disappeared* between the Actions snapshot and the plan build
        yields `matched > 0` with only `not_found` skips, which the CLI
        mapping alone would report as `NO_CHANGES` -- i.e. "already
        satisfied", which is untrue and misleading. Only the TUI can
        reach that state (its selection is captured ahead of planning,
        unlike a CLI selector resolved in one pass), so the refinement
        lives here rather than in the shared
        `build_bulk_action_plan_from_snapshot` model.

        `NO_MATCH`   -- nothing actionable was *found*.
        `NO_CHANGES` -- targets were found and already satisfied.
        `PREVIEW`    -- there is at least one real change to apply.
        """
        if plan.matched == 0:
            return MutationStatus.NO_MATCH
        if plan.changes:
            return MutationStatus.PREVIEW
        if all(skip.reason == "not_found" for skip in plan.skipped):
            return MutationStatus.NO_MATCH
        return MutationStatus.NO_CHANGES

    def apply_bulk_plan(
        self,
        plan: BulkTorrentActionPlan,
        *,
        should_proceed: Callable[[], bool] | None = None,
    ) -> bool:
        """Apply an already-frozen plan. Blocking I/O -- safe on a
        background worker thread, never the UI thread.

        Mutates exactly `plan.changes`; never rescans, never re-reads
        `selected_hashes`, never substitutes a different target. May
        raise -- the caller classifies the failure via
        `classify_recoverable_qbit_failure`, exactly like
        `collect_refresh`/`collect_tracker_details`.

        Returns whether the mutation was actually dispatched.

        `should_proceed` is a last-moment authority check evaluated
        **inside** `_remote_operation()`, i.e. *after* the shared remote
        lock has been acquired and immediately before any qBittorrent
        call (closure review finding N-2). This placement is the whole
        point: serializing remote access (F-2) means a mutation can now
        sit queued behind a blocked read for an unbounded time, so an
        authority check performed before the wait -- or by the caller
        before dispatching the worker -- proves nothing about the state
        of the world when the call would finally go out. The TUI passes
        `lambda: self.is_running`, so quitting while an Apply is queued
        means the request is never sent at all.

        When the predicate is false nothing is called, nothing raises,
        and `False` is returned so the caller can report a
        cancelled-before-dispatch outcome that is distinct from both a
        remote failure and a successful submission.
        """
        with self._remote_operation() as client:
            if should_proceed is not None and not should_proceed():
                return False
            apply_bulk_torrent_action(client, plan)
            return True

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

    @property
    def detail_request_id(self) -> int:
        """The current monotonic detail-fetch generation.

        Read-only outside this module -- `qbit_ops.tui.app` uses it only to
        confirm, at the moment a deferred Explain result is about to be
        applied, that no newer focus change or manual refresh has
        superseded the request it was waiting for (see
        `QbitOpsTuiApp.action_explain`/`_on_detail_worker_state_changed`).
        """
        return self._detail_request_id

    def raw_torrent_by_hash(self, torrent_hash: str) -> Any | None:
        """Look up one already-fetched raw `torrents_info()` item by hash.

        Pure, no I/O -- reads the same cached `_raw_torrents` list
        `_recompute_visible()` re-filters from. Returns `None` if the
        torrent is no longer present in the latest snapshot (e.g. it was
        removed from qBittorrent since it was focused).
        """
        for item in self._raw_torrents:
            if (
                get_field_as_string(item, "hash").lower()
                == torrent_hash.lower()
            ):
                return item
        return None

    def build_explanation(self) -> ExplanationReport | None:
        """Build an `ExplanationReport` for the focused torrent -- zero
        API calls, deterministic.

        Delegates every rule-evaluation decision to
        `qbit_ops.explain.build_torrent_explanation`, the exact same pure
        builder `qbit_ops.explain.explain_torrent` (the CLI's entry point)
        uses -- there is no second, TUI-only explanation catalogue.
        Returns `None` when nothing is focused, or when the focused
        torrent is no longer present in the latest raw snapshot (it
        disappeared) -- the caller must treat that as "not found", never
        open a modal, and notify instead.
        """
        if self.state.focused_hash is None:
            return None
        raw_torrent = self.raw_torrent_by_hash(self.state.focused_hash)
        if raw_torrent is None:
            return None
        return build_torrent_explanation(
            raw_torrent,
            self.state.focused_tracker_details,
            generated_at=datetime.now(UTC),
        )

    def collect_tracker_details(self, torrent_hash: str) -> Any:
        """Perform the blocking `torrents_trackers()` call only.

        No state mutation -- safe to call from a background thread.
        Returns the raw qBittorrent tracker payload; the caller applies
        it via `apply_tracker_details_success`/`_failure` on the UI
        thread, which also enforces the stale-result guard.
        """
        with self._remote_operation() as client:
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
        # query value, or unsanitized message (see qbit_ops.torrents).
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
                or torrent.hash.casefold().startswith(needle)
            )
        else:
            matched = filtered.matched

        self.state.visible = replace(filtered, matched=matched)

    def _reconcile_focus(self) -> None:
        if self.state.focused_hash is None:
            return
        if self.state.focused_torrent() is None:
            self.clear_focus()


def _count_stopped_torrents(raw_torrents: list[Any]) -> int:
    """Count paused/stopped torrents using the existing state helper.

    Deliberately *not* a new classifier and not folded into
    `qbit_ops.status.TransferCounts`: `classify_torrent_state` already
    reports every paused/stopped torrent under `downloading` or
    `seeding` (by direction), which is the classification `status`/
    `torrents list` are built around and must not change. The Overview
    wants this as an *additional*, overlapping breakdown for operators
    ("how many of my torrents are just sitting paused"), reusing
    `qbit_ops.torrent_states.is_stopped_state` (already the single source of
    truth for the qBittorrent 4 `paused*` vs 5 `stopped*` naming split)
    rather than inventing a second state vocabulary.
    """
    return sum(
        1
        for torrent in raw_torrents
        if is_stopped_state(get_field_as_string(torrent, "state"))
    )
