"""Braille dot-matrix bars, the alphabet the Overview's graph draws in.

A braille cell is a 2x4 dot grid, so it fills from the bottom *or* the
top with the same four steps -- which is the whole reason the mirrored
graph can be symmetric. Block glyphs cannot: `U+2581..U+2588` give
eight upward levels but only `▔ ▀ █` downward, a 2.7x asymmetry between
the two halves of the same picture.

    up   ⣀ ⣤ ⣶ ⣿        down   ⠉ ⠛ ⠿ ⣿

Pure text: no Rich, no Textual, no colour. Colour is applied by the
caller, since a glyph carries the magnitude and the hue carries the
direction.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# Ramps read from the axis outward: `UP_RAMP[0]` is the row of dots
# sitting *on* the axis, `DOWN_RAMP[0]` the row hanging from it.
UP_RAMP: tuple[str, ...] = ("⣀", "⣤", "⣶", "⣿")
DOWN_RAMP: tuple[str, ...] = ("⠉", "⠛", "⠿", "⣿")

DOT_ROWS_PER_CELL = 4

BLANK = " "

# The axis is drawn only under a slot that was actually measured, so a
# not-yet-recorded slot never reads as a recorded zero.
AXIS_RULE = "─"


def dot_column(
    value: float | None, rows: int, *, from_top: bool = False
) -> list[str]:
    """Render one bar as `rows` glyphs, top row first.

    `value` is already normalized to `0.0..1.0`. Any non-zero value
    floors to a single dot row: without that, a tracker moving 26 KiB/s
    beside one moving 3 MiB/s would draw the identical blank column a
    stopped tracker draws, and "slow" would be indistinguishable from
    "off".

    `None` is a second nobody measured. It draws blank, exactly like a
    measured zero: the two are told apart on the axis, not in the plot.
    """
    if value is None:
        return [BLANK] * rows
    total = rows * DOT_ROWS_PER_CELL
    filled = int(round(value * total))
    if value > 0:
        filled = max(1, filled)
    filled = min(filled, total)

    ramp = DOWN_RAMP if from_top else UP_RAMP
    column: list[str] = []
    for row in range(rows):
        # `row` 0 is the topmost line either way; only which end of the
        # bar it belongs to flips.
        distance = row if from_top else (rows - 1 - row)
        level = max(
            0, min(DOT_ROWS_PER_CELL, filled - distance * DOT_ROWS_PER_CELL)
        )
        column.append(BLANK if level == 0 else ramp[level - 1])
    return column


@dataclass(frozen=True)
class BarLayout:
    """Where each bar of a window sits, at one measured panel width.

    Bars keep a single width -- the bar *is* the datum, so it may not
    wobble from one slot to the next -- and the columns that do not
    divide evenly are spread across the gaps instead. That is what lets
    the last bar finish flush with the right edge of the panel: a plain
    floor division left the whole block short and hard against the left,
    which read as a ragged page edge rather than as a chart.
    """

    bar_width: int
    gaps: tuple[int, ...]

    @property
    def slots(self) -> int:
        return len(self.gaps) + 1

    @property
    def span(self) -> int:
        return self.slots * self.bar_width + sum(self.gaps)

    def trailing_span(self, slots: int) -> int:
        """Columns the newest `slots` bars occupy, their gaps included."""
        if slots <= 0:
            return 0
        kept = min(slots, self.slots)
        if kept == 1:
            return self.bar_width
        return kept * self.bar_width + sum(self.gaps[self.slots - kept :])


def fit_bars(
    width: int, slots: int, *, max_bar_width: int, min_gap: int = 1
) -> BarLayout:
    """Lay `slots` bars out across exactly `width` columns.

    The bar is made as wide as the width allows (capped, or a very wide
    panel would draw blocks instead of a trace), and every leftover
    column goes into the gaps, spread as evenly as they divide.

    `min_gap=0` is what turns discrete bars into a continuous trace: one
    column per sample with nothing between them, which is the only shape
    that reads as a curve rather than as a staircase.
    """
    if slots <= 0 or width <= 0:
        return BarLayout(bar_width=max(width, 0), gaps=())

    minimum_gaps = (slots - 1) * min_gap
    bar_width = max(1, min(max_bar_width, (width - minimum_gaps) // slots))
    gaps = [min_gap] * (slots - 1)
    slack = width - (slots * bar_width + sum(gaps))
    if slack > 0 and gaps:
        # Centred, not front-loaded: the gaps can rarely absorb the
        # slack evenly, and the odd one out belongs in the middle of the
        # trace rather than against one of its two visible edges.
        for step in range(slack):
            gaps[((2 * step + 1) * len(gaps)) // (2 * slack)] += 1
    return BarLayout(bar_width=bar_width, gaps=tuple(gaps))


def dot_bars(
    values: Sequence[float | None],
    rows: int,
    layout: BarLayout,
    *,
    from_top: bool = False,
) -> list[str]:
    """Render `values` as `rows` lines of side-by-side bars, top first."""
    columns = [dot_column(value, rows, from_top=from_top) for value in values]
    lines: list[str] = []
    for row in range(rows):
        parts: list[str] = []
        for index, column in enumerate(columns):
            if index:
                parts.append(BLANK * layout.gaps[index - 1])
            parts.append(column[row] * layout.bar_width)
        lines.append("".join(parts))
    return lines


def dot_axis(layout: BarLayout) -> str:
    """The axis rule, always drawn end to end.

    Complete from the very first frame, before a single sample exists:
    an axis that built itself up as samples arrived made a freshly
    opened page look broken for a whole minute.

    What was carried by *whether* the rule was drawn is carried by
    `measured_runs` and the style a caller applies to it instead -- an
    unmeasured stretch is dimmed, never blank, so "not measured" still
    reads apart from "measured zero" without the axis ever being a
    partial line.
    """
    return AXIS_RULE * layout.span


def measured_runs(
    measured: Sequence[bool], layout: BarLayout
) -> list[tuple[int, int, bool]]:
    """`(start, end, measured)` column runs across the axis.

    One entry per run of like slots, so a caller styles the axis in as
    few pieces as the window actually has.
    """
    runs: list[tuple[int, int, bool]] = []
    column = 0
    for index, is_measured in enumerate(measured[: layout.slots]):
        end = column + layout.bar_width
        if index < len(layout.gaps):
            end += layout.gaps[index]
        end = min(end, layout.span)
        if runs and runs[-1][2] is is_measured:
            runs[-1] = (runs[-1][0], end, is_measured)
        else:
            runs.append((column, end, is_measured))
        column = end
    if runs and runs[-1][1] < layout.span:
        runs[-1] = (runs[-1][0], layout.span, runs[-1][2])
    return runs


def dot_sparkline(values: Sequence[float | None], cells: int) -> str:
    """One-row dot sparkline, the same alphabet and the same floor.

    Right-aligned and padded to `cells`: the newest sample is always the
    rightmost cell, so two rows of different history length still line
    their "now" up.
    """
    recent = list(values)[-cells:]
    glyphs = []
    for value in recent:
        if value is None:
            glyphs.append(BLANK)
            continue
        level = int(round(value * DOT_ROWS_PER_CELL))
        if value > 0:
            level = max(1, level)
        level = min(level, DOT_ROWS_PER_CELL)
        glyphs.append(BLANK if level == 0 else UP_RAMP[level - 1])
    return "".join(glyphs).rjust(cells)


def normalize(values: Sequence[int | None], peak: int) -> list[float | None]:
    """Scale absolute measures against `peak`, the window's own maximum.

    A relative scale is what keeps the panel from being a flat line on a
    seedbox holding a steady 200 KiB/s -- and what makes the peak label
    load-bearing, since 60 s at 30 KiB/s and 60 s at 30 MiB/s draw the
    identical picture.
    """
    if peak <= 0:
        return [None if value is None else 0.0 for value in values]
    return [
        None if value is None else max(0.0, min(1.0, value / peak))
        for value in values
    ]
