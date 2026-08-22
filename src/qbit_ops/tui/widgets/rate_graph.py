"""The Overview's mirrored 60-second transfer graph.

Fills the band the wordmark reserved and never used, so it costs the
page no line of its own. Download grows upward from the axis, upload
downward, in the same four dot steps -- see `qbit_ops.tui.dots`.

The scale is relative to the window's own peak, which is what keeps the
panel from being a flat line on a steady seedbox. The consequence is
deliberate and is the reason the peak label may never be dropped: sixty
seconds at 30 KiB/s and sixty seconds at 30 MiB/s draw exactly the same
trace, and only the label tells them apart.
"""

from __future__ import annotations

from rich.text import Text
from textual import events
from textual.widgets import Static

from qbit_ops.tui.dots import BarLayout, dot_axis, dot_bars, fit_bars, normalize
from qbit_ops.tui.formatting import (
    DOWN_RATE_ACCENT,
    IDLE_RATE_STYLE,
    UP_RATE_ACCENT,
    _format_byte_rate,
)
from qbit_ops.tui.state import GRAPH_SLOTS, GRAPH_WINDOW_SECONDS, RateHistory

# Three lines per direction: twelve dot rows each way, an exactly
# symmetric mirror. A fourth would not improve the reading and the band
# the wordmark frees is only nine lines tall.
GRAPH_ROWS_PER_DIRECTION = 3

# At most four characters of scale, one space, then the axis character
# itself. The gutter is fixed so both halves of the mirror start at the
# same column whatever their peaks happen to be.
_SCALE_LABEL_WIDTH = 4
_AXIS_GUTTER = _SCALE_LABEL_WIDTH + 2

_AXIS_TICK = "┤"
_AXIS_ORIGIN = "┼"

# Below this the twelve slots cannot each get a column, so the plot is
# dropped and only the two summary lines are kept -- a squeezed trace
# would misreport the shape rather than merely show less of it.
_MIN_PLOT_SPAN = GRAPH_SLOTS

# Past this a bar stops reading as a trace and starts reading as a
# block; the columns beyond it go to the gaps.
_MAX_BAR_WIDTH = 6

# The direction is named on the row that touches the axis, not only on
# the summary lines four rows away: the eye reading a bar is at the
# axis, and that is where it must be able to tell one half from the
# other.
_DOWN_ARROW = "↓"
_UP_ARROW = "↑"


