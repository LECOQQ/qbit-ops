"""Pure, presentation-only helpers shared by `qbit_ops.tui` widgets/modals.

Every function here takes already-safe structured domain data
(`TuiState`, `TorrentSnapshot`, `BulkTorrentActionPlan`,
`MutationUiResult`, `ExplanationReport`/`Evidence`) and renders it as a
string/`Text` -- never a qBittorrent call, never a widget mount, never
a mutation.
"""

from __future__ import annotations

import re
from datetime import datetime, tzinfo
from typing import Any

from rich.cells import cell_len
from rich.text import Text

from qbit_core.errors import ErrorCategory
from qbit_core.features.explain import (
    Evidence,
    ExplanationFinding,
    ExplanationReport,
)
from qbit_core.features.explain import ExplanationSeverity as Severity
from qbit_core.features.torrents import BulkTorrentActionPlan
from qbit_core.features.trackers import TrackerHealth
from qbit_core.shared.execution import MutationStatus
from qbit_core.shared.selection import format_category_label
from qbit_core.shared.torrent_states import TorrentSnapshot
from qbit_ops.tui.state import (
    MutationUiResult,
    SortDirection,
    SortField,
    SortOrder,
    TuiState,
    _split_skips,
    _state_label,
)

NARROW_WIDTH_THRESHOLD = 100
WIDE_WIDTH_THRESHOLD = 130

# qbit-ops' warm orange -> coral brand family, the single source of
# truth for both `BrandHeader`'s gradient (`tui.widgets.overview`,
# which imports these two) and every restrained accent used here:
# focus indicator, selection mark, active-sort arrow, title marker.
# Never used for health/warning/error signalling -- that vocabulary
# stays exclusively in `_STATE_STYLES`/`_TRACKER_STYLES` below.
_GRADIENT_START = (255, 153, 51)
_GRADIENT_END = (214, 40, 57)
_BRAND_ACCENT = "#{:02x}{:02x}{:02x}".format(*_GRADIENT_START)

# The one deliberate exception to "the brand accent is the only
# accent" (see `WorkspaceTabs._tab_label`): the *inactive* workspace
# tab is real information (which page you are not on), not decoration,
# so it needs its own distinct, still-legible colour rather than the
# brand orange or a dim grey. Textual's own `$primary` (`#0178d4`) is
# more saturated than this restrained dark-terminal palette wants, so
# this is a literal hex from the sky-blue range a lighter theme
# variable would otherwise fall in.
_INACTIVE_TAB_ACCENT = "#5fa8d3"

# The outer AppFrame border (see `QbitOpsTuiApp.CSS`) is drawn only at
# non-narrow widths, one column each side -- accounted for here so
# column/`Name`-width math is never off by the frame's own overhead.
_FRAME_BORDER_COLS = 2


def _content_width(app_width: int) -> int:
    """Usable content width once the outer AppFrame border is
    accounted for -- the frame itself is removed entirely (see
    `Screen.narrow`'s CSS) below `NARROW_WIDTH_THRESHOLD`."""
    if app_width < NARROW_WIDTH_THRESHOLD:
        return app_width
    return app_width - _FRAME_BORDER_COLS


_SEVERITY_STYLES: dict[Severity, str] = {
    Severity.INFO: "bold green",
    Severity.WARNING: "bold yellow",
    Severity.CRITICAL: "bold red",
    Severity.UNKNOWN: "bold magenta",
}


