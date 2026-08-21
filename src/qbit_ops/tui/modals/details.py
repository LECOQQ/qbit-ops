"""`DetailsScreen` -- the sole access path to a torrent's full details.

See `qbit_ops.tui.modals`'s module docstring for the `self.app` typing
note.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.binding import Binding

from qbit_ops.tui.modals.base import KeyHint, QbitModal
from qbit_ops.tui.widgets.details import DetailsPanel

if TYPE_CHECKING:
    from qbit_ops.tui.app import QbitOpsTuiApp


class DetailsScreen(QbitModal):
    """A wide modal Details view, opened by `enter` at every terminal
    width -- the sole way to see a focused torrent's full detail.

    Rebinds `c`/`e`/`r` here, delegating to `QbitOpsTuiApp`: a
    non-priority App binding is unreachable while a `ModalScreen` is on
    top (see `FiltersScreen` for the `priority=True` exception).
    """

    MODAL_TITLE = "Torrent details"
    MODAL_WIDTH = "large"
    MODAL_KEYS = (
        KeyHint(("up", "down"), "Scroll"),
        KeyHint(("c",), "Copy hash"),
        KeyHint(("e",), "Explain"),
        KeyHint(("r",), "Refresh"),
        KeyHint(("escape",), "Close"),
    )
    DIALOG_ID = "details-dialog"

    BINDINGS = [
        Binding("escape", "dismiss", "Close", priority=True),
        Binding("c", "copy_hash", "Copy hash"),
        Binding("e", "explain", "Explain"),
        Binding("r", "refresh", "Refresh"),
    ]

    def compose_dialog(self) -> ComposeResult:
        yield DetailsPanel()

    def on_mount(self) -> None:
        app = cast("QbitOpsTuiApp", self.app)
        # Snapshot fields first, immediately -- tracker details (which
        # may still be loading, or stale from a prior focus) update in
        # place once the fetch dispatched below completes.
        self._render_panel()
        app.action_refresh_details()
        # Safety net: if the dispatch above never resolves (its result
        # discarded by an in-between focus/refresh request-id bump, or
        # simply lost), a bare "Loading..." with no user action would
        # hang forever. One silent, automatic retry after a few seconds
        # -- same effect as pressing `r` -- without waiting on it here.
        self.set_timer(4, self._retry_if_still_loading)

    def _retry_if_still_loading(self) -> None:
        app = cast("QbitOpsTuiApp", self.app)
        state = app.controller.state
        if (
            state.focused_tracker_details is None
            and not state.focused_tracker_fetch_failed
        ):
            app.action_refresh_details()

    def _render_panel(self) -> None:
        # Named `_render_panel`, not `_render`: `Widget._render()` is
        # Textual's own internal rendering-pipeline method (returns a
        # `Visual`) -- overriding it here silently broke this screen's
        # own paint (it started returning `None` instead of a `Visual`).
        app = cast("QbitOpsTuiApp", self.app)
        self.query_one(DetailsPanel).render_state(
            app.controller.state, app_width=app.size.width
        )

    def action_dismiss(self, result: None = None) -> None:  # type: ignore[override]
        self.dismiss()

    def action_copy_hash(self) -> None:
        app = cast("QbitOpsTuiApp", self.app)
        app.action_copy_hash()

    def action_explain(self) -> None:
        app = cast("QbitOpsTuiApp", self.app)
        app.action_explain()

    def action_refresh(self) -> None:
        app = cast("QbitOpsTuiApp", self.app)
        app.action_refresh_details()
