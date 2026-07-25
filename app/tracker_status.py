"""Collect and represent a filter-aware tracker status report.

Mirrors `app.doctor`/`app.status`'s collection/render split: this module
stays free of Typer and Rich so it can be reused by any future interface.
It reuses `app.torrents.select_torrents` for the shared, cheap torrent
filters (category/state/completed/active/stalled/errored) but performs
its own per-torrent `torrents_trackers()` collection rather than going
through `select_torrents`'s `tracker` filter -- that filter narrows the
*torrent* selection to one host and propagates lookup exceptions, while
`trackers status` needs per-torrent error tolerance (see
`collect_tracker_status`) and reports per-tracker aggregates, not a
torrent list.

Tracker identities are always `host` or `host:port`
(`app.trackers.normalize_tracker_host`), never a full announce URL: a
private tracker's announce URL commonly embeds a passkey in its path or
query string, and nothing in this module's output (table, JSON, JSONL,
CSV, or exception/log text) may render one. DHT/PeX/LSD pseudo-tracker
entries (qBittorrent represents these as sentinel strings like
`"** [DHT] **"`, not URLs) are excluded from aggregation entirely --
`normalize_tracker_host` cannot parse them as a host, and they are not
operationally meaningful trackers to report on.

Port numbers are preserved verbatim, never collapsed to a scheme's
default (e.g. `https://host:443` and `https://host` normalize to two
distinct identities, `host:443` and `host`): `normalize_tracker_host`
is reused unchanged rather than given a second, scheme-aware notion of
"the same host" that could disagree with every other command using it
(`torrents list --tracker`, bulk mutations).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from app.qbit_fields import get_field_as_string
from app.torrents import TorrentFilter, select_torrents, torrent_filter_to_dict
from app.trackers import (
    TrackerHealth,
    classify_raw_tracker_status,
    compute_tracker_aggregate_health,
    get_raw_tracker_status,
    normalize_tracker_host,
    sanitize_tracker_text,
)

__all__ = [
    "SCHEMA_VERSION",
    "TrackerHealth",
    "classify_raw_tracker_status",
    "TrackerEndpointObservation",
    "TrackerAggregate",
    "TrackerStatusReport",
    "tracker_status_exit_code",
    "collect_tracker_status",
    "tracker_aggregate_to_dict",
    "tracker_status_report_to_dict",
    "TRACKER_STATUS_CSV_FIELDNAMES",
    "tracker_status_report_to_csv_rows",
]

SCHEMA_VERSION = "1"


@dataclass(frozen=True)
class TrackerEndpointObservation:
    """One torrent's observation of one tracker endpoint."""

    torrent_hash: str
    tracker_identity: str
    raw_status: int | str | None
    health: TrackerHealth
    message: str | None
    enabled: bool | None


@dataclass(frozen=True)
class TrackerAggregate:
    """The aggregated health of one tracker identity across torrents."""

    identity: str
    health: TrackerHealth
    torrent_count: int
    endpoint_count: int
    healthy_count: int
    warning_count: int
    critical_count: int
    disabled_count: int
    unknown_count: int
    representative_message: str | None


@dataclass(frozen=True)
class TrackerStatusReport:
    """The deterministic result of collecting filter-aware tracker status."""

    schema_version: str
    generated_at: datetime
    filters: TorrentFilter
    scanned_torrents: int
    matched_torrents: int
    trackers: tuple[TrackerAggregate, ...]
    collection_errors: int
    overall_health: TrackerHealth


_EXIT_CODE_BY_OVERALL_HEALTH: dict[TrackerHealth, int] = {
    TrackerHealth.HEALTHY: 0,
    TrackerHealth.WARNING: 1,
    TrackerHealth.CRITICAL: 2,
    TrackerHealth.UNAVAILABLE: 3,
}


def tracker_status_exit_code(overall_health: TrackerHealth) -> int:
    """Map a report's overall health to its documented CLI exit code.

    `overall_health` computed by `collect_tracker_status` only ever
    produces HEALTHY/WARNING/CRITICAL/UNAVAILABLE (see
    `_compute_overall_health`), so DISABLED/UNKNOWN never reach this
    mapping.
    """
    return _EXIT_CODE_BY_OVERALL_HEALTH[overall_health]


def _is_pseudo_tracker_url(url: str) -> bool:
    """Return whether a tracker URL is a DHT/PeX/LSD sentinel, not a host.

    qBittorrent represents these as bracketed marker strings (e.g.
    `"** [DHT] **"`) rather than URLs -- `normalize_tracker_host` cannot
    parse them (they contain no valid netloc) and would raise.
    """
    stripped = url.strip()
    return stripped.startswith("**") and stripped.endswith("**")


