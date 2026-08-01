"""`FiltersScreen` -- the sole access path to filters.

See `qbit_ops.tui.modals`'s module docstring for the `self.app`
typing note.
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
    qBittorrent calls). Apply/Cancel/Clear are three distinct,
    deterministic interactions, each reachable by binding and button:
    Apply (`Enter`) closes, already in effect; Cancel (`Escape`)
    reverts to `original_filters` then closes; Clear (`ctrl+r`) resets
    to no filter and stays open.

    `enter`/`escape` are not bound here: both are `priority=True` on
    `QbitOpsTuiApp`, which always wins over a same-key Screen binding,
    so `action_activate`/`action_dismiss_overlay` special-case
    `FiltersScreen` instead.
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
        # actual field. Focus the category `Input` explicitly instead.
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
