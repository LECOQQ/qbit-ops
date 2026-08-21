"""`SetupScreen` -- the first-run connection form.

Collects and renders only. Validating, testing and writing all happen
through `qbit_core.features.connection_setup`, the same path
`qbit-ops init` uses, so the two façades cannot diverge on the file mode
or on the precedence warning.

See `qbit_ops.tui.modals`'s module docstring for the `self.app` typing
note.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from qbit_core.features.connection_setup import DEFAULT_HOST, DEFAULT_USERNAME

if TYPE_CHECKING:
    from qbit_ops.tui.app import QbitOpsTuiApp

_SAVE_LABEL = "Test and save"
_CONFIRM_LABEL = "Save anyway"


class SetupScreen(ModalScreen[None]):
    """Ask for the three connection settings, once, before anything else.

    Deliberately has no escape binding: the dashboard behind it has
    nothing to show without configuration, so leaving is an explicit
    Quit rather than a dismissal that lands nowhere.
    """

    BINDINGS = [
        Binding("up", "app.focus_previous", "Up", show=False),
        Binding("down", "app.focus_next", "Down", show=False),
    ]

    CSS = """
    SetupScreen {
        align: center middle;
    }
    #setup-dialog {
        width: 64;
        max-height: 90%;
        border: round #ff9933;
        background: $background;
        padding: 1 2;
    }
    #setup-dialog Button {
        width: 100%;
    }
    .setup-label {
        color: $text-muted;
    }
    #setup-status {
        margin-top: 1;
        color: $warning;
    }
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
    """

    def __init__(self) -> None:
        super().__init__()
        self._confirming = False

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="setup-dialog"):
            yield Static(
                "qbit-ops needs a qBittorrent instance to talk to.",
                classes="setup-label",
            )
            yield Static("Host", classes="setup-label")
            yield Input(value=DEFAULT_HOST, id="setup-host")
            yield Static("User", classes="setup-label")
            yield Input(value=DEFAULT_USERNAME, id="setup-user")
            yield Static("Password", classes="setup-label")
            yield Input(password=True, id="setup-password")
            yield Static("", id="setup-status")
            yield Button(_SAVE_LABEL, id="setup-save")
            yield Button("Quit", id="setup-quit")

    def on_mount(self) -> None:
        self.query_one("#setup-dialog").border_title = "Connection setup"
        self.query_one("#setup-host", Input).focus()

    @property
    def confirming(self) -> bool:
        """Whether the next press means "save despite what I was told"."""
        return self._confirming

    def show_message(self, message: str) -> None:
        """Report a local validation failure; the form stays as it is."""
        self._confirming = False
        self.query_one("#setup-status", Static).update(message)
        self.query_one("#setup-save", Button).label = _SAVE_LABEL

    def request_confirmation(self, reasons: Sequence[str]) -> None:
        """Report what stands in the way and offer to write regardless."""
        self._confirming = True
        self.query_one("#setup-status", Static).update("\n".join(reasons))
        save = self.query_one("#setup-save", Button)
        save.label = _CONFIRM_LABEL
        save.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "setup-quit":
            self.app.exit()
            return
        if event.button.id == "setup-save":
            self._submit()

    def _submit(self) -> None:
        app = cast("QbitOpsTuiApp", self.app)
        app.submit_setup(
            host=self.query_one("#setup-host", Input).value,
            username=self.query_one("#setup-user", Input).value,
            password=self.query_one("#setup-password", Input).value,
            force=self._confirming,
        )
