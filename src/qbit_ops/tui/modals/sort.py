"""`SortScreen` -- the sole access path to local torrent-table sorting.

See `qbit_ops.tui.modals`'s module docstring for the `self.app` typing
note.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import RadioButton, RadioSet, Static

from qbit_ops.tui.state import SortDirection, SortField, SortOrder

if TYPE_CHECKING:
    from qbit_ops.tui.app import QbitOpsTuiApp

# Declaration order is display order -- every `SortField` paired with
# both directions, never a cycling order the operator has to memorize.
_OPTIONS: tuple[tuple[SortField, SortDirection, str], ...] = (
    (SortField.NAME, SortDirection.ASCENDING, "Name (A-Z)"),
    (SortField.NAME, SortDirection.DESCENDING, "Name (Z-A)"),
    (SortField.STATE, SortDirection.ASCENDING, "State (A-Z)"),
    (SortField.STATE, SortDirection.DESCENDING, "State (Z-A)"),
    (SortField.PROGRESS, SortDirection.DESCENDING, "Progress (high-low)"),
    (SortField.PROGRESS, SortDirection.ASCENDING, "Progress (low-high)"),
    (SortField.DOWN, SortDirection.DESCENDING, "Download speed (high-low)"),
    (SortField.DOWN, SortDirection.ASCENDING, "Download speed (low-high)"),
    (SortField.UP, SortDirection.DESCENDING, "Upload speed (high-low)"),
    (SortField.UP, SortDirection.ASCENDING, "Upload speed (low-high)"),
    (SortField.RATIO, SortDirection.DESCENDING, "Ratio (high-low)"),
    (SortField.RATIO, SortDirection.ASCENDING, "Ratio (low-high)"),
    (SortField.CATEGORY, SortDirection.ASCENDING, "Category (A-Z)"),
    (SortField.CATEGORY, SortDirection.DESCENDING, "Category (Z-A)"),
)


def _option_id(field: SortField, direction: SortDirection) -> str:
    return f"sort-{field.value}-{direction.value}"


class SortScreen(ModalScreen[None]):
    """Pick the Torrents table's local sort order -- zero API calls.

    Operates entirely on the already-collected snapshot
    (`TuiController.set_sort`); selecting an option applies it
    immediately and closes, matching `ActionsScreen`'s
    pick-and-dismiss pattern rather than Filters' separate Apply step,
    since there's nothing here to preview first.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel", priority=True),
        Binding("up", "app.focus_previous", "Up", show=False),
        Binding("down", "app.focus_next", "Down", show=False),
    ]

    CSS = """
    SortScreen {
        align: center middle;
    }
    #sort-dialog {
        width: 42;
        max-height: 90%;
        border: round #ff9933;
        /* Same `$background`-not-`$surface` fix as `FiltersScreen`
           (see its CSS comment): keeps the round border reading as a
           floating outline on the uniform dark background rather than
           a visibly lighter grey box. */
        background: $background;
        padding: 1 2;
    }
    /* Textual's own default (unset here previously) draws the focused
       RadioSet's border and its highlighted option's `.toggle--label`
       in `$border`/`$block-cursor-background` -- both a saturated
       blue -- swapped for the brand orange used by every other modal.
       A `RadioButton` is never focused directly (confirmed
       empirically): the containing `RadioSet` is, and it highlights
       its own `.-selected` child's `.toggle--label` -- so that's the
       selector that needs overriding, not `RadioButton:focus`. */
    /* Also transparent (not `RadioSet`'s own `$surface` default): the
       RadioSet fills nearly this whole dialog body, so left alone it
       would repaint the same "grey box against the round border"
       clash the dialog-level `$background` fix above was meant to
       solve, just one level in. */
    RadioSet {
        border: round $panel-lighten-2;
        background: transparent;
        height: auto;
    }
    RadioSet:focus-within {
        border: round #ff9933;
    }
    /* `color: $background`, not `$text` (near-white): white text on
       the `#ff9933` fill is ~2:1 contrast, the app's own dark
       background colour on it is ~9:1. */
    RadioSet:focus > RadioButton.-selected > .toggle--label {
        background: #ff9933;
        color: $background;
        text-style: bold;
    }
    /* Textual's default `RadioSet:blur > RadioButton.-selected >
       .toggle--label` falls back to `$block-cursor-blurred-background`
       (a translucent `$primary`, i.e. electric blue) -- swapped for a
       translucent brand orange so the blurred selection reads as the
       same accent family, not a stray blue wash. */
    RadioSet:blur > RadioButton.-selected > .toggle--label {
        background: #ff9933 30%;
    }
    /* The "(x)" on-mark itself defaults to `$text-success` (green) --
       this modal has no green/red semantics, only the brand accent. */
    RadioSet > RadioButton.-on > .toggle--button {
        color: #ff9933;
    }
    """

    def __init__(self, current: SortOrder) -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="sort-dialog"):
            with RadioSet(id="sort-options"):
                for field, direction, label in _OPTIONS:
                    yield RadioButton(
                        label,
                        id=_option_id(field, direction),
                        value=(
                            field is self._current.field
                            and direction is self._current.direction
                        ),
                    )
            yield Static("[dim]Enter/Select · Esc/Cancel[/dim]")

    def on_mount(self) -> None:
        self.query_one("#sort-dialog").border_title = "Sort"
        self.query_one("#sort-options", RadioSet).focus()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        event.stop()
        button_id = event.pressed.id
        if button_id is None:
            return
        for field, direction, _label in _OPTIONS:
            if _option_id(field, direction) == button_id:
                app = cast("QbitOpsTuiApp", self.app)
                app.apply_sort(SortOrder(field=field, direction=direction))
                self.dismiss()
                return

    def action_dismiss(self, result: None = None) -> None:  # type: ignore[override]
        self.dismiss()