# Fallback order for `representative_message` when the aggregate's own
# `health` has no message of its own (e.g. a WARNING aggregate, which by
# `_compute_tracker_health`'s rules never has a WARNING-classified
# endpoint unless a raw NOT_CONTACTED is actually present -- it is
# reached by *mixing* other severities). Most severe first, so a mixed
# healthy/critical identity always surfaces the critical endpoint's
# message, never the healthy one's -- insertion (scan) order would
# otherwise let whichever endpoint happened to be read first win,
# independent of severity.
_MESSAGE_FALLBACK_ORDER = (
    TrackerHealth.CRITICAL,
    TrackerHealth.WARNING,
    TrackerHealth.UNKNOWN,
    TrackerHealth.DISABLED,
    TrackerHealth.HEALTHY,
)


@dataclass
class _AggregateBuilder:
    """Mutable accumulator for one tracker identity's observations."""

    identity: str
    torrent_hashes: set[str] = field(default_factory=set)
    healthy: int = 0
    warning: int = 0
    critical: int = 0
    disabled: int = 0
    unknown: int = 0
    messages_by_health: dict[TrackerHealth, str] = field(default_factory=dict)

    def record(self, observation: TrackerEndpointObservation) -> None:
        """Fold one endpoint observation into this aggregate's counters."""
        health = observation.health
        if health is TrackerHealth.HEALTHY:
            self.healthy += 1
        elif health is TrackerHealth.WARNING:
            self.warning += 1
        elif health is TrackerHealth.CRITICAL:
            self.critical += 1
        elif health is TrackerHealth.DISABLED:
            self.disabled += 1
        else:
            self.unknown += 1

        message = observation.message
        if message and health not in self.messages_by_health:
            self.messages_by_health[health] = message

    def finalize(self) -> TrackerAggregate:
        """Build the frozen `TrackerAggregate` for this identity."""
        health = compute_tracker_aggregate_health(
            healthy=self.healthy,
            warning=self.warning,
            critical=self.critical,
            disabled=self.disabled,
            unknown=self.unknown,
        )
        representative = next(
            (
                self.messages_by_health[candidate]
                for candidate in _MESSAGE_FALLBACK_ORDER
                if candidate in self.messages_by_health
            ),
            None,
        )
        endpoint_count = (
            self.healthy
            + self.warning
            + self.critical
            + self.disabled
            + self.unknown
        )

        return TrackerAggregate(
            identity=self.identity,
            health=health,
            torrent_count=len(self.torrent_hashes),
            endpoint_count=endpoint_count,
            healthy_count=self.healthy,
            warning_count=self.warning,
            critical_count=self.critical,
            disabled_count=self.disabled,
            unknown_count=self.unknown,
            representative_message=representative,
        )


def _compute_overall_health(
    trackers: tuple[TrackerAggregate, ...],
    *,
    matched_torrents: int,
    collection_errors: int,
) -> TrackerHealth:
    """Compute the report-level overall health.

    UNAVAILABLE only when there were torrents to inspect and tracker
    collection failed for every single one of them -- a total collection
    failure, distinct from a partial one (which degrades to WARNING, see
    below) or an empty selection (which is HEALTHY: there is nothing
    wrong with an intentionally narrow filter that matches nothing).
    """
    if matched_torrents > 0 and collection_errors == matched_torrents:
        return TrackerHealth.UNAVAILABLE
    if any(tracker.health is TrackerHealth.CRITICAL for tracker in trackers):
        return TrackerHealth.CRITICAL
    if collection_errors > 0:
        return TrackerHealth.WARNING
    if any(
        tracker.health in (TrackerHealth.WARNING, TrackerHealth.UNKNOWN)
        for tracker in trackers
    ):
        return TrackerHealth.WARNING
    return TrackerHealth.HEALTHY


