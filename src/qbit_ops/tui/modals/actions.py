"""`ActionsScreen` -- choose a LOW-risk bulk action for the selection.

See `qbit_ops.tui.modals`'s module docstring for the `self.app`
typing note.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from qbit_ops.features.torrents import TorrentBulkAction
from qbit_ops.tui.formatting import _truncate

if TYPE_CHECKING:
    from qbit_ops.tui.app import QbitOpsTuiApp


class ActionsScreen(ModalScreen[None]):
    """Choose a LOW-risk bulk action for the frozen selection snapshot.

    Only ever opened with a non-empty selection. No mutation here --
    picking an action just builds a frozen plan (zero API calls) and
    opens `PreviewScreen`; Cancel/Escape close with no side effect.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel", priority=True),
        # Up/Down move between the buttons, same as Tab/Shift+Tab --
        # see `FiltersScreen`'s identical bindings for why this
        # resolves correctly through `QbitOpsTuiApp.check_action`.
        Binding("up", "app.focus_previous", "Up", show=False),
        Binding("down", "app.focus_next", "Down", show=False),
    ]

    CSS = """
    ActionsScreen {
        align: center middle;
    }
    #actions-dialog {
        width: 48;
        max-height: 90%;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }
    #actions-dialog Button {
        width: 100%;
        margin-bottom: 1;
    }
    .actions-names {
        color: $text-muted;
        margin-bottom: 1;
    }
    """

    _ACTION_BY_BUTTON_ID: dict[str, TorrentBulkAction] = {
        "actions-pause": "pause",
        "actions-resume": "resume",
        "actions-reannounce": "reannounce",
    }

    def __init__(
        self, selected_hashes: tuple[str, ...], names: tuple[str, ...]
    ) -> None:
        super().__init__()
        self.selected_hashes = selected_hashes
        self._names = names

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="actions-dialog"):
            yield Static(
                f"[bold]Actions[/bold] · {len(self.selected_hashes)} selected"
            )
            preview = ", ".join(_truncate(name, 24) for name in self._names[:3])
            extra = len(self._names) - 3
            if extra > 0:
                preview += f" (+{extra} more)"
            yield Static(preview, classes="actions-names")
            yield Button("Pause", id="actions-pause")
            yield Button("Resume", id="actions-resume")
            yield Button("Reannounce", id="actions-reannounce")
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
        self.dismiss()
        app._open_preview_for_action(action, hashes)

    def action_dismiss(self, result: None = None) -> None:  # type: ignore[override]
        self.dismiss()
