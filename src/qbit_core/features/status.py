"""Collect and represent a qBittorrent instance status snapshot.

This module is the status "service": it must remain callable without
Typer and without Rich so it can be reused by future consumers (a
`--watch` loop, a TUI) without pulling in CLI or presentation concerns.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from qbit_core.qbit.fields import (
    get_field,
    get_field_as_float,
    get_field_as_int,
    get_field_as_string,
    get_optional_bool,
    get_optional_int,
    get_transfer_rates,
)
from qbit_core.shared.torrent_states import (
    TorrentStateGroup,
    classify_torrent_state,
    is_completed_torrent,
    is_stalled_up_without_network_load,
)

__all__ = [
    "Health",
    "InstanceStats",
    "StatusSnapshot",
    "TransferRates",
    "build_status_snapshot_from_data",
    "build_unavailable_snapshot",
    "collect_instance_stats",
    "collect_status_snapshot",
    "snapshot_to_csv_rows",
    "snapshot_to_json_dict",
    "status_exit_code",
    "watch_status",
]

SCHEMA_VERSION = "1"


class Health(StrEnum):
    """Represent the overall operational health of a status snapshot."""

    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class TransferCounts:
    """Count torrents grouped by activity state."""

    total: int
    downloading: int
    seeding: int
    completed: int
    stalled: int
    checking: int
    errored: int
    unknown: int


@dataclass(frozen=True)
class TransferRates:
    """Report current global transfer speeds."""

    download_bytes_per_second: int
    upload_bytes_per_second: int


@dataclass(frozen=True)
class InstanceStats:
    """One `sync_maindata()['server_state']` reduced to the values a
    dashboard shows, distinct from `TransferRates` (current speed) --
    not derived from `transfer_info()`. `instance_id`: unused hook for
    a future multi-instance caller.

    Three fields carry a value qBittorrent encodes as a marker rather
    than a measure, and each keeps `None` for "unknown" instead of a
    fabricated number a formatter would happily print:

    - `all_time_ratio`: `None`, never a fabricated `0.0`/`-1`.
    - `free_space_bytes`: `None` for any negative value. `-1` is a
      plain `int` on the wire, so an ordinary byte formatter renders
      it as "-1 B" without complaint.
    - `connection_status`: `None` when unreported. `firewalled` is a
      degraded success, not a failure, and must not read as connected.

    `download_rate_limit`/`upload_rate_limit` stay plain `int`: `0` is
    qBittorrent's encoding for "no limit", a real value rather than a
    missing one.
    """

    instance_id: str | None
    all_time_downloaded_bytes: int
    all_time_uploaded_bytes: int
    all_time_ratio: float | None
    connected_peers: int
    session_downloaded_bytes: int = 0
    session_uploaded_bytes: int = 0
    dht_nodes: int = 0
    download_rate_limit: int = 0
    upload_rate_limit: int = 0
    alternative_limits_enabled: bool = False
    free_space_bytes: int | None = None
    queueing_enabled: bool = False
    connection_status: str | None = None


def build_instance_stats_from_server_state(
    server_state: Mapping[str, Any],
    *,
    instance_id: str | None = None,
) -> InstanceStats:
    """Build `InstanceStats` from `sync_maindata()`'s `server_state`, zero
    API calls itself. `global_ratio` (`-1`/unparsable) normalizes to
    `None`."""
    raw_ratio = get_field(server_state, "global_ratio", None)
    ratio: float | None
    try:
        parsed_ratio = float(raw_ratio)
    except (TypeError, ValueError):
        ratio = None
    else:
        ratio = parsed_ratio if parsed_ratio >= 0 else None

    return InstanceStats(
        instance_id=instance_id,
        all_time_downloaded_bytes=get_field_as_int(server_state, "alltime_dl"),
        all_time_uploaded_bytes=get_field_as_int(server_state, "alltime_ul"),
        all_time_ratio=ratio,
        connected_peers=get_field_as_int(
            server_state, "total_peer_connections"
        ),
        session_downloaded_bytes=get_field_as_int(server_state, "dl_info_data"),
        session_uploaded_bytes=get_field_as_int(server_state, "up_info_data"),
        dht_nodes=get_field_as_int(server_state, "dht_nodes"),
        download_rate_limit=get_field_as_int(server_state, "dl_rate_limit"),
        upload_rate_limit=get_field_as_int(server_state, "up_rate_limit"),
        alternative_limits_enabled=bool(
            get_optional_bool(server_state, "use_alt_speed_limits")
        ),
        free_space_bytes=_free_space_or_none(server_state),
        queueing_enabled=bool(get_optional_bool(server_state, "queueing")),
        connection_status=(
            get_field_as_string(server_state, "connection_status") or None
        ),
    )


def _free_space_or_none(server_state: Mapping[str, Any]) -> int | None:
    """`free_space_on_disk` as a real byte count, or `None` when unknown.

    Negative is qBittorrent's "could not determine" marker, and it is an
    ordinary `int` on the wire -- so it has to be rejected here rather
    than left for a byte formatter that would print "-1 B".
    """
    free_space = get_optional_int(server_state, "free_space_on_disk")
    if free_space is None or free_space < 0:
        return None
    return free_space


def collect_instance_stats(
    client: Any,
    *,
    instance_id: str | None = None,
) -> InstanceStats:
    """One `sync_maindata()` call; only its `server_state` is used."""
    maindata = client.sync_maindata()
    server_state = get_field(maindata, "server_state", {})
    return build_instance_stats_from_server_state(
        server_state, instance_id=instance_id
    )


@dataclass(frozen=True)
class StatusAlert:
    """Describe one condition worth surfacing to an operator."""

    code: str
    severity: Health
    message: str
    count: int | None = None


@dataclass(frozen=True)
class StatusSnapshot:
    """Represent one point-in-time qBittorrent status snapshot."""

    schema_version: str
    generated_at: datetime
    health: Health
    connected: bool
    host: str | None
    qbittorrent_version: str | None
    api_version: str | None
    counts: TransferCounts
    rates: TransferRates
    alerts: tuple[StatusAlert, ...]


def collect_status_snapshot(
    client: Any,
    *,
    host: str | None = None,
) -> StatusSnapshot:
    """Collect a status snapshot using a bounded number of API calls.

    Performs exactly four calls regardless of torrent count:
    `app_version()`, `app_web_api_version()`, `transfer_info()`, and
    `torrents_info()`. It never calls `torrents_trackers()`, so it does
    not introduce an N+1 pattern against the torrent list.
    """
    qbittorrent_version = _get_optional_value(client, "app_version")
    api_version = _get_optional_value(client, "app_web_api_version")
    transfer_info_data = client.transfer_info()
    torrents = list(client.torrents_info())

    return build_status_snapshot_from_data(
        qbittorrent_version=qbittorrent_version,
        api_version=api_version,
        transfer_info_data=transfer_info_data,
        torrents=torrents,
        host=host,
    )


def build_status_snapshot_from_data(
    *,
    qbittorrent_version: str | None,
    api_version: str | None,
    transfer_info_data: Any,
    torrents: list[Any],
    host: str | None = None,
) -> StatusSnapshot:
    """Build a status snapshot from already-collected qBittorrent data.

    Performs zero API calls itself: every value is derived from data the
    caller already fetched. This is the seam a caller collecting a
    torrent list for another purpose in the same cycle (e.g. a TUI's
    periodic refresh, see `qbit_ops.app_services`) uses to avoid a second,
    redundant `torrents_info()`/`transfer_info()` call --
    `collect_status_snapshot` itself is just this function plus the four
    API calls that feed it.
    """
    rates = _transfer_rates_from_data(transfer_info_data)
    counts, unknown_states, unexplained_stalled = _count_torrents(torrents)
    alerts = _build_alerts(counts, unknown_states, unexplained_stalled)
    health = _compute_health(counts, unexplained_stalled)

    return StatusSnapshot(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(UTC),
        health=health,
        connected=True,
        host=_redact_host(host) if host else None,
        qbittorrent_version=qbittorrent_version,
        api_version=api_version,
        counts=counts,
        rates=rates,
        alerts=alerts,
    )


def build_unavailable_snapshot(
    *,
    code: str,
    message: str,
    host: str | None = None,
) -> StatusSnapshot:
    """Build a status snapshot representing an unreachable instance."""
    return StatusSnapshot(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(UTC),
        health=Health.UNAVAILABLE,
        connected=False,
        host=_redact_host(host) if host else None,
        qbittorrent_version=None,
        api_version=None,
        counts=TransferCounts(0, 0, 0, 0, 0, 0, 0, 0),
        rates=TransferRates(0, 0),
        alerts=(
            StatusAlert(
                code=code,
                severity=Health.UNAVAILABLE,
                message=message,
            ),
        ),
    )


EXIT_CODE_BY_HEALTH: dict[Health, int] = {
    Health.HEALTHY: 0,
    Health.WARNING: 1,
    Health.CRITICAL: 2,
    Health.UNAVAILABLE: 3,
}


def status_exit_code(health: Health) -> int:
    """Map a status health value to its documented CLI exit code."""
    return EXIT_CODE_BY_HEALTH[health]


def watch_status(
    *,
    collect: Callable[[], StatusSnapshot],
    emit: Callable[[StatusSnapshot], None],
    interval: float,
    sleep: Callable[[float], None],
    now: Callable[[], float],
) -> None:
    """Run collect -> emit -> wait, forever.

    No Typer, no Rich, no qBittorrent client: `collect`/`emit` are the
    only seams, plus `sleep`/`now` (a monotonic clock) for deterministic
    tests. Timing targets the *start* of each cycle, so a slow
    `collect()` never accumulates drift or overlaps the next cycle.
    Never returns on its own -- the only way out is an exception raised
    by `collect`, `emit`, or `sleep` (including `KeyboardInterrupt`).
    """
    while True:
        cycle_started_at = now()

        snapshot = collect()
        emit(snapshot)

        elapsed = now() - cycle_started_at
        remaining = interval - elapsed
        if remaining > 0:
            sleep(remaining)


def snapshot_to_json_dict(snapshot: StatusSnapshot) -> dict[str, Any]:
    """Convert a status snapshot into a JSON-serializable structure."""
    return {
        "schema_version": snapshot.schema_version,
        "generated_at": snapshot.generated_at.isoformat(),
        "health": snapshot.health.value,
        "connection": {
            "connected": snapshot.connected,
            "host": snapshot.host,
            "qbittorrent_version": snapshot.qbittorrent_version,
            "api_version": snapshot.api_version,
        },
        "transfers": {
            "total": snapshot.counts.total,
            "downloading": snapshot.counts.downloading,
            "seeding": snapshot.counts.seeding,
            "completed": snapshot.counts.completed,
            "stalled": snapshot.counts.stalled,
            "checking": snapshot.counts.checking,
            "errored": snapshot.counts.errored,
            "unknown": snapshot.counts.unknown,
        },
        "rates": {
            "download_bytes_per_second": (
                snapshot.rates.download_bytes_per_second
            ),
            "upload_bytes_per_second": snapshot.rates.upload_bytes_per_second,
        },
        "alerts": [
            {
                "code": alert.code,
                "severity": alert.severity.value,
                "message": alert.message,
                "count": alert.count,
            }
            for alert in snapshot.alerts
        ],
    }


def snapshot_to_csv_rows(
    snapshot: StatusSnapshot,
) -> list[tuple[str, str, str]]:
    """Convert a status snapshot into stable `section,key,value` rows."""
    rows: list[tuple[str, str, str]] = [
        ("connection", "health", snapshot.health.value),
        ("connection", "connected", str(snapshot.connected).lower()),
        ("connection", "host", snapshot.host or ""),
        (
            "connection",
            "qbittorrent_version",
            snapshot.qbittorrent_version or "",
        ),
        ("connection", "api_version", snapshot.api_version or ""),
        ("transfers", "total", str(snapshot.counts.total)),
        ("transfers", "downloading", str(snapshot.counts.downloading)),
        ("transfers", "seeding", str(snapshot.counts.seeding)),
        ("transfers", "completed", str(snapshot.counts.completed)),
        ("transfers", "stalled", str(snapshot.counts.stalled)),
        ("transfers", "checking", str(snapshot.counts.checking)),
        ("transfers", "errored", str(snapshot.counts.errored)),
        ("transfers", "unknown", str(snapshot.counts.unknown)),
        (
            "rates",
            "download_bytes_per_second",
            str(snapshot.rates.download_bytes_per_second),
        ),
        (
            "rates",
            "upload_bytes_per_second",
            str(snapshot.rates.upload_bytes_per_second),
        ),
    ]

    for alert in snapshot.alerts:
        rows.append(("alerts", alert.code, alert.message))

    return rows


def _transfer_rates_from_data(transfer_info: Any) -> TransferRates:
    """Build transfer rates from an already-fetched `transfer_info()` result.

    Routes through `qbit_core.qbit.fields.get_transfer_rates` instead of
    calling `.get()` on `transfer_info` directly: a
    malformed non-mapping payload now fails with an explicit `TypeError`
    there rather than an accidental `AttributeError` here.
    """
    download, upload = get_transfer_rates(transfer_info)
    return TransferRates(
        download_bytes_per_second=download,
        upload_bytes_per_second=upload,
    )


def _count_torrents(
    torrents: list[Any],
) -> tuple[TransferCounts, frozenset[str], int]:
    """Group torrents by state and count completed/unexplained-stalled ones.

    `unexplained_stalled` excludes `stalledUP` torrents completed purely
    from local data (see `is_stalled_up_without_network_load`) -- they
    need no action, so neither health nor the `torrents_stalled` alert
    may count them. `TransferCounts.stalled` itself stays the raw
    per-state tally reported to callers.
    """
    group_totals: dict[TorrentStateGroup, int] = {
        "downloading": 0,
        "seeding": 0,
        "stalled": 0,
        "checking": 0,
        "errored": 0,
        "unknown": 0,
    }
    unknown_states: set[str] = set()
    completed = 0
    unexplained_stalled = 0

    for torrent in torrents:
        state = get_field_as_string(torrent, "state")
        group = classify_torrent_state(state)
        group_totals[group] += 1
        if group == "unknown" and state != "":
            unknown_states.add(state)

        if group == "stalled":
            downloaded = get_field_as_int(torrent, "downloaded")
            progress = get_field_as_float(torrent, "progress")
            if not is_stalled_up_without_network_load(
                state, downloaded, progress
            ):
                unexplained_stalled += 1

        if is_completed_torrent(torrent):
            completed += 1

    counts = TransferCounts(
        total=len(torrents),
        downloading=group_totals["downloading"],
        seeding=group_totals["seeding"],
        completed=completed,
        stalled=group_totals["stalled"],
        checking=group_totals["checking"],
        errored=group_totals["errored"],
        unknown=group_totals["unknown"],
    )
    return counts, frozenset(unknown_states), unexplained_stalled


def _build_alerts(
    counts: TransferCounts,
    unknown_states: frozenset[str],
    unexplained_stalled: int,
) -> tuple[StatusAlert, ...]:
    """Build structured alerts from computed torrent counts.

    `torrents_stalled` counts `unexplained_stalled`, not
    `counts.stalled`: a stalled torrent that needs no action does not
    belong in a signal meant to say "intervene".
    """
    alerts: list[StatusAlert] = []

    if counts.errored > 0:
        alerts.append(
            StatusAlert(
                code="torrents_errored",
                severity=Health.CRITICAL,
                message=f"{counts.errored} torrent(s) reporting an error",
                count=counts.errored,
            )
        )

    if unexplained_stalled > 0:
        alerts.append(
            StatusAlert(
                code="torrents_stalled",
                severity=Health.WARNING,
                message=f"{unexplained_stalled} stalled torrent(s)",
                count=unexplained_stalled,
            )
        )

    if counts.unknown > 0:
        state_list = ", ".join(sorted(unknown_states))
        alerts.append(
            StatusAlert(
                code="torrents_unknown_state",
                severity=Health.WARNING,
                message=(
                    f"{counts.unknown} torrent(s) in an unrecognized state"
                    f" ({state_list})"
                ),
                count=counts.unknown,
            )
        )

    return tuple(alerts)


def _compute_health(counts: TransferCounts, unexplained_stalled: int) -> Health:
    """Compute overall health, keeping the most severe condition.

    Uses `unexplained_stalled`, not `counts.stalled`: a `stalledUP`
    torrent completed purely from local data needs no action and must
    not by itself turn a healthy library into a warning one.
    """
    if counts.errored > 0:
        return Health.CRITICAL
    if unexplained_stalled > 0 or counts.unknown > 0:
        return Health.WARNING
    return Health.HEALTHY


def _get_optional_value(client: Any, method_name: str) -> str | None:
    """Read an optional string value from a qBittorrent API method."""
    method = getattr(client, method_name, None)
    if method is None:
        return None

    try:
        value = method()
    except Exception:
        return None

    return str(value)


def _redact_host(host: str) -> str:
    """Strip any embedded credentials from a configured instance URL."""
    parsed_host = urlsplit(host)
    if "@" not in parsed_host.netloc:
        return host

    netloc = parsed_host.netloc.rsplit("@", 1)[-1]
    return urlunsplit(
        (parsed_host.scheme, netloc, parsed_host.path, parsed_host.query, "")
    )
