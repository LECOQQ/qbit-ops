"""The Overview workspace: `WorkspaceTabs` + `OverviewPanel`."""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widgets import Static

from qbit_ops.features.status import Health
from qbit_ops.tui.formatting import _format_byte_rate, _format_local_time
from qbit_ops.tui.state import ConnectionState, TuiState, Workspace

_HEALTH_STYLES: dict[Health, str] = {
    Health.HEALTHY: "bold green",
    Health.WARNING: "bold yellow",
    Health.CRITICAL: "bold red",
    Health.UNAVAILABLE: "bold red",
}

_CONNECTION_LABELS: dict[ConnectionState, str] = {
    ConnectionState.CONNECTING: "connecting",
    ConnectionState.CONNECTED: "connected",
    ConnectionState.RECONNECTING: "reconnecting",
    ConnectionState.AUTH_FAILED: "unavailable (authentication failed)",
    ConnectionState.CONFIG_FAILED: "unavailable (configuration invalid)",
}

_OVERVIEW_NAV_HINT = "[bold]Enter[/bold] / [bold]t[/bold]   Browse torrents"


class WorkspaceTabs(Static):
    """Always-visible indicator of which workspace is active.

    Purely presentational -- reflects `TuiState.workspace`, never
    decides navigation itself (see `QbitOpsTuiApp._switch_workspace`).
    """

    def render_state(self, workspace: Workspace) -> None:
        overview = _tab_label(
            "Overview", "1/g", workspace is Workspace.OVERVIEW
        )
        torrents = _tab_label(
            "Torrents", "2/t", workspace is Workspace.TORRENTS
        )
        self.update(f"{overview}   {torrents}")


def _tab_label(name: str, keys: str, active: bool) -> str:
    text = f"{name} ({keys})"
    return f"[reverse bold] {text} [/reverse bold]" if active else f" {text} "


class OverviewPanel(VerticalScroll):
    """The Overview workspace's content, grouped into distinct conceptual
    cards -- built entirely from the same `TuiState` the periodic
    refresh already populates. No qBittorrent call of its own.

    Deliberately three independent dimensions rather than one partition
    of "total": Activity (transfer state), Completion (progress), and
    Attention (conditions worth notice) -- a torrent can count toward
    more than one at once (e.g. seeding *and* completed *and* stopped).
    """

    def render_state(self, state: TuiState) -> None:
        self.remove_children()

        if state.status is None:
            self.mount(
                Static("Connecting to qBittorrent...", classes="ov-card")
            )
            self.mount(Static(_OVERVIEW_NAV_HINT, classes="ov-nav"))
            return

        self.mount(Static(_overview_connection_text(state), classes="ov-card"))
        self.mount(Static(_overview_transfer_text(state), classes="ov-card"))
        self.mount(Static(_overview_activity_text(state), classes="ov-card"))
        self.mount(Static(_overview_completion_text(state), classes="ov-card"))
        self.mount(
            Static(
                _overview_attention_text(state),
                classes="ov-card ov-attention",
            )
        )
        self.mount(
            Static(
                _overview_alerts_text(state),
                classes="ov-card ov-attention",
            )
        )
        self.mount(Static(_OVERVIEW_NAV_HINT, classes="ov-nav"))


def _overview_connection_text(state: TuiState) -> str:
    status = state.status
    assert status is not None
    label = _CONNECTION_LABELS[state.connection]
    lines = [f"[bold]Connection[/bold]: {label}"]
    if state.last_successful_refresh is not None:
        lines.append(
            f"  last successful refresh: "
            f"{_format_local_time(state.last_successful_refresh)}"
        )
    else:
        lines.append("  last successful refresh: never")
    if state.stale:
        lines.append(
            "  [bold yellow]STALE[/bold yellow] -- showing last-good data"
        )
    if status.qbittorrent_version:
        lines.append(f"  qBittorrent {status.qbittorrent_version}")
    if status.api_version:
        lines.append(f"  Web API {status.api_version}")
    return "\n".join(lines)


def _overview_transfer_text(state: TuiState) -> str:
    status = state.status
    assert status is not None
    down = _format_byte_rate(status.rates.download_bytes_per_second)
    up = _format_byte_rate(status.rates.upload_bytes_per_second)
    return f"[bold]Transfer[/bold]\n  ↓ {down}   ↑ {up}"


def _overview_activity_text(state: TuiState) -> str:
    status = state.status
    assert status is not None
    counts = status.counts
    return (
        f"[bold]Activity[/bold] · {counts.total} total\n"
        f"  {counts.downloading} downloading · {counts.seeding} seeding\n"
        f"  {state.stopped_count} stopped · {counts.checking} checking"
    )


def _overview_completion_text(state: TuiState) -> str:
    status = state.status
    assert status is not None
    counts = status.counts
    incomplete = max(counts.total - counts.completed, 0)
    return (
        "[bold]Completion[/bold]\n"
        f"  {counts.completed} completed · {incomplete} incomplete"
    )


def _overview_attention_text(state: TuiState) -> str:
    status = state.status
    assert status is not None
    counts = status.counts
    return (
        "[bold]Attention[/bold]\n"
        f"  {counts.stalled} stalled · {counts.errored} errored · "
        f"{counts.unknown} unknown"
    )


def _overview_alerts_text(state: TuiState) -> str:
    status = state.status
    assert status is not None
    style = _HEALTH_STYLES[status.health]
    alerts = status.alerts
    header = (
        f"[bold]Health[/bold]\n"
        f"  [{style}]{status.health.value.title()}[/{style}] · "
        f"{len(alerts)} finding(s)"
    )
    lines = [header]
    lines.extend(f"  {alert.message}" for alert in alerts)
    return "\n".join(lines)
