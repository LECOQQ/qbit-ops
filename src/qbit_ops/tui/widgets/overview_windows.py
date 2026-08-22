"""The Overview's two bordered windows: ᴛʀᴀᴄᴋᴇʀꜱ and ꜱᴇꜱꜱɪᴏɴ.

Both read only what the periodic refresh already fetched, so neither
costs an API call of its own.

The Trackers window states its own limits, because it shows a *derived*
truth: activity is inferred from torrent rates, not read from an
announce. Four devices keep it from being mistaken for the real thing --
a glyph alphabet disjoint from `TrackerHealth`'s, a title that names the
derivation, a last line naming what is not read here, and the Trackers
page keeping the announce status for itself.
"""

from __future__ import annotations

from rich.text import Text
from textual import events
from textual.widgets import Static

from qbit_core.features.status import InstanceStats
from qbit_ops.tui.dots import dot_sparkline
from qbit_ops.tui.formatting import (
    DOWN_RATE_ACCENT,
    IDLE_RATE_STYLE,
    UP_RATE_ACCENT,
    _format_byte_rate,
    _format_bytes,
)
from qbit_ops.tui.state import (
    GRAPH_SLOTS,
    LibraryBreakdown,
    RateHistory,
    TrackerActivity,
    TrackerActivityKind,
    TrackerBreakdown,
    TuiState,
)

# One arrow alphabet for the whole page. Seeding *is* uploading and
# leeching *is* downloading, so the graph's `↑`/`↓` and a second set of
# triangles were two shapes for one physical fact. The arrow now carries
# only the direction; the word beside it carries the state, which is the
# part `↕` cannot say.
#
# Still disjoint from `formatting._TRACKER_GLYPHS` (`● ! ○`): an
# operator who has learnt one alphabet can never read a row of the other
# by resemblance. Asserted in `tests/test_tui_overview_windows.py`.
TRACKER_ACTIVITY_GLYPHS: dict[TrackerActivityKind, str] = {
    TrackerActivityKind.BOTH: "↕",
    TrackerActivityKind.SEEDING: "↑",
    TrackerActivityKind.LEECHING: "↓",
    TrackerActivityKind.IDLE: "·",
    TrackerActivityKind.ERRORED: "✕",
    TrackerActivityKind.UNKNOWN: "?",
}

_ACTIVITY_STYLES: dict[TrackerActivityKind, str] = {
    TrackerActivityKind.BOTH: "",
    TrackerActivityKind.SEEDING: UP_RATE_ACCENT,
    TrackerActivityKind.LEECHING: DOWN_RATE_ACCENT,
    TrackerActivityKind.IDLE: IDLE_RATE_STYLE,
    TrackerActivityKind.ERRORED: "red",
    TrackerActivityKind.UNKNOWN: IDLE_RATE_STYLE,
}

# The legend's reading order, and the order the footer lists counts in.
_LEGEND_ORDER: tuple[tuple[TrackerActivityKind, str], ...] = (
    (TrackerActivityKind.SEEDING, "seed"),
    (TrackerActivityKind.LEECHING, "leech"),
    (TrackerActivityKind.BOTH, "both"),
    (TrackerActivityKind.IDLE, "idle"),
    (TrackerActivityKind.ERRORED, "err"),
    (TrackerActivityKind.UNKNOWN, "unknown"),
)

# One word, like ꜱᴇꜱꜱɪᴏɴ facing it. The border used to add "derived from
# torrent activity", which said the same thing as the window's own last
# line eleven rows below -- and said it less precisely, since the last
# line names what is *not* read and sits against the data it qualifies.
TRACKERS_TITLE = "Trackers"
# The window names what it does not know, on its own last line: "idle"
# here means "moving nothing", never "not announcing".
#
# It deliberately points nowhere. The line first read "see Trackers
# (3/k)", and both halves were false -- there is no Trackers workspace,
# and `k` is bound to `cursor_up`, so following the instruction moved a
# table cursor. An announcement is only worth making when the gesture
# it names exists and does what it says.
TRACKERS_DISCLAIMER = "announce status not read here"
SESSION_TITLE = "Session"

