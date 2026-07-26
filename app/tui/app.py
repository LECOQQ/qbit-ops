"""Textual application for `qbit-ops tui` (read-only V1, visual polish).

Security boundary (docs/TUI_ARCHITECTURE_REVIEW.md §10, revised for TUI
2 -- see docs/DECISIONS.md): this module and every other module under
`app/tui/` must never import `app.main`, `app.torrents.plan_bulk_torrent_action`
(always rescans, accepts `--all`), any tracker mutation `plan_*`/
`apply_*` function, any deletion function,
`app.torrents.list_torrents_with_trackers`, or
`app.torrents._get_tracker_details`. It may import exactly
`app.torrents.build_bulk_action_plan_from_snapshot`/
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
torrent, built by the same pure, zero-API `app.explain.
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
`app.torrents.build_bulk_action_plan_from_snapshot` -- no second rule
catalogue) shown in `PreviewScreen`; only an explicit Apply there
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
"""

from __future__ import annotations

from datetime import datetime, tzinfo
from typing import Any

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Input,
    RadioButton,
    RadioSet,
    Static,
)
from textual.worker import Worker, WorkerState

from app.app_services import (
    TuiRefreshResult,
    classify_recoverable_qbit_failure,
    create_qbit_client,
)
from app.execution import MutationStatus
from app.explain import Evidence, ExplanationFinding, ExplanationReport
from app.explain import ExplanationSeverity as Severity
from app.status import Health
from app.torrents import (
    BulkTorrentActionPlan,
    SelectedTorrent,
    TorrentBulkAction,
    TorrentFilter,
    build_torrent_filter,
    describe_torrent_filter,
)
from app.tui.state import (
    DEFAULT_REFRESH_INTERVAL_SECONDS,
    ConnectionState,
    TuiController,
    TuiState,
    Workspace,
)

NARROW_WIDTH_THRESHOLD = 100
WIDE_WIDTH_THRESHOLD = 130

# Worker group names -- used to tell a periodic-refresh worker's
# `Worker.StateChanged` message apart from a focused-detail worker's in
# `on_worker_state_changed` (Textual delivers both through the same
# handler). See docs/DECISIONS.md (worker hardening phase) for the full
# threading design.
REFRESH_WORKER_GROUP = "qbit-refresh"
DETAIL_WORKER_GROUP = "qbit-detail"
MUTATION_WORKER_GROUP = "qbit-mutation"

_PAST_TENSE_ACTION: dict[TorrentBulkAction, str] = {
    "pause": "paused",
    "resume": "resumed",
    "start": "started",
    "reannounce": "reannounced",
}

_HEALTH_STYLES: dict[Health, str] = {
    Health.HEALTHY: "bold green",
    Health.WARNING: "bold yellow",
    Health.CRITICAL: "bold red",
    Health.UNAVAILABLE: "bold red",
}

_SEVERITY_STYLES: dict[Severity, str] = {
    Severity.INFO: "bold green",
    Severity.WARNING: "bold yellow",
    Severity.CRITICAL: "bold red",
    Severity.UNKNOWN: "bold magenta",
}

_CONNECTION_LABELS: dict[ConnectionState, str] = {
    ConnectionState.CONNECTING: "connecting",
    ConnectionState.CONNECTED: "connected",
    ConnectionState.RECONNECTING: "reconnecting",
    ConnectionState.AUTH_FAILED: "unavailable (authentication failed)",
    ConnectionState.CONFIG_FAILED: "unavailable (configuration invalid)",
}

_OVERVIEW_NAV_HINT = "[bold]Enter[/bold] / [bold]t[/bold]   Browse torrents"


class WorkspaceTabs(Static):
    """Always-visible indicator of which workspace is active.

    Purely presentational -- reflects `TuiState.workspace`, never
    decides navigation itself (see `QbitOpsTuiApp._switch_workspace`).
    """

    def render_state(self, workspace: Workspace) -> None:
        overview = _tab_label(
            "Overview", "1/g", workspace is Workspace.OVERVIEW
        )
        torrents = _tab_label(
            "Torrents", "2/t", workspace is Workspace.TORRENTS
        )
        self.update(f"{overview}   {torrents}")


def _tab_label(name: str, keys: str, active: bool) -> str:
    text = f"{name} ({keys})"
    return f"[reverse bold] {text} [/reverse bold]" if active else f" {text} "


class OverviewPanel(VerticalScroll):
    """The Overview workspace's content, grouped into distinct conceptual
    cards -- built entirely from the same `TuiState` the periodic
    refresh already populates. No qBittorrent call of its own, no
    tracker-wide scan, no invented recommendations or confidence
    scores.

    Grouping deliberately keeps three dimensions separate rather than
    presenting them as one partition of "total": Activity (downloading/
    seeding/stopped/checking -- a torrent's current transfer state),
    Completion (completed/incomplete -- a torrent's progress, which a
    seeding *and* completed *and* stopped torrent all satisfy at once),
    and Attention (stalled/errored/unknown -- conditions worth an
    operator's attention, again independent of the other two). Every
    count reuses `app.status`/`app.torrent_states`'s existing
    classifiers -- see the module docstring.
    """

    def render_state(self, state: TuiState) -> None:
        self.remove_children()

        if state.status is None:
            self.mount(
                Static("Connecting to qBittorrent...", classes="ov-card")
            )
            self.mount(Static(_OVERVIEW_NAV_HINT, classes="ov-nav"))
            return

        self.mount(Static(_overview_connection_text(state), classes="ov-card"))
        self.mount(Static(_overview_transfer_text(state), classes="ov-card"))
        self.mount(Static(_overview_activity_text(state), classes="ov-card"))
        self.mount(Static(_overview_completion_text(state), classes="ov-card"))
        self.mount(
            Static(
                _overview_attention_text(state),
                classes="ov-card ov-attention",
            )
        )
        self.mount(
            Static(
                _overview_alerts_text(state),
                classes="ov-card ov-attention",
            )
        )
        self.mount(Static(_OVERVIEW_NAV_HINT, classes="ov-nav"))


