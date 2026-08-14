"""Aggregate library-wide torrent statistics from the central model.

Two blocks of different natures, never merged: `library` sums the
measures of the torrents currently present and retained by the
selection, while `instance` reports qBittorrent's own all-time counters,
which include torrents since deleted and cannot be segmented. Putting a
filtered total next to a global one would invite a comparison that means
nothing, so the second block only exists when the invocation narrowed
nothing (`SelectionRequest.has_selector`).

Free of Typer and Rich, like every other feature module. The aggregation
functions take a sequence of `TorrentSnapshot` and no client, so a test,
the TUI, or a future policy engine can call them directly.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import median
from typing import Any

from qbit_core.features.status import InstanceStats, collect_instance_stats
from qbit_core.features.torrents import select_torrents
from qbit_core.shared.selection import (
    EMPTY_TORRENT_FILTER,
    Selection,
    SelectionRequest,
    select_from_items,
    torrent_filter_to_dict,
    validate_selection_request,
)
from qbit_core.shared.torrent_states import TorrentSnapshot

SCHEMA_VERSION = "1"


@dataclass(frozen=True)
class LibraryStats:
    """Aggregated measures over the selected torrents.

    An aggregate with no defined value is `None`, never `0.0` and never
    a fabricated date: `average_size_bytes`/`largest_size_bytes` on an
    empty selection, `aggregate_ratio` when nothing was downloaded, and
    the seeding-time median when no torrent reports one. Counters stay
    `0` -- an empty selection is a legitimate answer, not a failure.
    """

    torrents: int
    scanned: int
    total_size_bytes: int
    average_size_bytes: int | None
    largest_size_bytes: int | None
    downloaded_bytes: int
    uploaded_bytes: int
    aggregate_ratio: float | None
    seeding_time_total_seconds: int
    seeding_time_median_seconds: int | None
    oldest_added_at: datetime | None
    newest_added_at: datetime | None


@dataclass(frozen=True)
class TorrentStatsReport:
    """One `torrents stats` report: the selection, then the two blocks.

    `instance` is `None` exactly when the invocation carried a selector
    -- the same condition that decides whether the all-time counters are
    read at all.
    """

    schema_version: str
    generated_at: datetime
    request: SelectionRequest
    library: LibraryStats
    instance: InstanceStats | None


def aggregate_library_stats(
    snapshots: Sequence[TorrentSnapshot],
    *,
    scanned: int,
) -> LibraryStats:
    """Aggregate the central model's measures. Zero API calls.

    `scanned` is how many torrents were read before filtering, which
    only the caller's selection knows.

    `aggregate_ratio` is the ratio of the totals, never the mean of the
    per-torrent ratios -- that would weigh a 50 MB torrent like an 80 GB
    one. An unknown measure is left out of its aggregate rather than
    counted as zero: qBittorrent's "unset" marker is negative, so
    summing it would take bytes *away* from a total.
    """
    sizes = [snapshot.size for snapshot in snapshots]
    downloaded = sum(_known(snapshot.downloaded for snapshot in snapshots))
    uploaded = sum(_known(snapshot.uploaded for snapshot in snapshots))
    seeding_times = _known(snapshot.seeding_time for snapshot in snapshots)
    added_moments = _known(snapshot.added_at for snapshot in snapshots)

    return LibraryStats(
        torrents=len(snapshots),
        scanned=scanned,
        total_size_bytes=sum(sizes),
        average_size_bytes=round(sum(sizes) / len(sizes)) if sizes else None,
        largest_size_bytes=max(sizes) if sizes else None,
        downloaded_bytes=downloaded,
        uploaded_bytes=uploaded,
        aggregate_ratio=(uploaded / downloaded) if downloaded > 0 else None,
        seeding_time_total_seconds=sum(seeding_times),
        seeding_time_median_seconds=(
            round(median(seeding_times)) if seeding_times else None
        ),
        oldest_added_at=min(added_moments) if added_moments else None,
        newest_added_at=max(added_moments) if added_moments else None,
    )


def build_stats_report(
    selection: Selection,
    *,
    instance: InstanceStats | None,
) -> TorrentStatsReport:
    """Assemble a report from an already-resolved selection. Zero API calls."""
    return TorrentStatsReport(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(UTC),
        request=selection.request,
        library=aggregate_library_stats(
            selection.matched, scanned=selection.scanned
        ),
        instance=instance,
    )


def validate_stats_request(request: SelectionRequest) -> None:
    """Reject an unsafe selector combination for a read-only report.

    Raises `ValueError` on `--hash` combined with `--all` or a filter,
    exactly like every mutation. The one difference: carrying no
    selector at all is legitimate here -- it is what asks for the
    instance block -- so the "no selector ever means everything" rule a
    mutation enforces does not apply to a report that changes nothing.
    """
    if request.has_selector:
        validate_selection_request(request)


def collect_torrent_stats(
    client: Any,
    request: SelectionRequest,
    on_progress: Callable[[int, int], None] | None = None,
) -> TorrentStatsReport:
    """Collect the statistics a `SelectionRequest` describes.

    Read-only, and bounded: one listing call, plus the tracker lookups a
    tracker filter already imposes on any selection, plus one all-time
    counters read *only* when nothing was selected. Selecting therefore
    makes this command cheaper, never more expensive.
    """
    validate_stats_request(request)

    instance = None if request.has_selector else collect_instance_stats(client)

    return build_stats_report(
        _resolve_selection(client, request, on_progress=on_progress),
        instance=instance,
    )


def _resolve_selection(
    client: Any,
    request: SelectionRequest,
    *,
    on_progress: Callable[[int, int], None] | None,
) -> Selection:
    """Resolve a request through the one existing selection pipeline.

    A hash selector needs no filter pass, so it reads the listing
    directly; everything else -- including "no selector at all", which
    means every torrent -- goes through `select_torrents`, tracker
    inspection included.
    """
    if request.torrent_hash is not None:
        torrents = list(client.torrents_info())
        if on_progress is not None:
            on_progress(len(torrents), len(torrents))
        return select_from_items(torrents, request)

    filters = EMPTY_TORRENT_FILTER if request.select_all else request.filters
    selection, _ = select_torrents(client, filters, on_progress=on_progress)
    # `select_torrents` only ever knows about filters; the report must
    # echo the request the operator actually made, `--all` included.
    return Selection(
        scanned=selection.scanned,
        matched=selection.matched,
        request=request,
    )


def stats_report_to_dict(report: TorrentStatsReport) -> dict[str, Any]:
    """Convert a stats report into a JSON-serializable structure."""
    return {
        "schema_version": report.schema_version,
        "generated_at": report.generated_at.isoformat(),
        "filters": torrent_filter_to_dict(report.request.filters),
        "library": {
            "torrents": report.library.torrents,
            "scanned": report.library.scanned,
            "total_size_bytes": report.library.total_size_bytes,
            "average_size_bytes": report.library.average_size_bytes,
            "largest_size_bytes": report.library.largest_size_bytes,
            "downloaded_bytes": report.library.downloaded_bytes,
            "uploaded_bytes": report.library.uploaded_bytes,
            "aggregate_ratio": report.library.aggregate_ratio,
            "seeding_time_total_seconds": (
                report.library.seeding_time_total_seconds
            ),
            "seeding_time_median_seconds": (
                report.library.seeding_time_median_seconds
            ),
            "oldest_added_at": _isoformat(report.library.oldest_added_at),
            "newest_added_at": _isoformat(report.library.newest_added_at),
        },
        "instance": (
            None
            if report.instance is None
            else {
                "all_time_downloaded_bytes": (
                    report.instance.all_time_downloaded_bytes
                ),
                "all_time_uploaded_bytes": (
                    report.instance.all_time_uploaded_bytes
                ),
                "all_time_ratio": report.instance.all_time_ratio,
            }
        ),
    }


def stats_report_to_csv_rows(
    report: TorrentStatsReport,
) -> list[tuple[str, str, str]]:
    """Convert a stats report into stable `section,key,value` rows.

    The long shape `status` already established: one row per metric, so
    an absent `instance` block is simply absent rather than a run of
    empty columns, and a metric added later appends rows instead of
    changing every existing one.
    """
    payload = stats_report_to_dict(report)
    rows = [
        ("library", key, _csv_value(value))
        for key, value in payload["library"].items()
    ]

    if payload["instance"] is not None:
        rows.extend(
            ("instance", key, _csv_value(value))
            for key, value in payload["instance"].items()
        )

    return rows


def _known[T](values: Iterable[T | None]) -> list[T]:
    """Keep only the measures qBittorrent actually reported."""
    return [value for value in values if value is not None]


def _isoformat(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _csv_value(value: Any) -> str:
    """Render one metric for CSV; an undefined aggregate stays blank."""
    return "" if value is None else str(value)
