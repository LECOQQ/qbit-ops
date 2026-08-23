"""`HelpScreen` -- the dedicated `?` help modal."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Static

from qbit_ops.tui.modals.base import KeyHint, QbitModal

# `{select_all}`/`{deselect_all}` are filled in at compose time, through
# the same OS-aware `get_key_display` every other rendered key goes
# through (`QbitOpsTuiApp.get_key_display`) -- everywhere else in this
# block spells a key literally because it names a *group* no single
# `Binding` covers (`1, g`, `j/k, ↑/↓`), not because it is exempt from
# that rule. Column width (11) matches every other entry here.
_HELP_TEMPLATE = """[bold]Global[/bold]
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
{select_all}Select all visible torrents
{deselect_all}Deselect all torrents
a          Actions for selected torrents
{reset_view}Reset filters, sort and selection

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
        yield Static(self._help_text())

    def _help_text(self) -> str:
        return _HELP_TEMPLATE.format(
            select_all=self._app_key_display("ctrl+a").ljust(11),
            deselect_all=self._app_key_display("ctrl+d").ljust(11),
            reset_view=self._app_key_display("ctrl+r").ljust(11),
        )

    def _app_key_display(self, key: str) -> str:
        """The OS-aware display for one of `QbitOpsTuiApp`'s own
        top-level bindings -- `ctrl+a`/`ctrl+d` are regular (non-
        priority) App bindings, so they never show up in this screen's
        own `active_bindings` (see `QbitModal._binding_for`'s
        docstring) and must be found in `App.BINDINGS` directly.

        Falls back to a bare `Binding(key, "", "")` when none is
        declared -- `tests/test_tui_architecture.py` mounts `HelpScreen`
        under a minimal harness `App` with no bindings of its own at
        all, and this text must still compose there.
        """
        binding = next(
            (
                binding
                for binding in self.app.BINDINGS
                if isinstance(binding, Binding) and binding.key == key
            ),
            None,
        ) or Binding(key, "", "")
        return self.app.get_key_display(binding)

    def action_dismiss(self, result: None = None) -> None:  # type: ignore[override]
        self.dismiss()