def _overview_connection_text(state: TuiState) -> str:
    status = state.status
    assert status is not None
    label = _CONNECTION_LABELS[state.connection]
    lines = [f"[bold]Connection[/bold]: {label}"]
    if state.last_successful_refresh is not None:
        lines.append(
            f"  last successful refresh: "
            f"{_format_local_time(state.last_successful_refresh)}"
        )
    else:
        lines.append("  last successful refresh: never")
    if state.stale:
        lines.append(
            "  [bold yellow]STALE[/bold yellow] -- showing last-good data"
        )
    if status.qbittorrent_version:
        lines.append(f"  qBittorrent {status.qbittorrent_version}")
    if status.api_version:
        lines.append(f"  Web API {status.api_version}")
    return "\n".join(lines)


def _overview_transfer_text(state: TuiState) -> str:
    status = state.status
    assert status is not None
    down = _format_byte_rate(status.rates.download_bytes_per_second)
    up = _format_byte_rate(status.rates.upload_bytes_per_second)
    return f"[bold]Transfer[/bold]\n  ↓ {down}   ↑ {up}"


def _overview_activity_text(state: TuiState) -> str:
    status = state.status
    assert status is not None
    counts = status.counts
    return (
        f"[bold]Activity[/bold] · {counts.total} total\n"
        f"  {counts.downloading} downloading · {counts.seeding} seeding\n"
        f"  {state.stopped_count} stopped · {counts.checking} checking"
    )


def _overview_completion_text(state: TuiState) -> str:
    status = state.status
    assert status is not None
    counts = status.counts
    incomplete = max(counts.total - counts.completed, 0)
    return (
        "[bold]Completion[/bold]\n"
        f"  {counts.completed} completed · {incomplete} incomplete"
    )


def _overview_attention_text(state: TuiState) -> str:
    status = state.status
    assert status is not None
    counts = status.counts
    return (
        "[bold]Attention[/bold]\n"
        f"  {counts.stalled} stalled · {counts.errored} errored · "
        f"{counts.unknown} unknown"
    )


def _overview_alerts_text(state: TuiState) -> str:
    status = state.status
    assert status is not None
    style = _HEALTH_STYLES[status.health]
    alerts = status.alerts
    header = (
        f"[bold]Health[/bold]\n"
        f"  [{style}]{status.health.value.title()}[/{style}] · "
        f"{len(alerts)} finding(s)"
    )
    lines = [header]
    lines.extend(f"  {alert.message}" for alert in alerts)
    return "\n".join(lines)


def _format_local_time(moment: datetime, *, tz: tzinfo | None = None) -> str:
    """Format a timestamp in the local system timezone.

    Refresh times default to local time, not UTC, with the timezone
    label always shown so a UTC-configured host is not silently
    ambiguous. `moment` is always timezone-aware (`datetime.now(UTC)`
    upstream, see `app.status`), so `.astimezone()` with no `tz`
    converts it to the system's local timezone -- exactly what
    `datetime.astimezone(tz=None)` already means. `tz` exists purely
    for deterministic tests: passing a fixed `tzinfo` (e.g. a
    `zoneinfo.ZoneInfo` or a fixed-offset `timezone`) verifies the
    conversion/label logic without depending on the CI machine's own
    system timezone (which may legitimately be UTC, making a bug and
    a UTC host indistinguishable by output alone).
    """
    local = moment.astimezone(tz)
    tz_label = local.tzname() or "local"
    return f"{local:%H:%M:%S} {tz_label}"


def _shorten_hash(full_hash: str) -> str:
    """Shorten a 40-character infohash for display, e.g. '8ac34f89…f95704b8'.

    Display-only: `c` (`action_copy_hash`) always copies the untouched
    `full_hash`, never this shortened form.
    """
    if len(full_hash) <= 20:
        return full_hash
    return f"{full_hash[:8]}…{full_hash[-8:]}"


class FiltersPanel(Vertical):
    """The shared `TorrentFilter` vocabulary, applied entirely in memory.

    Only ever mounted inside `FiltersScreen` (a modal, at every
    terminal width). No qBittorrent API call is ever triggered by a
    change here. Completion and Activity are each an exclusive
    `RadioSet` (Any/Completed/Incomplete, Any/Active/Inactive) so a
    contradictory pair (Completed *and* Incomplete) is structurally
    impossible through the UI -- `build_torrent_filter`'s own
    completed+incomplete/active+inactive rejection remains as defense
    in depth, never actually reachable from here.
    """

    def compose(self) -> ComposeResult:
        with Horizontal(classes="f-columns"):
            with Vertical(classes="f-col"):
                yield Static("[bold]Category[/bold]")
                yield Input(placeholder="films, tv", classes="f-category")
                yield Static("[bold]State[/bold]")
                yield Input(placeholder="stalled, errored", classes="f-state")
                yield Static("[bold]Attention[/bold]")
                yield Checkbox("Stalled", classes="f-stalled")
                yield Checkbox("Errored", classes="f-errored")
            with Vertical(classes="f-col"):
                yield Static("[bold]Completion[/bold]")
                yield RadioSet(
                    RadioButton("Any"),
                    RadioButton("Completed"),
                    RadioButton("Incomplete"),
                    classes="f-completion",
                )
                yield Static("[bold]Activity[/bold]")
                yield RadioSet(
                    RadioButton("Any"),
                    RadioButton("Active"),
                    RadioButton("Inactive"),
                    classes="f-activity",
                )
        yield Static("", classes="f-error")
        with Horizontal(classes="f-actions"):
            yield Button("Apply", id="filters-apply", variant="primary")
            yield Button("Clear", id="filters-clear")
            yield Button("Cancel", id="filters-cancel")

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

        completion_index = self.query_one(
            ".f-completion", RadioSet
        ).pressed_index
        activity_index = self.query_one(".f-activity", RadioSet).pressed_index

        return build_torrent_filter(
            categories=categories,
            states=states,
            completed=completion_index == 1,
            incomplete=completion_index == 2,
            active=activity_index == 1,
            inactive=activity_index == 2,
            stalled=self.query_one(".f-stalled", Checkbox).value,
            errored=self.query_one(".f-errored", Checkbox).value,
        )

    def sync_from(self, filters: TorrentFilter) -> None:
        """Reflect an already-applied `TorrentFilter` in this panel."""
        self.query_one(".f-category", Input).value = ", ".join(
            filters.categories
        )
        self.query_one(".f-state", Input).value = ", ".join(filters.states)
        self._select_radio(
            ".f-completion",
            (
                1
                if filters.completed is True
                else (2 if filters.completed is False else 0)
            ),
        )
        self._select_radio(
            ".f-activity",
            (
                1
                if filters.active is True
                else (2 if filters.active is False else 0)
            ),
        )
        self.query_one(".f-stalled", Checkbox).value = bool(filters.stalled)
        self.query_one(".f-errored", Checkbox).value = bool(filters.errored)

    def _select_radio(self, selector: str, index: int) -> None:
        radio_set = self.query_one(selector, RadioSet)
        buttons = list(radio_set.query(RadioButton))
        buttons[index].value = True

    def show_error(self, message: str) -> None:
        self.query_one(".f-error", Static).update(message)


