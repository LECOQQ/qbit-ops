"""`ExplainScreen` -- the evidence-based explanation modal.

See `qbit_ops.tui.modals`'s module docstring for the `self.app`
typing note.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Static

from qbit_core.features.explain import ExplanationReport
from qbit_ops.tui.formatting import _format_explain_text
from qbit_ops.tui.modals.base import KeyHint, QbitModal

if TYPE_CHECKING:
    from qbit_ops.tui.app import QbitOpsTuiApp


class ExplainScreen(QbitModal):
    """An evidence-based explanation of the focused torrent's state.

    `report` starts `None` while tracker data is still fetching --
    `refresh_content()` shows a loading line, and the App calls it
    again once a matching, still-current result arrives. Purely a
    renderer: never fetches anything or calls back into `TuiController`.
    """

    MODAL_TITLE = "Explain"
    MODAL_WIDTH = "large"
    MODAL_KEYS = (
        KeyHint(("up", "down"), "Scroll"),
        KeyHint(("escape",), "Close"),
    )
    DIALOG_ID = "explain-dialog"

    BINDINGS: list[Binding] = []
    """Deliberately empty: `escape` is already a `priority=True` App
    binding (`action_dismiss_overlay`), which always wins over any
    same-key Screen binding -- see `FiltersScreen`'s docstring. A
    Screen-level `escape` binding here would simply never fire."""

    def __init__(
        self, torrent_name: str, report: ExplanationReport | None
    ) -> None:
        super().__init__()
        self.torrent_name = torrent_name
        self.report = report

    def compose_dialog(self) -> ComposeResult:
        yield Static(id="explain-content")

    def on_mount(self) -> None:
        self.refresh_content()

    def refresh_content(self) -> None:
        app = cast("QbitOpsTuiApp", self.app)
        state = app.controller.state
        content = self.query_one("#explain-content", Static)
        content.update(
            _format_explain_text(self.torrent_name, self.report, state)
        )
