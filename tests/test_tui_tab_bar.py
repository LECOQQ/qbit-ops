"""`qbit_ops.tui.tab_bar` -- the filters modal's `border_title` strip.

The seven widths below are *imported*, not transcribed: the wireframe
generator (`.agents/features/tui-filters/wireframes/filters_modal.py`)
computes them from the same `tab_strip` this module mirrors and writes
them to `tests/fixtures/tui_filters_invariants.json` (tracked by Git)
via

    python3 filters_modal.py --emit-invariants <path>

run from `.agents/features/tui-filters/wireframes/`. A number that
existed by hand in this file and in the wireframe drifted four times on
one work-item (`W74`, `.agents/workflow-history/`); a tracked, imported
fixture closes that gap instead of relying on the two staying in sync
by discipline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from textual.content import Content

from qbit_ops.tui import tab_bar
from qbit_ops.tui.tab_bar import TabSpec, render_tab_strip
from qbit_ops.tui.widgets.overview import _tab_label

_TABS = (
    TabSpec("ORGANISATION", "ORG", "2*"),
    TabSpec("STATE", "STA", "1"),
    TabSpec("MEASURES", "MEA", "3*"),
    TabSpec("TRACKERS", "TRK", ""),
)

_INVARIANTS_PATH = (
    Path(__file__).parent / "fixtures" / "tui_filters_invariants.json"
)
_INVARIANTS = json.loads(_INVARIANTS_PATH.read_text(encoding="utf-8"))

# width -> expected ladder level, computed by the wireframe generator.
_LADDER_WIDTHS: tuple[tuple[int, int], ...] = tuple(
    (entry["width"], entry["level"]) for entry in _INVARIANTS["border_ladder"]
)


def test_the_rendered_strip_measures_exactly_width_minus_six() -> None:
    """The hard constraint: `border_title` truncates past `width - 6`
    rendered cells (measured against `render_border_label`), so the
    content this module hands it must hit that number exactly, at
    every one of the seven widths the wireframe's ladder names."""
    for width, _level in _LADDER_WIDTHS:
        content, _ = render_tab_strip(width, _TABS, active=0)
        assert Content.from_markup(content).cell_length == width - 6, width


def test_the_ladder_degrades_at_the_wireframed_widths() -> None:
    for width, expected_level in _LADDER_WIDTHS:
        _, level = render_tab_strip(width, _TABS, active=0)
        assert level == expected_level, (width, level, expected_level)


def test_markup_costs_zero_cells_not_its_character_count() -> None:
    """The trap named in the spec: a width computed with `len()`
    instead of `Content(...).cell_length` would under-fill every
    active tab by the width of its own markup tags -- Rich markup adds
    characters a terminal never draws. Measured, not quoted: the
    rendered length must match the *plain* text exactly, proving the
    markup tags themselves cost nothing."""
    active_label = _tab_label("ORGANISATION", None, True, badge="2*")
    plain = Content.from_markup(active_label).plain
    assert len(active_label) > len(plain), "the active tab must carry markup"
    rendered_len = Content.from_markup(active_label).cell_length
    assert rendered_len == len(plain), (rendered_len, plain)


def test_the_active_tab_keeps_its_name_count_and_marker_at_every_level() -> (
    None
):
    for width, _level in _LADDER_WIDTHS:
        content, _ = render_tab_strip(width, _TABS, active=0)
        plain = Content.from_markup(content).plain
        assert "ORGANISATION" in plain, (width, plain)
        assert "2*" in plain, (width, plain)


def test_an_inactive_tab_is_abbreviated_before_it_is_ever_dropped() -> None:
    content, level = render_tab_strip(64, _TABS, active=0)
    plain = Content.from_markup(content).plain
    assert level == 1
    assert "STA" in plain and "STATE" not in plain
    assert "TRK" in plain and "TRACKERS" not in plain


def test_the_dialog_title_is_the_first_thing_dropped() -> None:
    content, level = render_tab_strip(52, _TABS, active=0)
    plain = Content.from_markup(content).plain
    assert level == 2
    assert "FILTERS" not in plain


def test_only_the_active_tab_survives_the_narrowest_rung() -> None:
    content, level = render_tab_strip(36, _TABS, active=0)
    plain = Content.from_markup(content).plain
    assert level == 3
    assert plain.count("│") == 0
    assert "‹1/4›" in plain


def test_a_pane_with_no_filter_carries_no_badge() -> None:
    content, _ = render_tab_strip(100, _TABS, active=0)
    plain = Content.from_markup(content).plain
    assert "TRACKERS" in plain
    # No stray digit or marker glued to the empty-badge tab.
    tail = plain.split("TRACKERS", 1)[1]
    assert tail.strip(" ") == ""


def test_the_strip_never_outgrows_its_own_budget_below_the_solo_rung() -> None:
    """Below the width where even the lone active tab fits (~32-40
    cells for these tab names), the strip used to come back longer
    than `width - BORDER_LABEL_MARGIN` -- Textual's own border-label
    truncation would then cut it from the *end*, i.e. into the active
    tab, which is exactly the guarantee the module claims to make. A
    terminal resized under any `MODAL_WIDTHS` name reaches this path
    for real (`.qbit-dialog { max-width: 100%; }`)."""
    for width in (36, 32, 28, 24, 20, 16, 10, 6):
        content, _level = render_tab_strip(width, _TABS, active=0)
        budget = width - tab_bar.BORDER_LABEL_MARGIN
        assert Content.from_markup(content).cell_length <= budget, (
            width,
            content,
        )


def test_the_modal_and_the_workspace_tabs_render_through_one_function() -> None:
    """Criterion 4bis: verified by *call*, not by comparing strings --
    two renderings that happen to match today could still drift apart
    tomorrow if each kept its own copy."""
    assert tab_bar._tab_label is _tab_label


def test_the_modal_tab_strip_actually_calls_the_shared_renderer(
    monkeypatch,
) -> None:
    calls = 0
    real = tab_bar._tab_label

    def _spy(*args: Any, **kwargs: Any) -> str:
        nonlocal calls
        calls += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(tab_bar, "_tab_label", _spy)
    render_tab_strip(100, _TABS, active=0)

    assert calls == len(_TABS)


def test_border_label_margin_matches_what_textual_actually_reserves() -> None:
    """`BORDER_LABEL_MARGIN` is not a guess to keep in sync by hand: a
    label longer than the terminal is always truncated by Textual's own
    `render_border_label` -- the function every `border_title` and
    `border_subtitle` goes through -- to exactly `width -
    BORDER_LABEL_MARGIN` cells. A `width - 4` copy of this fact drifted
    from the true `width - 6` for as long as nothing measured it."""
    from textual._border import render_border_label
    from textual.style import Style

    style = Style()
    outer_width = 40
    overlong = Content.from_markup("x" * 100)
    segments = list(
        render_border_label(
            (overlong, style),
            True,
            "round",
            outer_width - 2,
            style,
            style,
            style,
            True,
            True,
        )
    )
    rendered = Content.from_markup(
        "".join(segment.text for segment in segments)
    )
    # `render_border_label` pads 1 cell on each side once both corners
    # are drawn -- that padding is not part of the label's own budget.
    measured = rendered.cell_length - 2
    assert measured == outer_width - tab_bar.BORDER_LABEL_MARGIN, measured