_ACTIVITY_WIDTH = 8
# Twelve columns is the widest a rate can print: `_format_bytes` only
# steps up a unit at 1024, so "1023.9 KiB/s" is the longest string it
# can produce. A narrower column would not truncate -- it would push
# every column to its right out of line.
_RATE_WIDTH = 12
_ERR_WIDTH = 3
_NAME_MIN_WIDTH = 12
# One row is: a space, the glyph, a space, the name, a space, the
# activity, a space, each rate, a space, the error count between two
# spaces, then the sparkline. Everything but the name is fixed, and the
# name absorbs what is left.
_FIXED_ROW_WIDTH = (
    1
    + 1
    + 1
    + 1
    + _ACTIVITY_WIDTH
    + 1
    + _RATE_WIDTH
    + 1
    + _RATE_WIDTH
    + 1
    + _ERR_WIDTH
    + 1
    + GRAPH_SLOTS
)

# Header, blank, then the four footer lines the window always keeps.
_TRACKER_CHROME_ROWS = 6

_LABEL_WIDTH = 13

# Two lines per list. The window's other fourteen rows are worth more
# than a complete inventory of category names.
_MAX_VALUE_LINES = 2

# The three markers `connection_status` can carry, each with its own
# glyph *and* its own word. A firewalled instance is a degraded success
# -- it talks to trackers but receives nothing -- and reading it as
# "Connected" would hide the exact cause of a flat upload graph.
_CONNECTION_MARKERS: dict[str, tuple[str, str, str]] = {
    "connected": ("●", "Connected", "bold green"),
    "firewalled": ("◐", "Firewalled", "bold yellow"),
    "disconnected": ("○", "Disconnected", "bold red"),
}
_UNKNOWN_CONNECTION_MARKER = ("?", "Unknown", "bold yellow")

_UNAVAILABLE = "unavailable"
_NOT_COMPUTED = "–"  # ai-hygiene: allow-em-dash
_NO_LIMIT = "off"


class TrackersWindow(Static):
    """Per-tracker activity, attributed from the torrent list."""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._breakdown: TrackerBreakdown | None = None
        self._history = RateHistory()

    def render_state(self, state: TuiState) -> None:
        self._breakdown = state.tracker_breakdown
        self._history = state.rate_history
        self._repaint()

    def _on_resize(self, event: events.Resize) -> None:
        self._repaint()

    def _repaint(self) -> None:
        self.update(
            build_trackers_window(
                self._breakdown,
                self._history,
                width=self.size.width,
                height=self.size.height,
            )
        )


class SessionWindow(Static):
    """The instance and the library, side by side with the trackers."""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._state: TuiState | None = None

    def render_state(self, state: TuiState) -> None:
        self._state = state
        self.update(build_session_window(state, width=self.size.width))

    def _on_resize(self, event: events.Resize) -> None:
        if self._state is not None:
            self.render_state(self._state)


def build_trackers_window(
    breakdown: TrackerBreakdown | None,
    history: RateHistory,
    *,
    width: int,
    height: int,
) -> Text:
    """Render the Trackers window's body at the size it was given."""
    text = Text()
    if breakdown is None:
        text.append("Waiting for the first refresh", style=IDLE_RATE_STYLE)
        return text

    name_width = max(width - _FIXED_ROW_WIDTH, _NAME_MIN_WIDTH)
    budget = max(height - _TRACKER_CHROME_ROWS, 1)
    shown = breakdown.rows[:budget]
    hidden = len(breakdown.rows) - len(shown)
    peak = history.tracker_peak

    text.append(_header_row(name_width), style=IDLE_RATE_STYLE)
    text.append("\n\n")
    for row in shown:
        _append_tracker_row(text, row, history, peak, name_width)
    if hidden > 0:
        # The count, not a route: it is true that more rows exist, and
        # nothing here can yet show them.
        text.append(f" + {hidden} more\n", style=IDLE_RATE_STYLE)

    text.append("\n")
    _append_tracker_footer(text, breakdown)
    # In place, and it returns `None` -- the trailing newline every row
    # writes would otherwise cost the window a phantom last line.
    text.rstrip()
    return text


def _header_row(name_width: int) -> str:
    return (
        f"   {'tracker':<{name_width}} {'activity':<{_ACTIVITY_WIDTH}} "
        f"{'up':>{_RATE_WIDTH}} {'down':>{_RATE_WIDTH}} "
        f"{'err':>{_ERR_WIDTH}} 60s"
    )


