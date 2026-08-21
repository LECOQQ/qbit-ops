"""`ResultScreen` -- a truthful report of what an Apply actually did.

See `qbit_ops.tui.modals`'s module docstring for the `self.app`
typing note.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Button, Static

from qbit_ops.tui.formatting import _format_result_text
from qbit_ops.tui.modals.base import KeyHint, QbitModal
from qbit_ops.tui.state import MutationUiResult

if TYPE_CHECKING:
    from qbit_ops.tui.app import QbitOpsTuiApp


class ResultScreen(QbitModal):
    """A truthful, dismissible report of what an Apply actually did.

    `outcome` is computed by the App from the mutation worker's real
    result before this screen is constructed -- never inferred from
    "Apply was pressed". Dismissing (`Esc`, or Close) never re-applies
    anything; both route through `QbitOpsTuiApp.action_dismiss_overlay`,
    which special-cases `ResultScreen` to trigger
    `_on_result_dismissed`'s selection policy (no Screen-level `escape`
    binding here -- it would never fire behind the App's priority one).
    """

    MODAL_TITLE = "Result"
    MODAL_WIDTH = "medium"
    MODAL_KEYS = (
        KeyHint(("up", "down"), "Scroll"),
        KeyHint(("escape",), "Close"),
    )
    DIALOG_ID = "result-dialog"

    BINDINGS: list[Binding] = []

    def __init__(self, outcome: MutationUiResult) -> None:
        super().__init__()
        self.outcome = outcome

    def compose_dialog(self) -> ComposeResult:
        yield Static(id="result-content")
        yield Button("Close", id="result-close")

    def on_mount(self) -> None:
        self.query_one("#result-content", Static).update(
            _format_result_text(self.outcome)
        )
        self.query_one("#result-close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        app = cast("QbitOpsTuiApp", self.app)
        if event.button.id == "result-close":
            app.action_dismiss_overlay()