def _format_local_time(moment: datetime, *, tz: tzinfo | None = None) -> str:
    """Format a timestamp in the local system timezone, label included.

    `moment` is always timezone-aware; `tz` exists only for
    deterministic tests, to pin the conversion without depending on
    the CI host's own system timezone.
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


def _format_bytes(byte_count: int) -> str:
    """Format a byte count using binary units, e.g. '12.4 MiB'.

    A deliberate small duplicate of
    `qbit_ops.cli.rendering.format_byte_rate`: TUI modules must never
    import from `qbit_ops.cli` (see the security boundary in
    `qbit_ops.tui.app`), and it's too small to justify a shared module.
    """
    value = float(max(byte_count, 0))
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1

    unit = units[unit_index]
    if unit == "B":
        return f"{int(value)} {unit}"

    return f"{value:.1f} {unit}"


def _format_byte_rate(bytes_per_second: int) -> str:
    """Format a byte rate using binary units, e.g. '12.4 MiB/s'."""
    return f"{_format_bytes(bytes_per_second)}/s"


def _format_global_rate(download_rate: int, upload_rate: int) -> Text:
    """Render the top-right global transfer-rate indicator.

    Per-direction colour, not one shared style: an active direction
    (rate > 0) is the brand accent, an inactive one the restrained
    blue also used for the inactive workspace tab -- both orange when
    both directions are active falls directly out of that rule, no
    special-cased "both" branch needed. Reuses `TuiState.status.rates`,
    the same data the Overview rail already displays -- never a second
    qBittorrent call.
    """
    text = Text()
    down_color = _BRAND_ACCENT if download_rate > 0 else _INACTIVE_TAB_ACCENT
    up_color = _BRAND_ACCENT if upload_rate > 0 else _INACTIVE_TAB_ACCENT
    text.append("↓ ", style=down_color)
    text.append(_format_byte_rate(download_rate), style=down_color)
    text.append("   ")
    text.append("↑ ", style=up_color)
    text.append(_format_byte_rate(upload_rate), style=up_color)
    return text


# User-oriented column order: Name first (always shown, gets the
# remaining width), Category before State, then the operational
# columns. Row *identity* (the DataTable row `key=`) is always the
# full torrent hash regardless of which columns are visible.
_ALL_COLUMNS: tuple[str, ...] = (
    "Name",
    "Category",
    "State",
    "Progress",
    "Rate",
    "Ratio",
)

# Compact, predictable widths for operational columns -- `Name` is
# deliberately left unset so it absorbs the remaining width instead of
# being squeezed to its content's natural size. `Progress` isn't here:
# it needs a wider column when it renders a bar than when it renders a
# bare percentage, see `_progress_column_width`. `State`'s width is its
# longest actual label ("Downloading", 11 cols), not a round number --
# a wider column here was the entire visible gap between `State` and
# `Progress`, since `DataTable`'s own `cell_padding` is table-wide, not
# settable per-column-pair. `Rate` fits the common "both directions
# active" case (e.g. "↓ 18.0 MiB/s  ↑ 2.0 MiB/s", 25 cells); an
# unusually large simultaneous rate on both directions may clip, the
# same trade-off the old fixed-10 `Down`/`Up` columns already made for
# any single rate past "999 MiB/s".
_COLUMN_WIDTHS: dict[str, int] = {
    "Sel": 4,
    "State": 11,
    "Rate": 26,
    "Ratio": 7,
    "Category": 14,
}

# `Sel` (the leading table column) carries two independent, always
# side-by-side glyphs -- focus first, selection second -- never merged
# into one symbol, so a reader can tell "focused", "selected", and
# "both" apart at a glance (`›✓`). `_SELECTED_MARK` (U+2714, "heavy
# check mark") is also reused, unstyled, as a plain bullet in preview
# text below -- keep both usages in sync if this glyph ever changes.
# Both glyphs use the brand accent, not Textual's default (blue)
# `$accent`, and never the row background -- see `QbitOpsTuiApp.CSS`'s
# `#torrents` cursor override for the row-level focus treatment.
_FOCUS_MARK = "›"
_SELECTED_MARK = "✔"
_UNSELECTED_MARK = " "


def _indicator_cell(*, focused: bool, selected: bool) -> Text:
    """Render the focus/selection indicator -- two independent glyphs.

    Moving focus never implies selection and vice versa: each glyph is
    driven by its own boolean and styled independently, so a selected
    row well away from the cursor stays visibly checked, and the
    cursor's own row stays visibly focused regardless of selection.
    """
    cell = Text()
    cell.append(
        _FOCUS_MARK if focused else _UNSELECTED_MARK,
        style=f"bold {_BRAND_ACCENT}" if focused else "",
    )
    cell.append(
        _SELECTED_MARK if selected else _UNSELECTED_MARK,
        style=f"bold {_BRAND_ACCENT}" if selected else "",
    )
    return cell


def _columns_for_width(width: int) -> tuple[str, ...]:
    """Pick which table columns to show, in order, for a given App width.

    `Sel`/`Name`/`State`/`Progress` are always shown. Removal order as
    width shrinks: Category first (medium tier), then Rate and Ratio
    together (narrow tier) -- Category is the least operationally
    urgent column, and at narrow widths every column below
    State/Progress competes directly with `Name`'s own legibility.
    """
    if width < NARROW_WIDTH_THRESHOLD:
        base = ("Name", "State", "Progress")
    elif width < WIDE_WIDTH_THRESHOLD:
        base = ("Name", "State", "Progress", "Rate", "Ratio")
    else:
        base = _ALL_COLUMNS
    return ("Sel", *base)


# Downloading/seeding are the two "active" groups a torrent spends most
# of its life in, so they get the two cyan/green brand-adjacent hues;
# everything else (including a still-unclassified raw state) stays
# neutral or, for a genuine error, red -- restrained rather than
# bolded, since this is one small token, not a row-wide signal.
_STATE_STYLES: dict[str, str] = {
    "Downloading": "cyan",
    "Seeding": "green",
    "Stalled": "yellow",
    "Error": "red",
    "Stopped": "dim",
    "Checking": "dim",
    "Unknown": "dim",
}


def _state_cell(raw_state: str) -> Text:
    label = _state_label(raw_state)
    return Text(label, style=_STATE_STYLES.get(label, ""))


# Tracker-endpoint health -> (glyph, style). Healthy/warning/critical
# keep the semantic vocabulary shared with `_STATE_STYLES`; `disabled`
# (DHT/PeX/LSD switched off) stays muted, deliberately never styled
# like a failure.
_TRACKER_GLYPHS: dict[str, str] = {
    TrackerHealth.HEALTHY.value: "●",
    TrackerHealth.WARNING.value: "!",
    TrackerHealth.CRITICAL.value: "●",
    TrackerHealth.UNAVAILABLE.value: "●",
    TrackerHealth.DISABLED.value: "○",
    TrackerHealth.UNKNOWN.value: "○",
}
_TRACKER_STYLES: dict[str, str] = {
    TrackerHealth.HEALTHY.value: "green",
    TrackerHealth.WARNING.value: "yellow",
    TrackerHealth.CRITICAL.value: "red",
    TrackerHealth.UNAVAILABLE.value: "red",
    TrackerHealth.DISABLED.value: "dim",
    TrackerHealth.UNKNOWN.value: "dim",
}


# Plain ASCII, not block-drawing Unicode (U+2588/U+2591): both measure
# as one cell under `rich.cells.cell_len`, but rendering one through a
# real Textual pilot (`export_screenshot`, read back and proofread --
# never trusted from the source string alone) showed the glyph's
# *actual* rendered width in a DataTable column disagreeing with that
# logical measurement, misaligning the trailing percentage against the
# next column. ASCII has no such ambiguity.
_PROGRESS_BAR_WIDTH = 10
_PROGRESS_FULL_CHAR = "#"
_PROGRESS_EMPTY_CHAR = "-"


def _clamp_progress(progress: float) -> float:
    return min(max(progress, 0.0), 1.0)


def _progress_percent_text(progress: float) -> str:
    return f"{_clamp_progress(progress) * 100:.0f}%"


def _progress_bar(progress: float, *, width: int = _PROGRESS_BAR_WIDTH) -> str:
    clamped = _clamp_progress(progress)
    raw_filled = round(clamped * width)
    filled = min(max(raw_filled, 0), width)
    return _PROGRESS_FULL_CHAR * filled + _PROGRESS_EMPTY_CHAR * (
        width - filled
    )


def _progress_cell(progress: float, *, bar: bool) -> str:
    """Render one Progress cell: `bar+percentage` when there's room,
    bare percentage otherwise -- never a different underlying value."""
    percent = _progress_percent_text(progress)
    if not bar:
        return percent
    return f"{_progress_bar(progress)} {percent:>4}"


def _progress_column_width(*, bar: bool) -> int:
    return _PROGRESS_BAR_WIDTH + 5 if bar else 4


_RATE_INACTIVE_CELL = "—"
_RATE_COLUMN_GAP = "  "


def _format_rate_cell(download_rate: int, upload_rate: int) -> str:
    """Render one compact Rate cell: `↓ down`, `↑ up`, both, or `—`.

    Never a colour cue here (unlike the top-right global rate display)
    -- one small table cell has no room for it, and the arrow plus
    presence/absence of a direction is already unambiguous.
    """
    parts = []
    if download_rate > 0:
        parts.append(f"↓ {_format_byte_rate(download_rate)}")
    if upload_rate > 0:
        parts.append(f"↑ {_format_byte_rate(upload_rate)}")
    if not parts:
        return _RATE_INACTIVE_CELL
    return _RATE_COLUMN_GAP.join(parts)


# DataTable's own default `cell_padding` (one column of padding added
# on *each* side of every column's declared width when rendering --
# see `textual.widgets.data_table.Column.get_render_width`). Used here
# to size `Name` so the *total* rendered row width still fits the
# terminal, not just the sum of declared column widths.
_CELL_PADDING = 1
# Target minimum from the design brief ("~24-30 cols") -- honoured
# whenever the terminal has room for it, but never at the cost of
# horizontal overflow (see `_name_column_width`). `_ABSOLUTE_MIN_WIDTH`
# is the true hard floor, just enough to stay legible.
_NAME_MIN_WIDTH = 24
_ABSOLUTE_MIN_WIDTH = 1
# A DataTable taller than its viewport shows a vertical scrollbar,
# which silently consumes one more column of width -- reserved
# defensively so a computed `Name` width never causes horizontal
# overflow/scrolling once 1,000+ rows are loaded.
_SCROLLBAR_RESERVE = 2


# The `#torrents` DataTable's own titled-region border (round, one
# column each side) -- see `QbitOpsTuiApp.CSS`.
_TORRENTS_BORDER_COLS = 2

# Mirrors `DetailsScreen.CSS`'s `#details-dialog` rule (`width: 86%;
# min-width: 60; max-width: 100`) -- computed here, not read back from
# the live widget, for the same pre-layout-timing reason as every
# other width helper in this module. `_DETAILS_DIALOG_BORDER_COLS`
# accounts for the dialog's own round border (2) plus `padding: 1 2`
# (4 columns).
_DETAILS_DIALOG_WIDTH_FRACTION = 0.86
_DETAILS_DIALOG_MIN_WIDTH = 60
_DETAILS_DIALOG_MAX_WIDTH = 100
_DETAILS_DIALOG_BORDER_COLS = 6


def _details_dialog_content_width(app_width: int) -> int:
    """The Details modal dialog's own inner (border+padding-excluded)
    content width at `app_width` -- used to wrap the full torrent name."""
    raw = round(_content_width(app_width) * _DETAILS_DIALOG_WIDTH_FRACTION)
    dialog_width = min(
        max(raw, _DETAILS_DIALOG_MIN_WIDTH), _DETAILS_DIALOG_MAX_WIDTH
    )
    return max(dialog_width - _DETAILS_DIALOG_BORDER_COLS, _ABSOLUTE_MIN_WIDTH)


def _name_column_width(
    app_width: int, other_columns: tuple[str, ...], *, bar: bool
) -> int:
    """Compute `Name`'s column width: whatever remains in the
    *DataTable's own* content width (`app_width` minus the outer
    AppFrame border, minus the table's own titled-region border) after
    every other visible column's own rendered width (declared width
    plus `DataTable`'s padding on both sides). The table is the sole
    occupant of the Torrents workspace's body now that the permanent
    Details side panel is gone.

    "No horizontal scrolling, ever" (see module-level design notes)
    outranks the ~24-30 target minimum: at the Wide tier's own lower
    edge, every fixed column plus a comfortable `Name` can very
    narrowly exceed the available width, so `_NAME_MIN_WIDTH` is only
    a *target* -- honoured whenever the budget allows it, never forced
    at the cost of overflow. Only `_ABSOLUTE_MIN_WIDTH` (small, just
    enough to stay legible and positive) is a hard floor.
    """
    table_width = _content_width(app_width) - _TORRENTS_BORDER_COLS
    reserved = sum(
        (
            _progress_column_width(bar=bar)
            if name == "Progress"
            else _COLUMN_WIDTHS.get(name, 0)
        )
        + 2 * _CELL_PADDING
        for name in other_columns
    )
    reserved += 2 * _CELL_PADDING  # Name's own padding.
    reserved += _SCROLLBAR_RESERVE
    return max(table_width - reserved, _ABSOLUTE_MIN_WIDTH)


def _highlighted_name_cell(name: str, search: str, width: int) -> Text:
    """Render the `Name` cell, orange-highlighting the first live match.

    A presentation-only echo of `TuiController.set_search`'s own
    matching (case-insensitive substring) -- never a second matching
    implementation, and never changes which rows are shown, only how
    the already-matched ones look. A no-op (plain truncated text) once
    `search` is empty or its match falls outside the truncated/visible
    portion of a long name.
    """
    truncated = _truncate(name, width)
    text = Text(truncated)
    if not search:
        return text
    index = truncated.casefold().find(search.casefold())
    if index == -1:
        return text
    text.stylize(f"bold {_BRAND_ACCENT}", index, index + len(search))
    return text


def _torrent_row_values(
    torrent: TorrentSnapshot,
    *,
    focused: bool,
    selected: bool,
    bar: bool,
    name_width: int,
    search: str = "",
) -> dict[str, Any]:
    return {
        "Sel": _indicator_cell(focused=focused, selected=selected),
        "Name": _highlighted_name_cell(torrent.name, search, name_width),
        "State": _state_cell(torrent.state),
        "Progress": _progress_cell(torrent.progress, bar=bar),
        "Rate": _format_rate_cell(torrent.download_rate, torrent.upload_rate),
        "Ratio": f"{torrent.ratio:.2f}",
        "Category": format_category_label(torrent.category),
    }


# Maps each sortable field to the table column it drives, so the
# active sort gets one small brand-accent arrow in that column's
# header -- the table's only per-header colour, everything else stays
# neutral (see `QbitOpsTuiApp.CSS`'s restrained `.datatable--header`).
# Down and Up both drive the same merged `Rate` column.
_SORT_FIELD_COLUMN: dict[SortField, str] = {
    SortField.NAME: "Name",
    SortField.STATE: "State",
    SortField.PROGRESS: "Progress",
    SortField.DOWN: "Rate",
    SortField.UP: "Rate",
    SortField.RATIO: "Ratio",
    SortField.CATEGORY: "Category",
}


def _format_torrents_title(shown: int, selected: int, sort_label: str) -> str:
    """The `#torrents` DataTable's floating border title: concise live
    context, never a long filter description (that stays on
    `FilterSummary`'s own line so the title never overflows/wraps)."""
    title = f"Torrents · {shown:,} shown"
    if selected:
        title += f" · {selected:,} selected"
    else:
        title += f" · {sort_label}"
    return title


def _column_header(
    name: str, sort: SortOrder, *, width: int | None = None
) -> Text:
    """Render one table header cell -- plain, except the column
    currently driving the active local sort gets a small trailing
    arrow in the brand accent.

    `width` is the column's own fixed render width (`None` for the
    flexible `Name` column). The arrow is only added if it fits --
    the narrowest columns (e.g. bare-percentage `Progress`) would
    otherwise clip it; the workspace summary's "Sorted by ..." line is
    the reliable channel regardless.
    """
    if _SORT_FIELD_COLUMN.get(sort.field) != name:
        return Text(name)
    arrow = "↑" if sort.direction is SortDirection.ASCENDING else "↓"
    if width is not None and width < len(name) + 2:
        return Text(name)
    header = Text(f"{name} ")
    header.append(arrow, style=f"bold {_BRAND_ACCENT}")
    return header


# Separators after which the Details modal's full torrent name may
# wrap: '.', '-', '_', '[', ']', and plain whitespace. Chosen to break
# release-name-shaped titles ("Ubuntu.22.04.6-desktop-amd64.iso") at
# meaningful boundaries instead of an arbitrary mid-word character.
_NAME_WRAP_TOKEN_RE = re.compile(r".*?(?:[ .\-_\[\]]|$)")


def _wrap_name_at_separators(name: str, width: int) -> str:
    """Wrap `name` to `width` cells, breaking only right after a space
    or one of '.', '-', '_', '[', ']' -- never truncates.

    Width is measured with `rich.cells.cell_len`, not `len()` (a
    Details modal name is plain, markup-free text at this point, but
    kept consistent with every other width-sensitive helper here). A
    single token longer than `width` on its own (no break character
    for an entire run) is hard-wrapped as a last resort -- still never
    truncated, just split.
    """
    if width < 1:
        return name
    tokens = [t for t in _NAME_WRAP_TOKEN_RE.findall(name) if t]
    lines: list[str] = []
    current = ""
    for token in tokens:
        candidate = current + token
        if current and cell_len(candidate) > width:
            lines.append(current)
            current = token
        else:
            current = candidate
        while cell_len(current) > width:
            lines.append(current[:width])
            current = current[width:]
    if current:
        lines.append(current)
    return "\n".join(lines) if lines else name


_DETAILS_PROGRESS_BAR_WIDTH = 20
_METRIC_COLUMN_GAP = "   "


def _format_details_identity(
    torrent: TorrentSnapshot, *, name_width: int
) -> str:
    """Render the Details modal's centered header: full name (wrapped,
    never truncated), state/completion, and a wide progress bar.

    The raw qBittorrent state stays visible, but only as a dim
    secondary line -- never the primary rendering (see `_state_label`).
    """
    name = _wrap_name_at_separators(torrent.name, name_width)
    label = _state_label(torrent.state)
    style = _STATE_STYLES.get(label, "")
    status = (
        f"{label.upper()} · COMPLETE"
        if torrent.progress >= 1.0
        else label.upper()
    )
    bar = _progress_bar(torrent.progress, width=_DETAILS_PROGRESS_BAR_WIDTH)
    percent = _progress_percent_text(torrent.progress)
    lines = [
        f"[bold]{name}[/bold]",
        "",
        f"[{style}]{status}[/{style}]",
        f"[dim]{torrent.state}[/dim]",
        "",
        f"{bar} {percent}",
    ]
    return "\n".join(lines)


def _format_details_metric_row(pairs: tuple[tuple[str, str], ...]) -> str:
    """Render one muted-label row + one bold-value row of a metric
    grid, columns separated by a fixed 2-3 column gap and each column
    as wide as its own label/value -- so a value centers directly
    under its own label regardless of neighbouring columns' widths.

    Column widths/centering are computed on the plain label/value text
    *before* Rich markup is added (never on a markup-bearing string --
    markup tags are zero-width once rendered, but not under `len()`).
    """
    widths = [max(len(label), len(value)) for label, value in pairs]
    label_line = _METRIC_COLUMN_GAP.join(
        label.center(width)
        for (label, _value), width in zip(pairs, widths, strict=True)
    )
    value_line = _METRIC_COLUMN_GAP.join(
        value.center(width)
        for (_label, value), width in zip(pairs, widths, strict=True)
    )
    return f"[dim]{label_line}[/dim]\n[bold]{value_line}[/bold]"


def _format_details_metrics(torrent: TorrentSnapshot) -> str:
    """Render the Details modal's centered metric grid + hash line."""
    row1 = (
        ("Progress", _progress_percent_text(torrent.progress)),
        ("Ratio", f"{torrent.ratio:.2f}"),
        ("Category", format_category_label(torrent.category)),
    )
    row2 = (
        ("Download", _format_byte_rate(torrent.download_rate)),
        ("Upload", _format_byte_rate(torrent.upload_rate)),
        ("Size", _format_bytes(torrent.size)),
    )
    hash_line = f"[dim]Hash[/dim]  [bold]{_shorten_hash(torrent.hash)}[/bold]"
    return "\n\n".join(
        (
            _format_details_metric_row(row1),
            _format_details_metric_row(row2),
            hash_line,
        )
    )


_TRACKER_LINE_GAP = "   "


def _format_details_tracker_line(endpoint: dict[str, Any]) -> str:
    """Render one safe, structural tracker endpoint as one line, its
    identity and health kept close together (a fixed gap, not padded
    out to a shared column width across every row) -- an optional
    sanitized message follows on its own indented line, only if useful.

    Never a raw URL, path, query value, userinfo, or passkey.
    `DISABLED` mechanisms (DHT/PeX/LSD) render muted, not as an error.
    """
    identity = _truncate(str(endpoint["tracker"]), 24)
    health = str(endpoint["health"])
    glyph = _TRACKER_GLYPHS.get(health, "○")
    style = _TRACKER_STYLES.get(health, "dim")
    label = health.title()
    line = (
        f"[{style}]{glyph}[/{style}] {identity}{_TRACKER_LINE_GAP}"
        f"[{style}]{label}[/{style}]"
    )
    message = endpoint.get("message")
    if message and health != "disabled":
        line += f"\n  [dim]{message}[/dim]"
    return line


def _format_peer_discovery_entry(entry: dict[str, Any]) -> str:
    """Render one peer-discovery mechanism as `Name state`, muted."""
    health = str(entry["health"])
    style = _TRACKER_STYLES.get(health, "dim")
    return f"[{style}]{entry['mechanism']} {health.title()}[/{style}]"


def _format_details_trackers(
    tracker_details: list[dict[str, Any]] | None,
    fetched_at: datetime | None,
    *,
    fetch_failed: bool = False,
    peer_discovery: list[dict[str, Any]] | None = None,
) -> str:
    """Render the Details modal's Trackers section from already-
    sanitized endpoints.

    `tracker_details` being `None` means the fetch hasn't resolved yet
    -- either still in flight (a loading line) or failed (`fetch_failed`,
    a distinct message) -- never left indistinguishable from "loading",
    which would otherwise hang forever with no retry hint.
    """
    if tracker_details is None:
        if fetch_failed:
            return (
                "[dim]Couldn't load tracker details -- press r to retry.[/dim]"
            )
        return "[dim]Loading tracker details...[/dim]"

    if not tracker_details:
        lines = ["[dim](no trackers)[/dim]"]
    else:
        lines = [
            _format_details_tracker_line(entry) for entry in tracker_details
        ]

    # DHT/PeX/LSD are peer-discovery mechanisms, not trackers: they are
    # never listed or counted above, but an operator still wants to know
    # whether they are on, so they get their own line.
    if peer_discovery:
        lines.append("")
        lines.append(
            "[dim]Peer discovery[/dim]  "
            + "  ".join(
                _format_peer_discovery_entry(entry) for entry in peer_discovery
            )
        )
    if fetched_at is not None:
        lines.append("")
        lines.append(
            f"[dim]Last fetched {_format_local_time(fetched_at)}[/dim]"
        )
    return "\n".join(lines)


_DETAILS_MODAL_ACTIONS: tuple[tuple[str, str], ...] = (
    ("Esc", "Close"),
    ("c", "Copy hash"),
    ("e", "Explain"),
    ("r", "Refresh"),
)


def _format_details_modal_footer() -> str:
    """The Details modal's own compact command-hint footer."""
    return _format_command_bar(list(_DETAILS_MODAL_ACTIONS))


def _format_command_entry(key_display: str, description: str) -> str:
    """One `[key→Description]` command token shared by the global
    command bar and the Details contextual-actions line: warm-accent
    key, muted brackets/arrow/description. The opening `[` is escaped
    (`\\[`) since it would otherwise open a Rich markup tag; a lone
    `]` needs no escaping (Rich only treats it as markup immediately
    after a matching unescaped `[`)."""
    return (
        f"[dim]\\[[/dim][{_BRAND_ACCENT}]{key_display}[/{_BRAND_ACCENT}]"
        f"[dim]→{description}][/dim]"
    )


def _format_command_value_entry(label: str, value: str) -> str:
    """One `|label: value|` footer token -- pipe-delimited, distinct
    from `_format_command_entry`'s `[key→description]` bracket
    convention, for a *live, currently-focused* token with no key to
    bind (the live search text, the running total): accent label, dim
    `: value`."""
    return (
        f"[dim]|[/dim][{_BRAND_ACCENT}]{label}:[/{_BRAND_ACCENT}]"
        f"[dim] {value}|[/dim]"
    )


def _format_command_bar(entries: list[tuple[str, str]]) -> str:
    """Render a compact command bar from already-filtered
    `(key_display, description)` pairs, in order.

    Never a second, manually maintained command inventory: the caller
    (`QbitOpsTuiApp`/`CommandBar`) derives `entries` from the live
    `Screen.active_bindings`, the same source `Footer` itself reads.
    """
    return " ".join(
        _format_command_entry(key, description) for key, description in entries
    )


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
    # (`qbit_core.features.explain.build_torrent_explanation`), always
    # the finding's own `explanation` -- printing both would show the
    # same sentence twice. Only show the summary here when it says something the
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
    plan: BulkTorrentActionPlan,
    snapshot_at: datetime | None,
    *,
    stale: bool = False,
) -> str:
    """Render a frozen `BulkTorrentActionPlan` for the Preview modal.

    Display-only: reads `plan`'s already-computed counts/changes/skips,
    never recomputes or rescans. `snapshot_at` is the refresh the
    plan's data came from, shown for freshness -- never "now". `stale`
    adds a warning and disables Apply; the preview itself stays fully
    readable.
    """
    action_label = plan.action.title()
    satisfied, not_found = _split_skips(plan)
    lines = [
        f"[bold]{action_label} · Preview[/bold]",
        "",
        f"Selected             {plan.matched}",
        f"Will {plan.action:<10}     {len(plan.changes)}",
        f"Already satisfied    {len(satisfied)}",
        f"Not found            {len(not_found)}",
    ]
    if snapshot_at is not None:
        lines.append(f"Snapshot             {_format_local_time(snapshot_at)}")
    if stale:
        lines.append("")
        lines.append(
            "[bold yellow]Snapshot stale[/bold yellow] -- qBittorrent is "
            "currently unreachable; this preview uses last-known data."
        )
        lines.append("Apply disabled — rebuild the preview after reconnection.")
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