class RateGraph(Static):
    """Passive renderer of `TuiState.rate_history`.

    No qBittorrent call and no worker: the window it draws is filled by
    the app's own sampling timer. Re-renders on resize because the bar
    geometry is computed from the width it is actually given.
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._history = RateHistory()

    def render_state(self, history: RateHistory) -> None:
        self._history = history
        self._repaint()

    def _on_resize(self, event: events.Resize) -> None:
        self._repaint()

    def _repaint(self) -> None:
        self.update(build_rate_graph(self._history, width=self.size.width))


def build_rate_graph(history: RateHistory, *, width: int) -> Text:
    """Render one rate window as coloured text, top line first."""
    downloads = history.downloads
    uploads = history.uploads
    peak_down = max(downloads, default=0)
    peak_up = max(uploads, default=0)

    text = Text()
    _append_line(
        text, _summary_line("↓", downloads, peak_down, DOWN_RATE_ACCENT)
    )

    plot_width = max(width - _AXIS_GUTTER, 0)
    if plot_width >= _MIN_PLOT_SPAN:
        _append_plot(
            text,
            history=history,
            layout=fit_bars(
                plot_width, GRAPH_SLOTS, max_bar_width=_MAX_BAR_WIDTH
            ),
            peak_down=peak_down,
            peak_up=peak_up,
        )

    _append_line(text, _summary_line("↑", uploads, peak_up, UP_RATE_ACCENT))
    return text


def _append_plot(
    text: Text,
    *,
    history: RateHistory,
    layout: BarLayout,
    peak_down: int,
    peak_up: int,
) -> None:
    down_style = DOWN_RATE_ACCENT if peak_down > 0 else IDLE_RATE_STYLE
    up_style = UP_RATE_ACCENT if peak_up > 0 else IDLE_RATE_STYLE

    down_rows = dot_bars(
        normalize(history.downloads, peak_down),
        GRAPH_ROWS_PER_DIRECTION,
        layout,
        from_top=False,
    )
    up_rows = dot_bars(
        normalize(history.uploads, peak_up),
        GRAPH_ROWS_PER_DIRECTION,
        layout,
        from_top=True,
    )

    # Outermost row of each half carries the scale, where the tallest bar
    # can reach; innermost row carries the direction, against the axis.
    for index, row in enumerate(down_rows):
        last = index == len(down_rows) - 1
        label = _DOWN_ARROW if last else ""
        if index == 0:
            label = _scale_label(peak_down)
        _append_row(text, label, _AXIS_TICK, row, down_style)

    _append_row(
        text,
        "0",
        _AXIS_ORIGIN,
        _with_ticks(dot_axis(history.measured, layout)),
        None,
    )

    for index, row in enumerate(up_rows):
        last = index == len(up_rows) - 1
        label = _UP_ARROW if index == 0 else ""
        if last:
            label = _scale_label(peak_up)
        _append_row(text, label, _AXIS_TICK, row, up_style)


def _append_row(
    text: Text, label: str, axis_char: str, body: str, style: str | None
) -> None:
    text.append(f"{label:>{_SCALE_LABEL_WIDTH}} ", style=IDLE_RATE_STYLE)
    text.append(axis_char, style=IDLE_RATE_STYLE)
    if style is None:
        text.append(body)
    else:
        text.append(body, style=style)
    text.append("\n")


def _append_line(text: Text, line: Text) -> None:
    text.append(" " * _AXIS_GUTTER)
    text.append_text(line)
    text.append("\n")


def _with_ticks(axis: str) -> str:
    """Write the window's three time marks over the axis rule.

    The marks are fixed because the window is: twelve slots of five
    seconds is sixty seconds no matter what `--interval` is set to.
    """
    marks = [
        (0, f"-{GRAPH_WINDOW_SECONDS}s"),
        ((len(axis) - 4) // 2, f"-{GRAPH_WINDOW_SECONDS // 2}s"),
        (len(axis) - 3, "now"),
    ]
    characters = list(axis)
    for start, label in marks:
        if start < 0:
            continue
        for offset, char in enumerate(label):
            if start + offset < len(characters):
                characters[start + offset] = char
    return "".join(characters)


def _summary_line(
    arrow: str, samples: tuple[int, ...], peak: int, accent: str
) -> Text:
    """The peak/average line, or an honest admission that there is none.

    A window with no sample reports that it has none rather than an
    average of zero: "not measured yet" and "measured, and it was zero"
    are different claims.
    """
    style = accent if peak > 0 else IDLE_RATE_STYLE
    line = Text()
    line.append(f"{arrow} ", style=style)
    if not samples:
        line.append("no samples yet", style=IDLE_RATE_STYLE)
        return line

    average = sum(samples) // len(samples)
    line.append(f"peak {_format_byte_rate(peak)}", style=style)
    line.append(" · ", style=IDLE_RATE_STYLE)
    line.append(f"avg {_format_byte_rate(average)}", style=style)
    return line


_SCALE_UNITS = ("", "K", "M", "G", "T")


def _scale_label(peak: int) -> str:
    """The axis scale, short enough to fit beside the plot.

    Approximate by design, and never wider than the gutter reserves:
    one column too many would shift the whole plot right by one and
    break the mirror. The exact peak is spelled out in full on the
    summary line right above or below it.
    """
    if peak <= 0:
        return ""
    value = float(peak)
    index = 0
    while value >= 1024 and index < len(_SCALE_UNITS) - 1:
        value /= 1024
        index += 1
    unit = _SCALE_UNITS[index]
    if index == 0 or value >= 10:
        return f"{round(value)}{unit}"
    return f"{value:.1f}{unit}"


def graph_span(width: int) -> int:
    """Columns the plot occupies at `width`, or 0 when it is dropped.

    Equal to the panel's own plot area whenever a plot is drawn at all:
    the layout spends every column it is given.
    """
    plot_width = max(width - _AXIS_GUTTER, 0)
    if plot_width < _MIN_PLOT_SPAN:
        return 0
    return fit_bars(plot_width, GRAPH_SLOTS, max_bar_width=_MAX_BAR_WIDTH).span
