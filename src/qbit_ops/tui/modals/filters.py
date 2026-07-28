"""`FiltersScreen` -- the sole access path to filters.

Moved out of `qbit_ops.tui.app` (see docs/DECISIONS.md, TUI reorg
phase). No behavior change; see `qbit_ops.tui.modals`'s module
docstring for the `self.app` typing note.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from qbit_ops.features.torrents import TorrentFilter
from qbit_ops.tui.widgets.filters import FiltersPanel

if TYPE_CHECKING:
    from qbit_ops.tui.app import QbitOpsTuiApp


class FiltersScreen(ModalScreen[None]):
    """The sole access path to filters, at every terminal width.

    Filters apply live, locally, as the user edits them (zero
    qBittorrent calls -- see `QbitOpsTuiApp._apply_filters_from_panel`),
    but Apply/Cancel/Clear are three distinct, deterministic
    interactions, each reachable both by binding and by a visible
    button (`FiltersPanel`'s `Apply`/`Clear`/`Cancel`):

    * Apply (`Enter`, or the Apply button) -- already in effect; closes.
    * Cancel (`Escape`, or the Cancel button) -- revert to the filter
      that was active when this screen opened, then close.
    * Clear (`ctrl+r`, or the Clear button) -- reset to no filter at
      all; the modal stays open so the operator can keep adjusting.

    `enter`/`escape` are deliberately *not* bound here: both are already
    `priority=True` bindings on `QbitOpsTuiApp`, and Textual resolves an
    App's own priority bindings before a Screen's -- even the Screen on
    top of the stack -- so a same-key Screen-level binding here would
    simply never fire (verified empirically). `action_activate`/
    `action_dismiss_overlay` on the App special-case `FiltersScreen`
    instead -- see their docstrings. The visible buttons exist
    specifically so Apply/Cancel/Clear are not *only* discoverable via
    a keyboard shortcut.
    """

    BINDINGS = [
        Binding("ctrl+r", "clear", "Clear"),
        # `up`/`down` move focus between fields/buttons, same as
        # Tab/Shift+Tab -- namespaced to `app.*` so they resolve through
        # `QbitOpsTuiApp.check_action`, which already always allows
        # `focus_next`/`focus_previous` regardless of which modal is
        # open (see its docstring) -- the same mechanism that already
        # makes Tab work in every modal.
        Binding("up", "app.focus_previous", "Up", show=False),
        Binding("down", "app.focus_next", "Down", show=False),
    ]

    CSS = """
    FiltersScreen {
        align: center middle;
    }
    #filters-dialog {
        width: 64;
        max-height: 90%;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }
    .f-columns {
        height: auto;
    }
    .f-col {
        width: 1fr;
        height: auto;
        padding: 0 1;
    }
    .f-actions {
        height: auto;
        margin-top: 1;
    }
    .f-actions Button {
        margin-right: 1;
    }
    /* Selected (on) vs focused vs both must be distinguishable without
       relying on color alone: RadioButton's own "( )"/"(x)" glyph
       already encodes selection non-color; `:focus` additionally gets
       an explicit border and bold text so keyboard focus position is
       visible even on a color-blind or monochrome terminal. */
    RadioSet {
        border: round $panel;
        height: auto;
    }
    RadioSet:focus-within {
        border: round $accent;
    }
    RadioButton:focus {
        text-style: bold underline;
        border: tall $accent;
    }
    """

    def __init__(self, current_filters: TorrentFilter) -> None:
        super().__init__()
        self.original_filters = current_filters
        """The filter in effect when this screen opened -- Cancel
        (handled by `QbitOpsTuiApp.action_dismiss_overlay`) reverts to
        exactly this value."""

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="filters-dialog"):
            yield Static("[bold]Filters[/bold]")
            yield FiltersPanel()
            yield Static("[dim]Enter/Apply · Esc/Cancel · Ctrl+R/Clear[/dim]")

    def on_mount(self) -> None:
        self.query_one(FiltersPanel).sync_from(self.original_filters)
        # Textual's default `AUTO_FOCUS = "*"` auto-focuses the *first*
        # focusable widget on the screen in DOM order -- which is
        # `#filters-dialog` itself (a `VerticalScroll`, and therefore
        # focusable) since it comes before any of its children,
        # including the category `Input`. Left alone, every keystroke
        # right after opening Filters goes to the scroll container
        # (which only understands up/down/page keys) instead of any
        # actual field -- verified empirically; this is what made
        # Filters look entirely unresponsive to the keyboard. Focus the
        # category `Input` explicitly instead.
        self.query_one(".f-category", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        app = cast("QbitOpsTuiApp", self.app)
        if event.button.id == "filters-apply":
            app.action_activate()
        elif event.button.id == "filters-cancel":
            app.action_dismiss_overlay()
        elif event.button.id == "filters-clear":
            self.action_clear()

    def action_clear(self) -> None:
        app = cast("QbitOpsTuiApp", self.app)
        empty = TorrentFilter()
        app.controller.set_filters(empty)
        self.query_one(FiltersPanel).sync_from(empty)
        self.query_one(FiltersPanel).show_error("")
        app._render_filter_summary()
        app._render_table()
        app._render_details_panels()
        # Clearing widens visibility, so this is unlikely to hide
        # anything -- but a search term may still narrow it back down,
        # so reconcile for correctness/consistency with Apply/Cancel.
        app._reconcile_selection_and_notify()