class DetailsPanel(VerticalScroll):
    """Safe details for the focused torrent, grouped into Identity,
    Transfer, and Trackers sections.

    Only ever renders `SelectedTorrent` fields (live from the periodic
    snapshot) and `get_safe_tracker_details`-shaped structural tracker
    fields -- never a raw announce URL, path, query value, userinfo, or
    unsanitized message.
    """

    def render_state(self, state: TuiState) -> None:
        """Render the currently focused torrent's safe details, or an
        explicit empty state when nothing is focused."""
        self.remove_children()
        torrent = state.focused_torrent()

        if torrent is None:
            self.mount(Static("No torrent focused."))
            return

        identity_lines = [
            f"[bold]{torrent.name}[/bold]",
            f"Hash: {_shorten_hash(torrent.hash)}  [dim](c to copy)[/dim]",
            f"Category: {torrent.category}",
        ]
        self.mount(Static("\n".join(identity_lines), classes="d-section"))

        transfer_lines = [
            "[bold]Transfer[/bold]",
            f"State: {torrent.state}",
            f"Progress: {torrent.progress * 100:.1f}%   "
            f"Ratio: {torrent.ratio:.2f}",
            f"Down: {_format_byte_rate(torrent.download_rate)}   "
            f"Up: {_format_byte_rate(torrent.upload_rate)}",
        ]
        self.mount(Static("\n".join(transfer_lines), classes="d-section"))

        tracker_details = state.focused_tracker_details
        if tracker_details is None:
            self.mount(
                Static(
                    "[bold]Trackers[/bold]\n  loading...", classes="d-section"
                )
            )
        else:
            fetched_at = state.focused_details_fetched_at
            freshness = (
                f"fetched {_format_local_time(fetched_at)}"
                if fetched_at is not None
                else ""
            )
            lines = [f"[bold]Trackers[/bold] ({freshness})"]
            if not tracker_details:
                lines.append("  (none)")
            else:
                lines.extend(
                    f"  {_format_endpoint(endpoint)}"
                    for endpoint in tracker_details
                )
            self.mount(Static("\n".join(lines), classes="d-section"))


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
        width: 64;
        max-height: 90%;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help-dialog"):
            yield Static(_HELP_TEXT)

    def action_dismiss(self, result: None = None) -> None:  # type: ignore[override]
        self.dismiss()


