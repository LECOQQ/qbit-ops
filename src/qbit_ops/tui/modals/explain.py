"""`ExplainScreen` -- the evidence-based explanation modal.

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
from textual.widgets import Static

from qbit_ops.features.explain import ExplanationReport
from qbit_ops.tui.formatting import _format_explain_text

if TYPE_CHECKING:
    from qbit_ops.tui.app import QbitOpsTuiApp


class ExplainScreen(ModalScreen[None]):
    """An evidence-based explanation of the focused torrent's state.

    `report` starts `None` while tracker data is still being fetched
    (see `QbitOpsTuiApp.action_explain`) -- `refresh_content()` shows a
    concise loading line in that case, and the App calls it again once
    (and only if) a matching, still-current result arrives (see
    `QbitOpsTuiApp._on_detail_worker_state_changed`'s explain-race
    handling). Purely a renderer: it never fetches anything itself and
    never calls back into `TuiController`.
    """

    BINDINGS: list[Binding] = []
    """Deliberately empty: `escape` is already a `priority=True` App
    binding (`action_dismiss_overlay`), which always wins over any
    same-key Screen binding -- see `FiltersScreen`'s docstring for the
    verified mechanism. A Screen-level `escape` binding here would
    simply never fire."""

    CSS = """
    ExplainScreen {
        align: center middle;
    }
    #explain-dialog {
        width: 80%;
        max-width: 96;
        height: 85%;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    def __init__(
        self, torrent_name: str, report: ExplanationReport | None
    ) -> None:
        super().__init__()
        self.torrent_name = torrent_name
        self.report = report

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="explain-dialog"):
            yield Static(id="explain-content")
            yield Static("[dim]Esc to close[/dim]")

    def on_mount(self) -> None:
        self.refresh_content()

    def refresh_content(self) -> None:
        app = cast("QbitOpsTuiApp", self.app)
        state = app.controller.state
        content = self.query_one("#explain-content", Static)
        content.update(
            _format_explain_text(self.torrent_name, self.report, state)
        )
