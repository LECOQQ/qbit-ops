"""`HelpScreen` -- the dedicated `?` help modal.

Moved out of `qbit_ops.tui.app` (see docs/DECISIONS.md, TUI reorg
phase). No behavior change.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

_HELP_TEXT = """[bold]Global[/bold]
1, g       Overview
2, t       Torrents
?          Help
esc        Close modal / clear selection / back
q          Quit

[bold]Torrents workspace[/bold]
j/k, ↑/↓   Navigate (moves focus)
/          Search (name or hash)
f          Filters
enter      Details (focused torrent)
c          Copy hash (focused torrent)
e          Explain (focused torrent)
r          Refresh tracker details (focused torrent)
space      Toggle selection (focused torrent)
ctrl+a     Select all visible torrents
ctrl+d     Deselect all torrents
a          Actions for selected torrents

[bold]In any modal (Filters, Actions, Preview, Result)[/bold]
tab, ↑/↓   Move between fields/buttons
enter      Apply / press the focused button

[dim]Focused = the highlighted row (one at a time).
Selected = marked with ✔ for bulk actions (any number).
Visible = shown after the current filter/search.
Copy/Explain/Refresh always act on the focused torrent only,
never the selection.[/dim]
"""


class HelpScreen(ModalScreen[None]):
    """A real, dedicated help screen listing only bindings that work."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close", priority=True),
        Binding("question_mark", "dismiss", "Close"),
    ]

    CSS = """
    HelpScreen {
        align: center middle;
    }
    #help-dialog {
        width: 64;
        max-height: 90%;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help-dialog"):
            yield Static(_HELP_TEXT)

    def action_dismiss(self, result: None = None) -> None:  # type: ignore[override]
        self.dismiss()
