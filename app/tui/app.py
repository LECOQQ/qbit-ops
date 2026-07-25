"""Textual application for `qbit-ops tui` (TUI 1, read-only).

Security boundary (docs/TUI_ARCHITECTURE_REVIEW.md §10, enforced by
`tests/test_tui_security.py`): this module and every other module under
`app/tui/` must never import `app.main`, any mutation `plan_*`/`apply_*`
function, `app.torrents.list_torrents_with_trackers`, or
`app.torrents._get_tracker_details`. Widgets only ever render safe,
structured domain outputs (`StatusSnapshot`, `SelectedTorrent`,
`get_safe_tracker_details` output, `AppError`).

Worker-hardening phase (see docs/DECISIONS.md): every qBittorrent API
call runs on a Textual thread worker (`run_worker(thread=True)`), never
on the UI thread -- the qBittorrent client is synchronous
(`qbittorrentapi`/`requests`), so a slow or unreachable instance must
never freeze key handling, filtering, search, or `q`. Two worker
groups, `REFRESH_WORKER_GROUP` (periodic refresh, at most one in
flight -- a tick that fires while the previous one is still running is
skipped, not queued) and `DETAIL_WORKER_GROUP` (focused-torrent tracker
details, guarded by `TuiController`'s monotonic `_detail_request_id` so
rapid focus movement A -> B -> C only ever applies C's result). Every
worker body is a plain function that never raises -- it returns a
tagged `(..., error)` tuple instead -- and only ever calls
`collect_*`/pure-network methods on `app.tui.state.TuiController`;
`apply_*` (state-mutating) methods are only ever called from
`on_worker_state_changed`, which Textual always delivers on the UI
thread. See `_start_periodic_refresh`/`_focus_torrent`/
`action_refresh_details` and `on_worker_state_changed`.

A worker thread physically blocked inside `requests`/`qbittorrentapi`
(e.g. an unreachable host with no response) cannot be forcibly killed
by Python; quitting (`q`) stops the UI and restores the terminal
immediately regardless, but the underlying OS process may not fully
exit until that thread's call returns or times out at the transport
level -- see docs/DECISIONS.md for the empirical basis of this
statement and why it is accepted rather than "fixed" with a custom
async HTTP client (out of scope for this phase).

Hotfix phase (see docs/DECISIONS.md): the filters/details panels are
reusable widgets with class-scoped (not id-scoped) internal children,
so the exact same `FiltersPanel`/`DetailsPanel` classes can be mounted
both inline (wide layout) and inside a modal screen (narrow layout).
See `_apply_filters_from_panel`/`_render_details_panels`, which keep
*every* mounted instance of each in sync, whichever is currently
visible. The help screen (`?`) is always a separate modal
(`HelpScreen`) at every width -- it no longer borrows the Details
panel as a temporary display area.
"""

from __future__ import annotations

from typing import Any

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen, Screen
from textual.widgets import Checkbox, DataTable, Footer, Input, Static
from textual.worker import Worker, WorkerState

from app.app_services import TuiRefreshResult, create_qbit_client
from app.status import Health
from app.torrents import (
    SelectedTorrent,
    TorrentFilter,
    build_torrent_filter,
    describe_torrent_filter,
)
from app.tui.state import (
    DEFAULT_REFRESH_INTERVAL_SECONDS,
    ConnectionState,
    TuiController,
    TuiState,
)

NARROW_WIDTH_THRESHOLD = 100

# Worker group names -- used to tell a periodic-refresh worker's
# `Worker.StateChanged` message apart from a focused-detail worker's in
# `on_worker_state_changed` (Textual delivers both through the same
# handler). See docs/DECISIONS.md (worker hardening phase) for the full
# threading design.
REFRESH_WORKER_GROUP = "qbit-refresh"
DETAIL_WORKER_GROUP = "qbit-detail"

_HEALTH_STYLES: dict[Health, str] = {
    Health.HEALTHY: "bold green",
    Health.WARNING: "bold yellow",
    Health.CRITICAL: "bold red",
    Health.UNAVAILABLE: "bold red",
}