class FiltersScreen(ModalScreen[None]):
    """The sole access path to filters, at every terminal width.

    Filters apply live, locally, as the user edits them (zero
    qBittorrent calls -- see `QbitOpsTuiApp._apply_filters_from_panel`),
    but Apply/Cancel/Clear are three distinct, deterministic
    interactions, each reachable both by binding and by a visible
    button (`FiltersPanel`'s `Apply`/`Clear`/`Cancel`):

    * Apply (`Enter`, or the Apply button) -- already in effect; closes.
    * Cancel (`Escape`, or the Cancel button) -- revert to the filter
      that was active when this screen opened, then close.
    * Clear (`ctrl+r`, or the Clear button) -- reset to no filter at
      all; the modal stays open so the operator can keep adjusting.

    `enter`/`escape` are deliberately *not* bound here: both are already
    `priority=True` bindings on `QbitOpsTuiApp`, and Textual resolves an
    App's own priority bindings before a Screen's -- even the Screen on
    top of the stack -- so a same-key Screen-level binding here would
    simply never fire (verified empirically). `action_activate`/
    `action_dismiss_overlay` on the App special-case `FiltersScreen`
    instead -- see their docstrings. The visible buttons exist
    specifically so Apply/Cancel/Clear are not *only* discoverable via
    a keyboard shortcut.
    """

    BINDINGS = [
        Binding("ctrl+r", "clear", "Clear"),
        # `up`/`down` move focus between fields/buttons, same as
        # Tab/Shift+Tab -- namespaced to `app.*` so they resolve through
        # `QbitOpsTuiApp.check_action`, which already always allows
        # `focus_next`/`focus_previous` regardless of which modal is
        # open (see its docstring) -- the same mechanism that already
        # makes Tab work in every modal.
        Binding("up", "app.focus_previous", "Up", show=False),
        Binding("down", "app.focus_next", "Down", show=False),
    ]

    CSS = """
    FiltersScreen {
        align: center middle;
    }
    #filters-dialog {
        width: 64;
        max-height: 90%;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }
    .f-columns {
        height: auto;
    }
    .f-col {
        width: 1fr;
        height: auto;
        padding: 0 1;
    }
    .f-actions {
        height: auto;
        margin-top: 1;
    }
    .f-actions Button {
        margin-right: 1;
    }
    /* Selected (on) vs focused vs both must be distinguishable without
       relying on color alone: RadioButton's own "( )"/"(x)" glyph
       already encodes selection non-color; `:focus` additionally gets
       an explicit border and bold text so keyboard focus position is
       visible even on a color-blind or monochrome terminal. */
    RadioSet {
        border: round $panel;
        height: auto;
    }
    RadioSet:focus-within {
        border: round $accent;
    }
    RadioButton:focus {
        text-style: bold underline;
        border: tall $accent;
    }
    """

    def __init__(self, current_filters: TorrentFilter) -> None:
        super().__init__()
        self.original_filters = current_filters
        """The filter in effect when this screen opened -- Cancel
        (handled by `QbitOpsTuiApp.action_dismiss_overlay`) reverts to
        exactly this value."""

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="filters-dialog"):
            yield Static("[bold]Filters[/bold]")
            yield FiltersPanel()
            yield Static("[dim]Enter/Apply · Esc/Cancel · Ctrl+R/Clear[/dim]")

    def on_mount(self) -> None:
        self.query_one(FiltersPanel).sync_from(self.original_filters)
        # Textual's default `AUTO_FOCUS = "*"` auto-focuses the *first*
        # focusable widget on the screen in DOM order -- which is
        # `#filters-dialog` itself (a `VerticalScroll`, and therefore
        # focusable) since it comes before any of its children,
        # including the category `Input`. Left alone, every keystroke
        # right after opening Filters goes to the scroll container
        # (which only understands up/down/page keys) instead of any
        # actual field -- verified empirically; this is what made
        # Filters look entirely unresponsive to the keyboard. Focus the
        # category `Input` explicitly instead.
        self.query_one(".f-category", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        app = self.app
        assert isinstance(app, QbitOpsTuiApp)
        if event.button.id == "filters-apply":
            app.action_activate()
        elif event.button.id == "filters-cancel":
            app.action_dismiss_overlay()
        elif event.button.id == "filters-clear":
            self.action_clear()

    def action_clear(self) -> None:
        app = self.app
        assert isinstance(app, QbitOpsTuiApp)
        empty = TorrentFilter()
        app.controller.set_filters(empty)
        self.query_one(FiltersPanel).sync_from(empty)
        self.query_one(FiltersPanel).show_error("")
        app._render_filter_summary()
        app._render_table()
        app._render_details_panels()
        # Clearing widens visibility, so this is unlikely to hide
        # anything -- but a search term may still narrow it back down,
        # so reconcile for correctness/consistency with Apply/Cancel.
        app._reconcile_selection_and_notify()


class DetailsScreen(ModalScreen[None]):
    """A modal Details panel -- the narrow-layout's access path to the
    focused torrent's details, opened by `enter`.

    Explicitly binds `c` (copy hash), delegating straight to
    `QbitOpsTuiApp.action_copy_hash` -- Textual restricts a *non*-
    priority key's binding lookup to `Screen._modal_binding_chain` while
    a `ModalScreen` is on top of the stack, which does **not** include
    the App's own `BINDINGS` (only `priority=True` ones bypass this, via
    a separate lookup -- see `FiltersScreen`'s docstring for that other
    case). A plain `Binding("c", "copy_hash", ...)` left only on the App
    would silently never fire while this screen is open -- verified
    empirically. `q`/`r`/`e` are deliberately not re-bound here: only
    Copy hash is a documented Details-view action (see docs/COMMANDS.md).
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close", priority=True),
        Binding("c", "copy_hash", "Copy hash"),
    ]

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
            yield Static("[dim]Esc to close · c to copy hash[/dim]")

    def on_mount(self) -> None:
        assert isinstance(self.app, QbitOpsTuiApp)
        self.query_one(DetailsPanel).render_state(self.app.controller.state)

    def action_dismiss(self, result: None = None) -> None:  # type: ignore[override]
        self.dismiss()

    def action_copy_hash(self) -> None:
        assert isinstance(self.app, QbitOpsTuiApp)
        self.app.action_copy_hash()


class ExplainScreen(ModalScreen[None]):
    """An evidence-based explanation of the focused torrent's state.

    `report` starts `None` while tracker data is still being fetched
    (see `QbitOpsTuiApp.action_explain`) -- `refresh_content()` shows a
    concise loading line in that case, and the App calls it again once
    (and only if) a matching, still-current result arrives (see
    `QbitOpsTuiApp._on_detail_worker_state_changed`'s explain-race
    handling). Purely a renderer: it never fetches anything itself and
    never calls back into `TuiController`.
    """

    BINDINGS: list[Binding] = []
    """Deliberately empty: `escape` is already a `priority=True` App
    binding (`action_dismiss_overlay`), which always wins over any
    same-key Screen binding -- see `FiltersScreen`'s docstring for the
    verified mechanism. A Screen-level `escape` binding here would
    simply never fire."""

    CSS = """
    ExplainScreen {
        align: center middle;
    }
    #explain-dialog {
        width: 80%;
        max-width: 96;
        height: 85%;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    def __init__(
        self, torrent_name: str, report: ExplanationReport | None
    ) -> None:
        super().__init__()
        self.torrent_name = torrent_name
        self.report = report

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="explain-dialog"):
            yield Static(id="explain-content")
            yield Static("[dim]Esc to close[/dim]")

    def on_mount(self) -> None:
        self.refresh_content()

    def refresh_content(self) -> None:
        assert isinstance(self.app, QbitOpsTuiApp)
        state = self.app.controller.state
        content = self.query_one("#explain-content", Static)
        content.update(
            _format_explain_text(self.torrent_name, self.report, state)
        )


