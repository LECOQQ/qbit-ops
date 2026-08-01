"""Textual application for `qbit-ops tui` (read-only, LOW-risk bulk actions).

Security boundary (see docs/ARCHITECTURE.md): only imports the
LOW-risk, frozen-plan Pause/Resume/Reannounce functions -- never a
rescanning or deletion function, or `qbit_ops.cli`. Enforced by
`tests/test_tui_security.py`.

Every qBittorrent API call runs on a Textual thread worker, never on
the UI thread; `apply_*` (state-mutating) `TuiController` methods only
ever run from `on_worker_state_changed`, which Textual delivers on the
UI thread.
"""

from __future__ import annotations

from typing import Any

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Input,
    RadioSet,
)
from textual.worker import Worker, WorkerState

from qbit_ops.app_services import (
    TuiRefreshResult,
    create_qbit_client,
)
from qbit_ops.features.explain import ExplanationReport
from qbit_ops.features.torrents import (
    BulkTorrentActionPlan,
    TorrentBulkAction,
    describe_torrent_filter,
)
from qbit_ops.shared.execution import MutationStatus
from qbit_ops.tui.formatting import (
    _COLUMN_WIDTHS,
    NARROW_WIDTH_THRESHOLD,
    _columns_for_width,
    _format_last_action_line,
    _format_result_notification,
    _shorten_hash,
    _torrent_row_values,
)
from qbit_ops.tui.modals.actions import ActionsScreen
from qbit_ops.tui.modals.details import DetailsScreen
from qbit_ops.tui.modals.explain import ExplainScreen
from qbit_ops.tui.modals.filters import FiltersScreen
from qbit_ops.tui.modals.help import HelpScreen
from qbit_ops.tui.modals.preview import PreviewScreen
from qbit_ops.tui.modals.result import ResultScreen
from qbit_ops.tui.state import (
    DEFAULT_REFRESH_INTERVAL_SECONDS,
    ConnectionState,
    MutationUiResult,
    TuiController,
    Workspace,
    _classify_mutation_error,
)
from qbit_ops.tui.widgets.details import DetailsPanel
from qbit_ops.tui.widgets.filters import FiltersPanel
from qbit_ops.tui.widgets.overview import OverviewPanel, WorkspaceTabs
from qbit_ops.tui.widgets.status_bar import (
    ConnectionBanner,
    FilterSummary,
    LastActionBar,
)

# Distinguishes worker messages in on_worker_state_changed, which Textual
# delivers through a single handler regardless of group.
REFRESH_WORKER_GROUP = "qbit-refresh"
DETAIL_WORKER_GROUP = "qbit-detail"
MUTATION_WORKER_GROUP = "qbit-mutation"


class MainScreen(Screen[None]):
    """The app's single primary screen.

    Exists only so `on_resize` fires reliably: Textual dispatches a
    real terminal resize's `on_resize` to the Screen, not the App.
    """

    def on_resize(self, event: events.Resize) -> None:
        self._apply_width(event.size.width)

    def _apply_width(self, width: int) -> None:
        is_narrow = width < NARROW_WIDTH_THRESHOLD
        self.set_class(is_narrow, "narrow")
        overview = self.query_one("#overview-workspace")
        overview.set_class(not is_narrow, "grid")

        assert isinstance(self.app, QbitOpsTuiApp)
        self.app._render_table()

        # Never leave a now-hidden inline Details widget focused. Skip
        # while a modal is open: its DetailsPanel is a separate widget.
        app = self.app
        if not is_narrow or app.focused is None or len(app.screen_stack) > 1:
            return

        try:
            app.focused.query_ancestor("DetailsPanel")
        except NoMatches:
            return
        self.query_one("#torrents", DataTable).focus()


