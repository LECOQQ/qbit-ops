"""Collect and represent a qBittorrent instance status snapshot.

This module is the status "service": it must remain callable without
Typer and without Rich so it can be reused by future consumers (a
`--watch` loop, a TUI) without pulling in CLI or presentation concerns.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.qbit_fields import get_field_as_string
from app.torrent_states import (
    TorrentStateGroup,
    classify_torrent_state,
    is_completed_torrent,
)

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
    periodic refresh, see `app.app_services`) uses to avoid a second,
    redundant `torrents_info()`/`transfer_info()` call --
    `collect_status_snapshot` itself is just this function plus the four
    API calls that feed it.
    """
    rates = _transfer_rates_from_data(transfer_info_data)
    counts, unknown_states = _count_torrents(torrents)
    alerts = _build_alerts(counts, unknown_states)
    health = _compute_health(counts)

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
    """Run collect → emit → wait, forever, reusing the existing snapshot model.

    No Typer, no Rich, no qBittorrent client: `collect` and `emit` are
    the only seams, plus `sleep`/`now` for deterministic tests (`now`
    should be a monotonic clock; real callers pass `time.monotonic`).

    Timing targets the *start* of each cycle, not a fixed post-collect
    delay, so a slow `collect()` does not accumulate drift: if a cycle
    takes longer than `interval`, the next collection starts immediately
    (never a negative sleep, never two overlapping collections — this
    loop is strictly sequential, no concurrency).

    Never returns on its own. The only ways out are an exception raised
    by `collect`, `emit`, or `sleep` (including `KeyboardInterrupt`,
    which callers are expected to catch) — there is no other stop
    condition, by design; this is a tight loop, not a scheduler
    framework.
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
    """Build transfer rates from an already-fetched `transfer_info()` result."""
    return TransferRates(
        download_bytes_per_second=_as_int(
            transfer_info.get("dl_info_speed", 0)
        ),
        upload_bytes_per_second=_as_int(transfer_info.get("up_info_speed", 0)),
    )


def _count_torrents(
    torrents: list[Any],
) -> tuple[TransferCounts, frozenset[str]]:
    """Group torrents by state and count completed torrents by progress."""
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

    for torrent in torrents:
        state = get_field_as_string(torrent, "state")
        group = classify_torrent_state(state)
        group_totals[group] += 1
        if group == "unknown" and state != "":
            unknown_states.add(state)

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
    return counts, frozenset(unknown_states)


def _build_alerts(
    counts: TransferCounts,
    unknown_states: frozenset[str],
) -> tuple[StatusAlert, ...]:
    """Build structured alerts from computed torrent counts."""
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

    if counts.stalled > 0:
        alerts.append(
            StatusAlert(
                code="torrents_stalled",
                severity=Health.WARNING,
                message=f"{counts.stalled} stalled torrent(s)",
                count=counts.stalled,
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


def _compute_health(counts: TransferCounts) -> Health:
    """Compute overall health, keeping the most severe condition."""
    if counts.errored > 0:
        return Health.CRITICAL
    if counts.stalled > 0 or counts.unknown > 0:
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


def _as_int(value: Any) -> int:
    """Convert a qBittorrent numeric field to int, defaulting to zero."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _redact_host(host: str) -> str:
    """Strip any embedded credentials from a configured instance URL."""
    parsed_host = urlsplit(host)
    if "@" not in parsed_host.netloc:
        return host

    netloc = parsed_host.netloc.rsplit("@", 1)[-1]
    return urlunsplit(
        (parsed_host.scheme, netloc, parsed_host.path, parsed_host.query, "")
    )
