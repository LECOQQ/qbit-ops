"""The Overview workspace: `BrandHeader` + `WorkspaceTabs` + `OverviewPanel`."""

from __future__ import annotations

from datetime import datetime, tzinfo
from enum import StrEnum

from rich.console import Group
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Static

from qbit_ops import __version__
from qbit_ops.tui.formatting import (
    _BRAND_ACCENT,
    _GRADIENT_END,
    _GRADIENT_START,
    _INACTIVE_TAB_ACCENT,
    TABLE_NAV_HINT,
    _window_title,
)
from qbit_ops.tui.state import ConnectionState, TuiState, Workspace
from qbit_ops.tui.widgets.overview_windows import (
    SESSION_TITLE,
    TRACKERS_TITLE,
    TRANSFER_TITLE,
    SessionWindow,
    TrackersWindow,
    connection_marker,
)
from qbit_ops.tui.widgets.rate_graph import RateGraph

_CONNECTION_LABELS: dict[ConnectionState, str] = {
    ConnectionState.CONNECTING: "connecting",
    ConnectionState.CONNECTED: "connected",
    ConnectionState.RECONNECTING: "reconnecting",
    ConnectionState.AUTH_FAILED: "unavailable (authentication failed)",
    ConnectionState.CONFIG_FAILED: "unavailable (configuration invalid)",
}

# Same semantic vocabulary as `_HEALTH_STYLES` (never the brand
# gradient) -- connected is the only "good" state, connecting/
# reconnecting are transitional, both failure states are red.
_CONNECTION_STYLES: dict[ConnectionState, str] = {
    ConnectionState.CONNECTING: "bold yellow",
    ConnectionState.CONNECTED: "bold green",
    ConnectionState.RECONNECTING: "bold yellow",
    ConnectionState.AUTH_FAILED: "bold red",
    ConnectionState.CONFIG_FAILED: "bold red",
}


# -- BrandHeader ----------------------------------------------------------
#
# Figlet-style reviewed wordmarks (both tiers) in plain ASCII -- no
# box-drawing/block Unicode (ambiguous East Asian width in some
# terminals) and no runtime font generator.
_LOGO_FULL: tuple[str, ...] = (
    " #####  ######  ### #######       ####### ######   #####  ",
    "#     # #     #  #     #          #     # #     # #     # ",
    "#     # #     #  #     #          #     # #     # #       ",
    "#     # ######   #     #    ##### #     # ######   #####  ",
    "#   # # #     #  #     #          #     # #             # ",
    "#    #  #     #  #     #          #     # #       #     # ",
    " #### # ######  ###    #          ####### #        #####  ",
)
_LOGO_COMPACT: tuple[str, ...] = (
    " ___  ____  ___ _____      ___  ____  ____   ",
    "/ _ \\| __ )|_ _|_   _|    / _ \\|  _ \\/ ___|  ",
    "| | | |  _ \\ | |  | |_____| | | | |_) \\___ \\ ",
    "| |_| | |_) || |  | |_____| |_| |  __/ ___) |",
    " \\__\\_\\____/|___| |_|      \\___/|_|   |____/ ",
)

# BrandHeader picks its variant from its own measured logo widths, not
# the general card-layout breakpoints in `formatting.py` -- reusing those
# made the compact wordmark disappear far before it needed to.
#
# The margins are one column each, not five and six: the wordmark no
# longer owns the full width, it owns the identity column inside the
# ᴛʀᴀɴꜱꜰᴇʀ window's border, beside the graph. A wider margin dropped the
# full wordmark at 140 columns -- the very width the design is drawn at.
_FULL_LOGO_WIDTH = max(len(line) for line in _LOGO_FULL)
_COMPACT_LOGO_WIDTH = max(len(line) for line in _LOGO_COMPACT)
_BRAND_FULL_MIN_WIDTH = _FULL_LOGO_WIDTH + 1  # 58
_BRAND_COMPACT_MIN_WIDTH = _COMPACT_LOGO_WIDTH + 1  # 46

# Kept only for the variant that has no wordmark to identify the app
# with. Beside a drawn wordmark the tagline restated what the picture
# already said, and the hint restated `[?→Help]` in the command bar --
# together they cost the masthead three of its ten lines, on the page
# whose measured problem was emptiness.
_TAGLINE_COMPACT = "Safe qBittorrent operations"
_HINT_NARROW = "Dry-run first · ? Help"