def _append_tracker_row(
    text: Text,
    row: TrackerActivity,
    history: RateHistory,
    peak: int,
    name_width: int,
) -> None:
    activity = row.kind
    style = _ACTIVITY_STYLES[activity]
    text.append(" ")
    text.append(TRACKER_ACTIVITY_GLYPHS[activity], style=style)
    text.append(f" {_ellipsize(row.label, name_width):<{name_width}} ")
    text.append(f"{activity.value:<{_ACTIVITY_WIDTH}}", style=style)
    text.append(" ")
    # The same rule as everywhere else: a direction that is moving gets
    # its own hue, one that is not gets none. No "both" branch anywhere.
    _append_rate(text, row.upload_rate, UP_RATE_ACCENT)
    text.append(" ")
    _append_rate(text, row.download_rate, DOWN_RATE_ACCENT)
    text.append(
        f" {row.errored:>{_ERR_WIDTH}} ",
        style="red" if row.errored else IDLE_RATE_STYLE,
    )
    samples = history.tracker(row.key)
    text.append(
        dot_sparkline(_normalized(samples, peak), GRAPH_SLOTS), style=style
    )
    text.append("\n")


def _normalized(samples: tuple[int, ...], peak: int) -> list[float]:
    if peak <= 0:
        return [0.0 for _ in samples]
    return [min(1.0, value / peak) for value in samples]


def _append_rate(text: Text, rate: int, accent: str) -> None:
    style = accent if rate > 0 else IDLE_RATE_STYLE
    shown = _format_byte_rate(rate) if rate > 0 else "0"
    text.append(f"{shown:>{_RATE_WIDTH}}", style=style)


def _append_tracker_footer(text: Text, breakdown: TrackerBreakdown) -> None:
    tracker_word = "tracker" if len(breakdown.rows) == 1 else "trackers"
    torrent_word = "torrent" if breakdown.torrents == 1 else "torrents"
    text.append(
        f" {len(breakdown.rows)} {tracker_word} · "
        f"{breakdown.torrents} {torrent_word}   "
        f"exclusive {breakdown.exclusive} · shared {breakdown.shared}\n",
        style=IDLE_RATE_STYLE,
    )

    text.append(" ")
    for kind, word in _LEGEND_ORDER:
        text.append(TRACKER_ACTIVITY_GLYPHS[kind], style=_ACTIVITY_STYLES[kind])
        text.append(
            f" {word} {breakdown.count_by_kind(kind)}   ",
            style=IDLE_RATE_STYLE,
        )
    text.append("\n")
    text.append(f" {TRACKERS_DISCLAIMER}", style=IDLE_RATE_STYLE)


def _ellipsize(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    return value[: max(width - 1, 0)] + "…"


def build_session_window(state: TuiState, *, width: int) -> Text:
    """Render the Session window's body: the instance and the library."""
    text = Text()
    status = state.status
    if status is None:
        text.append("Waiting for the first refresh", style=IDLE_RATE_STYLE)
        return text

    _append_torrent_counts(text, state)
    text.append("\n")
    _append_library(text, state.library_breakdown, width)
    text.append("\n")
    _append_instance(text, state.instance_stats)
    text.rstrip()
    return text


def _append_torrent_counts(text: Text, state: TuiState) -> None:
    status = state.status
    assert status is not None
    counts = status.counts
    if counts.total == 0:
        _row(text, "Torrents", "none yet")
        return

    incomplete = max(counts.total - counts.completed, 0)
    _row(text, "Torrents", f"{counts.total} total")
    for left, left_value, right, right_value in (
        ("complete", counts.completed, "incomplete", incomplete),
        ("seeding", counts.seeding, "downloading", counts.downloading),
        ("stopped", state.stopped_count, "checking", counts.checking),
        ("errored", counts.errored, "stalled", counts.stalled),
    ):
        text.append(
            f"   {left:<10} {left_value:>3}   "
            f"{right:<12} {right_value:>3}\n"
        )


def _append_library(
    text: Text, library: LibraryBreakdown | None, width: int
) -> None:
    if library is None or library.total_size_bytes == 0:
        _row(text, "Size", _NOT_COMPUTED)
        _row(text, "Categories", _NOT_COMPUTED)
        _row(text, "Tags", _NOT_COMPUTED)
        return

    _row(text, "Size", f"{_format_bytes(library.total_size_bytes)} on disk")
    _wrapped_row(text, "Categories", library.categories, width)
    tags = list(library.tags)
    if library.untagged:
        tags.append(("untagged", library.untagged))
    _wrapped_row(text, "Tags", tuple(tags), width)


def _wrapped_row(
    text: Text,
    label: str,
    counts: tuple[tuple[str, int], ...],
    width: int,
) -> None:
    """One label with a `name count` list, folded and then capped.

    Capped, not merely folded: this window's content is a fixed height,
    so an unbounded list does not push the rows below it down -- it
    pushes them *out*, and `Free space` and `Queueing` are the first to
    go. `counts` arrives busiest-first, so what the cap drops is always
    what mattered least.

    `+ N more` is laid out as an entry of its own rather than appended
    afterwards, and `N` is read back off what actually fitted. Appending
    it to a finished last line silently displaced two entries the count
    then failed to mention.
    """
    if not counts:
        _row(text, label, _NOT_COMPUTED)
        return

    budget = max(width - _LABEL_WIDTH - 1, 12)
    entries = [f"{name} {count}" for name, count in counts]

    lines: list[str] = []
    for shown in range(len(entries), -1, -1):
        hidden = len(entries) - shown
        items = entries[:shown]
        if hidden:
            items = [*items, f"+ {hidden} more"]
        folded = _fold(items, budget)
        if folded is not None:
            lines = folded
            break

    _row(text, label, lines[0] if lines else _NOT_COMPUTED)
    for continuation in lines[1:]:
        _row(text, "", continuation)


def _fold(items: list[str], budget: int) -> list[str] | None:
    """`items` joined into at most `_MAX_VALUE_LINES` lines, or `None`."""
    lines: list[str] = []
    current = ""
    for item in items:
        candidate = item if not current else f"{current} · {item}"
        if len(candidate) > budget and current:
            lines.append(current)
            current = item
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) > _MAX_VALUE_LINES or any(
        len(line) > budget for line in lines
    ):
        return None
    return lines


