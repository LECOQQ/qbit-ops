"""Textual application for `qbit-ops tui` (TUI 1, read-only).

Security boundary (docs/TUI_ARCHITECTURE_REVIEW.md §10, enforced by
`tests/test_tui_security.py`): this module and every other module under
`app/tui/` must never import `app.main`, any mutation `plan_*`/`apply_*`
function, `app.torrents.list_torrents_with_trackers`, or
`app.torrents._get_tracker_details`. Widgets only ever render safe,
structured domain outputs (`StatusSnapshot`, `SelectedTorrent`,
`get_safe_tracker_details` output, `AppError`).

Refresh is performed synchronously on Textual's event loop inside the
periodic timer callback, not on a background thread worker -- a
deliberate, documented simplification for TUI 1 (see the end-of-phase
report): qBittorrent's API calls are typically sub-100ms on a homelab
LAN, so a brief pause once per refresh interval is an acceptable
trade-off against the added complexity of thread-safe state handoff for
a first read-only slice. Revisit with `run_worker(thread=True)` if this
proves noticeable in practice.
"""

from __future__ import annotations

from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Checkbox, DataTable, Footer, Input, Static

from app.app_services import create_qbit_client
from app.status import Health
from app.torrents import SelectedTorrent, TorrentFilter, build_torrent_filter
from app.tui.state import (
    DEFAULT_REFRESH_INTERVAL_SECONDS,
    ConnectionState,
    TuiController,
)

NARROW_WIDTH_THRESHOLD = 100

_HEALTH_STYLES: dict[Health, str] = {
    Health.HEALTHY: "bold green",
    Health.WARNING: "bold yellow",
    Health.CRITICAL: "bold red",
    Health.UNAVAILABLE: "bold red",
}


class StatusHeader(Static):
    """The top status bar: health, transfer rates, last refresh, staleness."""


class ConnectionBanner(Static):
    """A dismissible-looking, non-blocking banner shown while reconnecting.

    Never replaces the torrent table underneath it -- see
    docs/TUI_ARCHITECTURE_REVIEW.md §5/§6 (stale data stays visible).
    """


class FiltersPanel(Vertical):
    """The shared `TorrentFilter` vocabulary, applied entirely in memory.

    No qBittorrent API call is ever triggered by a change here -- every
    change handler only calls `TuiController.set_filters`, which
    re-applies filters against the already-fetched torrent list (see
    `app.tui.state`).
    """

    def compose(self) -> ComposeResult:
        yield Static("[bold]Filters[/bold]")
        yield Input(placeholder="category", id="filter-category")
        yield Input(placeholder="state", id="filter-state")
        yield Checkbox("Completed", id="filter-completed")
        yield Checkbox("Incomplete", id="filter-incomplete")
        yield Checkbox("Active", id="filter-active")
        yield Checkbox("Inactive", id="filter-inactive")
        yield Checkbox("Stalled", id="filter-stalled")
        yield Checkbox("Errored", id="filter-errored")
        yield Static("", id="filter-error")


class DetailsPanel(VerticalScroll):
    """Safe details for the focused torrent.

    Only ever renders `SelectedTorrent` fields (live from the periodic
    snapshot) and `get_safe_tracker_details`-shaped structural tracker
    fields -- never a raw announce URL, path, query value, userinfo, or
    unsanitized message.
    """