def _gradient_row(line: str, width: int) -> Text:
    """Colour one logo row by visible column, orange (left) to coral (right)."""
    text = Text()
    span = max(width - 1, 1)
    for index, char in enumerate(line):
        ratio = index / span
        r, g, b = (
            round(start + (end - start) * ratio)
            for start, end in zip(_GRADIENT_START, _GRADIENT_END, strict=True)
        )
        text.append(char, style=Style(color=f"#{r:02x}{g:02x}{b:02x}"))
    return text


def _gradient_logo(lines: tuple[str, ...]) -> Group:
    width = max(len(line) for line in lines)
    return Group(*(_gradient_row(line, width) for line in lines))


class HeaderVariant(StrEnum):
    """The three responsive `BrandHeader` presentations."""

    FULL = "full"
    COMPACT = "compact"
    TEXT_ONLY = "text-only"


def _variant_for_width(width: int) -> HeaderVariant:
    if width >= _BRAND_FULL_MIN_WIDTH:
        return HeaderVariant.FULL
    if width >= _BRAND_COMPACT_MIN_WIDTH:
        return HeaderVariant.COMPACT
    return HeaderVariant.TEXT_ONLY


def _version_text() -> str:
    return f"v{__version__}"


def _render_variant(variant: HeaderVariant) -> Group:
    """The wordmark, and nothing else while there is a wordmark.

    The installed version is carried by the app frame's own border
    title, so dropping it from beside the logo loses nothing -- and the
    text-only variant, which has no wordmark at all, keeps it because
    there is nothing else there to name the application.
    """
    if variant is HeaderVariant.FULL:
        return Group(_gradient_logo(_LOGO_FULL))
    if variant is HeaderVariant.COMPACT:
        return Group(_gradient_logo(_LOGO_COMPACT))
    return Group(
        Text(f"qbit-ops {_version_text()}"),
        Text(_TAGLINE_COMPACT),
        Text(_HINT_NARROW),
    )


class BrandHeader(Static):
    """Passive branded header for the Overview workspace.

    No qBittorrent call, no worker, no mutable application state. Picks
    its `full`/`compact`/`text-only` variant from its own width via
    Textual's `Resize` event (delivered on first layout, not only later
    changes), so `QbitOpsTuiApp` needs no branding-specific
    orchestration to keep it responsive.
    """

    def __init__(self, *, id: str | None = None) -> None:
        self._variant = HeaderVariant.FULL
        super().__init__(_render_variant(self._variant), id=id)

    @property
    def variant(self) -> HeaderVariant:
        return self._variant

    def _on_resize(self, event: events.Resize) -> None:
        self.apply_width(event.size.width)

    def apply_width(self, width: int) -> None:
        variant = _variant_for_width(width)
        if variant is self._variant:
            return
        self._variant = variant
        self.update(_render_variant(variant))


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
        self.update(f"{overview}[dim]│[/dim]{torrents}")


def _tab_label(
    name: str, keys: str | None = None, active: bool = False, *, badge: str = ""
) -> str:
    """Style one tab shared by the workspace strip and the filters
    modal's `border_title` (see `qbit_ops.tui.tab_bar`): the active tab
    in brand orange, the others in a restrained blue
    (`_INACTIVE_TAB_ACCENT`) -- a real, deliberate exception to "no
    colour-block decoration" (see `qbit_ops.tcss`'s `#workspace-tabs`
    rule): which tab you're on is meaningful information, not
    decoration, so it stays legible without leaning on a background
    panel.

    The underline is scoped to `name` alone: the surrounding padding,
    the optional `(key)` hint and the optional `badge` keep the plain
    bold accent, so the underline reads as hugging the tab name rather
    than the whole tab block.

    `keys` (a workspace tab's `(1/g)` hint) and `badge` (a filters tab's
    pending count/marker) are mutually exclusive in practice, but both
    default to absent so a caller only ever states what it has -- one
    rendering, not a second one bolted on for the modal.
    """
    color = _BRAND_ACCENT if active else _INACTIVE_TAB_ACCENT
    plain_style = f"bold {color}"
    name_style = f"bold underline {color}" if active else plain_style
    trailer = (f" {badge}" if badge else "") + (f" ({keys})" if keys else "")
    return (
        f"[{plain_style}] [/{plain_style}]"
        f"[{name_style}]{name}[/{name_style}]"
        f"[{plain_style}]{trailer} [/{plain_style}]"
    )