_ERROR_HEADINGS: dict[ErrorCategory, tuple[str, str]] = {
    ErrorCategory.CONFIGURATION: (
        "[bold red]Configuration invalid[/bold red]",
        "Fix .env and restart qbit-ops. This is a local configuration "
        "problem -- neither a software defect nor a remote failure.",
    ),
    ErrorCategory.AUTHENTICATION: (
        "[bold red]Authentication failed[/bold red]",
        "Check QBIT_USER/QBIT_PASSWORD. Nothing was submitted.",
    ),
    ErrorCategory.UNAVAILABLE: (
        "[bold yellow]Unavailable[/bold yellow]",
        "qBittorrent could not be reached, so nothing was confirmed "
        "submitted.",
    ),
    ErrorCategory.INTERNAL: (
        "[bold red]Internal error[/bold red]",
        "This is a qbit-ops defect, not a remote failure. Nothing was "
        "confirmed submitted.",
    ),
}


def _format_result_text(outcome: MutationUiResult) -> str:
    """Render one truthful `MutationUiResult`.

    Never claims more certainty than qBittorrent's bulk endpoints can:
    APPLIED means *submitted*, not a per-hash confirmation. NO_MATCH
    ("not found") and NO_CHANGES ("already fine") are kept distinct.
    """
    if outcome.error_category is not None:
        heading, explanation = _ERROR_HEADINGS[outcome.error_category]
        message = outcome.error_message or ""
        planned = len(outcome.planned_hashes)
        return (
            f"{heading}\n\n{message}\n\n{explanation}\n\n"
            f"The frozen plan ({planned} torrent(s)) is unchanged and "
            "remains inspectable. Rebuild the preview before retrying: "
            "it is now grounded in a stale snapshot."
        )

    if outcome.status is MutationStatus.NO_MATCH:
        return (
            "[bold]Nothing to do[/bold]\n\n"
            "No selected torrents were found in the current snapshot.\n"
            f"{len(outcome.not_found_hashes)} selected torrent(s) had "
            "disappeared before the plan was built."
        )

    if outcome.status is MutationStatus.NO_CHANGES:
        lines = [
            "[bold]No changes[/bold]",
            "",
            f"{len(outcome.satisfied_hashes)} selected torrent(s) already "
            f"satisfied '{outcome.action}'.",
        ]
        if outcome.not_found_hashes:
            lines.append(
                f"{len(outcome.not_found_hashes)} were not found in the "
                "current snapshot."
            )
        return "\n".join(lines)

    if outcome.cancelled_before_dispatch:
        return (
            "[bold]Cancelled before dispatch[/bold]\n\n"
            "qbit-ops shut down while this action was queued behind "
            "another qBittorrent operation, so it was abandoned before "
            "being sent.\n\n"
            f"No request reached qBittorrent: 0 of "
            f"{len(outcome.planned_hashes)} planned torrent(s) were "
            "submitted, and nothing was changed."
        )

    if outcome.status is MutationStatus.CANCELLED:
        return "[bold]Cancelled[/bold]\n\nNothing was submitted."

    # APPLIED
    lines = [
        "[bold green]Submitted[/bold green]",
        "",
        f"Action submitted for {len(outcome.submitted_hashes)} torrent(s).",
        "A refresh will show the latest observable state.",
    ]
    if outcome.satisfied_hashes:
        lines.append("")
        lines.append(
            f"{len(outcome.satisfied_hashes)} already satisfied "
            f"'{outcome.action}'."
        )
    if outcome.not_found_hashes:
        lines.append(
            f"{len(outcome.not_found_hashes)} were not found in the "
            "current snapshot."
        )
    return "\n".join(lines)


