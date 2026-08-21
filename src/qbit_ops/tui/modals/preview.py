"""`PreviewScreen` -- preview of a frozen bulk-action plan before Apply.

See `qbit_ops.tui.modals`'s module docstring for the `self.app`
typing note.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, Static

from qbit_core.features.torrents import BulkTorrentActionPlan
from qbit_ops.tui.formatting import _format_preview_text
from qbit_ops.tui.modals.base import KeyHint, QbitModal

if TYPE_CHECKING:
    from qbit_ops.tui.app import QbitOpsTuiApp


class PreviewScreen(QbitModal):
    """Preview of a frozen `BulkTorrentActionPlan` before Apply.

    Owns and displays exactly the plan passed at construction -- the
    live selection/filters/search/focus may keep changing in the
    background while this modal is open, but never mutates it.

    Staleness is **sticky**: once the snapshot generation this plan is
    grounded in stops being current, the preview becomes permanently
    non-applicable. Recovery never re-enables it -- the operator must
    close and rebuild from current data. `mark_stale()` is one-way.
    """

    MODAL_TITLE = "Preview"
    MODAL_WIDTH = "large"
    MODAL_KEYS = (
        KeyHint(("up", "down"), "Move"),
        KeyHint(("enter",), "Apply"),
        KeyHint(("escape",), "Cancel"),
    )
    DIALOG_ID = "preview-dialog"

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel", priority=True),
        # Up/Down move between the Cancel/Apply buttons, same as
        # Tab/Shift+Tab -- see `FiltersScreen`'s identical bindings.
        Binding("up", "app.focus_previous", "Up", show=False),
        Binding("down", "app.focus_next", "Down", show=False),
    ]

    def __init__(
        self,
        plan: BulkTorrentActionPlan,
        snapshot_at: datetime | None,
        *,
        operation_id: int,
    ) -> None:
        super().__init__()
        self.plan = plan
        self.snapshot_at = snapshot_at
        self.operation_id = operation_id
        """Immutable identity of the mutation this preview owns -- a
        completion only ever touches the preview carrying its own id."""
        self.applying = False
        self.stale = False

    @property
    def can_apply(self) -> bool:
        """Whether Apply may dispatch: fresh snapshot, not already
        applying. The single source of truth behind both the button's
        `disabled` state and `QbitOpsTuiApp.action_apply_plan`'s guard,
        so keyboard Apply cannot bypass what the button forbids."""
        return not self.stale and not self.applying

    def compose_dialog(self) -> ComposeResult:
        yield Static(id="preview-content")
        with Horizontal(id="preview-actions"):
            yield Button("Cancel", id="preview-cancel")
            yield Button("Apply", id="preview-apply", variant="primary")

    def on_mount(self) -> None:
        self._render_content()
        self.query_one("#preview-apply", Button).focus()

    def _render_content(self) -> None:
        # The action is part of this modal's identity, not decoration:
        # "Pause · Preview" and "Resume · Preview" must never be
        # mistaken for each other.
        self.query_one(f"#{self.DIALOG_ID}").border_title = (
            f"{self.plan.action.title()} · {self.MODAL_TITLE}"
        )
        self.query_one("#preview-content", Static).update(
            _format_preview_text(self.plan, self.snapshot_at, stale=self.stale)
        )

    def mark_stale(self) -> None:
        """Permanently mark this preview's snapshot generation stale.

        One-way (see the class docstring): recovery never clears it. A
        no-op while a mutation is already in flight -- the request was
        dispatched against a snapshot that *was* fresh, and relabelling
        the button mid-flight would neither undo it nor add information.
        """
        if self.stale or self.applying:
            return
        self.stale = True
        self._render_content()
        self._sync_buttons()

    def set_applying(self, applying: bool) -> None:
        """Freeze the modal while a mutation is actually in flight --
        disables both buttons (Cancel too: see
        `QbitOpsTuiApp.action_dismiss_overlay`) and relabels Apply so
        double-pressing it (or pressing Enter twice) cannot dispatch a
        second mutation."""
        self.applying = applying
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        apply_button = self.query_one("#preview-apply", Button)
        apply_button.disabled = not self.can_apply
        if self.applying:
            apply_button.label = "Applying..."
        elif self.stale:
            apply_button.label = "Apply unavailable"
        else:
            apply_button.label = "Apply"
        self.query_one("#preview-cancel", Button).disabled = self.applying

    def on_button_pressed(self, event: Button.Pressed) -> None:
        app = cast("QbitOpsTuiApp", self.app)
        if event.button.id == "preview-cancel":
            if not self.applying:
                self.dismiss()
        elif event.button.id == "preview-apply":
            app.action_apply_plan()

    def action_dismiss(self, result: None = None) -> None:  # type: ignore[override]
        if self.applying:
            return
        self.dismiss()
