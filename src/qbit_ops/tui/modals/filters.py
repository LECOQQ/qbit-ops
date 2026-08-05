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

from qbit_core.features.torrents import TorrentFilter
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
        border: round #ff9933;
        /* `$surface` (a visibly lighter grey) read as a distinct box
           clashing with the round orange border floating on the app's
           now-uniform dark background -- `$background` matches that
           uniform tone, so only the border (not a grey panel) reads as
           "floating". */
        background: $background;
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
    /* Full-width, stacked one per row -- matches `ActionsScreen`'s
       button layout, the same component used the same way in both
       modals rather than a narrow horizontal row here alone. */
    .f-actions Button {
        width: 100%;
        margin-bottom: 0;
    }
    /* Selected (on) vs focused vs both must be distinguishable without
       relying on color alone: RadioButton's own "( )"/"(x)" glyph
       already encodes selection non-color; `:focus-within` on the
       RadioSet gives an explicit border so keyboard focus position is
       visible even on a color-blind or monochrome terminal.
       `background: transparent` here (and on `Input`/`Button`/
       `Checkbox` below): each of these widgets' own `DEFAULT_CSS`
       fills with `$surface`, a visibly lighter grey than this dialog's
       `$background` -- left alone, every field/button reads as its own
       grey box nested inside the (now-uniform) dialog, the same clash
       the round border fix targeted, just moved one level in. */
    RadioSet {
        border: round $panel-lighten-2;
        background: transparent;
        height: auto;
    }
    RadioSet:focus-within {
        border: round #ff9933;
    }
    /* Input/Checkbox/Button default focus/`-primary` styling all draw
       from Textual's own `$primary`/`$border`/`$block-cursor-*` (a
       saturated blue) -- replaced here with the same brand orange used
       everywhere else in this dialog. A `RadioButton` is never focused
       directly (confirmed empirically): the containing `RadioSet` is,
       and it highlights its own `.-selected` child's `.toggle--label`
       -- so that's the selector that actually needs overriding, not
       `RadioButton:focus`. */
    Input {
        background: transparent;
    }
    Input:focus {
        border: tall #ff9933;
    }
    Checkbox {
        background: transparent;
    }
    Checkbox:focus {
        border: tall #ff9933;
    }
    /* `color: $background` (not `$text`, near-white) for text sitting
       directly on the `#ff9933` fill below -- white-on-orange is
       roughly 2:1 contrast, well under a readable threshold; the
       app's own dark background colour against orange is ~9:1. */
    Checkbox:focus > .toggle--label,
    RadioSet:focus > RadioButton.-selected > .toggle--label {
        background: #ff9933;
        color: $background;
        text-style: bold;
    }
    /* Same blurred-selection and on-mark fixes as `SortScreen` -- see
       its CSS comments: both otherwise default to Textual's blue
       `$block-cursor-blurred-background` and green `$text-success`. */
    RadioSet:blur > RadioButton.-selected > .toggle--label {
        background: #ff9933 30%;
    }
    RadioSet > RadioButton.-on > .toggle--button {
        color: #ff9933;
    }
    /* Flat, single-row buttons -- same fix as `ActionsScreen` (see its
       CSS comment): Textual's default 3-row "3D" `Button` and blue
       `-primary` variant both clash with this dialog's restrained
       style, and made a stack of three buttons look oversized. */
    Button {
        height: 1;
        min-width: 0;
        border: none;
        background: transparent;
        color: $text;
        text-style: none;
    }
    Button:hover {
        background: $panel-lighten-2;
    }
    Button:focus {
        background: #ff9933 20%;
        color: #ff9933;
        text-style: bold;
    }
    Button.-primary {
        background: transparent;
        color: #ff9933;
        text-style: bold;
    }
    Button.-primary:hover {
        background: #ff9933 20%;
    }
    Button.-primary:focus {
        background: #ff9933;
        color: $background;
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
            yield FiltersPanel()
            yield Static("[dim]Enter/Apply · Esc/Cancel · Ctrl+R/Clear[/dim]")

    def on_mount(self) -> None:
        self.query_one("#filters-dialog").border_title = "Filters"
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
