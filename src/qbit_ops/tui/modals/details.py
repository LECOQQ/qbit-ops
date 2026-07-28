"""`DetailsScreen` -- the narrow-layout modal Details view.

Moved out of `qbit_ops.tui.app` (see docs/DECISIONS.md, TUI reorg
phase). No behavior change; see `qbit_ops.tui.modals`'s module
docstring for the `self.app` typing note.
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

    Explicitly binds `c` (copy hash), delegating straight to
    `QbitOpsTuiApp.action_copy_hash` -- Textual restricts a *non*-
    priority key's binding lookup to `Screen._modal_binding_chain` while
    a `ModalScreen` is on top of the stack, which does **not** include
    the App's own `BINDINGS` (only `priority=True` ones bypass this, via
    a separate lookup -- see `FiltersScreen`'s docstring for that other
    case). A plain `Binding("c", "copy_hash", ...)` left only on the App
    would silently never fire while this screen is open -- verified
    empirically. `q`/`r`/`e` are deliberately not re-bound here: only
    Copy hash is a documented Details-view action (see docs/COMMANDS.md).
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
