"""`FiltersScreen` -- the sole access path to filters.

See `qbit_ops.tui.modals`'s module docstring for the `self.app`
typing note.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Checkbox, Input, RadioSet, Rule, Static

from qbit_core.errors import InvalidInputError
from qbit_core.shared.selection import TorrentFilter
from qbit_ops.tui.filter_form import (
    PANE_ABBREVIATIONS,
    PANE_NAMES,
    pane_applied_count,
    pane_has_pending_edits,
)
from qbit_ops.tui.modals.base import KeyHint, QbitModal
from qbit_ops.tui.tab_bar import TabSpec, render_tab_strip
from qbit_ops.tui.widgets.filters import FiltersPanel

if TYPE_CHECKING:
    from qbit_ops.tui.app import QbitOpsTuiApp


class FiltersScreen(QbitModal):
    """The sole access path to filters, at every terminal width.

    Filters are edited in a local draft (`FiltersPanel.draft`, a
    `FiltersDraft`) and never touch `TuiController.state.filters` until
    `Apply`: no re-filtering on every keystroke (zero qBittorrent calls
    either way -- this whole screen is in-memory). `⏎` applies the
    draft and *stays open*; `esc` closes without undoing anything
    already applied; `ctrl+r` empties the draft without applying. See
    `.agents/specs/tui-filters.md`, "Les trois gestes, tranchés".

    `enter`/`escape` are not bound here: both are `priority=True` on
    `QbitOpsTuiApp`, which always wins over a same-key Screen binding,
    so `action_activate`/`action_dismiss_overlay` special-case
    `FiltersScreen` instead. `alt+left`/`alt+right` (section switch)
    *are* declared here, and must be: `check_action` (`app.py`) returns
    `False` for every App-level action once a modal is on top, so an
    App binding for them would never fire.
    """

    MODAL_TITLE = "Filters"
    MODAL_WIDTH = "large"
    MODAL_KEYS = (
        KeyHint(("alt+left",), "Section"),
        KeyHint(("tab",), "Move"),
        KeyHint(("enter",), "Apply"),
        KeyHint(("ctrl+r",), "Clear"),
        KeyHint(("escape",), "Cancel"),
    )
    DIALOG_ID = "filters-dialog"

    BINDINGS = [
        Binding(
            "alt+left",
            "prev_pane",
            "Section",
            key_display="alt+←/→",
        ),
        Binding("alt+right", "next_pane", "Section", show=False),
        Binding("ctrl+r", "clear", "Clear"),
        # `up`/`down` move focus between fields, same as Tab/Shift+Tab --
        # namespaced to `app.*` so they resolve through
        # `QbitOpsTuiApp.check_action`, which already always allows
        # `focus_next`/`focus_previous` regardless of which modal is
        # open (see its docstring) -- the same mechanism that already
        # makes Tab work in every modal.
        Binding("up", "app.focus_previous", "Up", show=False),
        Binding("down", "app.focus_next", "Down", show=False),
    ]

    def __init__(self, current_filters: TorrentFilter) -> None:
        super().__init__()
        self._applied = current_filters
        """The filter in effect -- read once to pre-fill the draft on
        open, then kept in step with every successful Apply. `esc`
        never reads it to revert anything (see the class docstring)."""
        self._active_index = 0

    def compose_dialog(self) -> ComposeResult:
        yield FiltersPanel()
        yield Static("", classes="f-spacer")
        yield Rule(classes="f-rule")
        yield Static("", classes="f-error")
        yield Static("", classes="f-pending")
        yield Static("", classes="f-count")

    def on_mount(self) -> None:
        panel = self.query_one(FiltersPanel)
        panel.sync_from(self._applied)
        panel.show_pane(PANE_NAMES[self._active_index])
        self._render_tab_strip()
        self._render_footer()
        # Textual's default `AUTO_FOCUS = "*"` auto-focuses the *first*
        # focusable widget in DOM order, which is `#filters-dialog`
        # itself (a `VerticalScroll`, and therefore focusable) -- left
        # alone, the first keystroke goes to the scroll container
        # instead of a field. Focus the first Organisation field
        # explicitly.
        self.query_one("#f-categories", Input).focus()

    def on_resize(self, event: events.Resize) -> None:
        self._render_tab_strip()

    # -- tab strip -----------------------------------------------------

    def _tab_specs(self) -> tuple[TabSpec, ...]:
        panel = self.query_one(FiltersPanel)
        draft = panel.pull_draft_from_widgets()
        specs = []
        for name in PANE_NAMES:
            count = pane_applied_count(name, self._applied)
            pending = pane_has_pending_edits(name, draft, self._applied)
            digits = str(count) if count else ""
            badge = digits + ("*" if pending else "")
            specs.append(TabSpec(name.upper(), PANE_ABBREVIATIONS[name], badge))
        return tuple(specs)

    def _render_tab_strip(self) -> None:
        dialog = self.query_one(f"#{self.DIALOG_ID}")
        # `outer_size`, not `size`: the latter is the *content* box,
        # already net of the 1-cell border and the `1 2` padding on
        # each side -- feeding it in would double-subtract those 6
        # cells on top of `render_tab_strip`'s own `width - 6`.
        width = dialog.outer_size.width or 100
        content, _level = render_tab_strip(
            width, self._tab_specs(), self._active_index
        )
        dialog.border_title = content

    # -- section switching ----------------------------------------------

    def action_prev_pane(self) -> None:
        self._switch_pane(-1)

    def action_next_pane(self) -> None:
        self._switch_pane(1)

    def _switch_pane(self, step: int) -> None:
        self._active_index = (self._active_index + step) % len(PANE_NAMES)
        self.query_one(FiltersPanel).show_pane(PANE_NAMES[self._active_index])
        self._render_tab_strip()
        first = self.query_one(FiltersPanel).query(
            f".-{PANE_NAMES[self._active_index].lower()}"
        )
        for row in first:
            focusable = row.query("Input, RadioSet, Checkbox")
            if focusable:
                focusable.first().focus()
                break

    # -- live draft feedback (never applies) -----------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        self._render_pending()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        self._render_pending()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        self._render_pending()

    def _render_pending(self) -> None:
        self._render_tab_strip()
        self._render_footer()

    def _render_footer(self) -> None:
        panel = self.query_one(FiltersPanel)
        draft = panel.pull_draft_from_widgets()
        pending = sum(
            1
            for name in PANE_NAMES
            if pane_has_pending_edits(name, draft, self._applied)
        )
        try:
            draft.to_filter()
            error = ""
        except (InvalidInputError, ValueError) as exc:
            error = str(exc)
        self.query_one(".f-error", Static).update(f"✕ {error}" if error else "")
        self.query_one(".f-pending", Static).update(
            f"* {pending} edits not applied" if pending else ""
        )
        applied_count = sum(
            pane_applied_count(name, self._applied) for name in PANE_NAMES
        )
        app = cast("QbitOpsTuiApp", self.app)
        state = app.controller.state
        shown = len(state.visible.matched) if state.visible is not None else 0
        total = state.total_torrents or 0
        # The list's own state, never the form's: this line never
        # reflects an un-applied draft, only what Apply last committed
        # (criterion 8).
        self.query_one(".f-count", Static).update(
            f"{applied_count} filters applied · showing {shown} of {total}"
        )

    # -- commit points ---------------------------------------------------

    def apply_draft(self) -> bool:
        """Parse the draft and, if valid, commit it. Returns whether it
        applied -- `False` disarms `⏎` without closing the modal (the
        `✕` line already explains why)."""
        panel = self.query_one(FiltersPanel)
        draft = panel.pull_draft_from_widgets()
        try:
            filters = draft.to_filter()
        except (InvalidInputError, ValueError):
            self._render_footer()
            return False

        app = cast("QbitOpsTuiApp", self.app)
        app.controller.set_filters(filters)
        self._applied = filters
        app._render_filter_summary()
        app._render_table()
        app._render_details_panels()
        app._reconcile_selection_and_notify()
        self._render_tab_strip()
        self._render_footer()
        return True

    def action_clear(self) -> None:
        panel = self.query_one(FiltersPanel)
        panel.draft = panel.draft.__class__()
        panel._write_draft_to_widgets()
        self._render_pending()
