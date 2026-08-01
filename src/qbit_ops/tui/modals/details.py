"""`DetailsScreen` -- the narrow-layout modal Details view.

See `qbit_ops.tui.modals`'s module docstring for the `self.app`
typing note.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from qbit_ops.tui.widgets.details import DetailsPanel

if TYPE_CHECKING:
    from qbit_ops.tui.app import QbitOpsTuiApp


class DetailsScreen(ModalScreen[None]):
    """A modal Details panel -- the narrow-layout's access path to the
    focused torrent's details, opened by `enter`.

    Explicitly rebinds `c` (copy hash) here delegating to
    `QbitOpsTuiApp.action_copy_hash`: a non-priority App binding is
    unreachable while a `ModalScreen` is on top (see `FiltersScreen`
    for the `priority=True` exception). `q`/`r`/`e` are deliberately
    not re-bound -- only copy hash is a documented Details action.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close", priority=True),
        Binding("c", "copy_hash", "Copy hash"),
    ]

    CSS = """
    DetailsScreen {
        align: center middle;
    }
    #details-dialog {
        width: 50;
        height: 80%;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="details-dialog"):
            yield DetailsPanel()
            yield Static("[dim]Esc to close · c to copy hash[/dim]")

    def on_mount(self) -> None:
        app = cast("QbitOpsTuiApp", self.app)
        self.query_one(DetailsPanel).render_state(app.controller.state)

    def action_dismiss(self, result: None = None) -> None:  # type: ignore[override]
        self.dismiss()

    def action_copy_hash(self) -> None:
        app = cast("QbitOpsTuiApp", self.app)
        app.action_copy_hash()