class StatusHeader(Static):
    """The top status bar: health, transfer rates, last refresh, staleness."""


class FilterSummary(Static):
    """A concise, always-visible line describing the active filter/search.

    e.g. "146 shown / 1105 · stalled" or
    "24 shown / 1105 · category: films · stalled · search: ubuntu" --
    see docs/COMMANDS.md ("TUI"). Purely presentational: derived from
    `TuiState`, never fetched.
    """


class ConnectionBanner(Static):
    """A dismissible-looking, non-blocking banner shown while reconnecting.

    Never replaces the torrent table underneath it -- see
    docs/TUI_ARCHITECTURE_REVIEW.md §5/§6 (stale data stays visible).
    """


class FiltersPanel(Vertical):
    """The shared `TorrentFilter` vocabulary, applied entirely in memory.

    No qBittorrent API call is ever triggered by a change here. Internal
    children are identified by CSS *class*, not `id`, so more than one
    `FiltersPanel` instance (the always-mounted inline one plus a modal
    one opened by `f` in narrow layouts) can coexist in the DOM without
    an id collision -- every query below is scoped to `self`.
    """

    def compose(self) -> ComposeResult:
        yield Static("[bold]Filters[/bold]")
        yield Input(
            placeholder="category (comma-separated)", classes="f-category"
        )
        yield Input(placeholder="state (comma-separated)", classes="f-state")
        yield Checkbox("Completed", classes="f-completed")
        yield Checkbox("Incomplete", classes="f-incomplete")
        yield Checkbox("Active", classes="f-active")
        yield Checkbox("Inactive", classes="f-inactive")
        yield Checkbox("Stalled", classes="f-stalled")
        yield Checkbox("Errored", classes="f-errored")
        yield Static("", classes="f-error")

    def build_filter(self) -> TorrentFilter:
        """Build a `TorrentFilter` from this panel's own widget values."""
        category_text = self.query_one(".f-category", Input).value
        state_text = self.query_one(".f-state", Input).value
        categories = [
            part.strip() for part in category_text.split(",") if part.strip()
        ]
        states = [
            part.strip() for part in state_text.split(",") if part.strip()
        ]

        return build_torrent_filter(
            categories=categories,
            states=states,
            completed=self.query_one(".f-completed", Checkbox).value,
            incomplete=self.query_one(".f-incomplete", Checkbox).value,
            active=self.query_one(".f-active", Checkbox).value,
            inactive=self.query_one(".f-inactive", Checkbox).value,
            stalled=self.query_one(".f-stalled", Checkbox).value,
            errored=self.query_one(".f-errored", Checkbox).value,
        )

    def sync_from(self, filters: TorrentFilter) -> None:
        """Reflect an already-applied `TorrentFilter` in this panel's widgets.

        Used when opening the narrow-layout filters modal, so it shows
        the filter currently in effect rather than resetting it blank.
        """
        self.query_one(".f-category", Input).value = ", ".join(
            filters.categories
        )
        self.query_one(".f-state", Input).value = ", ".join(filters.states)
        self.query_one(".f-completed", Checkbox).value = (
            filters.completed is True
        )
        self.query_one(".f-incomplete", Checkbox).value = (
            filters.completed is False
        )
        self.query_one(".f-active", Checkbox).value = filters.active is True
        self.query_one(".f-inactive", Checkbox).value = filters.active is False
        self.query_one(".f-stalled", Checkbox).value = bool(filters.stalled)
        self.query_one(".f-errored", Checkbox).value = bool(filters.errored)

    def show_error(self, message: str) -> None:
        self.query_one(".f-error", Static).update(message)


