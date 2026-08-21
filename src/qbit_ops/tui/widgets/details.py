"""`DetailsPanel` -- the Details modal's full content."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from qbit_ops.tui.formatting import (
    _details_dialog_content_width,
    _format_details_identity,
    _format_details_metrics,
    _format_details_trackers,
)
from qbit_ops.tui.state import TuiState


class DetailsPanel(Vertical):
    """Safe details for the focused torrent.

    A plain container, never a scroll region: `QbitModal`'s dialog is
    the one thing that scrolls, in this modal as in every other. Two
    nested scroll areas would split the wheel and the arrow keys
    between them.

    Only ever renders `TorrentSnapshot` fields (live from the periodic
    snapshot) and `get_safe_tracker_details`-shaped structural tracker
    data -- never a raw announce URL, path, query value, or
    unsanitized message.
    """

    def compose(self) -> ComposeResult:
        yield Static(id="details-identity")
        yield Static(id="details-metrics")
        yield Static("Trackers", id="details-trackers-heading")
        yield Static(id="details-trackers")

    def render_state(self, state: TuiState, *, app_width: int) -> None:
        torrent = state.focused_torrent()
        identity = self.query_one("#details-identity", Static)
        metrics = self.query_one("#details-metrics", Static)
        trackers = self.query_one("#details-trackers", Static)

        if torrent is None:
            identity.update("No torrent focused.")
            metrics.update("")
            trackers.update("")
            return

        name_width = _details_dialog_content_width(app_width)
        identity.update(
            _format_details_identity(torrent, name_width=name_width)
        )
        metrics.update(_format_details_metrics(torrent))
        trackers.update(
            _format_details_trackers(
                state.focused_tracker_details,
                state.focused_details_fetched_at,
                fetch_failed=state.focused_tracker_fetch_failed,
                peer_discovery=state.focused_peer_discovery,
            )
        )
