"""`ActionsScreen` -- choose a LOW-risk bulk action for the selection.

See `qbit_ops.tui.modals`'s module docstring for the `self.app`
typing note.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Button, Static

from qbit_ops.tui.formatting import _truncate
from qbit_ops.tui.modals.base import KeyHint, QbitModal
from qbit_ops.tui.state import TuiBulkAction

if TYPE_CHECKING:
    from qbit_ops.tui.app import QbitOpsTuiApp


class ActionsScreen(QbitModal):
    """Choose a LOW-risk bulk action for the frozen selection snapshot.

    Only ever opened with a non-empty selection. No mutation here --
    picking an action just builds a frozen plan (zero API calls) and
    opens `PreviewScreen`; Cancel/Escape close with no side effect.
    """

    MODAL_TITLE = "Actions"
    MODAL_WIDTH = "small"
    MODAL_KEYS = (
        KeyHint(("up", "down"), "Move"),
        KeyHint(("enter",), "Run"),
        KeyHint(("escape",), "Cancel"),
    )
    DIALOG_ID = "actions-dialog"

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel", priority=True),
        # Up/Down move between the buttons -- inherited from
        # `QbitModal.BINDINGS`.
    ]

    _ACTION_BY_BUTTON_ID: dict[str, TuiBulkAction] = {
        "actions-pause": "pause",
        "actions-resume": "resume",
        "actions-reannounce": "reannounce",
        "actions-category-set": "category_set",
        "actions-tag-add": "tag_add",
        "actions-tag-remove": "tag_remove",
        "actions-throttle": "throttle",
    }
    # The four that collect an argument first: routed to their own
    # modal instead of straight to Preview (see `_VALUE_ACTIONS` in
    # `on_button_pressed`).
    _VALUE_ACTIONS: frozenset[TuiBulkAction] = frozenset(
        {"category_set", "tag_add", "tag_remove", "throttle"}
    )

    def __init__(
        self, selected_hashes: tuple[str, ...], names: tuple[str, ...]
    ) -> None:
        super().__init__()
        self.selected_hashes = selected_hashes
        self._names = names

    def compose_dialog(self) -> ComposeResult:
        yield Static(f"{len(self.selected_hashes)} selected")
        preview = ", ".join(_truncate(name, 24) for name in self._names[:3])
        extra = len(self._names) - 3
        if extra > 0:
            preview += f" (+{extra} more)"
        yield Static(preview, classes="actions-names")
        yield Button("Pause", id="actions-pause")
        yield Button("Resume", id="actions-resume")
        yield Button("Reannounce", id="actions-reannounce")
        yield Button("Set category", id="actions-category-set")
        yield Button("Add tags", id="actions-tag-add")
        yield Button("Remove tags", id="actions-tag-remove")
        yield Button("Set limits", id="actions-throttle")
        yield Button("Cancel", id="actions-cancel")

    def on_mount(self) -> None:
        self.query_one("#actions-pause", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        app = cast("QbitOpsTuiApp", self.app)
        button_id = event.button.id
        if button_id == "actions-cancel" or button_id is None:
            self.dismiss()
            return
        action = self._ACTION_BY_BUTTON_ID.get(button_id)
        if action is None:
            return
        hashes = self.selected_hashes
        names = self._names
        self.dismiss()
        if action in self._VALUE_ACTIONS:
            app._open_value_screen(action, hashes, names)
        else:
            app._open_preview_for_action(action, hashes)

    def action_dismiss(self, result: None = None) -> None:  # type: ignore[override]
        self.dismiss()
