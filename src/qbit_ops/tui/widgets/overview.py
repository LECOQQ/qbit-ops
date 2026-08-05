"""The Overview workspace: `BrandHeader` + `WorkspaceTabs` + `OverviewPanel`."""

from __future__ import annotations

from datetime import datetime, tzinfo
from enum import StrEnum

from rich.console import Group
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from qbit_core.features.status import Health
from qbit_ops import __version__
from qbit_ops.tui.formatting import (
    _BRAND_ACCENT,
    _GRADIENT_END,
    _GRADIENT_START,
    _INACTIVE_TAB_ACCENT,
)
from qbit_ops.tui.state import ConnectionState, TuiState, Workspace

_HEALTH_STYLES: dict[Health, str] = {
    Health.HEALTHY: "bold green",
    Health.WARNING: "bold yellow",
    Health.CRITICAL: "bold red",
    Health.UNAVAILABLE: "bold red",
}

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

_OVERVIEW_NAV_HINT = "[bold]Enter[/bold] / [bold]t[/bold]   Browse torrents"

# Minimum Screen width for the `#overview-cards` two-column grid, kept
# distinct from `NARROW_WIDTH_THRESHOLD` (which governs `.narrow`,
# DetailsPanel visibility, etc.). Measured against this module's own
# rendered content at the `grid-columns: 3fr 2fr` ratio below: Health's
# widest realistic line ("999 errored · 999 unknown", 26 columns) needs
# >= 27 columns once its 1-column padding + border are added, which the
# grid first provides at Screen width 90.
OVERVIEW_GRID_MIN_WIDTH = 90


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
# made the compact wordmark disappear far before it needed to. Margins
# keep each logo from crowding its container.
_FULL_LOGO_WIDTH = max(len(line) for line in _LOGO_FULL)
_COMPACT_LOGO_WIDTH = max(len(line) for line in _LOGO_COMPACT)
_BRAND_FULL_MIN_WIDTH = _FULL_LOGO_WIDTH + 6  # 64
_BRAND_COMPACT_MIN_WIDTH = _COMPACT_LOGO_WIDTH + 5  # 50

_TAGLINE_FULL = "Safe qBittorrent operations from your terminal"
_TAGLINE_COMPACT = "Safe qBittorrent operations"
_HINT = "Dry-run first · Press ? for help"
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
    version = _version_text()
    if variant is HeaderVariant.FULL:
        return Group(
            _gradient_logo(_LOGO_FULL),
            Text(""),
            Text(f"{_TAGLINE_FULL}   {version}"),
            Text(_HINT),
        )
    if variant is HeaderVariant.COMPACT:
        # No blank separator here (unlike full): the compact band has
        # the least vertical room to spare above the fold.
        return Group(
            _gradient_logo(_LOGO_COMPACT),
            Text(f"{_TAGLINE_COMPACT} · {version}"),
            Text(_HINT),
        )
    return Group(
        Text(f"qbit-ops {version}"),
        Text(_TAGLINE_COMPACT),
        Text(_HINT_NARROW),
    )