class ActionsScreen(ModalScreen[None]):
    """Choose a LOW-risk bulk action for the frozen selection snapshot.

    Only ever opened with a non-empty selection (see
    `QbitOpsTuiApp.action_open_actions`). No mutation happens here --
    picking an action just builds a frozen `BulkTorrentActionPlan`
    (zero API calls) and opens `PreviewScreen`; Cancel/Escape close
    without any side effect at all.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel", priority=True),
        # Up/Down move between the buttons, same as Tab/Shift+Tab --
        # see `FiltersScreen`'s identical bindings for why this
        # resolves correctly through `QbitOpsTuiApp.check_action`.
        Binding("up", "app.focus_previous", "Up", show=False),
        Binding("down", "app.focus_next", "Down", show=False),
    ]

    CSS = """
    ActionsScreen {
        align: center middle;
    }
    #actions-dialog {
        width: 48;
        max-height: 90%;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }
    #actions-dialog Button {
        width: 100%;
        margin-bottom: 1;
    }
    .actions-names {
        color: $text-muted;
        margin-bottom: 1;
    }
    """

    _ACTION_BY_BUTTON_ID: dict[str, TorrentBulkAction] = {
        "actions-pause": "pause",
        "actions-resume": "resume",
        "actions-reannounce": "reannounce",
    }

    def __init__(
        self, selected_hashes: tuple[str, ...], names: tuple[str, ...]
    ) -> None:
        super().__init__()
        self.selected_hashes = selected_hashes
        self._names = names

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="actions-dialog"):
            yield Static(
                f"[bold]Actions[/bold] · {len(self.selected_hashes)} selected"
            )
            preview = ", ".join(_truncate(name, 24) for name in self._names[:3])
            extra = len(self._names) - 3
            if extra > 0:
                preview += f" (+{extra} more)"
            yield Static(preview, classes="actions-names")
            yield Button("Pause", id="actions-pause")
            yield Button("Resume", id="actions-resume")
            yield Button("Reannounce", id="actions-reannounce")
            yield Button("Cancel", id="actions-cancel")

    def on_mount(self) -> None:
        self.query_one("#actions-pause", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        app = self.app
        assert isinstance(app, QbitOpsTuiApp)
        button_id = event.button.id
        if button_id == "actions-cancel" or button_id is None:
            self.dismiss()
            return
        action = self._ACTION_BY_BUTTON_ID.get(button_id)
        if action is None:
            return
        hashes = self.selected_hashes
        self.dismiss()
        app._open_preview_for_action(action, hashes)

    def action_dismiss(self, result: None = None) -> None:  # type: ignore[override]
        self.dismiss()


class PreviewScreen(ModalScreen[None]):
    """Preview of a frozen `BulkTorrentActionPlan` before Apply.

    Owns and displays exactly the plan passed at construction --
    `plan`/`snapshot_at` never change after `__init__`. The live
    selection, filters, search, and focus may keep changing in the
    background while this modal is open; none of that ever mutates
    this screen's plan (see docs/DECISIONS.md, "frozen plan"
    invariant).
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel", priority=True),
        # Up/Down move between the Cancel/Apply buttons, same as
        # Tab/Shift+Tab -- see `FiltersScreen`'s identical bindings.
        Binding("up", "app.focus_previous", "Up", show=False),
        Binding("down", "app.focus_next", "Down", show=False),
    ]

    CSS = """
    PreviewScreen {
        align: center middle;
    }
    #preview-dialog {
        width: 76%;
        max-width: 90;
        max-height: 90%;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }
    #preview-actions {
        height: auto;
        margin-top: 1;
    }
    #preview-actions Button {
        margin-right: 1;
    }
    """

    def __init__(
        self,
        plan: BulkTorrentActionPlan,
        snapshot_at: datetime | None,
    ) -> None:
        super().__init__()
        self.plan = plan
        self.snapshot_at = snapshot_at
        self.applying = False

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="preview-dialog"):
            yield Static(id="preview-content")
            with Horizontal(id="preview-actions"):
                yield Button("Cancel", id="preview-cancel")
                yield Button("Apply", id="preview-apply", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#preview-content", Static).update(
            _format_preview_text(self.plan, self.snapshot_at)
        )
        self.query_one("#preview-apply", Button).focus()

    def set_applying(self, applying: bool) -> None:
        """Freeze the modal while a mutation is actually in flight --
        disables both buttons (Cancel too: see
        `QbitOpsTuiApp.action_dismiss_overlay`) and relabels Apply so
        double-pressing it (or pressing Enter twice) cannot dispatch a
        second mutation."""
        self.applying = applying
        apply_button = self.query_one("#preview-apply", Button)
        apply_button.disabled = applying
        apply_button.label = "Applying..." if applying else "Apply"
        self.query_one("#preview-cancel", Button).disabled = applying

    def on_button_pressed(self, event: Button.Pressed) -> None:
        app = self.app
        assert isinstance(app, QbitOpsTuiApp)
        if event.button.id == "preview-cancel":
            if not self.applying:
                self.dismiss()
        elif event.button.id == "preview-apply":
            app.action_apply_plan()

    def action_dismiss(self, result: None = None) -> None:  # type: ignore[override]
        if self.applying:
            return
        self.dismiss()


