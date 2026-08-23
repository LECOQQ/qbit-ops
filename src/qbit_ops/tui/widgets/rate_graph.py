"""The Overview's mirrored transfer graph, one column per second.

Fills the band the wordmark reserved and never used, so it costs the
page no line of its own. Download grows upward from the axis, upload
downward, in the same four dot steps -- see `qbit_ops.tui.dots`.

**The window decides the plot, not the panel** (see `plot_slots`): one
sample per second and one column per sample, normally sixty of them. A
panel too narrow for the whole window shows only the trailing seconds
that fit, and the axis label always says that real number -- it is
never rounded to a nicer one.

The scale is relative to the window's own peak, which is what keeps the
panel from being a flat line on a steady seedbox. The consequence is
deliberate and is the reason the peak label may never be dropped: a
minute at 30 KiB/s and a minute at 30 MiB/s draw exactly the same
trace, and only the label tells them apart.
"""

from __future__ import annotations

from rich.text import Text
from textual import events
from textual.widgets import Static

from qbit_ops.tui.dots import (
    BarLayout,
    dot_axis,
    dot_bars,
    fit_bars,
    measured_runs,
    normalize,
)
from qbit_ops.tui.formatting import (
    DOWN_RATE_ACCENT,
    IDLE_RATE_STYLE,
    UP_RATE_ACCENT,
    _format_byte_rate,
)
from qbit_ops.tui.state import (
    GRAPH_MIN_SLOTS,
    GRAPH_SAMPLE_INTERVAL_SECONDS,
    GRAPH_WINDOW_SLOTS,
    RateHistory,
)

# Three lines per direction: twelve dot rows each way, an exactly
# symmetric mirror. A fourth would not improve the reading and the band
# the wordmark frees is only nine lines tall.
GRAPH_ROWS_PER_DIRECTION = 3

# `NNNN`, one space, then the axis character itself. This is the
# *minimum* gutter: whatever the panel has over the sixty columns the
# window needs is added to it, so the plot stays sixty wide and still
# reaches the right edge.
_SCALE_LABEL_WIDTH = 4
_MIN_AXIS_GUTTER = _SCALE_LABEL_WIDTH + 2  # label, space, axis

_AXIS_TICK = "┤"
_AXIS_ORIGIN = "┼"

# The direction is named on the row that touches the axis, not only on
# the summary lines four rows away: the eye reading the trace is at the
# axis, and that is where it must be able to tell one half from the
# other.
_DOWN_ARROW = "↓"
_UP_ARROW = "↑"

# Tick marks are bracketed so they read as bounds on the rule rather
# than as words that happen to sit in it.
_TICK_OPEN = "|"
_TICK_CLOSE = "|"


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
    slots = plot_slots(width)
    gutter = max(width - slots, _MIN_AXIS_GUTTER) if slots else _MIN_AXIS_GUTTER
    downloads, uploads = history.window(slots)
    peak_down = max(
        (value for value in downloads if value is not None), default=0
    )
    peak_up = max((value for value in uploads if value is not None), default=0)

    text = Text()
    _append_line(
        text, gutter, _summary_line("↓", downloads, peak_down, DOWN_RATE_ACCENT)
    )
    if slots:
        _append_plot(
            text,
            downloads=downloads,
            uploads=uploads,
            gutter=gutter,
            layout=fit_bars(slots, slots, max_bar_width=1, min_gap=0),
            peak_down=peak_down,
            peak_up=peak_up,
        )
    _append_line(
        text, gutter, _summary_line("↑", uploads, peak_up, UP_RATE_ACCENT)
    )
    return text


def plot_slots(width: int) -> int:
    """Seconds the plot shows at `width`, or 0 when it is dropped.

    Normally `GRAPH_WINDOW_SLOTS` -- the window fixes the plot, not the
    panel. A panel too narrow for the whole window shows the trailing
    seconds that do fit, and the axis label then says *that* number:
    the label never stops being the truth, it just has less to tell.
    """
    room = max(width - _MIN_AXIS_GUTTER, 0)
    if room < GRAPH_MIN_SLOTS:
        return 0
    return min(GRAPH_WINDOW_SLOTS, room)


