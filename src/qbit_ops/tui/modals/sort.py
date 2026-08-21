"""`SortScreen` -- the sole access path to local torrent-table sorting.

See `qbit_ops.tui.modals`'s module docstring for the `self.app` typing
note.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import RadioButton, RadioSet

from qbit_ops.tui.modals.base import KeyHint, QbitModal
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


class SortScreen(QbitModal):
    """Pick the Torrents table's local sort order -- zero API calls.

    Operates entirely on the already-collected snapshot
    (`TuiController.set_sort`); selecting an option applies it
    immediately and closes, matching `ActionsScreen`'s
    pick-and-dismiss pattern rather than Filters' separate Apply step,
    since there's nothing here to preview first.
    """

    MODAL_TITLE = "Sort"
    MODAL_WIDTH = "small"
    MODAL_KEYS = (
        KeyHint(("up", "down"), "Move"),
        KeyHint(("space",), "Select"),
        KeyHint(("escape",), "Cancel"),
    )
    DIALOG_ID = "sort-dialog"

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel", priority=True),
        Binding("up", "app.focus_previous", "Up", show=False),
        Binding("down", "app.focus_next", "Down", show=False),
    ]

    def __init__(self, current: SortOrder) -> None:
        super().__init__()
        self._current = current

    def compose_dialog(self) -> ComposeResult:
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

    def on_mount(self) -> None:
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
