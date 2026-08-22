"""The filters modal's `border_title` tab strip.

`border_title` is one Rich-markup string; Textual wraps it in its own
`<corner><dash><space> ... <space>[dash]<corner>` decoration and, past
`width - 6` rendered cells, truncates it with an ellipsis (measured
empirically against `render_border_label`: a label of exactly
`width - 6` cells is the largest one Textual leaves untouched, always
padding it out to the corner with one dash of its own). This module is
what keeps our own string at or under that ceiling on purpose, at four
widening degrees of abbreviation, so the active tab is the last thing
ever cut -- never the reverse.

Degrading a *string*, not a layout: the four levels mirror
`.agents/specs/tui-filters.wireframes/filters_modal.py`'s `tab_strip`
exactly (title kept?, inactive tabs abbreviated?, only the active tab
left?), transcribed here because `.agents/` is not part of the
repository a test can read.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.content import Content

from qbit_ops.tui.widgets.overview import _tab_label

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
    """Rendered width: `[b]…[/b]` costs zero cells, not the eight
    characters `len()` would count -- the trap this module exists to
    avoid (see the module docstring)."""
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
    title: str = "FILTERS",
) -> tuple[str, int]:
    """The one `border_title` string, degraded until it fits `width`.

    Always returns content measuring exactly `width - 6` rendered
    cells (`Content.from_markup(...).cell_length`) -- Textual supplies
    the corner/dash/space decoration around it, so this never includes
    a literal `╭`/`╮`. `level` (0-3) is the ladder rung used,
    for tests that want to assert the degradation itself rather than
    just the width.
    """
    target = width - 6
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
    return head + "─" * max(gap, 0) + tail, level