def _append_plot(
    text: Text,
    *,
    downloads: list[int | None],
    uploads: list[int | None],
    gutter: int,
    layout: BarLayout,
    peak_down: int,
    peak_up: int,
) -> None:
    down_style = DOWN_RATE_ACCENT if peak_down > 0 else IDLE_RATE_STYLE
    up_style = UP_RATE_ACCENT if peak_up > 0 else IDLE_RATE_STYLE

    down_rows = dot_bars(
        normalize(downloads, peak_down),
        GRAPH_ROWS_PER_DIRECTION,
        layout,
        from_top=False,
    )
    up_rows = dot_bars(
        normalize(uploads, peak_up),
        GRAPH_ROWS_PER_DIRECTION,
        layout,
        from_top=True,
    )

    # Outermost row of each half carries the scale, where the tallest bar
    # can reach; innermost row carries the direction, against the axis.
    for index, row in enumerate(down_rows):
        label = _scale_label(peak_down) if index == 0 else ""
        if index == len(down_rows) - 1:
            label = _DOWN_ARROW
        _append_row(text, gutter, label, _AXIS_TICK, row, down_style)

    _append_axis(text, downloads, gutter, layout)

    for index, row in enumerate(up_rows):
        label = _UP_ARROW if index == 0 else ""
        if index == len(up_rows) - 1:
            label = _scale_label(peak_up)
        _append_row(text, gutter, label, _AXIS_TICK, row, up_style)


def _append_axis(
    text: Text, samples: list[int | None], gutter: int, layout: BarLayout
) -> None:
    """The axis: complete from the first frame, dimmed where unmeasured."""
    rule = list(_with_ticks(dot_axis(layout), layout.span))
    measured = [value is not None for value in samples]

    text.append(f"{'0':>{gutter - 2}} ", style=IDLE_RATE_STYLE)
    text.append(_AXIS_ORIGIN, style=IDLE_RATE_STYLE)
    for start, end, is_measured in measured_runs(measured, layout):
        text.append(
            "".join(rule[start:end]),
            style=None if is_measured else IDLE_RATE_STYLE,
        )
    text.append("\n")


def _with_ticks(axis: str, span: int) -> str:
    """Write the window's three time marks over the axis rule.

    The marks are read off the width, not written beside it: the plot is
    one column per second, so `span` columns *is* the window in seconds.
    """
    seconds = round(span * GRAPH_SAMPLE_INTERVAL_SECONDS)
    marks = [
        (0, _bracket(f"-{seconds}s")),
        (None, _bracket(f"-{seconds // 2}s")),
        (-1, _bracket("now")),
    ]
    characters = list(axis)
    for anchor, label in marks:
        if len(label) > span:
            continue
        if anchor == 0:
            start = 0
        elif anchor == -1:
            start = span - len(label)
        else:
            start = (span - len(label)) // 2
        for offset, char in enumerate(label):
            if 0 <= start + offset < len(characters):
                characters[start + offset] = char
    return "".join(characters)


def _bracket(label: str) -> str:
    return f"{_TICK_OPEN}{label}{_TICK_CLOSE}"


def _append_row(
    text: Text,
    gutter: int,
    label: str,
    axis_char: str,
    body: str,
    style: str | None,
) -> None:
    text.append(f"{label:>{gutter - 2}} ", style=IDLE_RATE_STYLE)
    text.append(axis_char, style=IDLE_RATE_STYLE)
    if style is None:
        text.append(body)
    else:
        text.append(body, style=style)
    text.append("\n")


def _append_line(text: Text, gutter: int, line: Text) -> None:
    text.append(" " * gutter)
    text.append_text(line)
    text.append("\n")


def _summary_line(
    arrow: str, samples: list[int | None], peak: int, accent: str
) -> Text:
    """The peak/average line, or an honest admission that there is none.

    A window with no sample reports that it has none rather than an
    average of zero: "not measured yet" and "measured, and it was zero"
    are different claims.
    """
    style = accent if peak > 0 else IDLE_RATE_STYLE
    line = Text()
    line.append(f"{arrow} ", style=style)
    measured = [value for value in samples if value is not None]
    if not measured:
        line.append("no samples yet", style=IDLE_RATE_STYLE)
        return line

    average = sum(measured) // len(measured)
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
    """Columns the plot occupies at `width`, or 0 when it is dropped."""
    return plot_slots(width)