class QbitOpsTuiApp(App[None]):
    """The read-only TUI 1 vertical slice."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #status-header {
        height: 3;
        padding: 0 1;
        border: solid $accent;
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
    FiltersPanel {
        width: 26;
        border: solid $accent;
        padding: 0 1;
    }
    DataTable {
        width: 1fr;
    }
    DetailsPanel {
        width: 36;
        border: solid $accent;
        padding: 0 1;
    }
    Screen.narrow FiltersPanel {
        display: none;
    }
    Screen.narrow DetailsPanel {
        display: none;
    }
    Screen.narrow.show-details DataTable {
        display: none;
    }
    Screen.narrow.show-details DetailsPanel {
        display: block;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("slash", "focus_search", "Search"),
        Binding("f", "focus_filters", "Filters"),
        Binding("enter", "open_details", "Details"),
        Binding("r", "refresh_details", "Refresh details"),
        Binding("question_mark", "toggle_help", "Help"),
    ]

    show_help = reactive(False)

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

    def compose(self) -> ComposeResult:
        yield StatusHeader(id="status-header")
        yield ConnectionBanner(id="banner")
        with Horizontal(id="main"):
            yield FiltersPanel(id="filters")
            yield DataTable(id="torrents", cursor_type="row")
            yield DetailsPanel(id="details")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#torrents", DataTable)
        table.add_columns("Name", "State", "Progress", "Down", "Up", "Ratio")
        # The torrent table is the primary panel: focus it by default so
        # single-letter bindings (q, f, /, r, ?) reach the App instead of
        # being consumed as text by whichever Input happens to be first
        # in the DOM.
        table.focus()
        self.set_interval(self.refresh_interval, self._on_tick)
        self._on_tick()

    def on_resize(self) -> None:
        self.screen.set_class(
            self.size.width < NARROW_WIDTH_THRESHOLD, "narrow"
        )

    # -- refresh -----------------------------------------------------

    def _on_tick(self) -> None:
        if self.controller.state.refreshing:
            return
        try:
            self.controller.refresh()
        except Exception as error:  # a real internal defect, not remote failure
            self._show_fatal(error)
            return
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
        self._render_table()
        self._render_details()

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

    def _render_table(self) -> None:
        table = self.query_one("#torrents", DataTable)
        state = self.controller.state
        visible = state.visible

        previously_focused = state.focused_hash
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

    def _render_details(self) -> None:
        details = self.query_one("#details", DetailsPanel)
        details.remove_children()
        state = self.controller.state
        torrent = state.focused_torrent()

        if torrent is None:
            details.mount(Static("No torrent focused."))
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
        details.mount(Static("\n".join(lines)))

        tracker_details = state.focused_tracker_details
        if tracker_details is None:
            details.mount(Static("Trackers: loading..."))
        else:
            fetched_at = state.focused_details_fetched_at
            freshness = (
                fetched_at.strftime("fetched %H:%M:%S UTC")
                if fetched_at is not None
                else ""
            )
            details.mount(Static(f"[bold]Trackers[/bold] ({freshness})"))
            if not tracker_details:
                details.mount(Static("  (none)"))
            for endpoint in tracker_details:
                details.mount(Static(f"  {_format_endpoint(endpoint)}"))

    # -- interaction -----------------------------------------------------

    def on_data_table_row_highlighted(
        self, event: DataTable.RowHighlighted
    ) -> None:
        if event.row_key.value is None:
            return
        self.controller.set_focus(str(event.row_key.value))
        self._render_details()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-category":
            self._apply_filters_from_widgets()
        elif event.input.id == "filter-state":
            self._apply_filters_from_widgets()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id and event.checkbox.id.startswith("filter-"):
            self._apply_filters_from_widgets()

    def _apply_filters_from_widgets(self) -> None:
        error_widget = self.query_one("#filter-error", Static)
        try:
            filters = _build_filter_from_widgets(self)
        except ValueError as error:
            error_widget.update(f"[red]{error}[/red]")
            return

        error_widget.update("")
        self.controller.set_filters(filters)
        self._render_table()
        self._render_details()

    def action_focus_search(self) -> None:
        self.mount_search_input()

    def mount_search_input(self) -> None:
        existing = self.query("#search-input")
        if existing:
            existing.first().focus()
            return
        search = Input(placeholder="Search by name...", id="search-input")
        self.query_one("#status-header", StatusHeader).mount(search)
        search.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search-input":
            self.controller.set_search(event.value)
            self._render_table()
            self._render_details()

    def action_focus_filters(self) -> None:
        self.query_one("#filter-category", Input).focus()

    def action_cursor_down(self) -> None:
        self.query_one("#torrents", DataTable).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#torrents", DataTable).action_cursor_up()

    def action_open_details(self) -> None:
        if "narrow" in self.screen.classes:
            self.screen.toggle_class("show-details")
        else:
            self.query_one("#details", DetailsPanel).focus()

    def action_refresh_details(self) -> None:
        self.controller.refresh_focused_details()
        self._render_details()

    def action_toggle_help(self) -> None:
        self.show_help = not self.show_help
        details = self.query_one("#details", DetailsPanel)
        if self.show_help:
            details.remove_children()
            details.mount(Static(_HELP_TEXT))
        else:
            self._render_details()


_HELP_TEXT = """[bold]Keys[/bold]
up/down, j/k   navigate torrents
/              search by name
f              focus filters
enter          open/toggle details
r              refresh focused torrent's tracker details
?              toggle this help
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


def _build_filter_from_widgets(app: QbitOpsTuiApp) -> TorrentFilter:
    category_text = app.query_one("#filter-category", Input).value
    state_text = app.query_one("#filter-state", Input).value
    categories = [
        part.strip() for part in category_text.split(",") if part.strip()
    ]
    states = [part.strip() for part in state_text.split(",") if part.strip()]

    return build_torrent_filter(
        categories=categories,
        states=states,
        completed=app.query_one("#filter-completed", Checkbox).value,
        incomplete=app.query_one("#filter-incomplete", Checkbox).value,
        active=app.query_one("#filter-active", Checkbox).value,
        inactive=app.query_one("#filter-inactive", Checkbox).value,
        stalled=app.query_one("#filter-stalled", Checkbox).value,
        errored=app.query_one("#filter-errored", Checkbox).value,
    )


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
