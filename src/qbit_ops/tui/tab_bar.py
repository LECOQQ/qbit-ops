"""The filters modal's `border_title` tab strip.

`border_title` is one Rich-markup string; Textual wraps it in its own
`<corner><dash><space> ... <space>[dash]<corner>` decoration and, past
`width - BORDER_LABEL_MARGIN` rendered cells, truncates it with an
ellipsis (measured empirically against `render_border_label`: a label
of exactly that many cells is the largest one Textual leaves
untouched, always padding it out to the corner with one dash of its
own). This module is what keeps our own string at or under that
ceiling on purpose, at four widening degrees of abbreviation, so the
active tab is the last thing ever cut -- never the reverse (down to a
`MODAL_WIDTHS`-scale width; see `render_tab_strip`'s own docstring for
the floor below that).

Degrading a *string*, not a layout: the four levels mirror
`.agents/features/tui-filters/wireframes/filters_modal.py`'s `tab_strip`
exactly (title kept?, inactive tabs abbreviated?, only the active tab
left?). The seven widths a test checks that ladder against come from
that same generator's `--emit-invariants`, into a fixture Git tracks --
not transcribed, because a number that exists by hand in two places
drifts (`tests/fixtures/tui_filters_invariants.json`,
`tests/test_tui_tab_bar.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from textual.content import Content

from qbit_ops.tui.widgets.overview import _tab_label

# What Textual reserves around *any* border label once both corners
# are present: 2 cells `_styles_cache.py` subtracts before ever calling
# `render_border_label`, plus 4 more `cells_reserved` itself keeps for
# the two corners. Measured empirically against `render_border_label`,
# and the one place this number is written -- `qbit_ops.tui.modals.base`
# and `tests/test_tui_app.py` both import it rather than keep their own
# copy, which is how a `width - 4` guess survived next to this correct
# `width - 6` for as long as it did.
BORDER_LABEL_MARGIN: Final[int] = 6

# (keep the dialog's own title?, abbreviate the inactive tabs?, drop
# every tab but the active one?) -- the active tab itself is never
# abbreviated or dropped at any level.
_LADDER: tuple[tuple[bool, bool, bool], ...] = (
    (True, False, False),
    (True, True, False),
    (False, True, False),
    (False, False, True),
)


@dataclass(frozen=True)
class TabSpec:
    """One tab: its full name, a short form for the ladder's middle
    rungs, and its badge (`""` when the pane has no filter posed -- the
    absence of a count already says that, so it carries no marker)."""

    name: str
    short: str
    badge: str = ""


def _cell_len(markup: str) -> int:
    """Rendered width: the `[b]…[/b]` tags around a label cost zero
    cells, not the characters `len()` would count them as -- only the
    text they wrap still costs its own cells. The trap this module
    exists to avoid (see the module docstring)."""
    return Content.from_markup(markup).cell_length


def _tabs_joined(tabs: tuple[TabSpec, ...], active: int, *, short: bool) -> str:
    return "[dim]│[/dim]".join(
        _tab_label(
            tab.name if (i == active or not short) else tab.short,
            None,
            i == active,
            badge=tab.badge,
        )
        for i, tab in enumerate(tabs)
    )


def _solo(tabs: tuple[TabSpec, ...], active: int) -> str:
    tab = tabs[active]
    position = f"‹{active + 1}/{len(tabs)}›"
    badge = f"{tab.badge} {position}" if tab.badge else position
    return _tab_label(tab.name, None, True, badge=badge)


def render_tab_strip(
    width: int,
    tabs: tuple[TabSpec, ...],
    active: int,
    *,
    title: str = "Filters",
) -> tuple[str, int]:
    """The one `border_title` string, degraded until it fits `width`.

    Returns content measuring **at most** `BORDER_LABEL_MARGIN` fewer
    cells than `width` (`Content.from_markup(...).cell_length`) --
    Textual supplies the corner/dash/space decoration around it, so
    this never includes a literal `╭`/`╮`. `level` (0-3) is the ladder
    rung used, for tests that want to assert the degradation itself
    rather than just the width.

    Exactly that many cells whenever the narrowest rung (title and
    every inactive tab dropped, only the active one left) still fits.
    Below that -- a modal narrower than any `MODAL_WIDTHS` name, which
    only happens if a terminal itself shrinks under the smallest one --
    even the lone active tab is truncated with an ellipsis, the same
    way Textual would truncate it for us if we let it overflow. Doing
    it here keeps the result at or under budget on every path, so
    Textual's own truncation -- which cuts from the *end*, i.e. the
    active tab -- never has to run.
    """
    target = width - BORDER_LABEL_MARGIN
    head, tail, gap, level = "", "", 0, len(_LADDER) - 1
    for rung, (keep_title, short, solo) in enumerate(_LADDER):
        head = f"{title} " if keep_title else ""
        # `_tab_label` already opens with its own leading space (see
        # its docstring), so `tail` needs none of its own -- adding one
        # here would double it, one from each source.
        tail = (
            _solo(tabs, active)
            if solo
            else _tabs_joined(tabs, active, short=short)
        )
        gap = target - _cell_len(head) - _cell_len(tail)
        level = rung
        if gap >= 1:
            break
    result = Content.from_markup(head + "─" * max(gap, 0) + tail)
    if result.cell_length > target:
        result = result.truncate(target, ellipsis=True)
    return result.markup, level
