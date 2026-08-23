"""Collect and represent a filter-aware tracker status report.

Tracker identities are always `host` or `host:port`
(`qbit_core.features.trackers.tracker_host_or_none`), never a full
announce URL, so a passkey embedded in a tracker's path or query
string never reaches this module's output. DHT/PeX/LSD pseudo-tracker
entries are excluded from aggregation entirely, since they have no
parseable host; a host that fails to parse for any other reason (e.g.
an out-of-range port) is grouped under `"unknown"` rather than
aborting collection, mirroring `describe_tracker_url`'s own fallback.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from qbit_core.errors import InvalidInputError
from qbit_core.features.stats import aggregate_measure_totals
from qbit_core.features.torrents import (
    matches_tracker_health,
    select_torrents,
)
from qbit_core.features.trackers import (
    TrackerHealth,
    classify_raw_tracker_status,
    compute_tracker_aggregate_health,
    sanitize_tracker_text,
    tracker_host_or_none,
)
from qbit_core.qbit.fields import get_field_as_string, get_raw_tracker_status
from qbit_core.shared.inspection import inspect_trackers
from qbit_core.shared.selection import (
    TorrentFilter,
    torrent_filter_to_dict,
    without_inspection_criteria,
)
from qbit_core.shared.torrent_states import TorrentSnapshot

__all__ = [
    "TRACKER_STATUS_CSV_FIELDNAMES",
    "TrackerStatusReport",
    "collect_tracker_status",
    "tracker_status_exit_code",
    "tracker_status_report_to_csv_rows",
    "tracker_status_report_to_dict",
]

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
    """One tracker identity's health and volume across the selected torrents.

    A torrent announcing to three trackers counts *entirely* in each of
    the three aggregates, so summing a column over every identity
    exceeds the library total. `exclusive_torrent_count` is the honest
    answer to "what would I lose by leaving this tracker": the torrents
    carrying this identity and no other, a disabled endpoint counting as
    another identity since it was disabled, not removed.

    The measures come from `aggregate_measure_totals`, the same
    arithmetic `torrents stats` uses, so the two reports can never sum
    the same bytes differently.
    """

    identity: str
    health: TrackerHealth
    torrent_count: int
    exclusive_torrent_count: int
    endpoint_count: int
    healthy_count: int
    warning_count: int
    critical_count: int
    disabled_count: int
    unknown_count: int
    representative_message: str | None
    total_size_bytes: int
    downloaded_bytes: int
    uploaded_bytes: int
    aggregate_ratio: float | None
    seeding_time_total_seconds: int


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


# Most severe first, so a mixed healthy/critical identity always
# surfaces the critical endpoint's message, never the healthy one's.
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
    torrents: list[TorrentSnapshot] = field(default_factory=list)
    exclusive_torrents: int = 0
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
        totals = aggregate_measure_totals(self.torrents)

        return TrackerAggregate(
            identity=self.identity,
            health=health,
            torrent_count=len(self.torrent_hashes),
            exclusive_torrent_count=self.exclusive_torrents,
            endpoint_count=endpoint_count,
            healthy_count=self.healthy,
            warning_count=self.warning,
            critical_count=self.critical,
            disabled_count=self.disabled,
            unknown_count=self.unknown,
            representative_message=representative,
            total_size_bytes=totals.total_size_bytes,
            downloaded_bytes=totals.downloaded_bytes,
            uploaded_bytes=totals.uploaded_bytes,
            aggregate_ratio=totals.aggregate_ratio,
            seeding_time_total_seconds=totals.seeding_time_total_seconds,
        )


def _compute_overall_health(
    trackers: tuple[TrackerAggregate, ...],
    *,
    matched_torrents: int,
    collection_errors: int,
) -> TrackerHealth:
    """Compute the report-level overall health.

    UNAVAILABLE only when every matched torrent's tracker lookup failed
    -- distinct from a partial failure (WARNING) or an empty selection
    (HEALTHY: a narrow filter matching nothing is not itself a problem).
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

    Applies every cheap filter first, then collects tracker data for
    every surviving torrent via `inspect_trackers` -- one bulk
    `torrents_info(include_trackers=True)` call on a server that
    supports it (Web API >= 2.11.4), falling back to
    `client.torrents_trackers()` per torrent otherwise (see
    `qbit_core.shared.inspection`). A lookup failure is caught and
    counted rather than aborting: `collection_errors` is always
    visible, and `overall_health` can never read fully healthy while
    errors > 0. When `filters.tracker` is set, every surviving torrent
    is still scanned and the aggregates are filtered afterward --
    `--tracker` restricts the report, not the API-call volume. Several
    values combine with OR.

    `filters.tracker_health` narrows the torrents the report covers, and
    costs nothing extra: the verdict is read off the tracker data this
    single pass already collected.
    """
    if filters.trackers_excluded:
        raise InvalidInputError(
            "--exclude-tracker is not supported by `trackers status`: this "
            "report aggregates every tracker of the selected torrents, so a "
            "tracker exclusion cannot be applied to the selection. Narrow "
            "with the torrent filters, or use --tracker to restrict the "
            "report."
        )

    # `--tracker` is supported, but as a *report* restriction applied to
    # the aggregates below, not as a selection criterion -- so it is
    # removed from the cheap pass on purpose, not silently dropped.
    cheap_filters = without_inspection_criteria(filters)
    selection, _ = select_torrents(client, cheap_filters)
    inspection = inspect_trackers(
        client,
        selection,
        on_progress=on_progress,
        tolerate_lookup_errors=True,
    )

    aggregates: dict[str, _AggregateBuilder] = {}
    collection_errors = inspection.lookup_failures
    retained = [
        inspected
        for inspected in inspection.torrents
        if matches_tracker_health(
            filters,
            inspected.raw_trackers,
            lookup_failed=inspected.lookup_failed,
        )
    ]

    for inspected in retained:
        if inspected.lookup_failed:
            continue

        torrent = inspected.snapshot
        identities_for_torrent: set[str] = set()
        for raw_tracker in inspected.raw_trackers:
            url = get_field_as_string(raw_tracker, "url")
            if url == "" or _is_pseudo_tracker_url(url):
                continue

            # `tracker_host_or_none`, not `normalize_tracker_host`: `url`
            # is qBittorrent-reported, not operator-supplied, and this
            # module's own contract (see docstring above) is that a
            # lookup failure is counted, never fatal. An unparsable host
            # (e.g. an out-of-range port) still needs counting somewhere,
            # so it joins the same "unknown" bucket
            # `describe_tracker_url` falls back to.
            identity = tracker_host_or_none(url) or "unknown"
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

        # A torrent counts in full in each identity it announces to.
        # `identities_for_torrent` is built from every endpoint, disabled
        # ones included, so a torrent on an active X and a disabled Y is
        # exclusive to neither.
        for identity in identities_for_torrent:
            builder = aggregates[identity]
            builder.torrent_hashes.add(torrent.hash)
            builder.torrents.append(torrent)
            if len(identities_for_torrent) == 1:
                builder.exclusive_torrents += 1

    if filters.trackers:
        aggregates = {
            identity: builder
            for identity, builder in aggregates.items()
            if identity in filters.trackers
        }

    trackers = tuple(
        sorted(
            (builder.finalize() for builder in aggregates.values()),
            key=lambda aggregate: aggregate.identity,
        )
    )

    # Computed over the inspected population, not the health-retained
    # one: `collection_errors` counts every failure the collection hit,
    # so comparing it against a subset could read a partial failure as a
    # total one. `--tracker-health` is only reachable from `trackers
    # list`, which has no health-driven exit code.
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
        matched_torrents=len(retained),
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