class BrandHeader(Static):
    """Passive branded header for the Overview workspace.

    Static logo + installed `qbit_ops.__version__` + one tagline + one
    help hint -- no qBittorrent call, no worker, no mutable application
    state. Picks its `full`/`compact`/`text-only` variant from its own
    width via Textual's `Resize` event (delivered on first layout, not
    only later changes), so `QbitOpsTuiApp` needs no branding-specific
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


def _tab_label(name: str, keys: str, active: bool) -> str:
    """Style one workspace tab: the active page in brand orange, the
    other in a restrained blue (`_INACTIVE_TAB_ACCENT`) -- a real,
    deliberate exception to "no colour-block decoration" (see
    `QbitOpsTuiApp.CSS`'s `#workspace-tabs` rule): which page you're on
    is meaningful information, not decoration, so it stays legible
    without leaning on a background panel.

    The underline is scoped to `name` alone: the surrounding padding
    and the `(key)` hint keep the plain bold accent, so the underline
    reads as hugging the page name rather than the whole tab block."""
    color = _BRAND_ACCENT if active else _INACTIVE_TAB_ACCENT
    plain_style = f"bold {color}"
    name_style = f"bold underline {color}" if active else plain_style
    return (
        f"[{plain_style}] [/{plain_style}]"
        f"[{name_style}]{name}[/{name_style}]"
        f"[{plain_style}] ({keys}) [/{plain_style}]"
    )


class OverviewPanel(VerticalScroll):
    """The Overview workspace's content: one operational homepage built
    entirely from the same `TuiState` the periodic refresh already
    populates. No qBittorrent call of its own.

    Four visual levels, in mount order: `BrandHeader` (branding);
    `#overview-rail` (compact connection + transfer status); the
    `#overview-cards` primary/secondary pair (Torrents, then Health);
    and the Browse-torrents nav hint. The header and nav hint are
    mounted once and never torn down; `render_state()` only updates
    the rail's text and replaces the two cards.

    Torrents and Health each fold two formerly-separate cards
    (Activity+Completion, Attention+Health) into one section without
    implying their sub-counts partition the total -- a torrent can
    count toward more than one at once (e.g. seeding *and* completed
    *and* stalled).
    """

    def compose(self) -> ComposeResult:
        yield BrandHeader(id="brand-header")
        yield Static(id="overview-rail", classes="ov-rail")
        yield Vertical(id="overview-cards")
        yield Static(_OVERVIEW_NAV_HINT, classes="ov-nav", id="overview-nav")

    def render_state(self, state: TuiState) -> None:
        rail = self.query_one("#overview-rail", Static)
        cards = self.query_one("#overview-cards", Vertical)
        cards.remove_children()

        if state.status is None:
            rail.update("Connecting to qBittorrent...")
            return

        rail.update(_overview_rail_text(state))
        torrents = Static(_overview_torrents_text(state), classes="ov-torrents")
        health_class = f"ov-health ov-health-{state.status.health.value}"
        health = Static(_overview_health_text(state), classes=health_class)
        cards.mount(torrents, health)


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
    """Connection/version/refresh status, one line.

    Transfer rates live in the top-right `GlobalRateDisplay` now, not
    here -- this rail only ever restates connection identity, never
    duplicates data another region already owns.
    """
    status = state.status
    assert status is not None
    style = _CONNECTION_STYLES[state.connection]
    label = _CONNECTION_LABELS[state.connection].capitalize()
    parts = [f"[{style}]●[/{style}] [bold]{label}[/bold]"]
    if status.qbittorrent_version:
        parts.append(f"qBittorrent {status.qbittorrent_version}")
    if status.api_version:
        parts.append(f"API {status.api_version}")

    if state.last_successful_refresh is not None:
        refresh_time = _format_rail_time(state.last_successful_refresh)
        parts.append(f"Refreshed {refresh_time}")
    else:
        parts.append("Refreshed never")

    lines = ["   ".join(parts)]
    if state.stale:
        lines.append(
            "[bold yellow]STALE[/bold yellow] -- showing last-good data"
        )
    return "\n".join(lines)


# Label/value pairs, left column then right column, in display order.
_TORRENT_ROWS: tuple[tuple[str, str], ...] = (
    ("Completed", "Incomplete"),
    ("Downloading", "Seeding"),
    ("Stopped", "Checking"),
)
_TORRENT_LEFT_LABEL_WIDTH = max(len(left) for left, _ in _TORRENT_ROWS)
_TORRENT_RIGHT_LABEL_WIDTH = max(len(right) for _, right in _TORRENT_ROWS)


def _overview_torrents_text(state: TuiState) -> str:
    status = state.status
    assert status is not None
    counts = status.counts
    incomplete = max(counts.total - counts.completed, 0)

    value_pairs = (
        (counts.completed, incomplete),
        (counts.downloading, counts.seeding),
        (state.stopped_count, counts.checking),
    )
    # Shared value width so digit-count jumps (e.g. 4-digit totals)
    # never shift one row's columns out of line with the others.
    value_width = max(len(str(value)) for pair in value_pairs for value in pair)

    rows = "\n".join(
        f"{left_label:<{_TORRENT_LEFT_LABEL_WIDTH}}  "
        f"{left_value:>{value_width}}    "
        f"{right_label:<{_TORRENT_RIGHT_LABEL_WIDTH}}  "
        f"{right_value:>{value_width}}"
        for (left_label, right_label), (left_value, right_value) in zip(
            _TORRENT_ROWS, value_pairs, strict=True
        )
    )
    return (
        "[bold]Torrents[/bold]\n"
        "\n"
        f"[bold]{counts.total} total[/bold]\n"
        "\n"
        f"{rows}"
    )


def _overview_health_text(state: TuiState) -> str:
    status = state.status
    assert status is not None
    counts = status.counts
    style = _HEALTH_STYLES[status.health]
    finding_count = len(status.alerts)
    finding_word = "finding" if finding_count == 1 else "findings"
    lines = [
        "[bold]Health[/bold]",
        "",
        f"[{style}]{status.health.value.title()}[/{style}] · "
        f"{finding_count} {finding_word}",
        f"{counts.stalled} stalled",
        f"{counts.errored} errored · {counts.unknown} unknown",
    ]
    return "\n".join(lines)
