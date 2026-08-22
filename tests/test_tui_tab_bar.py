"""`qbit_ops.tui.tab_bar` -- the filters modal's `border_title` strip.

The seven widths below are transcribed from
`.agents/features/tui-filters/wireframes/filters-border-ladder.txt`
(measured against the running wireframe generator) rather than read
from it: `.agents/` is gitignored, so a test cannot open that file.
"""

from __future__ import annotations

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

# width -> expected ladder level, transcribed from the wireframe.
_LADDER_WIDTHS: tuple[tuple[int, int], ...] = (
    (100, 0),
    (92, 0),
    (76, 0),
    (64, 1),
    (52, 2),
    (44, 3),
    (36, 3),
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
    """The trap named in the spec: `[b]Overview[/b]` is 8 rendered
    cells and 21 characters. A width computed with `len()` instead of
    `Content(...).cell_length` would under-fill every active tab by the
    width of its own markup tags."""
    active_label = _tab_label("ORGANISATION", None, True, badge="2*")
    assert "[" in active_label, "the active tab must actually carry markup"
    plain_len = len(active_label)
    rendered_len = Content.from_markup(active_label).cell_length
    assert rendered_len < plain_len, (rendered_len, plain_len)


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