class DetailsPanel(VerticalScroll):
    """Safe details for the focused torrent.

    Only ever renders `SelectedTorrent` fields (live from the periodic
    snapshot) and `get_safe_tracker_details`-shaped structural tracker
    fields -- never a raw announce URL, path, query value, userinfo, or
    unsanitized message. No internal `id`s, for the same multi-instance
    reason as `FiltersPanel`.
    """

    def render_state(self, state: TuiState) -> None:
        """Render the currently focused torrent's safe details, or an
        explicit empty state when nothing is focused."""
        self.remove_children()
        torrent = state.focused_torrent()

        if torrent is None:
            self.mount(Static("No torrent focused."))
            return

        lines = [
            f"[bold]{torrent.name}[/bold]",
            f"Hash: {torrent.hash}",
            f"State: {torrent.state}",
            f"Category: {torrent.category}",
            f"Progress: {torrent.progress * 100:.1f}%",
            f"Ratio: {torrent.ratio:.2f}",
            f"Down: {_format_byte_rate(torrent.download_rate)}",
            f"Up: {_format_byte_rate(torrent.upload_rate)}",
        ]
        self.mount(Static("\n".join(lines)))

        tracker_details = state.focused_tracker_details
        if tracker_details is None:
            self.mount(Static("Trackers: loading..."))
        else:
            fetched_at = state.focused_details_fetched_at
            freshness = (
                fetched_at.strftime("fetched %H:%M:%S UTC")
                if fetched_at is not None
                else ""
            )
            self.mount(Static(f"[bold]Trackers[/bold] ({freshness})"))
            if not tracker_details:
                self.mount(Static("  (none)"))
            for endpoint in tracker_details:
                self.mount(Static(f"  {_format_endpoint(endpoint)}"))


class HelpScreen(ModalScreen[None]):
    """A real, dedicated help screen listing only bindings that work."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close", priority=True),
        Binding("question_mark", "dismiss", "Close"),
    ]

    CSS = """
    HelpScreen {
        align: center middle;
    }
    #help-dialog {
        width: 60;
        height: auto;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            yield Static(_HELP_TEXT)

    def action_dismiss(self, result: None = None) -> None:  # type: ignore[override]
        self.dismiss()


