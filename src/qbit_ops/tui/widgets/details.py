"""`DetailsPanel` -- the focused torrent's safe detail view.

Moved out of `qbit_ops.tui.app` (see docs/DECISIONS.md, TUI reorg
phase). No behavior change.
"""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widgets import Static

from qbit_ops.tui.formatting import (
    _format_byte_rate,
    _format_endpoint,
    _format_local_time,
    _shorten_hash,
)
from qbit_ops.tui.state import TuiState


class DetailsPanel(VerticalScroll):
    """Safe details for the focused torrent, grouped into Identity,
    Transfer, and Trackers sections.

    Only ever renders `SelectedTorrent` fields (live from the periodic
    snapshot) and `get_safe_tracker_details`-shaped structural tracker
    fields -- never a raw announce URL, path, query value, userinfo, or
    unsanitized message.
    """

    def render_state(self, state: TuiState) -> None:
        """Render the currently focused torrent's safe details, or an
        explicit empty state when nothing is focused."""
        self.remove_children()
        torrent = state.focused_torrent()

        if torrent is None:
            self.mount(Static("No torrent focused."))
            return

        identity_lines = [
            f"[bold]{torrent.name}[/bold]",
            f"Hash: {_shorten_hash(torrent.hash)}  [dim](c to copy)[/dim]",
            f"Category: {torrent.category}",
        ]
        self.mount(Static("\n".join(identity_lines), classes="d-section"))

        transfer_lines = [
            "[bold]Transfer[/bold]",
            f"State: {torrent.state}",
            f"Progress: {torrent.progress * 100:.1f}%   "
            f"Ratio: {torrent.ratio:.2f}",
            f"Down: {_format_byte_rate(torrent.download_rate)}   "
            f"Up: {_format_byte_rate(torrent.upload_rate)}",
        ]
        self.mount(Static("\n".join(transfer_lines), classes="d-section"))

        tracker_details = state.focused_tracker_details
        if tracker_details is None:
            self.mount(
                Static(
                    "[bold]Trackers[/bold]\n  loading...", classes="d-section"
                )
            )
        else:
            fetched_at = state.focused_details_fetched_at
            freshness = (
                f"fetched {_format_local_time(fetched_at)}"
                if fetched_at is not None
                else ""
            )
            lines = [f"[bold]Trackers[/bold] ({freshness})"]
            if not tracker_details:
                lines.append("  (none)")
            else:
                lines.extend(
                    f"  {_format_endpoint(endpoint)}"
                    for endpoint in tracker_details
                )
            self.mount(Static("\n".join(lines), classes="d-section"))
