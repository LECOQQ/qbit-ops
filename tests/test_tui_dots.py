"""The dot alphabet and the small capitals, checked as pure functions.

Both are the kind of guarantee that is invisible on screen until it is
wrong: a bar that vanished, a title half-converted. Neither needs a
running app to be proven.
"""

from __future__ import annotations

import unicodedata

import pytest
from rich.cells import cell_len

from qbit_ops.tui.dots import (
    AXIS_RULE,
    DOT_ROWS_PER_CELL,
    DOWN_RAMP,
    UP_RAMP,
    dot_axis,
    dot_bars,
    dot_column,
    dot_sparkline,
    fit_bars,
    normalize,
)
from qbit_ops.tui.formatting import _SMALL_CAPS, _small_caps, _window_title

pytestmark = pytest.mark.tui


# --- the mirror ----------------------------------------------------------


def test_both_ramps_offer_the_same_number_of_levels() -> None:
    assert len(UP_RAMP) == len(DOWN_RAMP) == DOT_ROWS_PER_CELL


def test_a_value_renders_the_same_height_in_both_directions() -> None:
    for value in (0.1, 0.25, 0.5, 0.75, 1.0):
        upward = dot_column(value, 3, from_top=False)
        downward = dot_column(value, 3, from_top=True)
        inked_up = sum(1 for glyph in upward if glyph.strip())
        inked_down = sum(1 for glyph in downward if glyph.strip())
        assert inked_up == inked_down, value


def test_an_upward_bar_grows_from_the_bottom_row() -> None:
    column = dot_column(0.1, 3, from_top=False)
    assert column[0] == " " and column[1] == " "
    assert column[2].strip()


def test_a_downward_bar_grows_from_the_top_row() -> None:
    column = dot_column(0.1, 3, from_top=True)
    assert column[0].strip()
    assert column[1] == " " and column[2] == " "


# --- the floor -----------------------------------------------------------


def test_a_non_zero_value_always_inks_at_least_one_dot_row() -> None:
    """A tracker at 26 KiB/s beside one at 3 MiB/s normalizes to a value
    that rounds to nothing -- and must still be visible, or "slow" and
    "stopped" draw the same picture."""
    tiny = normalize([26_000], peak=3_100_000)[0]
    assert tiny > 0
    assert round(tiny * 3 * DOT_ROWS_PER_CELL) == 0
    assert dot_column(tiny, 3, from_top=False)[2] == UP_RAMP[0]


def test_a_zero_value_inks_nothing_at_all() -> None:
    assert dot_column(0.0, 3, from_top=False) == [" ", " ", " "]


def test_a_sparkline_floors_every_non_zero_sample_too() -> None:
    line = dot_sparkline(normalize([0, 26_000, 3_100_000], peak=3_100_000), 3)
    assert line == f" {UP_RAMP[0]}{UP_RAMP[-1]}"


# --- absent is not zero --------------------------------------------------


def test_an_unmeasured_slot_leaves_the_axis_bare() -> None:
    """A recorded zero draws a blank bar over ruled ground; a slot that
    was never measured draws a blank bar over bare ground. Without the
    difference the first twelve seconds would announce "no traffic"."""
    layout = fit_bars(70, 12, max_bar_width=6)
    partial = dot_axis(2, layout)
    full = dot_axis(12, layout)

    assert len(partial) == len(full) == layout.span
    assert full == AXIS_RULE * len(full)
    assert partial != full
    assert partial.endswith(AXIS_RULE)
    assert partial.startswith(" ")
    assert partial.count(AXIS_RULE) == layout.trailing_span(2)


def test_nothing_measured_yet_rules_no_axis_at_all() -> None:
    layout = fit_bars(70, 12, max_bar_width=6)
    assert dot_axis(0, layout) == " " * layout.span


# --- the relative scale --------------------------------------------------


def test_two_windows_of_very_different_amplitude_draw_the_same_bars() -> None:
    """Criterion: only the peak label separates 30 KiB/s from 30 MiB/s.
    The trace itself is deliberately identical."""
    shape = [1, 4, 9, 2, 7]
    small = [value * 30_000 for value in shape]
    large = [value * 30_000_000 for value in shape]

    layout = fit_bars(30, len(shape), max_bar_width=6)
    small_bars = dot_bars(normalize(small, max(small)), 3, layout)
    large_bars = dot_bars(normalize(large, max(large)), 3, layout)

    assert small_bars == large_bars
    assert max(small) != max(large)


def test_an_all_zero_window_scales_to_nothing_rather_than_dividing() -> None:
    assert normalize([0, 0, 0], peak=0) == [0.0, 0.0, 0.0]


@pytest.mark.parametrize("width", [24, 45, 64, 69, 75, 88, 132, 200])
def test_bars_spend_every_column_the_panel_gives_them(width: int) -> None:
    """A floor division left the block short and hard against the left,
    which read as a ragged page edge rather than as a chart."""
    layout = fit_bars(width, 12, max_bar_width=6)
    rendered = dot_bars([1.0] * 12, 3, layout)

    assert layout.span == width
    assert all(len(line) == width for line in rendered)
    assert min(layout.gaps) >= 1
    # The bar is the datum: it never wobbles from one slot to the next.
    assert max(layout.gaps) - min(layout.gaps) <= 1


def test_the_uneven_gap_never_lands_against_a_visible_edge() -> None:
    layout = fit_bars(69, 12, max_bar_width=6)
    odd = min(layout.gaps)

    assert layout.gaps.count(odd) == 1
    assert layout.gaps[0] != odd
    assert layout.gaps[-1] != odd


# --- small capitals ------------------------------------------------------


def test_every_small_capital_measures_exactly_one_cell() -> None:
    for glyph in _SMALL_CAPS.values():
        assert cell_len(glyph) == 1, glyph
        assert unicodedata.east_asian_width(glyph) == "N", glyph


def test_a_convertible_word_is_converted_whole() -> None:
    converted = _small_caps("Trackers")
    assert converted == "ᴛʀᴀᴄᴋᴇʀꜱ"
    assert not set(converted) & set("abcdefghijklmnopqrstuvwxyz")


def test_a_word_with_an_unmappable_letter_renders_unchanged() -> None:
    """Unicode has no SMALL CAPITAL X, and the `Explain` screen exists."""
    assert "x" not in _SMALL_CAPS
    assert _small_caps("Explain") == "Explain"
    assert _small_caps("Explain") != "Expl" + _SMALL_CAPS["a"] + "in"


def test_no_word_is_ever_converted_only_in_part() -> None:
    for word in ("Trackers", "Session", "Explain", "Sixty x"):
        converted = _small_caps(word)
        letters = [c for c in converted if c.isalpha()]
        in_small_caps = [c in _SMALL_CAPS.values() for c in letters]
        assert all(in_small_caps) or not any(in_small_caps), word


def test_the_ascii_setting_falls_back_without_reaching_for_small_caps() -> None:
    assert _window_title("Trackers", small_caps=False) == "TRACKERS"
    assert _window_title("Trackers", small_caps=True) == "ᴛʀᴀᴄᴋᴇʀꜱ"