class QbitOpsTuiApp(App[None]):
    """The Overview-first, read-only TUI."""

    # No qbit-ops commands live in the command palette yet.
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    Screen {
        layout: vertical;
    }
    #workspace-tabs {
        height: 1;
        padding: 0 1;
        background: $panel;
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
    #overview-workspace {
        height: 1fr;
        padding: 0 1;
    }
    #overview-workspace.grid {
        layout: grid;
        grid-size: 2;
        grid-gutter: 1 1;
        grid-rows: auto;
    }
    .ov-card {
        border: round $accent;
        padding: 0 1;
        height: auto;
        min-height: 5;
    }
    .ov-card.ov-attention {
        border: round $warning;
    }
    .ov-nav {
        color: $text-muted;
        padding: 0 1;
        height: 1;
    }
    #filter-summary {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    #last-action {
        height: 1;
        padding: 0 1;
        color: $text-muted;
        display: none;
    }
    #last-action.visible {
        display: block;
    }
    #main {
        height: 1fr;
    }
    DataTable {
        width: 1fr;
    }
    #main > DetailsPanel {
        width: 40;
        border: solid $accent;
        padding: 0 1;
    }
    Screen.narrow #main > DetailsPanel {
        display: none;
    }
    .d-section {
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        # check_action hides whichever direction doesn't apply to the
        # current workspace, so only one of these ever shows at once.
        Binding("1", "show_overview", "Overview", show=False),
        Binding("g", "show_overview", "Overview", show=True),
        Binding("2", "show_torrents", "Torrents", show=False),
        Binding("t", "show_torrents", "Torrents", show=True),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("slash", "focus_search", "Search"),
        Binding("f", "open_filters", "Filters"),
        # priority=True: DataTable's own `enter` binding (select_cursor)
        # would otherwise win while the table has focus.
        Binding("enter", "activate", "Open", show=False, priority=True),
        Binding("c", "copy_hash", "Copy"),
        Binding("e", "explain", "Explain"),
        Binding("r", "refresh_details", "Refresh"),
        # Explicit multi-selection, distinct from focus: only an
        # explicit Space press toggles it, never mere highlighting.
        Binding("space", "toggle_selection", "Select"),
        Binding("ctrl+a", "select_all_visible", "Select visible", show=False),
        Binding("ctrl+d", "deselect_all", "Deselect all", show=False),
        Binding("a", "open_actions", "Actions"),
        Binding("question_mark", "toggle_help", "Help"),
        # priority=True so escape always wins over whatever has focus.
        Binding("escape", "dismiss_overlay", "Back", show=False, priority=True),
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
        self._pending_explain_request_id: int | None = None
        self._explain_screen: ExplainScreen | None = None
        self._mutation_worker: Worker[Any] | None = None
        self._preview_screen: PreviewScreen | None = None
        self._active_mutation_plan: BulkTorrentActionPlan | None = None
        self._last_operation_id = 0
        self._last_mutation_result: MutationUiResult | None = None

    def get_default_screen(self) -> Screen[None]:
        return MainScreen(id="_default")

    def compose(self) -> ComposeResult:
        yield WorkspaceTabs(id="workspace-tabs")
        yield ConnectionBanner(id="banner")
        yield OverviewPanel(id="overview-workspace")
        with Vertical(id="torrents-workspace"):
            yield FilterSummary(id="filter-summary")
            yield LastActionBar(id="last-action")
            with Horizontal(id="main"):
                yield DataTable(id="torrents", cursor_type="row")
                yield DetailsPanel()
        yield Footer()

    def on_mount(self) -> None:
        # Render the initial empty state so the screen isn't blank
        # while the first refresh worker is still in flight.
        self._render_workspace_visibility()
        self._render_all()
        self.refresh_bindings()
        self.set_interval(self.refresh_interval, self._start_periodic_refresh)
        self._start_periodic_refresh()

    # -- refresh -----------------------------------------------------

    def _start_periodic_refresh(self) -> None:
        """Start one periodic refresh tick, unless one is already running.

        Coalesces rather than queues: a tick that fires while the
        previous refresh (or a mutation) is still in flight is skipped,
        not deferred -- `action_apply_plan` triggers a refresh
        explicitly right after a mutation completes instead.
        """
        if (
            self._refresh_worker is not None
            and not self._refresh_worker.is_finished
        ):
            return
        if (
            self._mutation_worker is not None
            and not self._mutation_worker.is_finished
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
        """Run on a background thread: blocking I/O only, never raises.

        The outcome travels back as a tagged `(result, error)` tuple
        rather than relying on Textual's `WorkerState.ERROR`/
        `exit_on_error` machinery.
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
        elif event.worker.group == MUTATION_WORKER_GROUP:
            self._on_mutation_worker_state_changed(event)

    def _on_refresh_worker_state_changed(
        self, event: Worker.StateChanged
    ) -> None:
        if event.state is not WorkerState.SUCCESS:
            return
        if not self.is_running:
            # A late result must never mutate state or touch a widget
            # that may already be torn down.
            return

        assert event.worker.result is not None
        result, error = event.worker.result
        # finally ensures preview freshness fails closed even on an
        # unclassifiable exception, not just a recognized failure.
        try:
            if error is not None:
                try:
                    self.controller.apply_refresh_failure(error)
                except Exception as internal_error:
                    self._show_fatal(internal_error)
                    return
            else:
                assert result is not None
                # Never reconcile the selection out from under an
                # uncommitted Filters draft: a half-typed exact-match
                # category transiently matches nothing, which would
                # erase the selection before the user finishes typing.
                self.controller.apply_refresh_success(
                    result, reconcile=not self._filters_draft_is_open()
                )
            self._render_all()
        finally:
            self._refresh_preview_freshness(refresh_failed=error is not None)

    def _filters_draft_is_open(self) -> bool:
        return any(
            isinstance(screen, FiltersScreen) for screen in self.screen_stack
        )

    def _show_fatal(self, error: Exception) -> None:
        banner = self.query_one("#banner", ConnectionBanner)
        banner.update(
            f"[bold red]Internal error[/bold red]: {type(error).__name__}: "
            f"{error} -- the TUI stopped refreshing. Restart to recover."
        )
        banner.add_class("visible")

    # -- rendering -----------------------------------------------------

    def _render_all(self) -> None:
        self._render_workspace_tabs()
        self._render_banner()
        self._render_overview()
        self._render_filter_summary()
        self._render_last_action()
        self._render_table()
        self._render_details_panels()

    def _render_workspace_tabs(self) -> None:
        self.query_one("#workspace-tabs", WorkspaceTabs).render_state(
            self.controller.state.workspace
        )

    def _render_overview(self) -> None:
        self.query_one("#overview-workspace", OverviewPanel).render_state(
            self.controller.state
        )

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

        parts = [f"{shown:,} shown / {total:,}"]
        if state.selected_hashes:
            parts.append(f"{len(state.selected_hashes):,} selected")
        description = describe_torrent_filter(state.filters)
        if description != "none":
            parts.append(description)
        if state.search:
            parts.append(f"search: {state.search}")

        summary.update(" · ".join(parts))

    def _render_last_action(self) -> None:
        bar = self.query_one("#last-action", LastActionBar)
        outcome = self._last_mutation_result
        if outcome is None:
            bar.remove_class("visible")
            return
        bar.update(_format_last_action_line(outcome))
        bar.add_class("visible")

    def _render_table(self) -> None:
        table = self.query_one("#torrents", DataTable)
        state = self.controller.state
        visible = state.visible
        columns = _columns_for_width(self.size.width)

        previously_focused = state.focused_hash
        self._rebuilding_table = True
        try:
            table.clear(columns=True)
            self._hash_by_row = {}
            for name in columns:
                table.add_column(name, width=_COLUMN_WIDTHS.get(name), key=name)

            if visible is None or not visible.matched:
                return

            for index, torrent in enumerate(visible.matched):
                values = _torrent_row_values(
                    torrent, torrent.hash in state.selected_hashes
                )
                table.add_row(
                    *(values[name] for name in columns), key=torrent.hash
                )
                self._hash_by_row[index] = torrent.hash

            if previously_focused is not None:
                for row_index, torrent_hash in self._hash_by_row.items():
                    if torrent_hash == previously_focused:
                        table.move_cursor(row=row_index)
                        break
        finally:
            self._rebuilding_table = False

    def _render_details_panels(self) -> None:
        state = self.controller.state
        for panel in self.query(DetailsPanel):
            panel.render_state(state)

    # -- context-aware footer ---------------------------------------------

    def check_action(
        self, action: str, parameters: tuple[object, ...]
    ) -> bool | None:
        """Hide (and disable) footer/help-panel actions not meaningful
        in the current context; also gates whether a key press fires.
        """
        if action in (
            "quit",
            "toggle_help",
            "activate",
            "dismiss_overlay",
            "cursor_up",
            "cursor_down",
            # Textual's Screen base binds Tab/Shift+Tab to
            # app.focus_next/focus_previous -- must stay allowed even
            # while a modal is open, or in-modal Tab navigation breaks.
            "focus_next",
            "focus_previous",
        ):
            return True
        if len(self.screen_stack) > 1:
            return False

        state = self.controller.state
        if action == "show_overview":
            return state.workspace is Workspace.TORRENTS
        if action == "show_torrents":
            return state.workspace is Workspace.OVERVIEW
        if action in ("focus_search", "open_filters"):
            return state.workspace is Workspace.TORRENTS
        if action in ("copy_hash", "explain", "refresh_details"):
            return (
                state.workspace is Workspace.TORRENTS
                and state.focused_hash is not None
            )
        if action == "toggle_selection":
            return state.workspace is Workspace.TORRENTS
        if action == "select_all_visible":
            return state.workspace is Workspace.TORRENTS and bool(
                state.visible and state.visible.matched
            )
        if action == "deselect_all":
            return state.workspace is Workspace.TORRENTS and bool(
                state.selected_hashes
            )
        if action == "open_actions":
            return state.workspace is Workspace.TORRENTS and bool(
                state.selected_hashes
            )
        return True

    # -- workspace navigation --------------------------------------------

    def action_show_overview(self) -> None:
        self._switch_workspace(Workspace.OVERVIEW)

    def action_show_torrents(self) -> None:
        self._switch_workspace(Workspace.TORRENTS)

    def _switch_workspace(self, workspace: Workspace) -> None:
        """Switch the active workspace; a no-op while a modal is open."""
        if len(self.screen_stack) > 1:
            return
        if self.controller.state.workspace is workspace:
            if workspace is Workspace.TORRENTS:
                self.query_one("#torrents", DataTable).focus()
            return

        self.controller.set_workspace(workspace)
        self._render_workspace_visibility()
        self._render_workspace_tabs()
        if workspace is Workspace.TORRENTS:
            self.query_one("#torrents", DataTable).focus()
        else:
            self.screen.set_focus(None)
        self.refresh_bindings()

    def _render_workspace_visibility(self) -> None:
        workspace = self.controller.state.workspace
        self.query_one("#overview-workspace", OverviewPanel).display = (
            workspace is Workspace.OVERVIEW
        )
        self.query_one("#torrents-workspace", Vertical).display = (
            workspace is Workspace.TORRENTS
        )

    def _in_torrents_workspace(self) -> bool:
        return self.controller.state.workspace is Workspace.TORRENTS

    # -- interaction -----------------------------------------------------

    def on_data_table_row_highlighted(
        self, event: DataTable.RowHighlighted
    ) -> None:
        # Textual can post row_key=None/cursor_row=-1 for an empty or
        # transitioning table. Suppressed entirely while _render_table
        # is rebuilding; only the post-rebuild cursor restoration
        # should reach the controller.
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
        self.controller.clear_focus()
        self._render_details_panels()
        self.refresh_bindings()

    def _focus_torrent(self, torrent_hash: str) -> Worker[Any] | None:
        """Focus a torrent and dispatch a background tracker-details fetch.

        Returns the dispatched `Worker`, or `None` if no fetch was
        needed -- used only by tests awaiting one specific fetch.
        """
        request_id = self.controller.begin_focus_change(torrent_hash)
        self._render_details_panels()
        self.refresh_bindings()
        if request_id is None:
            return None
        return self._start_detail_fetch(torrent_hash, request_id)

    def _start_detail_fetch(
        self, torrent_hash: str, request_id: int
    ) -> Worker[Any]:
        """Dispatch one focused-detail fetch on a background thread.

        Multiple detail workers may be in flight at once -- an older
        one is left to finish rather than cancelled. `TuiController`
        discards any result whose `request_id` no longer matches its
        current `_detail_request_id`, so rapid focus A -> B -> C only
        ever applies C's result.
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
        """Run on a background thread: blocking I/O only, never raises."""
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
        self._maybe_resolve_pending_explain(request_id, torrent_hash)

    def _maybe_resolve_pending_explain(
        self, request_id: int, torrent_hash: str
    ) -> None:
        """Update a still-open Explain modal once its awaited detail
        fetch completes; a no-op for any stale or superseded result.
        """
        if self._pending_explain_request_id != request_id:
            return
        self._pending_explain_request_id = None

        if self._explain_screen is None or self._explain_screen not in (
            self.screen_stack
        ):
            return
        if self.controller.state.focused_hash != torrent_hash:
            return
        if self.controller.detail_request_id != request_id:
            return

        report = self.controller.build_explanation()
        if report is None:
            self.pop_screen()
            self.notify("Torrent no longer available.", severity="warning")
            return
        self._explain_screen.report = report
        self._explain_screen.refresh_content()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search-input":
            self._apply_search(event.value)
            return
        try:
            panel = event.input.query_ancestor(FiltersPanel)
        except NoMatches:
            return
        self._apply_filters_from_panel(panel)

    def _apply_search(self, text: str) -> None:
        """Apply search text live, as it's typed -- no I/O or debounce
        needed since `set_search` is pure in-memory filtering.

        Unlike Filters' Apply/Clear/Cancel, the hidden-selection count
        `set_search` returns is not surfaced as a notification: search
        reconciles on every keystroke, and a toast per character would
        be noise. The filter summary (re-rendered below) still
        reflects it immediately.
        """
        self.controller.set_search(text)
        self._render_filter_summary()
        self._render_table()
        self._render_details_panels()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        try:
            panel = event.checkbox.query_ancestor(FiltersPanel)
        except NoMatches:
            return
        self._apply_filters_from_panel(panel)

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        try:
            panel = event.radio_set.query_ancestor(FiltersPanel)
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
        self._render_filter_summary()
        self._render_table()
        self._render_details_panels()
        # No selection reconciliation here: this fires on every
        # keystroke, and a category filter is exact-match, so
        # reconciling mid-typing would wipe a selection before the
        # user finishes the filter that would have kept it visible.
        # The Filters Apply/Clear/Cancel commit points reconcile instead.

    def _reconcile_selection_and_notify(self) -> None:
        removed = self.controller.reconcile_selection()
        self._render_table()
        self._render_filter_summary()
        if removed:
            self.notify(f"{removed} hidden selection(s) cleared.")

    def action_focus_search(self) -> None:
        if not self._in_torrents_workspace():
            return
        self.mount_search_input()

    def mount_search_input(self) -> None:
        existing = self.query("#search-input")
        if existing:
            existing.first().focus()
            return
        search = Input(
            placeholder="Search name or hash... (Enter/Esc to close)",
            value=self.controller.state.search,
            id="search-input",
        )
        self.query_one("#torrents-workspace", Vertical).mount(search, before=0)
        search.focus()

    # Input.Submitted never fires for #search-input: the App's priority
    # `enter` binding (action_activate) intercepts it first and handles
    # the search input directly instead.

    def action_open_filters(self) -> None:
        if not self._in_torrents_workspace():
            return
        self.push_screen(FiltersScreen(self.controller.state.filters))
        self.refresh_bindings()

    def action_cursor_down(self) -> None:
        if not self._in_torrents_workspace():
            return
        self.query_one("#torrents", DataTable).action_cursor_down()

    def action_cursor_up(self) -> None:
        if not self._in_torrents_workspace():
            return
        self.query_one("#torrents", DataTable).action_cursor_up()

    def action_activate(self) -> None:
        """Bound to `enter` with `priority=True`, so this always fires
        before a focused widget's own `enter` binding -- including
        `#search-input`'s native submit and a focused modal `Button`'s
        native click, which this dispatches manually via `Button.press()`.
        `FiltersScreen` applies its edits live, so `enter` there only
        closes the modal. Otherwise dispatches by active workspace.
        """
        if len(self.screen_stack) > 1:
            if isinstance(self.screen, FiltersScreen):
                self.pop_screen()
                self._reconcile_selection_and_notify()
                self.refresh_bindings()
                return
            focused = self.focused
            if isinstance(focused, Button):
                focused.press()
            return

        if self.controller.state.workspace is Workspace.OVERVIEW:
            self._switch_workspace(Workspace.TORRENTS)
            return

        focused = self.focused
        if isinstance(focused, Input) and focused.id == "search-input":
            self.query_one("#torrents", DataTable).focus()
            return

        if self._is_narrow():
            self.push_screen(DetailsScreen())
            self.refresh_bindings()
        else:
            self.query_one("#main > DetailsPanel", DetailsPanel).focus()

    def action_refresh_details(self) -> Worker[Any] | None:
        """Manually refresh the focused torrent's tracker details.

        Always allocates a new request id so this explicit request
        wins over a slower, already-in-flight automatic fetch.
        """
        if not self._in_torrents_workspace():
            return None
        torrent_hash = self.controller.state.focused_hash
        if torrent_hash is None:
            return None
        request_id = self.controller.begin_manual_detail_refresh()
        if request_id is None:
            return None
        return self._start_detail_fetch(torrent_hash, request_id)

    def action_copy_hash(self) -> None:
        """Copy the focused torrent's full canonical hash to the clipboard.

        Uses Textual's `App.copy_to_clipboard` (OSC 52) -- some
        terminals (notably macOS Terminal.app) silently ignore it.
        """
        torrent_hash = self.controller.state.focused_hash
        if torrent_hash is None:
            self.notify("No torrent focused.", severity="warning")
            return
        self.copy_to_clipboard(torrent_hash)
        self.notify(f"Copied hash {_shorten_hash(torrent_hash)}")

    def action_explain(self) -> Worker[Any] | None:
        """Open an evidence-based explanation of the focused torrent.

        Reuses an already in-flight focused-detail worker instead of
        starting a redundant fetch when one is already running.
        """
        if not self._in_torrents_workspace():
            return None
        state = self.controller.state
        torrent_hash = state.focused_hash
        if torrent_hash is None:
            self.notify("No torrent focused.", severity="warning")
            return None

        torrent = state.focused_torrent()
        name = torrent.name if torrent is not None else torrent_hash

        if state.focused_tracker_details is not None:
            report = self.controller.build_explanation()
            if report is None:
                self.notify("Torrent no longer available.", severity="warning")
                return None
            self._open_explain_screen(name, report)
            return None

        self._open_explain_screen(name, None)
        worker_in_flight = (
            self._last_detail_worker is not None
            and not self._last_detail_worker.is_finished
        )
        if worker_in_flight:
            self._pending_explain_request_id = self.controller.detail_request_id
            return self._last_detail_worker

        request_id = self.controller.begin_manual_detail_refresh()
        if request_id is None:
            return None
        self._pending_explain_request_id = request_id
        self._render_details_panels()
        return self._start_detail_fetch(torrent_hash, request_id)

    def _open_explain_screen(
        self, torrent_name: str, report: ExplanationReport | None
    ) -> None:
        screen = ExplainScreen(torrent_name, report)
        self._explain_screen = screen
        self.push_screen(screen)
        self.refresh_bindings()

    # -- multi-selection + LOW-risk bulk actions -----------------------------

    def action_toggle_selection(self) -> None:
        if not self._in_torrents_workspace():
            return
        torrent_hash = self.controller.state.focused_hash
        if torrent_hash is None:
            self.notify("No torrent focused.", severity="warning")
            return
        self.controller.toggle_selection(torrent_hash)
        self._render_table()
        self._render_filter_summary()
        self.refresh_bindings()

    def action_select_all_visible(self) -> None:
        if not self._in_torrents_workspace():
            return
        self.controller.select_all_visible()
        self._render_table()
        self._render_filter_summary()
        self.refresh_bindings()

    def action_deselect_all(self) -> None:
        if not self._in_torrents_workspace():
            return
        if not self.controller.state.selected_hashes:
            return
        count = len(self.controller.state.selected_hashes)
        self.controller.clear_selection()
        self._render_table()
        self._render_filter_summary()
        self.refresh_bindings()
        self.notify(f"Cleared {count} selection(s).")

    def action_open_actions(self) -> None:
        if not self._in_torrents_workspace():
            return
        state = self.controller.state
        if not state.selected_hashes:
            self.notify("No torrents selected.", severity="warning")
            return

        snapshot = tuple(sorted(state.selected_hashes))
        name_by_hash = {
            torrent.hash: torrent.name
            for torrent in (state.visible.matched if state.visible else ())
        }
        names = tuple(
            name_by_hash.get(torrent_hash, _shorten_hash(torrent_hash))
            for torrent_hash in snapshot
        )
        self.push_screen(ActionsScreen(snapshot, names))
        self.refresh_bindings()

    def _open_preview_for_action(
        self, action: TorrentBulkAction, hashes: tuple[str, ...]
    ) -> None:
        """Build a frozen plan from `hashes` (already a snapshot taken
        at Actions-selection time) and open its Preview.

        Zero API calls: `TuiController.build_bulk_plan` is pure, built
        entirely from the current in-memory torrent snapshot -- no
        `torrents_info()` rescan is needed merely to preview a plan.
        """
        plan = self.controller.build_bulk_plan(action, hashes)
        self._last_operation_id += 1
        preview = PreviewScreen(
            plan,
            self.controller.state.last_successful_refresh,
            operation_id=self._last_operation_id,
        )
        self.push_screen(preview)
        # A preview built while already disconnected/stale is stale from
        # birth -- it was computed from data known not to be current.
        if not self._snapshot_is_fresh():
            preview.mark_stale()
        self.refresh_bindings()

    def _snapshot_is_fresh(self) -> bool:
        """Whether the current torrent snapshot may ground an Apply.

        Requires a live connection *and* a non-stale snapshot: any of
        RECONNECTING/AUTH_FAILED/CONFIG_FAILED, or a stale flag set by a
        failed refresh, withdraws Apply.
        """
        state = self.controller.state
        return state.connection is ConnectionState.CONNECTED and not state.stale

    def _refresh_preview_freshness(
        self, *, refresh_failed: bool = False
    ) -> None:
        """Propagate snapshot staleness into any open `PreviewScreen`.

        Called from a `finally` after every refresh outcome, so no
        failure path can skip it. Staleness is sticky -- a later
        recovery never re-enables an old preview, the operator
        rebuilds it. `refresh_failed=True` invalidates unconditionally
        rather than trusting `_snapshot_is_fresh()`, since an
        unclassifiable exception can leave connection/stale flags
        looking healthy despite refreshing having actually stopped.
        """
        if not refresh_failed and self._snapshot_is_fresh():
            return
        for screen in self.screen_stack:
            if isinstance(screen, PreviewScreen):
                screen.mark_stale()

    def action_apply_plan(self) -> None:
        """Apply the plan owned by the currently open `PreviewScreen`.

        A no-op unless `PreviewScreen` is on top and no mutation is
        already in flight -- double-pressing Apply dispatches at most
        one worker. `preview.can_apply` is re-checked here (not just
        the button's `disabled` state) so a keyboard Apply on a stale
        preview is genuinely rejected, not merely discouraged.
        """
        if not isinstance(self.screen, PreviewScreen):
            return
        if (
            self._mutation_worker is not None
            and not self._mutation_worker.is_finished
        ):
            return

        preview_screen = self.screen
        if not preview_screen.can_apply:
            self.notify(
                "Snapshot stale — rebuild the preview after reconnection.",
                severity="warning",
            )
            return

        preview_screen.set_applying(True)
        self._preview_screen = preview_screen
        self._active_mutation_plan = preview_screen.plan
        plan = preview_screen.plan
        operation_id = preview_screen.operation_id
        self._mutation_worker = self.run_worker(
            lambda: self._mutation_worker_body(plan, operation_id),
            group=MUTATION_WORKER_GROUP,
            thread=True,
            exit_on_error=False,
        )

    def _mutation_worker_body(
        self, plan: BulkTorrentActionPlan, operation_id: int
    ) -> tuple[int, bool, Exception | None]:
        """Run on a background thread: blocking I/O only, never raises.

        `operation_id` lets a late completion be matched to the exact
        preview that started it, not whatever is on top of the stack.
        `should_proceed` re-checks `self.is_running` after the shared
        remote lock is acquired, so a mutation queued behind a blocked
        read is abandoned rather than sent if the app shut down while
        it waited.
        """
        try:
            dispatched = self.controller.apply_bulk_plan(
                plan, should_proceed=lambda: self.is_running
            )
            return (operation_id, dispatched, None)
        except Exception as error:
            return (operation_id, False, error)

    def _on_mutation_worker_state_changed(
        self, event: Worker.StateChanged
    ) -> None:
        if event.state is not WorkerState.SUCCESS:
            return
        if not self.is_running:
            # The app is shutting down -- never enqueue another
            # operation, never let a late result touch a closed app.
            return

        preview_screen = self._preview_screen
        plan = self._active_mutation_plan
        self._preview_screen = None
        self._active_mutation_plan = None
        if preview_screen is None or plan is None:
            return

        assert event.worker.result is not None
        operation_id, dispatched, error = event.worker.result
        if operation_id != preview_screen.operation_id:
            # A completion from a superseded operation -- never allow it
            # to touch a newer preview or result.
            return

        outcome = self._classify_mutation_outcome(plan, error, dispatched)
        self._last_mutation_result = outcome
        self._render_last_action()

        # Whatever happens to the UI, this operation's own preview must
        # always stop being logically "applying" -- otherwise, once an
        # overlay above it is closed, the operator is left on a modal
        # that neither Escape nor Cancel will dismiss, with the whole
        # TUI stuck after a mutation that really was submitted.
        preview_is_active = self.screen is preview_screen
        preview_still_exists = preview_screen in self.screen_stack

        if preview_still_exists:
            preview_screen.set_applying(False)
            if not preview_is_active:
                # Hidden behind an unrelated screen: never pop or
                # replace that screen, never reopen this one. Just hand
                # back control of it -- and mark it stale, since its
                # plan has now been acted on and must not be re-applied.
                preview_screen.mark_stale()

        if preview_is_active:
            # Only ever dismiss *this* operation's own preview, and only
            # while it is genuinely the active screen. A bare
            # `pop_screen()` would remove whatever sits on top --
            # potentially Help/Filters/Details/Explain.
            self.pop_screen()
            self.refresh_bindings()

        if outcome.status is MutationStatus.APPLIED:
            # One immediate refresh after a real mutation, per plan.
            # Never scheduled for a cancelled-before-dispatch outcome:
            # nothing was sent, so there is nothing new to observe.
            self._start_periodic_refresh()

        if preview_is_active:
            self._push_result_screen(outcome)
            return

        # The request really may have been submitted, so it must never
        # vanish silently just because its preview is no longer on top.
        # The persistent last-action line (rendered above) is the
        # durable record; this transient toast is supplemental only.
        self.notify(
            _format_result_notification(outcome),
            severity=(
                "information"
                if outcome.status is MutationStatus.APPLIED
                else "warning"
            ),
        )
        self._apply_selection_policy(outcome)

    def _classify_mutation_outcome(
        self,
        plan: BulkTorrentActionPlan,
        error: Exception | None,
        dispatched: bool,
    ) -> MutationUiResult:
        """Map a raw Apply outcome to one truthful, structured result.

        `error` is classified via `_classify_mutation_error`, checking
        `__cause__` too since `apply_bulk_torrent_action` wraps
        failures in a `RuntimeError`. `dispatched=False` with no error
        means the mutation lost authority while queued behind the
        shared remote lock and never left -- reported as a distinct
        cancelled-before-dispatch outcome, not UNAVAILABLE or APPLIED.
        """
        if error is None and not dispatched:
            return MutationUiResult.from_plan(
                plan,
                MutationStatus.CANCELLED,
                operation_id=self._last_operation_id,
                cancelled_before_dispatch=True,
            )

        if error is None:
            status = self.controller.classify_plan_status(plan)
            if status is MutationStatus.PREVIEW:
                status = MutationStatus.APPLIED
            return MutationUiResult.from_plan(
                plan, status, operation_id=self._last_operation_id
            )

        category, message = _classify_mutation_error(error)
        return MutationUiResult.from_plan(
            plan,
            MutationStatus.CANCELLED,
            operation_id=self._last_operation_id,
            error_category=category,
            error_message=message,
        )

    def _push_result_screen(self, outcome: MutationUiResult) -> None:
        self.push_screen(ResultScreen(outcome))
        self.refresh_bindings()

    def _on_result_dismissed(self, outcome: MutationUiResult) -> None:
        """Dismissing a Result never dispatches, rebuilds, or mutates
        anything -- it only applies the selection policy below."""
        self._apply_selection_policy(outcome)

    def _apply_selection_policy(self, outcome: MutationUiResult) -> None:
        """Update the selection after a mutation, per outcome status.

        APPLIED drops submitted hashes, NO_CHANGES drops planned
        hashes, NO_MATCH drops only hashes proven absent -- on any
        failure the selection is left intact for a deliberate retry.
        Survivors are then reconciled against visible hashes.
        """
        if outcome.cancelled_before_dispatch:
            # Nothing was sent, so nothing may be treated as submitted:
            # the live selection is kept intact.
            pass
        elif outcome.error_category is None:
            if outcome.status is MutationStatus.APPLIED:
                self.controller.clear_selection_for(outcome.submitted_hashes)
            elif outcome.status is MutationStatus.NO_CHANGES:
                self.controller.clear_selection_for(outcome.planned_hashes)
            elif outcome.status is MutationStatus.NO_MATCH:
                self.controller.clear_selection_for(outcome.not_found_hashes)

        self.controller.reconcile_selection()
        self._render_table()
        self._render_filter_summary()
        self.refresh_bindings()

    def action_toggle_help(self) -> None:
        self.push_screen(HelpScreen())
        self.refresh_bindings()

    def action_dismiss_overlay(self) -> None:
        """Close a modal, return focus from a text input to the table,
        or clear a non-empty selection.

        On `FiltersScreen`, escape means *cancel* -- revert to
        `original_filters` before closing. Refuses to close
        `PreviewScreen` while `applying=True`, so an already-dispatched
        Apply always has something to observe its result. On
        `ResultScreen`, this priority binding pre-empts the screen's
        own `action_dismiss`, so its selection-clearing policy is
        triggered explicitly here instead.
        """
        if len(self.screen_stack) > 1:
            screen = self.screen
            if isinstance(screen, PreviewScreen) and screen.applying:
                return
            if isinstance(screen, FiltersScreen):
                self.controller.set_filters(screen.original_filters)
                self._render_filter_summary()
                self._render_table()
                self._render_details_panels()
                self._reconcile_selection_and_notify()
            if isinstance(screen, ResultScreen):
                outcome = screen.outcome
                self.pop_screen()
                self.refresh_bindings()
                self._on_result_dismissed(outcome)
                return
            self.pop_screen()
            self.refresh_bindings()
            return

        was_editing_text = isinstance(self.focused, Input)

        search_input = self.query("#search-input")
        if search_input:
            search_input.first().remove()
            was_editing_text = True

        if was_editing_text:
            self.query_one("#torrents", DataTable).focus()
            return

        if self.controller.state.selected_hashes:
            count = len(self.controller.state.selected_hashes)
            self.controller.clear_selection()
            self._render_table()
            self._render_filter_summary()
            self.refresh_bindings()
            self.notify(f"Cleared {count} selection(s).")

    def _is_narrow(self) -> bool:
        return self.size.width < NARROW_WIDTH_THRESHOLD


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
