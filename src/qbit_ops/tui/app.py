"""Textual application for `qbit-ops tui` (LOW-risk bulk actions only).

Security boundary -- only imports the
LOW-risk, frozen-plan Pause/Resume/Reannounce functions -- never a
rescanning or deletion function, or `qbit_ops.cli`. Enforced by
`tests/test_tui_security.py`.

Every qBittorrent API call runs on a Textual thread worker, never on
the UI thread; `apply_*` (state-mutating) `TuiController` methods only
ever run from `on_worker_state_changed`, which Textual delivers on the
UI thread.
"""

from __future__ import annotations

from time import monotonic
from typing import Any, cast

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    RadioSet,
)
from textual.widgets.data_table import (
    CellDoesNotExist,
    ColumnDoesNotExist,
    RowDoesNotExist,
)
from textual.worker import Worker, WorkerState

from qbit_core.config import QbitConfig
from qbit_core.errors import InvalidInputError
from qbit_core.features.connection_setup import (
    ConnectionAttempt,
    EnvFileExistsError,
    build_connection_config,
    try_connection,
    write_connection_env_file,
)
from qbit_core.features.explain import ExplanationReport
from qbit_core.features.torrents import (
    BulkTorrentActionPlan,
    TorrentBulkAction,
)
from qbit_core.shared.execution import MutationStatus
from qbit_core.shared.selection import (
    describe_torrent_filter,
)
from qbit_ops import __version__
from qbit_ops.app_services import (
    TuiRefreshResult,
    create_qbit_client,
)
from qbit_ops.config import collect_masking_sources, get_user_env_file
from qbit_ops.tui.formatting import (
    _COLUMN_WIDTHS,
    NARROW_WIDTH_THRESHOLD,
    _column_header,
    _columns_for_width,
    _format_last_action_line,
    _format_result_notification,
    _format_torrents_title,
    _indicator_cell,
    _name_column_width,
    _progress_column_width,
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
from qbit_ops.tui.modals.setup import SetupScreen
from qbit_ops.tui.modals.sort import SortScreen
from qbit_ops.tui.state import (
    DEFAULT_REFRESH_INTERVAL_SECONDS,
    GRAPH_SAMPLE_INTERVAL_SECONDS,
    ConnectionState,
    MutationUiResult,
    SortOrder,
    TuiController,
    Workspace,
    _classify_mutation_error,
)
from qbit_ops.tui.theme import (
    BRAND_CSS_VARIABLES,
    QBIT_OPS_THEME,
    THEME_NAME,
)
from qbit_ops.tui.widgets.details import DetailsPanel
from qbit_ops.tui.widgets.filters import FiltersPanel
from qbit_ops.tui.widgets.overview import OverviewPanel, WorkspaceTabs
from qbit_ops.tui.widgets.status_bar import (
    CommandBar,
    ConnectionBanner,
    FilterSummary,
    FooterTotal,
    GlobalRateDisplay,
    LastActionBar,
)

# Distinguishes worker messages in on_worker_state_changed, which Textual
# delivers through a single handler regardless of group.
REFRESH_WORKER_GROUP = "qbit-refresh"
DETAIL_WORKER_GROUP = "qbit-detail"
MUTATION_WORKER_GROUP = "qbit-mutation"
SETUP_WORKER_GROUP = "qbit-setup"
SAMPLE_WORKER_GROUP = "qbit-sample"

# The horizontal half of the keyboard grammar: `up`/`down` move within
# a surface, `left`/`right` between the two pages. Advertised in the
# table's own border rather than the footer, so the keys are read where
# they apply.
TABLE_NAV_HINT = "← Overview · Torrents →"


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

        assert isinstance(self.app, QbitOpsTuiApp)
        self.app._render_table()


class QbitOpsTuiApp(App[None]):
    """The Overview-first operator dashboard."""

    # No qbit-ops commands live in the command palette yet.
    ENABLE_COMMAND_PALETTE = False

    # Every rule lives in one external sheet, shipped inside the wheel
    # (see `tests/test_distribution.py`). Nine per-class `CSS` blocks
    # used to re-decide the same frame nine times; the sheet plus
    # `QbitModal` leave each surface only what is its own.
    CSS_PATH = "qbit_ops.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        # Never shown in the footer: the top workspace-tabs strip is
        # the sole visible way to advertise switching pages now (see
        # `WorkspaceTabs`) -- these keys still work.
        Binding("1", "show_overview", "Overview", show=False),
        Binding("g", "show_overview", "Overview", show=False),
        Binding("2", "show_torrents", "Torrents", show=False),
        Binding("t", "show_torrents", "Torrents", show=False),
        # Non-priority on purpose: a focused `Input` handles `left`/
        # `right` first and keeps them, so typing a search term still
        # moves the caret instead of changing page.
        Binding("left", "show_overview", "Overview", show=False),
        Binding("right", "show_torrents", "Torrents", show=False),
        # One visible token, announcing the arrows alone: the command bar
        # teaches the gesture a first-time reader reaches for, it is not
        # the key inventory. `j`/`k` keep working, and the help modal
        # lists `j/k, ↑/↓` together for whoever wants the whole set.
        #
        # The token hangs off `j`, not `down`, and that is load-bearing:
        # a focused `DataTable` binds `up`/`down` itself, so the screen's
        # own arrow bindings are shadowed out of `active_bindings` and
        # the command bar would render nothing at all.
        Binding("j", "cursor_down", "Navigate", key_display="↑/↓"),
        Binding("k", "cursor_up", "Navigate", show=False),
        Binding("down", "cursor_down", "Navigate", show=False),
        Binding("up", "cursor_up", "Navigate", show=False),
        Binding("slash", "focus_search", "Search"),
        Binding("f", "open_filters", "Filters"),
        Binding("s", "open_sort", "Sort"),
        # priority=True: DataTable's own `enter` binding (select_cursor)
        # would otherwise win while the table has focus.
        Binding("enter", "activate", "Open", show=False, priority=True),
        # Focused-torrent actions: reachable by key and via Help/the
        # Details modal, but not in the global footer (kept to primary
        # workspace actions -- see `check_action`/Footer rendering).
        Binding("c", "copy_hash", "Copy", show=False),
        Binding("e", "explain", "Explain", show=False),
        Binding("r", "refresh_details", "Refresh", show=False),
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
        needs_setup: bool = False,
        small_caps_titles: bool = False,
    ) -> None:
        super().__init__()
        self.controller = TuiController(
            client_factory=client_factory, host=host
        )
        self.refresh_interval = refresh_interval
        self.needs_setup = needs_setup
        # Off by default: window titles are letter-spaced ordinary
        # capitals, which need no glyph a terminal font might not have.
        # The Unicode small capitals stay available for whoever has a
        # font that covers all three of their blocks.
        self.small_caps_titles = small_caps_titles
        self._hash_by_row: dict[int, str] = {}
        self._rebuilding_table = False
        # Incremental-update bookkeeping for `_render_table` -- see its
        # docstring. `_last_table_signature` also folds in the active
        # sort, since a sort-only change still needs a fresh header
        # arrow even when the column set/widths themselves haven't
        # changed (`_column_header` renders it into the header text).
        self._last_table_signature: tuple[Any, ...] | None = None
        self._last_row_order: list[str] = []
        self._last_row_values: dict[str, dict[str, Any]] = {}
        self._last_row_sources: dict[str, tuple[Any, ...]] = {}
        self._refresh_worker: Worker[Any] | None = None
        self._last_detail_worker: Worker[Any] | None = None
        self._pending_explain_request_id: int | None = None
        self._explain_screen: ExplainScreen | None = None
        self._mutation_worker: Worker[Any] | None = None
        self._preview_screen: PreviewScreen | None = None
        self._active_mutation_plan: BulkTorrentActionPlan | None = None
        self._last_operation_id = 0
        self._last_mutation_result: MutationUiResult | None = None
        self._setup_worker: Worker[Any] | None = None
        self._sample_worker: Worker[Any] | None = None
        self._sample_tick: int | None = None
        self._sample_timer: Timer | None = None
        self._sampling_stopped_at: float | None = None
        # Nothing may reach qBittorrent before the first-run form is
        # answered, and `_render_workspace_visibility()` runs before it.
        # Sampling therefore stays shut until `_begin_refreshing()` has
        # opened it, rather than relying on call order.
        self._refreshing_started = False
        self._setup_config: QbitConfig | None = None

    def get_default_screen(self) -> Screen[None]:
        return MainScreen(id="_default")

    def compose(self) -> ComposeResult:
        with Horizontal(id="top-bar"):
            yield WorkspaceTabs(id="workspace-tabs")
            yield GlobalRateDisplay(id="global-rate")
        yield ConnectionBanner(id="banner")
        yield OverviewPanel(
            id="overview-workspace", small_caps=self.small_caps_titles
        )
        with Vertical(id="torrents-workspace"):
            yield FilterSummary(id="filter-summary")
            yield DataTable(id="torrents", cursor_type="row")
            yield LastActionBar(id="last-action")
        with Horizontal(id="footer-row"):
            yield CommandBar(id="command-bar")
            yield FooterTotal(id="footer-total")

    def get_css_variables(self) -> dict[str, str]:
        # The brand gradient has two ends and a `Theme` has one
        # `primary` slot, so the ends reach the stylesheet here.
        return {**super().get_css_variables(), **BRAND_CSS_VARIABLES}

    def on_mount(self) -> None:
        # Registered before anything paints: `$primary` must already be
        # the brand orange the first time a rule resolves it.
        self.register_theme(QBIT_OPS_THEME)
        self.theme = THEME_NAME
        self.screen.border_title = f"qbit-ops v{__version__}"
        self.query_one("#torrents", DataTable).border_subtitle = TABLE_NAV_HINT
        # Render the initial empty state so the screen isn't blank
        # while the first refresh worker is still in flight.
        self._render_workspace_visibility()
        self._render_all()
        self.refresh_bindings()
        if self.needs_setup:
            # Nothing may reach qBittorrent before the form is answered:
            # the periodic refresh only starts once a file is written.
            self.push_screen(SetupScreen())
            return
        self._begin_refreshing()

    def _begin_refreshing(self) -> None:
        self._refreshing_started = True
        self.set_interval(self.refresh_interval, self._start_periodic_refresh)
        self._start_periodic_refresh()
        self._resume_sampling()

    # -- the graph's own clock ----------------------------------------
    #
    # A second timer, deliberately not `refresh_interval`: the graph
    # window is a span of wall-clock time, so its axis label would stop
    # being true the moment an operator passed `--interval`.
    #
    # It costs one `transfer_info()` per second, which is why it runs
    # *only while the Overview is on screen*. On the Torrents page it is
    # stopped outright, and the seconds it did not watch are recorded as
    # unmeasured rather than back-filled with zeroes -- nobody was
    # looking, and the graph says so instead of drawing a still library.

    def _resume_sampling(self) -> None:
        if not self._refreshing_started or self._sample_timer is not None:
            return
        if self._sampling_stopped_at is not None:
            elapsed = monotonic() - self._sampling_stopped_at
            self.controller.skip_rate_samples(
                int(elapsed // GRAPH_SAMPLE_INTERVAL_SECONDS)
            )
            self._sampling_stopped_at = None
        self._sample_timer = self.set_interval(
            GRAPH_SAMPLE_INTERVAL_SECONDS, self._start_rate_sample
        )
        self._start_rate_sample()

    def _pause_sampling(self) -> None:
        if self._sample_timer is None:
            return
        self._sample_timer.stop()
        self._sample_timer = None
        self._sampling_stopped_at = monotonic()

    def _start_rate_sample(self) -> None:
        """Start one rate sample, unless one is still in flight.

        Coalesces rather than queues, exactly like the periodic refresh:
        an instance slower than a second must not accumulate a backlog
        of workers.
        """
        if self.controller.state.connection in (
            ConnectionState.AUTH_FAILED,
            ConnectionState.CONFIG_FAILED,
        ):
            return

        # The column is opened first, on the clock, whatever happens
        # next: a second that passed is a second the trace has to
        # account for, even if nothing is asked of qBittorrent for it.
        tick = self.controller.open_rate_slot()
        self._render_overview()
        if self._sample_worker is not None and self._sample_worker.is_running:
            # An instance slower than a second: this tick asks nothing
            # and its slot stays unmeasured, which is the truth.
            return
        self._sample_tick = tick
        self._sample_worker = self.run_worker(
            self.controller.collect_transfer_rates,
            group=SAMPLE_WORKER_GROUP,
            thread=True,
            exit_on_error=False,
        )

    def _on_sample_worker_state_changed(
        self, event: Worker.StateChanged
    ) -> None:
        if event.state is not WorkerState.SUCCESS:
            # A failed sample is not an incident: the periodic refresh
            # owns connection state, and this second simply goes
            # unmeasured like any other second nobody watched.
            return
        rates = event.worker.result
        if rates is None or self._sample_tick is None:
            return
        self.controller.settle_rate_sample(self._sample_tick, rates)
        self._sample_tick = None
        self._render_overview()

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
        elif event.worker.group == SAMPLE_WORKER_GROUP:
            self._on_sample_worker_state_changed(event)
        elif event.worker.group == SETUP_WORKER_GROUP:
            self._on_setup_worker_state_changed(event)

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
        self._render_global_rate()
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

    def _render_global_rate(self) -> None:
        self.query_one("#global-rate", GlobalRateDisplay).render_state(
            self.controller.state.status
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
        """One line: active criteria and sort on the left, the count
        flushed right -- a row saved, and a right edge that lines up."""
        summary = self.query_one("#filter-summary", FilterSummary)
        state = self.controller.state
        visible = state.visible
        total = state.total_torrents or 0
        shown = len(visible.matched) if visible is not None else 0

        counts = f"{shown:,} shown / {total:,}"
        if state.selected_hashes:
            counts += f" · {len(state.selected_hashes):,} selected"

        criteria = []
        description = describe_torrent_filter(state.filters)
        if description != "none":
            criteria.append(description)
        if state.search:
            criteria.append(f"search: {state.search}")

        criteria_text = " · ".join(criteria) if criteria else "No filters"
        summary.render_state(
            criteria=f"{criteria_text} · Sorted by {state.sort.label}",
            counts=counts,
        )

        self.query_one("#torrents", DataTable).border_title = (
            _format_torrents_title(
                shown, len(state.selected_hashes), state.sort.label
            )
        )
        self._refresh_search_command_bar_if_active()

    def _render_last_action(self) -> None:
        bar = self.query_one("#last-action", LastActionBar)
        outcome = self._last_mutation_result
        if outcome is None:
            bar.remove_class("visible")
            return
        bar.update(_format_last_action_line(outcome))
        bar.add_class("visible")

    def _render_table(self) -> None:
        """Apply `state.visible` to the `#torrents` `DataTable`.

        Formatting (`_last_row_sources`), column rebuild
        (`_last_table_signature`), row rebuild (visible-hash order), and
        per-cell diff (`_last_row_values`) are each gated
        independently, so a resize or a search keystroke only pays for
        what actually changed -- an unwatched torrent is never
        reformatted, and an unchanged cell is never re-written.
        Re-measure with `scripts/profile_tui_table.py`.
        """
        table = self.query_one("#torrents", DataTable)
        state = self.controller.state
        visible = state.visible
        columns = _columns_for_width(self.size.width)
        # Progress renders a bar+percentage once there's room for it,
        # a bare percentage below the narrow threshold -- same
        # threshold `_columns_for_width` already uses to decide
        # whether Rate fits.
        bar_progress = self.size.width >= NARROW_WIDTH_THRESHOLD
        # `Name` gets whatever width remains once every other visible
        # column's own rendered width (declared width + DataTable's
        # padding) is accounted for -- never auto-sized to its content,
        # or a long release name would force horizontal scrolling.
        name_width = _name_column_width(
            self.size.width,
            tuple(c for c in columns if c != "Name"),
            bar=bar_progress,
        )
        previously_focused = state.focused_hash

        table_signature = (columns, name_width, bar_progress, state.sort)
        columns_rebuilt = table_signature != self._last_table_signature
        # Shared across every row this render -- folded into each row's
        # own source tuple below, so a bar/width/search change alone
        # (without the row's own data changing) still forces a
        # reformat of that row's `Name`/`Progress` cell.
        shared_context = (bar_progress, name_width, state.search)

        new_order = [] if visible is None else [t.hash for t in visible.matched]
        new_values: dict[str, dict[str, Any]] = {}
        new_sources: dict[str, tuple[Any, ...]] = {}
        changed_hashes: set[str] = set()
        if visible is not None:
            for torrent in visible.matched:
                focused = torrent.hash == previously_focused
                selected = torrent.hash in state.selected_hashes
                source = (
                    torrent.name,
                    torrent.state,
                    torrent.progress,
                    torrent.download_rate,
                    torrent.upload_rate,
                    torrent.ratio,
                    torrent.category,
                    focused,
                    selected,
                    shared_context,
                )
                new_sources[torrent.hash] = source
                if source == self._last_row_sources.get(torrent.hash):
                    new_values[torrent.hash] = self._last_row_values[
                        torrent.hash
                    ]
                    continue
                new_values[torrent.hash] = _torrent_row_values(
                    torrent,
                    focused=focused,
                    selected=selected,
                    bar=bar_progress,
                    name_width=name_width,
                    search=state.search,
                )
                changed_hashes.add(torrent.hash)

        rows_reusable = (
            not columns_rebuilt and new_order == self._last_row_order
        )

        self._rebuilding_table = True
        try:
            if columns_rebuilt:
                table.clear(columns=True)
                for name in columns:
                    width = (
                        name_width
                        if name == "Name"
                        else (
                            _progress_column_width(bar=bar_progress)
                            if name == "Progress"
                            else _COLUMN_WIDTHS.get(name)
                        )
                    )
                    table.add_column(
                        _column_header(name, state.sort, width=width),
                        width=width,
                        key=name,
                    )
                self._last_table_signature = table_signature
                self._add_all_rows(table, columns, new_order, new_values)
            elif rows_reusable:
                self._diff_rows(table, columns, changed_hashes, new_values)
            else:
                table.clear()
                self._add_all_rows(table, columns, new_order, new_values)

            if not rows_reusable and previously_focused is not None:
                for row_index, torrent_hash in enumerate(new_order):
                    if torrent_hash == previously_focused:
                        table.move_cursor(row=row_index)
                        break
        finally:
            self._rebuilding_table = False

        self._hash_by_row = dict(enumerate(new_order))
        self._last_row_order = new_order
        self._last_row_values = new_values
        self._last_row_sources = new_sources

    def _add_all_rows(
        self,
        table: DataTable[Any],
        columns: tuple[str, ...],
        order: list[str],
        values_by_hash: dict[str, dict[str, Any]],
    ) -> None:
        for torrent_hash in order:
            values = values_by_hash[torrent_hash]
            table.add_row(*(values[name] for name in columns), key=torrent_hash)

    def _diff_rows(
        self,
        table: DataTable[Any],
        columns: tuple[str, ...],
        changed_hashes: set[str],
        new_values: dict[str, dict[str, Any]],
    ) -> None:
        """Update only the cells of rows whose source data actually
        changed, and only the cells whose formatted value differs.

        `_last_row_values` may be missing or stale for a hash touched
        only by `_refresh_indicator_cell`, which writes the cell
        directly without updating this cache -- that self-heals here:
        the comparison still detects the already-applied difference and
        re-issues the same value, a harmless no-op, never a correctness
        issue.
        """
        for torrent_hash in changed_hashes:
            values = new_values[torrent_hash]
            old_values = self._last_row_values.get(torrent_hash)
            for name in columns:
                value = values[name]
                if old_values is not None and old_values.get(name) == value:
                    continue
                try:
                    table.update_cell(
                        torrent_hash, name, value, update_width=False
                    )
                except (CellDoesNotExist, ColumnDoesNotExist, RowDoesNotExist):
                    pass

    def _refresh_indicator_cell(self, torrent_hash: str | None) -> None:
        """Update one row's focus/selection glyphs in place.

        Cheaper than a full `_render_table()` rebuild for a cursor move
        or selection toggle. A no-op if the row isn't currently in the
        table (e.g. focus cleared, or filtered out).
        """
        if torrent_hash is None:
            return
        table = self.query_one("#torrents", DataTable)
        state = self.controller.state
        cell = _indicator_cell(
            focused=state.focused_hash == torrent_hash,
            selected=torrent_hash in state.selected_hashes,
        )
        try:
            table.update_cell(torrent_hash, "Sel", cell, update_width=False)
        except (CellDoesNotExist, ColumnDoesNotExist, RowDoesNotExist):
            pass

    def _render_details_panels(self) -> None:
        # `self.query()` only ever searches `App.default_screen` -- the
        # Details modal's own `DetailsPanel` lives on whichever screen
        # is currently on top of `screen_stack`, so this must query
        # `self.screen` instead (a harmless no-op when nothing is open,
        # since `DetailsPanel` no longer lives outside the modal).
        state = self.controller.state
        for panel in self.screen.query(DetailsPanel):
            panel.render_state(state, app_width=self.size.width)

    # -- context-aware footer ---------------------------------------------

    def check_action(
        self, action: str, parameters: tuple[object, ...]
    ) -> bool | None:
        """Hide (and disable) footer/help-panel actions not meaningful
        in the current context; also gates whether a key press fires.
        """
        if action in ("cursor_up", "cursor_down"):
            # Unchanged inside a modal, where the dialog scrolls. On
            # the main screen `action_cursor_*` already no-ops outside
            # Torrents -- gating it here only stops the command bar
            # advertising a move there is nothing to move through.
            if len(self.screen_stack) > 1:
                return True
            return self.controller.state.workspace is Workspace.TORRENTS
        if action in (
            "quit",
            "toggle_help",
            "activate",
            "dismiss_overlay",
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
        if action in ("focus_search", "open_filters", "open_sort"):
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
            # `#search-input` now lives in the always-mounted
            # `#footer-row`, not inside `#torrents-workspace` -- it no
            # longer disappears on its own when that workspace is
            # hidden, and would otherwise keep eating keystrokes meant
            # for Overview navigation.
            search_inputs = self.query("#search-input")
            if search_inputs:
                search_inputs.first().remove()
                self._push_search_state(None)
            self.screen.set_focus(None)
        self.refresh_bindings()

    def _render_workspace_visibility(self) -> None:
        workspace = self.controller.state.workspace
        # The per-second sampler exists for the graph, and the graph is
        # only on this page.
        if workspace is Workspace.OVERVIEW:
            self._resume_sampling()
        else:
            self._pause_sampling()
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
        previous_hash = self.controller.state.focused_hash
        self.controller.clear_focus()
        self._render_details_panels()
        self._refresh_indicator_cell(previous_hash)
        self.refresh_bindings()

    def _focus_torrent(self, torrent_hash: str) -> None:
        """Focus a torrent -- zero qBittorrent calls.

        `begin_focus_change` bumps the detail-request generation
        (invalidating any in-flight fetch for the previous torrent) and
        clears the cached tracker details; the fetch itself is
        dispatched only when the Details modal opens or is refreshed --
        see `DetailsScreen.on_mount`/`action_refresh_details`.
        """
        previous_hash = self.controller.state.focused_hash
        self.controller.begin_focus_change(torrent_hash)
        self._render_details_panels()
        self._refresh_indicator_cell(previous_hash)
        self._refresh_indicator_cell(torrent_hash)
        self.refresh_bindings()

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

        The hidden-selection count `set_search` returns isn't surfaced
        as a notification: search reconciles on every keystroke, and a
        toast per character would be noise. The filter summary
        (re-rendered below) still reflects it immediately.
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

    async def action_focus_search(self) -> None:
        if not self._in_torrents_workspace():
            return
        await self.mount_search_input()

    async def mount_search_input(self) -> None:
        existing = self.query("#search-input")
        if existing:
            existing.first().focus()
            return
        search = Input(value=self.controller.state.search, id="search-input")
        # Mounted into `#footer-row`, alongside `CommandBar` -- not a
        # separate row above the footer. Zero-width/borderless (see the
        # the stylesheet): it renders nothing itself, it only captures
        # keystrokes, while `CommandBar.set_search_state` renders the
        # visible `search: xxx`/`Total: y` tokens in its place. Awaited:
        # focusing a widget immediately after an unawaited `mount()` can
        # silently lose the focus request under real key-dispatch timing.
        await self.query_one("#footer-row", Horizontal).mount(search)
        self._push_search_state(self.controller.state.search)
        search.focus()

    def _push_search_state(self, text: str | None) -> None:
        """Push the live search text (or `None` to restore the normal
        `[/→Search]` token) into `CommandBar`, and the right-aligned
        `|Total: y|` token (or clear it) into `FooterTotal`."""
        command_bar = self.query_one("#command-bar", CommandBar)
        footer_total = self.query_one("#footer-total", FooterTotal)
        if text is None:
            command_bar.set_search_state(None)
            footer_total.set_total(None)
            return
        command_bar.set_search_state(text)
        state = self.controller.state
        total = state.total_torrents or 0
        footer_total.set_total(total)

    def _refresh_search_command_bar_if_active(self) -> None:
        """Keep the footer's `|Total: y|` token current across a
        refresh, without touching it while search isn't open."""
        if self.query("#search-input"):
            self._push_search_state(self.controller.state.search)

    # Input.Submitted never fires for #search-input: the App's priority
    # `enter` binding (action_activate) intercepts it first and handles
    # the search input directly instead.

    def action_open_filters(self) -> None:
        if not self._in_torrents_workspace():
            return
        self.push_screen(FiltersScreen(self.controller.state.filters))
        self.refresh_bindings()

    def action_open_sort(self) -> None:
        if not self._in_torrents_workspace():
            return
        self.push_screen(SortScreen(self.controller.state.sort))
        self.refresh_bindings()

    def apply_sort(self, order: SortOrder) -> None:
        """Apply a new local sort order picked from `SortScreen`.

        Purely local reordering (zero API calls) -- focus and
        selection are untouched by `TuiController.set_sort`, and
        `_render_table` already restores the cursor to the previously
        focused hash after any rebuild.
        """
        self.controller.set_sort(order)
        self._render_table()
        self._render_filter_summary()

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

        self.push_screen(DetailsScreen())
        self.refresh_bindings()

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
        self._refresh_indicator_cell(torrent_hash)
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
                "Snapshot stale -- rebuild the preview after reconnection.",
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

    # -- first-run setup -------------------------------------------------

    def submit_setup(
        self, *, host: str, username: str, password: str, force: bool
    ) -> None:
        """Validate the form, then test the connection off the UI thread.

        `force` carries the operator's answer to whatever
        `SetupScreen.request_confirmation` already reported (a failed
        test, an existing file), so a confirmed submission writes
        without testing again.
        """
        screen = self._setup_screen()
        if screen is None:
            return
        try:
            config = build_connection_config(
                host=host, username=username, password=password
            )
        except InvalidInputError as error:
            screen.show_message(str(error))
            return

        if force:
            self._complete_setup(config, force=True)
            return

        if (
            self._setup_worker is not None
            and not self._setup_worker.is_finished
        ):
            return

        self._setup_config = config
        screen.show_message(f"Testing {config.host}...")
        self._setup_worker = self.run_worker(
            self._setup_worker_body,
            group=SETUP_WORKER_GROUP,
            thread=True,
            exit_on_error=False,
        )

    def _setup_worker_body(self) -> ConnectionAttempt:
        """Run on a background thread: one login attempt, never raises
        for a recognized unreachable/rejected instance."""
        assert self._setup_config is not None
        return try_connection(self._setup_config)

    def _on_setup_worker_state_changed(
        self, event: Worker.StateChanged
    ) -> None:
        if event.state is not WorkerState.SUCCESS or not self.is_running:
            return
        screen = self._setup_screen()
        config = self._setup_config
        if screen is None or config is None:
            return

        attempt = cast(ConnectionAttempt, event.worker.result)
        reasons: list[str] = []
        if not attempt.ok:
            reasons.append(attempt.detail or "Connection failed.")
        target = get_user_env_file()
        if target.exists():
            reasons.append(f"{target} already exists.")

        if reasons:
            # A failed test informs, it never cancels: an instance that
            # is simply not started yet is as ordinary as a typo.
            screen.request_confirmation(reasons)
            return
        self._complete_setup(config, force=False)

    def _complete_setup(self, config: QbitConfig, *, force: bool) -> None:
        screen = self._setup_screen()
        if screen is None:
            return
        target = get_user_env_file()
        try:
            written = write_connection_env_file(target, config, force=force)
        except (EnvFileExistsError, OSError) as error:
            screen.show_message(str(error))
            return

        self.controller.set_host(config.host)
        self.needs_setup = False
        self.pop_screen()
        self.notify(f"Wrote {written} (0600).")
        for source in collect_masking_sources(written):
            self.notify(source.message, severity="warning")
        self.refresh_bindings()
        self._begin_refreshing()

    def _setup_screen(self) -> SetupScreen | None:
        return next(
            (
                screen
                for screen in self.screen_stack
                if isinstance(screen, SetupScreen)
            ),
            None,
        )

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
            if isinstance(screen, SetupScreen):
                # Nothing behind it to go back to -- leaving is the
                # form's own Quit button, never a dismissal.
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

        search_inputs = self.query("#search-input")
        if search_inputs:
            search_inputs.first().remove()
            self._push_search_state(None)
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


def run_tui(
    *,
    client_factory: Any = create_qbit_client,
    host: str | None = None,
    refresh_interval: float = DEFAULT_REFRESH_INTERVAL_SECONDS,
    needs_setup: bool = False,
    small_caps_titles: bool = False,
) -> None:
    """Run the TUI application (blocking until the user quits)."""
    app = QbitOpsTuiApp(
        client_factory=client_factory,
        host=host,
        refresh_interval=refresh_interval,
        needs_setup=needs_setup,
        small_caps_titles=small_caps_titles,
    )
    app.run()