def collect_tracker_status(
    client: Any,
    filters: TorrentFilter,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> TrackerStatusReport:
    """Collect a filter-aware tracker status report.

    Applies every cheap (torrent-info-only) filter first via
    `select_torrents` (with `tracker` cleared, so that call never
    performs its own per-torrent lookup), then calls
    `client.torrents_trackers()` at most once per surviving torrent.
    A single torrent's lookup failure is caught and counted rather than
    aborting collection: `collection_errors` is always visible, and
    `overall_health` can never read fully healthy while errors > 0 (see
    `_compute_overall_health`).

    When `filters.tracker` is set, every surviving torrent is still
    scanned (the identity a torrent's trackers will normalize to is not
    known until they are read) and the resulting aggregates are filtered
    down to that one identity afterward -- `--tracker` here restricts
    the *report*, not the API-call volume, unlike `torrents list
    --tracker`.
    """
    cheap_filters = replace(filters, tracker=None)
    selection = select_torrents(client, cheap_filters)

    aggregates: dict[str, _AggregateBuilder] = {}
    collection_errors = 0
    candidate_total = len(selection.matched)

    for index, torrent in enumerate(selection.matched, start=1):
        try:
            raw_trackers = list(client.torrents_trackers(torrent.hash))
        except Exception:
            collection_errors += 1
            if on_progress is not None:
                on_progress(index, candidate_total)
            continue

        identities_for_torrent: set[str] = set()
        for raw_tracker in raw_trackers:
            url = get_field_as_string(raw_tracker, "url")
            if url == "" or _is_pseudo_tracker_url(url):
                continue

            identity = normalize_tracker_host(url)
            raw_status = get_raw_tracker_status(raw_tracker)
            health, enabled = classify_raw_tracker_status(raw_status)
            raw_message = get_field_as_string(raw_tracker, "msg")
            message = (
                sanitize_tracker_text(raw_message) if raw_message else None
            )

            observation = TrackerEndpointObservation(
                torrent_hash=torrent.hash,
                tracker_identity=identity,
                raw_status=raw_status,
                health=health,
                message=message or None,
                enabled=enabled,
            )

            builder = aggregates.setdefault(
                identity, _AggregateBuilder(identity)
            )
            builder.record(observation)
            identities_for_torrent.add(identity)

        for identity in identities_for_torrent:
            aggregates[identity].torrent_hashes.add(torrent.hash)

        if on_progress is not None:
            on_progress(index, candidate_total)

    if filters.tracker is not None:
        aggregates = {
            identity: builder
            for identity, builder in aggregates.items()
            if identity == filters.tracker
        }

    trackers = tuple(
        sorted(
            (builder.finalize() for builder in aggregates.values()),
            key=lambda aggregate: aggregate.identity,
        )
    )

    overall_health = _compute_overall_health(
        trackers,
        matched_torrents=len(selection.matched),
        collection_errors=collection_errors,
    )

    return TrackerStatusReport(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(UTC),
        filters=filters,
        scanned_torrents=selection.scanned,
        matched_torrents=len(selection.matched),
        trackers=trackers,
        collection_errors=collection_errors,
        overall_health=overall_health,
    )


def tracker_aggregate_to_dict(aggregate: TrackerAggregate) -> dict[str, Any]:
    """Convert one tracker aggregate into a JSON-serializable structure."""
    return {
        "identity": aggregate.identity,
        "health": aggregate.health.value,
        "torrent_count": aggregate.torrent_count,
        "endpoint_count": aggregate.endpoint_count,
        "healthy_count": aggregate.healthy_count,
        "warning_count": aggregate.warning_count,
        "critical_count": aggregate.critical_count,
        "disabled_count": aggregate.disabled_count,
        "unknown_count": aggregate.unknown_count,
        "representative_message": aggregate.representative_message,
    }


def tracker_status_report_to_dict(
    report: TrackerStatusReport,
) -> dict[str, Any]:
    """Convert a tracker status report into a JSON-serializable structure."""
    return {
        "schema_version": report.schema_version,
        "generated_at": report.generated_at.isoformat(),
        "filters": torrent_filter_to_dict(report.filters),
        "scanned_torrents": report.scanned_torrents,
        "matched_torrents": report.matched_torrents,
        "collection_errors": report.collection_errors,
        "overall_health": report.overall_health.value,
        "trackers": [
            tracker_aggregate_to_dict(aggregate)
            for aggregate in report.trackers
        ],
    }


TRACKER_STATUS_CSV_FIELDNAMES = [
    "tracker",
    "health",
    "torrent_count",
    "endpoint_count",
    "healthy_count",
    "warning_count",
    "critical_count",
    "disabled_count",
    "unknown_count",
]


def tracker_status_report_to_csv_rows(
    report: TrackerStatusReport,
) -> list[tuple[str, ...]]:
    """Convert a tracker status report into stable CSV rows.

    Deliberately omits `representative_message`: the CSV contract is a
    fixed, narrow column set (see `TRACKER_STATUS_CSV_FIELDNAMES`) built
    for spreadsheet/scripting consumption, not full detail -- use `json`/
    `jsonl` (or `--verbose` table output) for messages.
    """
    return [
        (
            aggregate.identity,
            aggregate.health.value,
            str(aggregate.torrent_count),
            str(aggregate.endpoint_count),
            str(aggregate.healthy_count),
            str(aggregate.warning_count),
            str(aggregate.critical_count),
            str(aggregate.disabled_count),
            str(aggregate.unknown_count),
        )
        for aggregate in report.trackers
    ]