def _format_last_action_line(outcome: MutationUiResult) -> str:
    """One compact, persistent line summarising the latest mutation.

    Reuses the Result modal's vocabulary ("submitted", never "applied
    to each torrent"); short enough to stay readable at 80 columns.
    """
    when = _format_local_time(outcome.completed_at)
    action = outcome.action.title()

    if outcome.cancelled_before_dispatch:
        summary = "cancelled before dispatch (nothing sent)"
    elif outcome.error_category is not None:
        summary = f"failed · {outcome.error_category.value}"
    elif outcome.status is MutationStatus.APPLIED:
        summary = f"submitted for {len(outcome.submitted_hashes)} torrent(s)"
    elif outcome.status is MutationStatus.NO_MATCH:
        summary = "no selected torrents found"
    elif outcome.status is MutationStatus.NO_CHANGES:
        summary = (
            f"no change needed ({len(outcome.satisfied_hashes)} already "
            "satisfied)"
        )
    else:
        summary = "cancelled"

    return f"[dim]Last action ·[/dim] {action} {summary} [dim]· {when}[/dim]"


def _format_result_notification(outcome: MutationUiResult) -> str:
    """One-line fallback shown when the Result modal cannot be reached
    -- a submitted mutation must never vanish silently."""
    if outcome.cancelled_before_dispatch:
        return (
            f"{outcome.action.title()} cancelled before dispatch -- "
            "nothing was sent to qBittorrent."
        )
    if outcome.error_category is not None:
        return (
            f"{outcome.action.title()} failed "
            f"({outcome.error_category.value}): "
            f"{outcome.error_message or 'no detail available'}"
        )
    if outcome.status is MutationStatus.APPLIED:
        return (
            f"{outcome.action.title()} submitted for "
            f"{len(outcome.submitted_hashes)} torrent(s)."
        )
    if outcome.status is MutationStatus.NO_MATCH:
        return (
            f"{outcome.action.title()}: no selected torrents were found "
            "in the current snapshot."
        )
    return (
        f"{outcome.action.title()}: no changes needed "
        f"({len(outcome.satisfied_hashes)} already satisfied)."
    )


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
# `qbit_core.features.explain._build_torrent_finding`'s `common_evidence` tuple.
_RATE_EVIDENCE_CODES = frozenset({"download_rate", "upload_rate"})


def _format_evidence(evidence: Evidence) -> str:
    """Render one evidence row with a humanized value where possible.

    Purely cosmetic, keyed off the stable `evidence.code` (never
    inferred from the label text) -- never changes the underlying
    `Evidence`/JSON model or invents a value it doesn't already have.
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
