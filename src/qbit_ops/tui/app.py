"""Textual application for `qbit-ops tui` (read-only V1, visual polish).

Security boundary (docs/TUI_ARCHITECTURE_REVIEW.md §10, revised for TUI
2 -- see docs/DECISIONS.md): this module and every other module under
`qbit_ops/tui/` must never import `qbit_ops.main`,
`qbit_ops.features.torrents.plan_bulk_torrent_action` (always rescans, accepts
`--all`), any tracker mutation `plan_*`/`apply_*` function, any deletion
function,
`qbit_ops.features.torrents.list_torrents_with_trackers`, or
`qbit_ops.features.torrents._get_tracker_details`. It may import exactly
`qbit_ops.features.torrents.build_bulk_action_plan_from_snapshot`/
`apply_bulk_torrent_action` (LOW-risk Pause/Resume/Reannounce only,
frozen-plan-in, frozen-plan-out, never a live rescan). Widgets only
ever render safe, structured domain outputs (`StatusSnapshot`,
`SelectedTorrent`, `get_safe_tracker_details` output,
`ExplanationReport`, `BulkTorrentActionPlan`, `AppError`).

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
`collect_*`/pure-network methods on `qbit_ops.tui.state.TuiController`;
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

Overview-first redesign (see docs/DECISIONS.md): the TUI opens on an
Overview workspace (built entirely from the same periodic refresh
result -- no extra API call) instead of a bare torrent table, with a
second, explicit Torrents workspace for browsing/search/filtering/
inspection. `TuiState.workspace` tracks which one is active; switching
is a pure local operation (`TuiController.set_workspace`) -- see
`_switch_workspace`. The two workspaces are two always-mounted
containers (`#overview-workspace`/`#torrents-workspace`) whose
`display` is toggled, not two Textual `Screen`s, so a switch preserves
every widget's state for free. The screen stack is reserved for real
modals (filters, details, help, explain).

Visual-polish + Explain phase (see docs/DECISIONS.md): Overview is
grouped into distinct, non-overlapping conceptual cards (Connection,
Transfer, Activity, Completion, Attention, Health/alerts) instead of
one long block of counters that could look like a single mutually
exclusive partition. The Torrents table gained a `Category` column and
responsive column disclosure (`_columns_for_width`). Details is grouped
into Identity/Transfer/Trackers sections, shows a shortened hash, and
`c` copies the full canonical hash via `App.copy_to_clipboard` (a
terminal-support limitation, not a qbit-ops bug -- see
`action_copy_hash`). The Filters modal replaced two contradictory
Checkbox pairs (Completed+Incomplete, Active+Inactive) with exclusive
`RadioSet`s, and gained visible Apply/Clear/Cancel buttons alongside
the existing bindings. `e` opens an Explain modal for the focused
torrent, built by the same pure, zero-API `qbit_ops.features.explain.
build_torrent_explanation` the CLI's `explain torrent` delegates to --
see `TuiController.build_explanation` and `action_explain`.

Hotfix phase (see docs/DECISIONS.md): `on_data_table_row_highlighted`
checks `event.row_key is None`/`event.cursor_row < 0` before ever
touching `.value`, with an explicit `_rebuilding_table` guard around
table rebuilds. The help screen (`?`) is always a separate modal
(`HelpScreen`) at every width.

TUI 2 (see docs/DECISIONS.md): explicit multi-selection
(`TuiState.selected_hashes`) plus a safe, three-step bulk-action loop
for LOW-risk torrent mutations only (Pause/Resume/Reannounce) --
`Space` toggles the focused row, `Ctrl+A` selects only currently
*visible* rows, `a` opens `ActionsScreen`. Choosing an action there
freezes `tuple(sorted(selected_hashes))` into a `BulkTorrentActionPlan`
(`TuiController.build_bulk_plan`, pure, zero API calls, reusing
`qbit_ops.features.torrents.build_bulk_action_plan_from_snapshot` -- no
second rule catalogue) shown in `PreviewScreen`; only an explicit Apply there
dispatches a `MUTATION_WORKER_GROUP` worker
(`TuiController.apply_bulk_plan`) that consumes exactly that frozen
plan, never a live re-read of `selected_hashes`. Selection is always a
subset of currently visible torrents by construction
(`TuiController._reconcile_selection`, called after every filter/
search change and every periodic refresh) -- `set()` never means
"all". The periodic refresh timer skips a tick while a mutation is in
flight (`_start_periodic_refresh`), and an explicit one-shot refresh is
triggered right after a mutation completes. No tracker mutation, no
deletion, no `--all`/whole-instance selector is reachable from the TUI.

TUI reorg phase (see docs/DECISIONS.md): this module now retains only
the root `App`, `MainScreen` (exists solely for a real terminal
resize's `on_resize`, which Textual delivers to the Screen, not the
App), composition, global bindings/actions, worker lifecycle, and
startup/shutdown (`run_tui`). Pure state/formatting helpers moved to
`qbit_ops.tui.formatting`; `MutationUiResult` and its classifiers moved
to `qbit_ops.tui.state` (state/transformation, not presentation);
passive widgets moved to `qbit_ops.tui.widgets`; `ModalScreen`
subclasses moved to `qbit_ops.tui.modals`. No `tui/screens/` package
was created: the layout is panels in one screen plus modals, exactly
the case the reorg's own instructions say not to split into screen
classes for. Every moved class/function is re-exported here by import
so `tests/test_tui_app.py`/`tests/test_tui_bulk_mutation_audit.py`'s
existing `from qbit_ops.tui.app import ...` statements keep working
unchanged.
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

# Worker group names -- used to tell a periodic-refresh worker's
# `Worker.StateChanged` message apart from a focused-detail worker's in
# `on_worker_state_changed` (Textual delivers both through the same
# handler). See docs/DECISIONS.md (worker hardening phase) for the full
# threading design.
REFRESH_WORKER_GROUP = "qbit-refresh"
DETAIL_WORKER_GROUP = "qbit-detail"
MUTATION_WORKER_GROUP = "qbit-mutation"


class MainScreen(Screen[None]):
    """The app's single primary screen.

    A custom subclass exists only so `on_resize` fires reliably: Textual
    dispatches a real terminal resize's `on_resize` to the *Screen*, not
    the *App* (`App._on_resize`/`Screen._on_resize` both call
    `event.stop()`, which stops the underlying event's public dispatch
    on the App node but not on the Screen node -- verified empirically).
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

        # Never leave a now-hidden inline Details widget focused -- an
        # invisible focused widget can neither be seen nor meaningfully
        # interacted with. Skip while a modal is open: its own
        # DetailsPanel instance is a separate, still-visible widget on a
        # different screen, and this screen's focus is irrelevant while
        # it isn't on top.
        app = self.app
        if not is_narrow or app.focused is None or len(app.screen_stack) > 1:
            return

        try:
            app.focused.query_ancestor("DetailsPanel")
        except NoMatches:
            return
        self.query_one("#torrents", DataTable).focus()