class FiltersScreen(ModalScreen[None]):
    """A modal Filters panel -- the narrow-layout's access path to
    filters, since the inline `FiltersPanel` is hidden below
    `NARROW_WIDTH_THRESHOLD`."""

    BINDINGS = [Binding("escape", "dismiss", "Close", priority=True)]

    CSS = """
    FiltersScreen {
        align: center middle;
    }
    #filters-dialog {
        width: 44;
        height: auto;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    def __init__(self, current_filters: TorrentFilter) -> None:
        super().__init__()
        self._current_filters = current_filters

    def compose(self) -> ComposeResult:
        with Vertical(id="filters-dialog"):
            yield FiltersPanel()
            yield Static("[dim]Esc to close[/dim]")

    def on_mount(self) -> None:
        self.query_one(FiltersPanel).sync_from(self._current_filters)

    def action_dismiss(self, result: None = None) -> None:  # type: ignore[override]
        self.dismiss()


class DetailsScreen(ModalScreen[None]):
    """A modal Details panel -- the narrow-layout's access path to the
    focused torrent's details, opened by `enter`."""

    BINDINGS = [Binding("escape", "dismiss", "Close", priority=True)]

    CSS = """
    DetailsScreen {
        align: center middle;
    }
    #details-dialog {
        width: 50;
        height: 80%;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="details-dialog"):
            yield DetailsPanel()
            yield Static("[dim]Esc to close[/dim]")

    def on_mount(self) -> None:
        assert isinstance(self.app, QbitOpsTuiApp)
        self.query_one(DetailsPanel).render_state(self.app.controller.state)

    def action_dismiss(self, result: None = None) -> None:  # type: ignore[override]
        self.dismiss()


class MainScreen(Screen[None]):
    """The app's single primary screen.

    A custom subclass exists only so `on_resize` fires reliably: Textual
    dispatches a real terminal resize's `on_resize` to the *Screen*, not
    the *App* (`App._on_resize`/`Screen._on_resize` both call
    `event.stop()`, which stops the underlying event's public dispatch
    on the App node but not on the Screen node -- verified empirically).
    An `App`-level `on_resize` override, as this project shipped
    initially, silently never ran.
    """

    def on_resize(self, event: events.Resize) -> None:
        is_narrow = event.size.width < NARROW_WIDTH_THRESHOLD
        self.set_class(is_narrow, "narrow")

        # Never leave a now-hidden inline Filters/Details widget focused
        # -- an invisible focused widget can neither be seen nor
        # meaningfully typed into. Skip while a modal is open: its own
        # Filters/Details instance is a separate, still-visible widget
        # on a different screen, and this screen's focus is irrelevant
        # while it isn't on top.
        app = self.app
        if not is_narrow or app.focused is None or len(app.screen_stack) > 1:
            return

        try:
            app.focused.query_ancestor("FiltersPanel, DetailsPanel")
        except NoMatches:
            return
        self.query_one("#torrents", DataTable).focus()


class QbitOpsTuiApp(App[None]):
    """The read-only TUI 1 vertical slice."""

    # Textual's built-in Ctrl+P command palette has no qbit-ops commands
    # yet and only confused dogfooders ("^p palette" in the footer) --
    # disabled until there is a meaningful palette to show.
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    Screen {
        layout: vertical;
    }
    #status-header {
        height: 3;
        padding: 0 1;
        border: solid $accent;
    }
    #filter-summary {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    #banner {
        height: auto;
        padding: 0 1;
        background: $warning-darken-2;
        display: none;
    }
    #banner.visible {
        display: block;
    }
    #main {
        height: 1fr;
    }
    #main > FiltersPanel {
        width: 26;
        border: solid $accent;
        padding: 0 1;
    }
    DataTable {
        width: 1fr;
    }
    #main > DetailsPanel {
        width: 36;
        border: solid $accent;
        padding: 0 1;
    }
    Screen.narrow #main > FiltersPanel {
        display: none;
    }
    Screen.narrow #main > DetailsPanel {
        display: none;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("slash", "focus_search", "Search"),
        Binding("f", "open_filters", "Filters"),
        # `priority=True`: `DataTable` binds its own `enter` to
        # `select_cursor` (a no-op `RowSelected` message we don't
        # handle) -- without priority, that binding wins while the
        # table has focus and "open details" silently never fires.
        # Verified empirically that priority *does* override a focused
        # child widget's own declarative bindings (unlike `Input`'s
        # printable-character handling, which bypasses the bindings
        # system entirely -- see the `escape` binding's comment above).
        Binding("enter", "open_details", "Details", show=False, priority=True),
        Binding("r", "refresh_details", "Refresh details"),
        Binding("question_mark", "toggle_help", "Help"),
        # `escape` must win over whatever has focus (a filter/search
        # Input never binds it, but priority makes the intent explicit
        # and future-proof) -- see docs/DECISIONS.md for why `/`/`f`/`?`
        # cannot use `priority=True` the same way (Textual's `Input`
        # consumes printable characters before bindings are resolved,
        # regardless of priority; verified empirically).
        Binding("escape", "dismiss_overlay", "Back", priority=True),
    ]

    def __init__(
        self,
        *,
        client_factory: Any = create_qbit_client,
        host: str | None = None,
        refresh_interval: float = DEFAULT_REFRESH_INTERVAL_SECONDS,
    ) -> None:
        super().__init__()
        self.controller = TuiController(
            client_factory=client_factory, host=host
        )
        self.refresh_interval = refresh_interval
        self._hash_by_row: dict[int, str] = {}
        self._rebuilding_table = False
        self._refresh_worker: Worker[Any] | None = None
        self._last_detail_worker: Worker[Any] | None = None

    def get_default_screen(self) -> Screen[None]:
        return MainScreen(id="_default")

    def compose(self) -> ComposeResult:
        yield StatusHeader(id="status-header")
        yield FilterSummary(id="filter-summary")
        yield ConnectionBanner(id="banner")
        with Horizontal(id="main"):
            yield FiltersPanel()
            yield DataTable(id="torrents", cursor_type="row")
            yield DetailsPanel()
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#torrents", DataTable)
        table.add_columns("Name", "State", "Progress", "Down", "Up", "Ratio")
        # The torrent table is the primary panel: focus it by default so
        # single-letter bindings (q, f, /, r, ?) reach the App instead of
        # being consumed as text by whichever Input happens to be first
        # in the DOM.
        table.focus()
        # Render the initial (empty) state immediately -- pure, no I/O --
        # so the screen never sits blank while the first refresh worker
        # is still in flight.
        self._render_all()
        self.set_interval(self.refresh_interval, self._start_periodic_refresh)
        self._start_periodic_refresh()

    # -- refresh -----------------------------------------------------

    def _start_periodic_refresh(self) -> None:
        """Start one periodic refresh tick, unless one is already running.

        Deterministic coalescing, not queueing: a tick that fires while
        the previous refresh's worker is still in flight is simply
        skipped this cycle -- `set_interval` keeps firing at its normal
        cadence regardless (this method itself never blocks), and the
        very next tick tries again. This is also what guarantees at
        most one `torrents_info()`/`transfer_info()`/`app_version()`/
        `app_web_api_version()` set of calls is ever in flight at once
        -- never two periodic refreshes hitting the client concurrently.
        """
        if (
            self._refresh_worker is not None
            and not self._refresh_worker.is_finished
        ):
            return

        self.controller.state.refreshing = True
        self._refresh_worker = self.run_worker(
            self._refresh_worker_body,
            group=REFRESH_WORKER_GROUP,
            thread=True,
            exit_on_error=False,
        )

    def _refresh_worker_body(
        self,
    ) -> tuple[TuiRefreshResult | None, Exception | None]:
        """Run on a background thread: blocking I/O only, never state
        mutation, and never raises -- the outcome (success or failure)
        travels back as a plain tagged tuple so the UI-thread handler
        (`_on_refresh_worker_state_changed`) does not need to depend on
        Textual's `WorkerState.ERROR`/`exit_on_error` machinery to tell
        them apart.
        """
        try:
            return (self.controller.collect_refresh(), None)
        except Exception as error:
            return (None, error)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group == REFRESH_WORKER_GROUP:
            self._on_refresh_worker_state_changed(event)
        elif event.worker.group == DETAIL_WORKER_GROUP:
            self._on_detail_worker_state_changed(event)

    def _on_refresh_worker_state_changed(
        self, event: Worker.StateChanged
    ) -> None:
        if event.state is not WorkerState.SUCCESS:
            # PENDING/RUNNING: nothing to do yet. CANCELLED/ERROR: this
            # worker's body never raises and is never explicitly
            # cancelled, so these should not occur in practice; if they
            # ever do, there is nothing safe to apply.
            return
        if not self.is_running:
            # The app is shutting down (or already stopped) -- a late
            # result must never mutate state or touch a widget that may
            # already be torn down.
            return

        assert event.worker.result is not None
        result, error = event.worker.result
        if error is not None:
            try:
                self.controller.apply_refresh_failure(error)
            except Exception as internal_error:
                self._show_fatal(internal_error)
                return
        else:
            assert result is not None
            self.controller.apply_refresh_success(result)
        self._render_all()

    def _show_fatal(self, error: Exception) -> None:
        banner = self.query_one("#banner", ConnectionBanner)
        banner.update(
            f"[bold red]Internal error[/bold red]: {type(error).__name__}: "
            f"{error} -- the TUI stopped refreshing. Restart to recover."
        )
        banner.add_class("visible")

    # -- rendering -----------------------------------------------------

    def _render_all(self) -> None:
        self._render_header()
        self._render_banner()
        self._render_filter_summary()
        self._render_table()
        self._render_details_panels()

    def _render_header(self) -> None:
        header = self.query_one("#status-header", StatusHeader)
        state = self.controller.state
        status = state.status
        if status is None:
            header.update("Connecting...")
            return

        style = _HEALTH_STYLES[status.health]
        parts = [
            f"[bold]qbit-ops[/bold] · [{style}]{status.health.value}[/{style}]"
        ]
        parts.append(
            f"↓ {_format_byte_rate(status.rates.download_bytes_per_second)}  "
            f"↑ {_format_byte_rate(status.rates.upload_bytes_per_second)}"
        )
        if state.last_successful_refresh is not None:
            parts.append(
                "last refresh "
                f"{state.last_successful_refresh.strftime('%H:%M:%S UTC')}"
            )
        if state.stale:
            parts.append("[bold yellow]STALE[/bold yellow]")
        header.update("  ·  ".join(parts))

    def _render_banner(self) -> None:
        banner = self.query_one("#banner", ConnectionBanner)
        state = self.controller.state
        if state.connection is ConnectionState.CONNECTED:
            banner.remove_class("visible")
            return

        if state.connection is ConnectionState.AUTH_FAILED:
            message = (
                "[bold red]Authentication failed[/bold red] -- check "
                "QBIT_USER/QBIT_PASSWORD and restart."
            )
        elif state.connection is ConnectionState.CONFIG_FAILED:
            message = (
                "[bold red]Configuration invalid[/bold red] -- fix .env "
                "and restart."
            )
        else:
            detail = state.last_error.message if state.last_error else ""
            message = (
                f"[bold yellow]Reconnecting to qBittorrent...[/bold yellow] "
                f"{detail}"
            )
        banner.update(message)
        banner.add_class("visible")

    def _render_filter_summary(self) -> None:
        summary = self.query_one("#filter-summary", FilterSummary)
        state = self.controller.state
        visible = state.visible
        total = state.torrent_snapshot.scanned if state.torrent_snapshot else 0
        shown = len(visible.matched) if visible is not None else 0

        parts = [f"{shown} shown / {total}"]
        description = describe_torrent_filter(state.filters)
        if description != "none":
            parts.append(description)
        if state.search:
            parts.append(f"search: {state.search}")

        summary.update(" · ".join(parts))

    def _render_table(self) -> None:
        table = self.query_one("#torrents", DataTable)
        state = self.controller.state
        visible = state.visible

        previously_focused = state.focused_hash
        self._rebuilding_table = True
        try:
            table.clear()
            self._hash_by_row = {}

            if visible is None or not visible.matched:
                return

            for index, torrent in enumerate(visible.matched):
                table.add_row(*_torrent_row(torrent), key=torrent.hash)
                self._hash_by_row[index] = torrent.hash

            if previously_focused is not None:
                for row_index, torrent_hash in self._hash_by_row.items():
                    if torrent_hash == previously_focused:
                        table.move_cursor(row=row_index)
                        break
        finally:
            self._rebuilding_table = False

    def _render_details_panels(self) -> None:
        """Update every mounted `DetailsPanel` -- inline and/or modal."""
        state = self.controller.state
        for panel in self.query(DetailsPanel):
            panel.render_state(state)

    # -- interaction -----------------------------------------------------

    def on_data_table_row_highlighted(
        self, event: DataTable.RowHighlighted
    ) -> None:
        """Handle every `RowHighlighted` shape safely.

        Textual can post this event with `row_key=None` and
        `cursor_row=-1` (an empty table, or a table transitioning
        through zero rows) -- `event.row_key` must be checked for
        `None` *before* `.value` is ever accessed. While `_render_table`
        is rebuilding the table (`self._rebuilding_table`), any
        highlight events it triggers are suppressed entirely: the final
        post-rebuild cursor restoration (`move_cursor`) is the only
        highlight that should ever reach the controller.
        """
        if self._rebuilding_table:
            return

        if event.row_key is None or event.cursor_row < 0:
            self._clear_focus_and_render()
            return

        torrent_hash = event.row_key.value
        if torrent_hash is None:
            self._clear_focus_and_render()
            return

        self._focus_torrent(str(torrent_hash))

    def _clear_focus_and_render(self) -> None:
        """Clear controller focus/details and render the empty state.

        Never calls `torrents_trackers()` -- `TuiController.clear_focus`
        is a pure state mutation with no qBittorrent call. Also
        invalidates any focused-detail worker still in flight (via
        `_detail_request_id`), so a late result for the torrent that was
        just unfocused is discarded on arrival rather than applied.
        """
        self.controller.clear_focus()
        self._render_details_panels()

    def _focus_torrent(self, torrent_hash: str) -> Worker[Any] | None:
        """Focus a torrent and, if needed, dispatch a background fetch
        for its tracker details.

        `begin_focus_change` itself performs no I/O (fast, UI-thread
        safe): it updates `focused_hash` and clears any stale cached
        details immediately, so the Details panel shows "loading..."
        right away rather than stale data from a previously focused
        torrent. The actual `torrents_trackers()` call -- at most one --
        runs on a background worker. Returns the dispatched `Worker`, or
        `None` if no fetch was needed (e.g. `torrent_hash` was already
        focused) -- used only by tests wanting to await one specific
        fetch rather than every in-flight worker; production code never
        reads this return value.
        """
        request_id = self.controller.begin_focus_change(torrent_hash)
        self._render_details_panels()
        if request_id is None:
            return None
        return self._start_detail_fetch(torrent_hash, request_id)

    def _start_detail_fetch(
        self, torrent_hash: str, request_id: int
    ) -> Worker[Any]:
        """Dispatch one focused-detail fetch on a background thread.

        Multiple detail workers may be in flight simultaneously (rapid
        focus movement A -> B -> C never cancels A/B's workers -- they
        are left to finish on their own thread, since a blocking network
        call cannot be reliably interrupted). Correctness instead comes
        entirely from `request_id`:
        `TuiController.apply_tracker_details_success`/`_failure` silently
        discard any result whose id no longer matches the controller's
        current `_detail_request_id` -- see `app.tui.state` for the full
        guarantee. Returns the `Worker`, for the same test-observability
        reason as `_focus_torrent`.
        """
        worker = self.run_worker(
            lambda: self._detail_worker_body(torrent_hash, request_id),
            group=DETAIL_WORKER_GROUP,
            thread=True,
            exit_on_error=False,
        )
        self._last_detail_worker = worker
        return worker

    def _detail_worker_body(
        self, torrent_hash: str, request_id: int
    ) -> tuple[int, str, Any, Exception | None]:
        """Run on a background thread: blocking I/O only, never state
        mutation, and never raises -- see `_refresh_worker_body` for why
        outcomes travel back as a plain tagged tuple rather than relying
        on Textual's `WorkerState.ERROR`. `request_id`/`torrent_hash`
        travel with the result since several of these can be in flight
        for different torrents at once.
        """
        try:
            raw_trackers = self.controller.collect_tracker_details(torrent_hash)
            return (request_id, torrent_hash, raw_trackers, None)
        except Exception as error:
            return (request_id, torrent_hash, None, error)

    def _on_detail_worker_state_changed(
        self, event: Worker.StateChanged
    ) -> None:
        if event.state is not WorkerState.SUCCESS:
            return
        if not self.is_running:
            return

        assert event.worker.result is not None
        request_id, torrent_hash, raw_trackers, error = event.worker.result
        if error is not None:
            try:
                self.controller.apply_tracker_details_failure(request_id, error)
            except Exception as internal_error:
                self._show_fatal(internal_error)
                return
        else:
            self.controller.apply_tracker_details_success(
                request_id, torrent_hash, raw_trackers
            )
        self._render_details_panels()

    def on_input_changed(self, event: Input.Changed) -> None:
        try:
            panel = event.input.query_ancestor(FiltersPanel)
        except NoMatches:
            return
        self._apply_filters_from_panel(panel)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        try:
            panel = event.checkbox.query_ancestor(FiltersPanel)
        except NoMatches:
            return
        self._apply_filters_from_panel(panel)

    def _apply_filters_from_panel(self, panel: FiltersPanel) -> None:
        try:
            filters = panel.build_filter()
        except ValueError as error:
            panel.show_error(str(error))
            return

        panel.show_error("")
        self.controller.set_filters(filters)
        # Keep every mounted FiltersPanel instance (inline + modal, if
        # both happen to exist) showing the same, just-applied filter.
        for other in self.query(FiltersPanel):
            if other is not panel:
                other.sync_from(filters)
        self._render_filter_summary()
        self._render_table()
        self._render_details_panels()

    def action_focus_search(self) -> None:
        self.mount_search_input()

    def mount_search_input(self) -> None:
        existing = self.query("#search-input")
        if existing:
            existing.first().focus()
            return
        search = Input(
            placeholder="Search by name... (Enter to apply, Esc to close)",
            id="search-input",
        )
        self.query_one("#status-header", StatusHeader).mount(search)
        search.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search-input":
            self.controller.set_search(event.value)
            self._render_filter_summary()
            self._render_table()
            self._render_details_panels()
            self.query_one("#torrents", DataTable).focus()

    def action_open_filters(self) -> None:
        if self._is_narrow():
            self.push_screen(FiltersScreen(self.controller.state.filters))
        else:
            self.query_one("#main > FiltersPanel", FiltersPanel).query_one(
                ".f-category", Input
            ).focus()

    def action_cursor_down(self) -> None:
        self.query_one("#torrents", DataTable).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#torrents", DataTable).action_cursor_up()

    def action_open_details(self) -> None:
        if self._is_narrow():
            self.push_screen(DetailsScreen())
        else:
            self.query_one("#main > DetailsPanel", DetailsPanel).focus()

    def action_refresh_details(self) -> Worker[Any] | None:
        """Manually refresh the focused torrent's tracker details.

        `begin_manual_detail_refresh` always allocates a *new* request
        id, even though the focused hash is unchanged -- this guarantees
        this explicit request wins over a slower, already-in-flight
        automatic fetch for the same torrent, regardless of which
        happens to complete first. Returns the dispatched `Worker` (or
        `None` if nothing is focused), for the same test-observability
        reason as `_focus_torrent`.
        """
        torrent_hash = self.controller.state.focused_hash
        if torrent_hash is None:
            return None
        request_id = self.controller.begin_manual_detail_refresh()
        if request_id is None:
            return None
        return self._start_detail_fetch(torrent_hash, request_id)

    def action_toggle_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_dismiss_overlay(self) -> None:
        """Close a modal, or return focus from a text input to the table."""
        if len(self.screen_stack) > 1:
            self.pop_screen()
            return

        was_editing_text = isinstance(self.focused, Input)

        search_input = self.query("#search-input")
        if search_input:
            search_input.first().remove()
            was_editing_text = True

        if was_editing_text:
            self.query_one("#torrents", DataTable).focus()

    def _is_narrow(self) -> bool:
        return self.size.width < NARROW_WIDTH_THRESHOLD


_HELP_TEXT = """[bold]Keys[/bold]
up/down, j/k   navigate torrents
/              search by name (Enter to apply, Esc to close)
f              open filters
enter          open torrent details (narrow layout)
r              refresh focused torrent's tracker details
esc            close a modal, or return focus to the torrent list
q              quit
"""


def _format_byte_rate(bytes_per_second: int) -> str:
    """Format a byte rate using binary units, e.g. '12.4 MiB/s'.

    A deliberate small duplicate of `app.ui.format_byte_rate`: TUI
    modules must never import from `app.ui` (a CLI/Rich rendering
    module, see the security boundary at the top of this file), and
    this pure formatting function is too small to justify promoting it
    to a third, shared module.
    """
    value = float(max(bytes_per_second, 0))
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1

    unit = units[unit_index]
    if unit == "B":
        return f"{int(value)} {unit}/s"

    return f"{value:.1f} {unit}/s"


def _torrent_row(torrent: SelectedTorrent) -> tuple[Any, ...]:
    return (
        torrent.name,
        torrent.state,
        f"{torrent.progress * 100:.0f}%",
        _format_byte_rate(torrent.download_rate),
        _format_byte_rate(torrent.upload_rate),
        f"{torrent.ratio:.2f}",
    )


def _format_endpoint(endpoint: dict[str, Any]) -> str:
    """Render one safe, structural tracker endpoint -- never a raw URL."""
    parts = [str(endpoint["tracker"]), str(endpoint["health"])]
    if not endpoint["enabled"]:
        parts.append("disabled")
    return " ".join(parts)


def run_tui(
    *,
    client_factory: Any = create_qbit_client,
    host: str | None = None,
    refresh_interval: float = DEFAULT_REFRESH_INTERVAL_SECONDS,
) -> None:
    """Run the TUI application (blocking until the user quits)."""
    app = QbitOpsTuiApp(
        client_factory=client_factory,
        host=host,
        refresh_interval=refresh_interval,
    )
    app.run()
