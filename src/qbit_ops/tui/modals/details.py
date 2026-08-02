"""`DetailsScreen` -- the sole access path to a torrent's full details.

See `qbit_ops.tui.modals`'s module docstring for the `self.app` typing
note.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen

from qbit_ops.tui.widgets.details import DetailsPanel

if TYPE_CHECKING:
    from qbit_ops.tui.app import QbitOpsTuiApp


class DetailsScreen(ModalScreen[None]):
    """A wide modal Details view, opened by `enter` at every terminal
    width -- the sole way to see a focused torrent's full detail, now
    that the permanent side panel is gone.

    Rebinds `c`/`e`/`r` here, delegating to `QbitOpsTuiApp`: a
    non-priority App binding is unreachable while a `ModalScreen` is on
    top (see `FiltersScreen` for the `priority=True` exception).
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close", priority=True),
        Binding("c", "copy_hash", "Copy hash"),
        Binding("e", "explain", "Explain"),
        Binding("r", "refresh", "Refresh"),
    ]

    CSS = """
    DetailsScreen {
        align: center middle;
    }
    #details-dialog {
        width: 86%;
        min-width: 60;
        max-width: 100;
        height: auto;
        max-height: 80%;
        border: round #ff9933;
        background: $background;
        padding: 1 2;
    }
    #details-identity {
        height: auto;
        text-align: center;
        margin-bottom: 1;
    }
    #details-metrics {
        height: auto;
        text-align: center;
    }
    #details-trackers-heading {
        height: 1;
        text-align: center;
        text-style: bold;
        border-top: solid $panel-lighten-2;
        padding-top: 1;
    }
    #details-trackers {
        height: auto;
        text-align: center;
    }
    #details-footer {
        height: auto;
        text-align: center;
        border-top: solid $panel-lighten-2;
        padding-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="details-dialog"):
            yield DetailsPanel()

    def on_mount(self) -> None:
        self.query_one("#details-dialog").border_title = "Torrent details"
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