class ResultScreen(ModalScreen[None]):
    """A truthful, dismissible report of what an Apply actually did.

    Never inferred from "Apply was pressed" -- `status`/
    `unavailable_message`/`internal_error` are computed by the App from
    the mutation worker's real outcome (see
    `QbitOpsTuiApp._on_mutation_worker_state_changed`/
    `_show_mutation_failure`) before this screen is even constructed.
    Dismissing (`Esc`, or the Close button) never re-applies anything;
    it only triggers `QbitOpsTuiApp._on_result_dismissed`'s documented
    selection-clearing policy.

    Deliberately no Screen-level `escape` binding: `escape` is already
    a `priority=True` App binding
    (`QbitOpsTuiApp.action_dismiss_overlay`), which always wins over a
    same-key `Screen` binding regardless -- a `Binding("escape",
    "dismiss", ...)` here would simply never fire (verified
    empirically; see docs/MEMORY.md, the same mechanism documented for
    `FiltersScreen`/`ExplainScreen`). `action_dismiss_overlay` special-
    cases `ResultScreen` instead. The Close button below reuses that
    same central path rather than duplicating the dismissal policy.
    """

    BINDINGS: list[Binding] = []

    CSS = """
    ResultScreen {
        align: center middle;
    }
    #result-dialog {
        width: 64;
        max-height: 90%;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }
    #result-close {
        margin-top: 1;
    }
    """

    def __init__(
        self,
        plan: BulkTorrentActionPlan,
        status: MutationStatus | None,
        *,
        applied: bool,
        unavailable_message: str | None = None,
        internal_error: Exception | None = None,
    ) -> None:
        super().__init__()
        self.plan = plan
        self.status = status
        self.applied = applied
        self.unavailable_message = unavailable_message
        self.internal_error = internal_error

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="result-dialog"):
            yield Static(id="result-content")
            yield Button("Close", id="result-close")

    def on_mount(self) -> None:
        self.query_one("#result-content", Static).update(
            _format_result_text(
                self.plan,
                self.status,
                unavailable_message=self.unavailable_message,
                internal_error=self.internal_error,
            )
        )
        self.query_one("#result-close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        app = self.app
        assert isinstance(app, QbitOpsTuiApp)
        if event.button.id == "result-close":
            app.action_dismiss_overlay()


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

    def get_default_screen(self) -> Screen[None]:
        return MainScreen(id="_default")

    def compose(self) -> ComposeResult:
        yield WorkspaceTabs(id="workspace-tabs")
        yield ConnectionBanner(id="banner")
        yield OverviewPanel(id="overview-workspace")
        with Vertical(id="torrents-workspace"):
            yield FilterSummary(id="filter-summary")
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
        self._render_workspace_tabs()
        self._render_banner()
        self._render_overview()
        self._render_filter_summary()
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
        self.push_screen(
            PreviewScreen(plan, self.controller.state.last_successful_refresh)
        )
        self.refresh_bindings()

    def action_apply_plan(self) -> None:
        """Apply the plan owned by the currently open `PreviewScreen`.

        A no-op unless `PreviewScreen` is actually on top (Apply is
        only ever reachable by pressing its button, but this guard
        keeps the method safe to call directly too) and unless no
        mutation is already in flight -- double-pressing Apply (or any
        other path that might call this twice) dispatches at most one
        `MUTATION_WORKER_GROUP` worker; a second call while the first is
        still running is silently ignored, never queued.
        """
        if not isinstance(self.screen, PreviewScreen):
            return
        if (
            self._mutation_worker is not None
            and not self._mutation_worker.is_finished
        ):
            return

        preview_screen = self.screen
        preview_screen.set_applying(True)
        self._preview_screen = preview_screen
        self._active_mutation_plan = preview_screen.plan
        plan = preview_screen.plan
        self._mutation_worker = self.run_worker(
            lambda: self._mutation_worker_body(plan),
            group=MUTATION_WORKER_GROUP,
            thread=True,
            exit_on_error=False,
        )

    def _mutation_worker_body(
        self, plan: BulkTorrentActionPlan
    ) -> tuple[bool, Exception | None]:
        """Run on a background thread: blocking I/O only, never state
        mutation, and never raises -- see `_refresh_worker_body` for
        why the outcome travels back as a plain tagged tuple.
        """
        try:
            self.controller.apply_bulk_plan(plan)
            return (True, None)
        except Exception as error:
            return (False, error)

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
        if (
            preview_screen is None
            or plan is None
            or preview_screen not in self.screen_stack
        ):
            # The Preview modal was already closed (or replaced) --
            # discard this result rather than updating unrelated UI.
            return

        assert event.worker.result is not None
        _succeeded, error = event.worker.result

        self.pop_screen()
        self.refresh_bindings()

        if error is not None:
            self._show_mutation_failure(plan, error)
            return

        self._start_periodic_refresh()  # one immediate refresh, per plan
        # `classify_plan_status` truthfully distinguishes an empty plan
        # (NO_MATCH/NO_CHANGES -- `apply_bulk_torrent_action` itself is a
        # no-op for these, never an API call) from a plan that had real
        # changes, which -- having reached here without an exception --
        # is reported as APPLIED. Never inferred merely from "Apply was
        # pressed".
        status = self.controller.classify_plan_status(plan)
        if status is MutationStatus.PREVIEW:
            status = MutationStatus.APPLIED
        self._push_result_screen(plan, status, applied=bool(plan.changes))

    def _show_mutation_failure(
        self, plan: BulkTorrentActionPlan, error: Exception
    ) -> None:
        """Report a failed Apply truthfully -- never claim success.

        `apply_bulk_torrent_action` wraps every transport/API failure in
        a `RuntimeError` (for the CLI's own error rendering), so the
        original exception (needed to tell a recoverable connection
        failure apart from a genuine internal defect) is recovered via
        `__cause__` -- `raise RuntimeError(...) from error` in
        `app.torrents.apply_bulk_torrent_action` is what sets it.
        """
        cause = error.__cause__
        original: Exception = cause if isinstance(cause, Exception) else error
        failure = classify_recoverable_qbit_failure(original)
        if failure is None:
            self._push_result_screen(
                plan, None, applied=False, internal_error=original
            )
            return
        self._push_result_screen(
            plan, None, applied=False, unavailable=failure.message
        )

    def _push_result_screen(
        self,
        plan: BulkTorrentActionPlan,
        status: MutationStatus | None,
        *,
        applied: bool,
        unavailable: str | None = None,
        internal_error: Exception | None = None,
    ) -> None:
        self.push_screen(
            ResultScreen(
                plan,
                status,
                applied=applied,
                unavailable_message=unavailable,
                internal_error=internal_error,
            )
        )
        self.refresh_bindings()

    def _on_result_dismissed(
        self, plan: BulkTorrentActionPlan, applied: bool
    ) -> None:
        """Apply TUI 2's documented post-dismissal selection policy.

        Only ever clears the hashes the plan actually *changed*
        (`plan.changes`) and only when the mutation genuinely applied --
        a skipped torrent (already satisfied) or one belonging to a
        failed/cancelled attempt keeps its selection untouched, so the
        operator can reconsider it rather than losing track of it.
        """
        if not applied:
            return
        changed_hashes = [change.hash for change in plan.changes]
        self.controller.clear_selection_for(changed_hashes)
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
                plan, applied = screen.plan, screen.applied
                self.pop_screen()
                self.refresh_bindings()
                self._on_result_dismissed(plan, applied)
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


class ConnectionBanner(Static):
    """A dismissible-looking, non-blocking banner shown while reconnecting.

    Never replaces the workspace content underneath it -- stale data
    stays visible (see docs/TUI_ARCHITECTURE_REVIEW.md §5/§6).
    """


class FilterSummary(Static):
    """A concise, always-visible line describing the active filter/search.

    e.g. "146 shown / 1,105 · stalled" or
    "24 shown / 1,105 · category: films · stalled · search: ubuntu" --
    see docs/COMMANDS.md ("TUI"). Purely presentational: derived from
    `TuiState`, never fetched. Only shown in the Torrents workspace.
    """


_HELP_TEXT = """[bold]Global[/bold]
1, g       Overview
2, t       Torrents
?          Help
esc        Close modal / clear selection / back
q          Quit

[bold]Torrents workspace[/bold]
j/k, ↑/↓   Navigate (moves focus)
/          Search (name or hash)
f          Filters
enter      Details (focused torrent)
c          Copy hash (focused torrent)
e          Explain (focused torrent)
r          Refresh tracker details (focused torrent)
space      Toggle selection (focused torrent)
ctrl+a     Select all visible torrents
ctrl+d     Deselect all torrents
a          Actions for selected torrents

[bold]In any modal (Filters, Actions, Preview, Result)[/bold]
tab, ↑/↓   Move between fields/buttons
enter      Apply / press the focused button

[dim]Focused = the highlighted row (one at a time).
Selected = marked with ✔ for bulk actions (any number).
Visible = shown after the current filter/search.
Copy/Explain/Refresh always act on the focused torrent only,
never the selection.[/dim]
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


# Column display order -- "a user-oriented column order", per the
# visual-polish phase: Name first (always shown, gets the remaining
# width), then operational columns, Category last (shown only once
# width permits). Row *identity* (the DataTable row `key=`) is always
# the full torrent hash regardless of which columns are visible.
_ALL_COLUMNS: tuple[str, ...] = (
    "Name",
    "State",
    "Progress",
    "Down",
    "Up",
    "Ratio",
    "Category",
)

# Compact, predictable widths for operational columns -- `Name` is
# deliberately left unset so it absorbs the remaining width instead of
# being squeezed to its content's natural size.
_COLUMN_WIDTHS: dict[str, int] = {
    "Sel": 4,
    "State": 12,
    "Progress": 7,
    "Down": 10,
    "Up": 10,
    "Ratio": 6,
    "Category": 14,
}

# A heavier glyph (U+2714, "heavy check mark") than a plain "✓", plus
# bold+color, so a selected row's marker reads clearly at a glance
# instead of blending into the row -- requested after dogfooding found
# the previous plain "✓" too easy to miss.
_SELECTED_MARK = "✔"
_UNSELECTED_MARK = " "


def _selection_cell(selected: bool) -> Text:
    if not selected:
        return Text(_UNSELECTED_MARK)
    return Text(_SELECTED_MARK, style="bold green")


def _columns_for_width(width: int) -> tuple[str, ...]:
    """Pick which table columns to show, in order, for a given App width.

    `Sel` (the selection marker, TUI 2) is always shown at every width
    -- multi-selection must never lose its only visual indicator just
    because the terminal is narrow. Progressive disclosure otherwise:
    Name/State/Progress are always shown; Down/Up appear at normal
    width; Ratio/Category only once the terminal is comfortably wide.
    Details, Filters, Search, Copy, and Explain never depend on which
    columns happen to be visible.
    """
    if width < NARROW_WIDTH_THRESHOLD:
        base = ("Name", "State", "Progress")
    elif width < WIDE_WIDTH_THRESHOLD:
        base = ("Name", "State", "Progress", "Down", "Up")
    else:
        base = _ALL_COLUMNS
    return ("Sel", *base)


def _torrent_row_values(
    torrent: SelectedTorrent, selected: bool
) -> dict[str, Any]:
    return {
        "Sel": _selection_cell(selected),
        "Name": torrent.name,
        "State": torrent.state,
        "Progress": f"{torrent.progress * 100:.0f}%",
        "Down": _format_byte_rate(torrent.download_rate),
        "Up": _format_byte_rate(torrent.upload_rate),
        "Ratio": f"{torrent.ratio:.2f}",
        "Category": torrent.category,
    }


def _format_endpoint(endpoint: dict[str, Any]) -> str:
    """Render one safe, structural tracker endpoint as one aligned line.

    Never a raw URL, path, query value, userinfo, or passkey. Shows
    identity and health/status as separate, clearly labeled columns,
    and a sanitized message only when one is present -- never a bare
    status word with nothing identifying which tracker it belongs to.
    Deliberately does *not* also append a synthetic "disabled" suffix
    from `endpoint["enabled"]`: `health` is already `"disabled"`
    whenever `enabled` is `False` for a classifiable status
    (`app.trackers`'s single status->health mapping), so doing both
    previously produced a duplicated "disabled disabled".
    """
    identity = str(endpoint["tracker"])
    health = str(endpoint["health"])
    line = f"{identity:<10} {health}"
    message = endpoint.get("message")
    if message and health != "disabled":
        line += f"  -- {message}"
    return line


def _format_explain_text(
    torrent_name: str,
    report: ExplanationReport | None,
    state: TuiState,
) -> str:
    """Render an `ExplanationReport` as one scrollable, structured block.

    Display-only: never invents a recommendation, a confidence score,
    or hidden reasoning beyond what `report` itself carries. `report`
    being `None` means tracker data is still being fetched in the
    background -- shown as a concise loading line, not a blank modal.
    """
    freshness_lines = []
    if state.last_successful_refresh is not None:
        freshness_lines.append(
            f"Torrent snapshot refreshed "
            f"{_format_local_time(state.last_successful_refresh)}"
        )
    if state.focused_details_fetched_at is not None:
        freshness_lines.append(
            f"Tracker details fetched "
            f"{_format_local_time(state.focused_details_fetched_at)}"
        )
    if state.stale:
        freshness_lines.append(
            "[bold yellow]STALE[/bold yellow] -- qBittorrent is currently "
            "unreachable; this explanation uses last-known data."
        )

    header = [f"[bold]Explain[/bold] · {_truncate(torrent_name, 60)}"]
    header.extend(freshness_lines)

    if report is None:
        header.append("")
        header.append("Fetching tracker data...")
        return "\n".join(header)

    style = _SEVERITY_STYLES[report.overall_severity]
    header.append(f"[{style}]{report.overall_severity.value.title()}[/{style}]")

    # A single-finding report's summary is, by construction
    # (`app.explain.build_torrent_explanation`), always the finding's
    # own `explanation` -- printing both would show the same sentence
    # twice. Only show the summary here when it says something the
    # first finding block does not already say.
    if not report.findings or report.summary != report.findings[0].explanation:
        header.append("")
        header.append(report.summary)

    blocks = ["\n".join(header)]

    for finding in report.findings:
        blocks.append(_format_finding(finding))

    return "\n\n".join(blocks)


def _truncate(text: str, limit: int) -> str:
    """Truncate a display string safely, e.g. for a long torrent title."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


_MAX_PREVIEW_ROWS = 50
_MAX_SKIPPED_ROWS = 20


def _format_preview_text(
    plan: BulkTorrentActionPlan, snapshot_at: datetime | None
) -> str:
    """Render a frozen `BulkTorrentActionPlan` for the Preview modal.

    Display-only: reads `plan`'s already-computed counts/changes/
    skips, never recomputes or rescans anything. `snapshot_at` is the
    periodic refresh the plan's torrent data came from -- shown so an
    operator can judge freshness, never "now" (no clock is read here).
    """
    action_label = plan.action.title()
    lines = [
        f"[bold]{action_label} · Preview[/bold]",
        "",
        f"Selected             {plan.matched}",
        f"Will {plan.action:<10}     {len(plan.changes)}",
        f"Skipped              {len(plan.skipped)}",
    ]
    if snapshot_at is not None:
        lines.append(f"Snapshot             {_format_local_time(snapshot_at)}")
    lines.append("")

    lines.append("[bold]Affected torrents[/bold]")
    if not plan.changes:
        lines.append("  (none)")
    else:
        for change in plan.changes[:_MAX_PREVIEW_ROWS]:
            lines.append(
                f"  {_SELECTED_MARK} {_truncate(change.name, 40):<40} "
                f"{_shorten_hash(change.hash)}"
            )
        remaining = len(plan.changes) - _MAX_PREVIEW_ROWS
        if remaining > 0:
            lines.append(f"  … and {remaining} more")

    if plan.skipped:
        lines.append("")
        lines.append("[bold]Skipped[/bold]")
        for skip in plan.skipped[:_MAX_SKIPPED_ROWS]:
            lines.append(f"  - {_truncate(skip.name, 40):<40} ({skip.reason})")
        remaining_skips = len(plan.skipped) - _MAX_SKIPPED_ROWS
        if remaining_skips > 0:
            lines.append(f"  … and {remaining_skips} more")

    return "\n".join(lines)


def _format_result_text(
    plan: BulkTorrentActionPlan,
    status: MutationStatus | None,
    *,
    unavailable_message: str | None = None,
    internal_error: Exception | None = None,
) -> str:
    """Render the truthful outcome of an Apply attempt.

    `status is None` means Apply itself never completed (a recoverable
    connection failure or a genuine internal defect, distinguished by
    which of `unavailable_message`/`internal_error` is set) -- never
    rendered as if it were `CANCELLED` or any other terminal status
    that would misrepresent what actually happened.
    """
    if internal_error is not None:
        return (
            "[bold red]Internal error[/bold red]\n\n"
            f"{type(internal_error).__name__}: {internal_error}\n\n"
            "No change was confirmed applied. This is a qbit-ops defect, "
            "not a remote failure -- it is not retried automatically."
        )

    if unavailable_message is not None:
        return (
            "[bold yellow]Unavailable[/bold yellow]\n\n"
            f"{unavailable_message}\n\n"
            f"The plan is unchanged ({len(plan.changes)} torrent(s) queued "
            f"to {plan.action}), but the mutation could not be confirmed."
        )

    if status is MutationStatus.NO_MATCH:
        return "[bold]No changes[/bold]\n\nNo torrents matched this selection."

    if status is MutationStatus.NO_CHANGES:
        return (
            "[bold]No changes[/bold]\n\n"
            f"All selected torrents already satisfied '{plan.action}'."
        )

    if status is MutationStatus.CANCELLED:
        return "[bold]Cancelled[/bold]\n\nNo mutation was applied."

    # APPLIED
    past_tense = _PAST_TENSE_ACTION[plan.action]
    lines = [
        "[bold green]Applied[/bold green]",
        "",
        f"{len(plan.changes)} torrent(s) {past_tense}",
    ]
    if plan.skipped:
        lines.append(f"{len(plan.skipped)} skipped")
    return "\n".join(lines)


def _format_finding(finding: ExplanationFinding) -> str:
    style = _SEVERITY_STYLES[finding.severity]
    lines = [
        f"[{style}]{finding.severity.value.upper()}[/{style}] "
        f"[bold]{finding.title}[/bold]",
        finding.explanation,
    ]

    if finding.evidence:
        lines.append("")
        lines.append("[bold]Evidence[/bold]")
        lines.extend(_format_evidence(item) for item in finding.evidence)

    if finding.limitations:
        lines.append("")
        lines.append("[bold]Limitations[/bold]")
        lines.extend(f"  - {item}" for item in finding.limitations)

    if finding.next_commands:
        lines.append("")
        lines.append("[bold]Consider[/bold]")
        lines.extend(f"  $ {command}" for command in finding.next_commands)

    return "\n".join(lines)


# Evidence codes that carry a raw byte-per-second rate, per
# `app.explain._build_torrent_finding`'s `common_evidence` tuple.
_RATE_EVIDENCE_CODES = frozenset({"download_rate", "upload_rate"})


def _format_evidence(evidence: Evidence) -> str:
    """Render one evidence row with a humanized value where possible.

    Never changes the underlying `Evidence`/JSON model (`app.explain`'s
    `evidence_to_dict` is untouched) and never invents a value this
    formatting doesn't already have -- purely cosmetic, keyed off
    `evidence.code` (a stable identifier `app.explain` already assigns,
    not inferred from the label text).
    """
    label = f"{evidence.label}:"
    return f"  {label:<15} {_format_evidence_value(evidence)}"


def _format_evidence_value(evidence: Evidence) -> str:
    value = evidence.value
    if evidence.code == "progress" and isinstance(value, int | float):
        return f"{value * 100:.1f}%"
    if evidence.code in _RATE_EVIDENCE_CODES and isinstance(value, int | float):
        return _format_byte_rate(int(value))
    if evidence.code == "tracker_health" and isinstance(value, str):
        return value.title()
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None:
        return "(none)"
    return str(value)


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