def _append_instance(text: Text, stats: InstanceStats | None) -> None:
    if stats is None:
        _row(text, "Ratio", _NOT_COMPUTED)
        return

    _row(text, "Ratio", _format_ratio(stats.all_time_ratio))
    _row(
        text,
        "All-time",
        _pair(stats.all_time_downloaded_bytes, stats.all_time_uploaded_bytes),
    )
    _row(
        text,
        "Since start",
        _pair(stats.session_downloaded_bytes, stats.session_uploaded_bytes),
    )
    text.append("\n")
    _row(
        text,
        "Peers",
        f"{stats.connected_peers} · DHT {stats.dht_nodes} nodes",
    )
    _row(
        text,
        "Limits",
        f"↓ {_format_limit(stats.download_rate_limit):<11} "
        f"↑ {_format_limit(stats.upload_rate_limit)}",
    )
    _row(text, "Alt limits", _on_off(stats.alternative_limits_enabled))
    _row(text, "Free space", _format_free_space(stats.free_space_bytes))
    _row(text, "Queueing", _on_off(stats.queueing_enabled))


def _pair(down: int, up: int) -> str:
    return f"↓ {_format_bytes(down):<11} ↑ {_format_bytes(up)}"


def _row(text: Text, label: str, value: str) -> None:
    text.append(f" {label:<{_LABEL_WIDTH}}", style=IDLE_RATE_STYLE)
    text.append(f"{value}\n")


def _format_ratio(ratio: float | None) -> str:
    """`None` means qBittorrent has no ratio computed yet -- which is
    never the same claim as a ratio of zero."""
    if ratio is None:
        return f"{_NOT_COMPUTED}            not computed yet"
    return f"{ratio:.2f}"


def _format_free_space(free_space_bytes: int | None) -> str:
    """`None` means qBittorrent could not measure the disk.

    Never a size: the wire value is `-1`, a plain integer an ordinary
    byte formatter renders as "-1 B" without complaint -- the quietest
    of the three markers this screen has to refuse.
    """
    if free_space_bytes is None:
        return _UNAVAILABLE
    return _format_bytes(free_space_bytes)


def _format_limit(limit: int) -> str:
    """`0` is qBittorrent's encoding for "no cap", a real value."""
    if limit <= 0:
        return _NO_LIMIT
    return _format_byte_rate(limit)


def _on_off(enabled: bool) -> str:
    return "on" if enabled else _NO_LIMIT


def connection_marker(
    stats: InstanceStats | None,
) -> tuple[str, str, str]:
    """The glyph, word and style for the instance's connection status.

    Every status gets a glyph *and* a spelled-out word: the glyph alone
    would need to be learnt, and the word alone would not be findable at
    a glance. An unreported status says so rather than borrowing
    "Connected", which is the false claim this replaces.
    """
    if stats is None or stats.connection_status is None:
        return _UNKNOWN_CONNECTION_MARKER
    return _CONNECTION_MARKERS.get(
        stats.connection_status.lower(), _UNKNOWN_CONNECTION_MARKER
    )