class QbitOpsTuiApp(App[None]):
    """The Overview-first, read-only TUI (V1)."""

    # Textual's built-in Ctrl+P command palette has no qbit-ops commands
    # yet and only confused dogfooders ("^p palette" in the footer) --
    # disabled until there is a meaningful palette to show. Workspace
    # navigation is explicit bindings (1/g, 2/t), never the palette.
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
        # `show=True` on both directions of each nav pair: `check_action`
        # below hides whichever one is not applicable to the *current*
        # workspace, so only one of "Overview"/"Torrents" ever actually
        # appears in the footer at a time -- see `check_action`.
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
        # `priority=True`: `DataTable` binds its own `enter` to
        # `select_cursor` (a no-op `RowSelected` message we don't
        # handle) -- without priority, that binding wins while the
        # table has focus and "activate" silently never fires. Verified
        # empirically that priority *does* override a focused child
        # widget's own declarative bindings (unlike `Input`'s
        # printable-character handling, which bypasses the bindings
        # system entirely -- see `action_activate`'s docstring).
        Binding("enter", "activate", "Open", show=False, priority=True),
        Binding("c", "copy_hash", "Copy"),
        Binding("e", "explain", "Explain"),
        Binding("r", "refresh_details", "Refresh"),
        # TUI 2: explicit multi-selection, distinct from focus -- see
        # the module docstring. `space` never selects merely by
        # highlighting a row; only an explicit press toggles it.
        Binding("space", "toggle_selection", "Select"),
        Binding("ctrl+a", "select_all_visible", "Select visible", show=False),
        Binding("ctrl+d", "deselect_all", "Deselect all", show=False),
        Binding("a", "open_actions", "Actions"),
        Binding("question_mark", "toggle_help", "Help"),
        # `escape` must win over whatever has focus (a filter/search
        # Input never binds it, but priority makes the intent explicit
        # and future-proof) -- Textual's `Input` consumes printable
        # characters before bindings are resolved, regardless of
        # priority; verified empirically.
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
        # Render the initial (empty) state immediately -- pure, no I/O --
        # so the screen never sits blank while the first refresh worker
        # is still in flight. The app opens on Overview; the Torrents
        # workspace's table starts unfocused and hidden.
        self._render_workspace_visibility()
        self._render_all()
        self.refresh_bindings()
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

        Also skipped (same coalescing, not queueing) while a bulk
        mutation is in flight (TUI 2): a refresh racing an Apply could
        otherwise show a transient, confusing mix of pre- and post-
        mutation state. `action_apply_plan` triggers one refresh
        explicitly right after the mutation completes instead.
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
        elif event.worker.group == MUTATION_WORKER_GROUP:
            self._on_mutation_worker_state_changed(event)

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
        # Closure review, R-2 residual: preview freshness must fail
        # closed on *every* unsuccessful refresh, including one whose
        # exception `classify_recoverable_qbit_failure` cannot classify.
        # That path used to `return` straight after `_show_fatal`,
        # skipping invalidation entirely -- leaving an open Preview
        # applicable while the TUI had in fact stopped refreshing. The
        # `finally` makes the invalidation unconditional.
        try:
            if error is not None:
                try:
                    self.controller.apply_refresh_failure(error)
                except Exception as internal_error:
                    self._show_fatal(internal_error)
                    return
            else:
                assert result is not None
                # Audit finding F-1: never reconcile the selection out
                # from under an uncommitted Filters draft -- a
                # half-typed exact-match category matches nothing, so
                # reconciling here would silently erase the operator's
                # selection. The modal's own Apply/Clear/Cancel commit
                # points reconcile instead.
                self.controller.apply_refresh_success(
                    result, reconcile=not self._filters_draft_is_open()
                )
            self._render_all()
        finally:
            self._refresh_preview_freshness(refresh_failed=error is not None)

    def _filters_draft_is_open(self) -> bool:
        """Whether a `FiltersScreen` is anywhere on the screen stack."""
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
        """Render the persistent latest-mutation line, or hide it.

        Hidden entirely until a mutation has completed at least once, so
        an operator who never used a bulk action never sees an empty
        placeholder.
        """
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
        """Update every mounted `DetailsPanel` -- inline and/or modal."""
        state = self.controller.state
        for panel in self.query(DetailsPanel):
            panel.render_state(state)

    # -- context-aware footer ---------------------------------------------

    def check_action(
        self, action: str, parameters: tuple[object, ...]
    ) -> bool | None:
        """Hide (and disable) footer/help-panel actions that are not
        meaningful in the current context.

        Textual consults this both to decide what the `Footer` widget
        shows and whether a key press actually dispatches -- so this is
        also what keeps, say, `c`/`e` from doing anything while on
        Overview, not just from being *advertised* there. `refresh_bindings()`
        is called wherever workspace or focus changes (see
        `_switch_workspace`/`_focus_torrent`/`_clear_focus_and_render`)
        so the `Footer` actually redraws when the answer here changes.

        Always-available actions (`quit`, `toggle_help`, and the
        internal `activate`/`dismiss_overlay`/`cursor_up`/`cursor_down`,
        all `show=False` regardless) return `True` unconditionally.
        Anything else is unavailable while a modal is on top of the
        stack (non-priority App bindings cannot fire there anyway --
        see docs/MEMORY.md -- so this is a defensive, not load-bearing,
        guard). `show_overview`/`show_torrents` are each hidden while
        already on that workspace, so only the one meaningful direction
        ever appears. `focus_search`/`open_filters` require the
        Torrents workspace. `copy_hash`/`explain`/`refresh_details`
        additionally require a focused torrent.
        """
        if action in (
            "quit",
            "toggle_help",
            "activate",
            "dismiss_overlay",
            "cursor_up",
            "cursor_down",
            # `Tab`/`Shift+Tab` are declared on Textual's own `Screen`
            # base class as `Binding(key="tab", action="app.focus_next",
            # ...)` (and `focus_previous`), namespaced to the App --
            # `check_action` is consulted for every such action
            # regardless of which class's BINDINGS declared it, so the
            # blanket "any modal open -> False" rule below silently
            # broke in-modal Tab navigation between fields (e.g.
            # FiltersScreen's category/state Inputs, checkboxes, radio
            # sets) -- verified empirically. These must always be
            # allowed, modal or not.
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
        """Switch the active workspace.

        Zero qBittorrent API calls (`TuiController.set_workspace` is a
        pure state assignment). A no-op while a modal is on top of the
        screen stack, so a stray workspace-switch keystroke never
        switches the workspace underneath an open Filters/Details/Help/
        Explain modal. Search/filter state and the focused torrent are
        untouched by construction (`set_workspace` does not reset
        them); only widget *focus* is actively managed here, so a
        switch never leaves a now-hidden widget focused.
        """
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
        self.refresh_bindings()

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
        self.refresh_bindings()
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
        current `_detail_request_id` -- see `qbit_ops.tui.state` for the full
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
        self._maybe_resolve_pending_explain(request_id, torrent_hash)

    def _maybe_resolve_pending_explain(
        self, request_id: int, torrent_hash: str
    ) -> None:
        """Update a still-open Explain modal once its awaited detail
        fetch completes -- race-safe (see `action_explain`).

        A no-op unless this exact request is the one Explain is
        waiting on. Even then, the update is applied only if: the
        Explain modal is still open (never reopens one the user already
        closed), focus has not moved to a different torrent since, and
        no newer detail request has superseded this one -- any of these
        failing means a stale result for a torrent Explain no longer
        cares about, discarded like any other late result.
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
        """Apply search text live, as it's typed -- no I/O, no debounce
        needed since `TuiController.set_search` is pure in-memory
        filtering. If this hides the currently focused torrent,
        `set_search` itself clears focus/details/any pending detail
        fetch (see `TuiController._reconcile_focus`/`clear_focus`).

        `set_search`'s returned hidden-selection count is deliberately
        not surfaced as a notification here (unlike Filters' Apply/
        Clear/Cancel, see `_reconcile_selection_and_notify`): search
        reconciles on every keystroke, and popping a notification for
        every narrowing character would be noise, not signal. The
        selected count in the filter summary (re-rendered below) still
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
        # Deliberately no selection reconciliation here: this fires on
        # every keystroke while editing Filters live, and a category
        # filter is exact-match -- reconciling now would wipe a
        # selection while the user is still mid-way through typing the
        # very filter that would have kept it visible. Reconciliation
        # happens once, at each real commit point instead -- see
        # `TuiController.set_filters`'s docstring.

    def _reconcile_selection_and_notify(self) -> None:
        """Reconcile the selection against the current filter, and show
        a concise notification if anything was actually dropped.

        The single commit-point helper for Filters Apply/Clear/Cancel
        (see `action_activate`/`FiltersScreen.action_clear`/
        `action_dismiss_overlay`) -- search reconciles inline instead
        (see `TuiController.set_search`'s docstring for why the two
        differ).
        """
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

    # `Input.Submitted` (its `enter` -> `submit` binding) never actually
    # fires for `#search-input`: the App's own `enter` binding
    # (`action_activate`) is `priority=True`, and Textual resolves
    # priority App bindings *before* dispatching the key to the focused
    # widget's own declarative bindings at all -- verified empirically.
    # This differs from a printable character, which `Input._on_key`
    # consumes directly (bypassing bindings resolution entirely, see the
    # `escape` binding's comment above), so typing itself is unaffected.
    # `action_activate` below handles `enter` on the search input
    # directly instead of relying on this handler.

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
        """Bound to `enter` with `priority=True` (see `BINDINGS`), so
        this always fires before any focused widget's own `enter`
        binding -- including `#search-input`'s native `submit`, which
        as a result never actually runs (see `mount_search_input`'s
        neighboring comment).

        Also special-cases `FiltersScreen`: its filter edits already
        apply live (see `on_input_changed`/`on_checkbox_changed`/
        `on_radio_set_changed`), so `enter` there only needs to close
        the modal -- see `FiltersScreen`'s docstring for why this can't
        just be a Screen-level binding.

        For any other modal, if a `Button` currently has focus (e.g.
        after navigating there with Tab/Up/Down), `enter` presses it --
        `Button.press()` triggers exactly the same `Button.Pressed`
        handling a mouse click would. Without this, `enter` silently did
        nothing in `ActionsScreen`/`PreviewScreen`/`ResultScreen`: this
        same `priority=True` binding intercepts the key before the
        focused `Button`'s own native `enter`-activates-click behavior
        ever gets a chance to run (the same mechanism documented for
        `FiltersScreen`/`#search-input` below). Modals with no buttons
        at all (Details, Help, Explain) simply ignore `enter`.

        Otherwise dispatches by active workspace: from Overview, `enter`
        is the documented "browse torrents" shortcut (same as `t`);
        from Torrents, it opens the focused torrent's details -- unless
        a text `Input` (search) currently has focus, in which case
        search already applies live as the user types (see
        `on_input_changed`/`_apply_search`), so `enter` here only needs
        to keep the current text and return focus to the table, not
        open Details.
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

        `begin_manual_detail_refresh` always allocates a *new* request
        id, even though the focused hash is unchanged -- this guarantees
        this explicit request wins over a slower, already-in-flight
        automatic fetch for the same torrent, regardless of which
        happens to complete first. Returns the dispatched `Worker` (or
        `None` if nothing is focused), for the same test-observability
        reason as `_focus_torrent`.
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

        A safe no-op (a notification, not a crash) when nothing is
        focused. Performs no qBittorrent API call. Uses `Textual`'s own
        `App.copy_to_clipboard` (OSC 52), which writes to the terminal
        emulator's clipboard -- some terminals (notably macOS
        Terminal.app) do not support this escape sequence and will
        silently not receive it; that is a terminal limitation, not
        something qbit-ops can detect or work around (see
        docs/COMMANDS.md).
        """
        torrent_hash = self.controller.state.focused_hash
        if torrent_hash is None:
            self.notify("No torrent focused.", severity="warning")
            return
        self.copy_to_clipboard(torrent_hash)
        self.notify(f"Copied hash {_shorten_hash(torrent_hash)}")

    def action_explain(self) -> Worker[Any] | None:
        """Open an evidence-based explanation of the focused torrent.

        Only meaningful in the Torrents workspace; a safe notification
        (never a crash) when nothing is focused. Zero API calls when
        the focused torrent's tracker details are already loaded --
        `TuiController.build_explanation` is pure. Otherwise reuses the
        existing in-flight focused-detail worker if one is already
        running for the current focus (never starts a second,
        redundant `torrents_trackers()` call), or starts exactly one if
        none is in flight (e.g. an earlier fetch failed) -- see
        `_maybe_resolve_pending_explain` for the race-safe completion
        path. Returns the dispatched `Worker` when a new fetch was
        started, for the same test-observability reason as
        `_focus_torrent`; `None` otherwise.
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

    # -- TUI 2: multi-selection + LOW-risk bulk actions ---------------------

    def action_toggle_selection(self) -> None:
        """Toggle the focused torrent's membership in the selection.

        Never implied by navigation alone -- only an explicit `Space`
        (or this method) changes the selection. A safe notification,
        not a crash, when nothing is focused (e.g. an empty filter
        result).
        """
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
        """Select every currently *visible* torrent -- never a hidden
        one, never "the whole instance". Zero qBittorrent API calls."""
        if not self._in_torrents_workspace():
            return
        self.controller.select_all_visible()
        self._render_table()
        self._render_filter_summary()
        self.refresh_bindings()

    def action_deselect_all(self) -> None:
        """Clear the entire selection -- the explicit counterpart to
        `Ctrl+A`. `Escape` already clears a non-empty selection when
        nothing else needs it first (no modal open, not editing text),
        but this binding works unconditionally and unambiguously,
        without competing with Escape's other jobs. Zero API calls; a
        safe no-op (no notification) when nothing is selected."""
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
        """Open the Actions modal for the current selection.

        A safe notification, never a crash, when the selection is
        empty -- `check_action` already hides this from the footer in
        that case, but the guard stays here too since this method can
        be reached directly (tests, or a future caller).
        """
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
        failed refresh, withdraws Apply (audit finding R-2).
        """
        state = self.controller.state
        return state.connection is ConnectionState.CONNECTED and not state.stale

    def _refresh_preview_freshness(
        self, *, refresh_failed: bool = False
    ) -> None:
        """Propagate snapshot staleness into any open `PreviewScreen`.

        Called after every refresh outcome, from a `finally` so no
        failure path can skip it. Staleness is sticky
        (`PreviewScreen.mark_stale`), so a later recovery deliberately
        does not re-enable an old preview -- the operator rebuilds it.

        `refresh_failed=True` invalidates unconditionally, without
        consulting `_snapshot_is_fresh()` (closure review, R-2
        residual): an unclassifiable refresh exception leaves
        `state.connection` at CONNECTED and `state.stale` unset, so the
        freshness heuristic alone would wrongly conclude all is well
        while the TUI has in fact stopped refreshing. Every
        unsuccessful refresh -- connection, authentication,
        configuration, known domain error, or unexpected internal
        exception -- fails closed here.
        """
        if not refresh_failed and self._snapshot_is_fresh():
            return
        for screen in self.screen_stack:
            if isinstance(screen, PreviewScreen):
                screen.mark_stale()

    def action_apply_plan(self) -> None:
        """Apply the plan owned by the currently open `PreviewScreen`.

        A no-op unless `PreviewScreen` is actually on top (Apply is
        only ever reachable by pressing its button, but this guard
        keeps the method safe to call directly too) and unless no
        mutation is already in flight -- double-pressing Apply (or any
        other path that might call this twice) dispatches at most one
        `MUTATION_WORKER_GROUP` worker; a second call while the first is
        still running is silently ignored, never queued.

        `preview.can_apply` is checked here too, not only reflected in
        the button's `disabled` state, so a keyboard Apply on a stale
        preview is genuinely rejected rather than merely discouraged
        (audit finding R-2). No mutation call is ever dispatched while
        stale, reconnecting, unavailable, authentication-failed, or
        configuration-failed.
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
        """Run on a background thread: blocking I/O only, never state
        mutation, and never raises -- see `_refresh_worker_body` for
        why the outcome travels back as a plain tagged tuple.

        `operation_id` travels with the result so a late completion can
        be matched to the exact preview that started it (audit finding
        R-1) rather than to whatever happens to be on top of the stack.
        The middle element reports whether the mutation was actually
        dispatched: `should_proceed` re-checks `self.is_running` *after*
        the shared remote lock is acquired, so a mutation queued behind
        a blocked read is abandoned rather than sent if the application
        shut down while it waited (closure review finding N-2).
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
            # to touch a newer preview or result (audit finding R-1).
            return

        outcome = self._classify_mutation_outcome(plan, error, dispatched)
        self._last_mutation_result = outcome
        self._render_last_action()

        # Completion of the *operation* is deliberately separated from
        # presentation of a *Result modal* (closure review finding N-1).
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
            # Audit finding R-1: only ever dismiss *this* operation's
            # own preview, and only while it is genuinely the active
            # screen. A bare `pop_screen()` would remove whatever sits
            # on top -- potentially Help/Filters/Details/Explain.
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

        # Audit finding R-3 / closure review N-3: the request really may
        # have been submitted, so it must never vanish silently just
        # because its preview is no longer on top. The persistent
        # last-action line (rendered above) is the durable record; this
        # transient toast is supplemental only. Never touches an
        # unrelated modal.
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

        Error-classification order (audit findings F-4 and F-5), applied
        to the **outer** exception first:

        1. `ConfigError`            -> CONFIGURATION
        2. `QbitAuthenticationError`-> AUTHENTICATION
        3. `QbitConnectionError`/`OSError` -> UNAVAILABLE
        4. otherwise, retry the same ladder on `__cause__` (only as
           supporting context, never in preference to a recognized
           outer type -- `apply_bulk_torrent_action` wraps failures in
           a `RuntimeError`, so the cause is where a recoverable error
           usually hides)
        5. still unclassified       -> INTERNAL

        A recoverable outer error therefore can never be downgraded to
        INTERNAL merely because it carries an opaque cause, and a
        `ConfigError` is never blamed on qbit-ops.

        `dispatched=False` with no error means the mutation deliberately
        never left: it lost authority while queued behind the shared
        remote lock (closure review finding N-2). That is reported as a
        distinct cancelled-before-dispatch outcome -- not UNAVAILABLE
        (nothing failed remotely), not APPLIED (nothing was sent), and
        not the operator cancelling before pressing Apply.
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
        """The documented selection policy, one branch per outcome.

        * APPLIED    -- drop exactly the hashes the frozen plan
          submitted; a skipped torrent keeps its selection.
        * NO_CHANGES -- drop the plan's hashes: they were found and
          already satisfied, so re-selecting them changes nothing.
        * NO_MATCH   -- drop only the hashes *proven absent* from the
          snapshot (`not_found` skips); nothing else was established.
        * any failure (CONFIGURATION/AUTHENTICATION/UNAVAILABLE/
          INTERNAL) -- retain the live selection so the operator can
          retry deliberately; the frozen plan stays inspectable, and a
          fresh preview is required before any later Apply because the
          old one is by then sticky-stale (see `PreviewScreen`).

        Whatever survives is then reconciled against currently visible
        hashes, preserving the "selection ⊆ visible" invariant.
        """
        if outcome.cancelled_before_dispatch:
            # Nothing was sent, so nothing may be treated as submitted:
            # the live selection is kept intact (closure review N-2).
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
        or (TUI 2) clear a non-empty selection.

        Special-cases `FiltersScreen`: `escape` there means *cancel*,
        i.e. revert to `FiltersScreen.original_filters` before closing
        -- not a plain `Screen`-level binding, for the same App-priority
        reason documented on `FiltersScreen`/`action_activate`. Also
        refuses to close `PreviewScreen` while a mutation is actually
        in flight (`applying=True`) -- cancelling out from under an
        already-dispatched Apply would leave nothing able to observe
        its result.

        Also special-cases `ResultScreen` for the exact same reason:
        `escape` is a `priority=True` App binding, which always wins
        over a same-key `Screen`-level binding -- `ResultScreen`'s own
        `action_dismiss` (which triggers the documented post-dismissal
        selection-clearing policy) would otherwise silently never run,
        leaving successfully-acted-on torrents stuck selected. Verified
        empirically; see docs/MEMORY.md.
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