class OverviewPanel(VerticalScroll):
    """The Overview workspace: what the machine is doing, in one screen.

    Four regions, all built from the same `TuiState` the periodic
    refresh already populates -- no qBittorrent call of its own:

        identity   the wordmark, and the instance's own status line
        graph      sixty seconds of transfer, in the band beside it
        ᴛʀᴀᴄᴋᴇʀꜱ    per-tracker activity, derived from torrent rates
        ꜱᴇꜱꜱɪᴏɴ     the instance's counters and the library's shape

    Every child is mounted once and never torn down; `render_state()`
    only updates their content, so a refresh never costs a remount.
    """

    def __init__(
        self, *, id: str | None = None, small_caps: bool = True
    ) -> None:
        super().__init__(id=id)
        self._small_caps = small_caps

    def compose(self) -> ComposeResult:
        with Horizontal(id="overview-masthead"):
            with Vertical(id="overview-identity"):
                yield BrandHeader(id="brand-header")
                yield Static(id="overview-rail", classes="ov-rail")
            yield RateGraph(id="rate-graph")
        yield Static(id="overview-stale", classes="ov-stale")
        with Horizontal(id="overview-windows"):
            yield TrackersWindow(id="trackers-window")
            yield SessionWindow(id="session-window")

    def on_mount(self) -> None:
        masthead = self.query_one("#overview-masthead", Horizontal)
        masthead.border_title = _window_title(
            TRANSFER_TITLE, small_caps=self._small_caps
        )
        trackers = self.query_one("#trackers-window", TrackersWindow)
        trackers.border_title = _window_title(
            TRACKERS_TITLE, small_caps=self._small_caps
        )
        session = self.query_one("#session-window", SessionWindow)
        session.border_title = _window_title(
            SESSION_TITLE, small_caps=self._small_caps
        )

    def render_state(self, state: TuiState) -> None:
        rail = self.query_one("#overview-rail", Static)
        if state.status is None:
            rail.update("Connecting to qBittorrent...")
        else:
            rail.update(_overview_rail_text(state))

        # The refresh moment rides the window's own border, not the rail:
        # the rail is one fixed line and the status word already varies
        # in length, so an extra clause there would be the first thing
        # truncated away. `TABLE_NAV_HINT` shares this one slot with it,
        # verbatim -- the same string the torrents table's own border
        # already carries, not a second phrasing of the same gesture.
        masthead = self.query_one("#overview-masthead", Horizontal)
        masthead.border_subtitle = (
            f"{_refresh_subtitle(state)} · {TABLE_NAV_HINT}"
        )

        # Its own widget, outside the fixed-height masthead: folding it
        # into the rail made every window below move a row the moment a
        # refresh went stale.
        stale = self.query_one("#overview-stale", Static)
        stale.display = state.stale
        stale.update(
            "[bold yellow]STALE[/bold yellow] -- showing last-good data"
        )

        self.query_one("#rate-graph", RateGraph).render_state(
            state.rate_history
        )
        self.query_one("#trackers-window", TrackersWindow).render_state(state)
        self.query_one("#session-window", SessionWindow).render_state(state)


def _format_rail_time(moment: datetime, *, tz: tzinfo | None = None) -> str:
    """Format a refresh moment for the rail, minutes precision only.

    A rail-local variant of `qbit_ops.tui.formatting._format_local_time`:
    that shared helper's seconds precision is relied on elsewhere
    (e.g. Explain/Preview freshness lines), so it stays untouched.
    """
    local = moment.astimezone(tz)
    tz_label = local.tzname() or "local"
    return f"{local:%H:%M} {tz_label}"


def _overview_rail_text(state: TuiState) -> str:
    """The instance's own status line: how it is reachable, and what it is.

    While the TUI is connected, the word and the glyph come from
    qBittorrent's *own* `connection_status`, not from the fact that the
    call succeeded. The two are not the same claim: a firewalled
    instance answers every request and still receives nothing, and
    printing "Connected" over it hid exactly the cause a flat download
    graph would otherwise leave unexplained.

    Transfer rates live in the top-right `GlobalRateDisplay` and in the
    graph, never here.
    """
    status = state.status
    assert status is not None

    if state.connection is ConnectionState.CONNECTED:
        glyph, label, style = connection_marker(state.instance_stats)
    else:
        glyph = "●"
        label = _CONNECTION_LABELS[state.connection].capitalize()
        style = _CONNECTION_STYLES[state.connection]

    parts = [f"[{style}]{glyph}[/{style}] [bold]{label}[/bold]"]
    if status.qbittorrent_version:
        parts.append(f"qBittorrent {status.qbittorrent_version}")
    if status.api_version:
        parts.append(f"API {status.api_version}")

    return " · ".join(parts)


def _refresh_subtitle(state: TuiState) -> str:
    if state.last_successful_refresh is None:
        return "Refreshed never"
    return f"Refreshed {_format_rail_time(state.last_successful_refresh)}"
