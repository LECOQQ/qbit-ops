"""`HelpScreen` -- the dedicated `?` help modal."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Static

from qbit_ops.tui.modals.base import KeyHint, QbitModal

_HELP_TEXT = """[bold]Global[/bold]
1, g       Overview
2, t       Torrents
←/→        Previous / next page
?          Help
esc        Close modal / clear selection / back
q          Quit

[bold]Torrents workspace[/bold]
j/k, ↑/↓   Navigate (moves focus)
/          Search (name or hash)
f          Filters
s          Sort
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


class HelpScreen(QbitModal):
    """A real, dedicated help screen listing only bindings that work."""

    MODAL_TITLE = "Help"
    MODAL_WIDTH = "medium"
    MODAL_KEYS = (
        KeyHint(("up", "down"), "Scroll"),
        KeyHint(("escape",), "Close"),
    )
    DIALOG_ID = "help-dialog"

    BINDINGS = [
        Binding("escape", "dismiss", "Close", priority=True),
        Binding("question_mark", "dismiss", "Close"),
    ]

    def compose_dialog(self) -> ComposeResult:
        yield Static(_HELP_TEXT)

    def action_dismiss(self, result: None = None) -> None:  # type: ignore[override]
        self.dismiss()
