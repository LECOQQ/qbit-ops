"""Bounded, read-only views over `qbit_core`, shaped for an agent.

The problem this package exists to solve was measured, not assumed: a
`torrents list --format json` over a 1000-torrent library serialises
~346 KiB. An MCP tool result cannot be piped through `jq` or `head` --
it lands in the model's context whole. So the bound lives here, on the
server, never in the model's good intentions.

The shape is drill-down, and it is deliberate:

    summary  ->  find (bounded)  ->  inspect one  ->  explain one

Each step returns just enough to choose the next one. `find_torrents`
returns six fields per torrent, not the sixteen a snapshot carries,
because a hash, a name and a state are what a next step is chosen from.

No tool mutates. No tool holds a rule of its own: every classification
comes from `qbit_core`, so this surface cannot drift from the CLI's
answers -- and it can be deleted without taking a verdict with it.
"""

from __future__ import annotations

from typing import Any

from qbit_core.features.explain import explain_torrent
from qbit_core.features.stats import collect_torrent_stats
from qbit_core.features.status import collect_status_snapshot
from qbit_core.features.torrents import (
    build_torrent_filter,
    list_torrent_snapshots,
)
from qbit_core.shared.selection import SelectionRequest
from qbit_core.shared.torrent_states import TorrentSnapshot

# A hard server-side cap. `DEFAULT_LIMIT` is what an agent gets when it
# does not think about it; `MAX_LIMIT` is what it gets when it asks for
# five thousand. Neither is negotiable from the client side.
DEFAULT_LIMIT = 20
MAX_LIMIT = 50


def _bounded(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


def _brief(snapshot: TorrentSnapshot) -> dict[str, Any]:
    """The fields a next drill-down step is chosen from, and no more."""
    return {
        "hash": snapshot.hash,
        "name": snapshot.name,
        "category": snapshot.category,
        "state": snapshot.state,
        "state_group": snapshot.state_group,
        "progress": snapshot.progress,
        "ratio": snapshot.ratio,
    }


def library_summary(client: Any) -> dict[str, Any]:
    """Counts and health for the whole library, in a fixed-size answer.

    Fixed-size is the point: this is the one tool that reads everything,
    so it must never grow with the library. It reuses
    `collect_status_snapshot`, which already carries the alert
    vocabulary and the health verdict the CLI shows.
    """
    snapshot = collect_status_snapshot(client)
    return {
        "torrents": snapshot.counts.total,
        "by_state": {
            "downloading": snapshot.counts.downloading,
            "seeding": snapshot.counts.seeding,
            "completed": snapshot.counts.completed,
            "stalled": snapshot.counts.stalled,
            "errored": snapshot.counts.errored,
            "checking": snapshot.counts.checking,
            "unknown": snapshot.counts.unknown,
        },
        "health": str(snapshot.health),
        "alerts": [
            {
                "code": alert.code,
                "severity": str(alert.severity),
                "count": alert.count,
            }
            for alert in snapshot.alerts
        ],
    }


def find_torrents(
    client: Any,
    *,
    state_group: str | None = None,
    category: str | None = None,
    name_contains: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    """Find torrents, bounded, with just enough to pick one.

    Deliberately not every filter `torrents list` offers: each extra
    parameter is permanent context cost for a tool that may never be
    called. `category` earns its place because it is the one axis the
    operator defines himself -- it is what separates a release from a
    cross-seed link to that same release, which no other field shows.

    `matched` counts before truncation, so the agent can tell a slice
    from a whole answer -- the same contract `torrents search` has.

    `offset` makes the cap a page size rather than a dead end. Telling a
    caller that 69 matched and then refusing to show 19 of them is half
    an answer; paging keeps every result reachable while each response
    stays bounded. The doctrine holds -- depth is paid in calls, never
    in context.
    """
    snapshots = list_torrent_snapshots(client)

    if state_group is not None:
        wanted = state_group.strip().lower()
        snapshots = tuple(
            snapshot for snapshot in snapshots if snapshot.state_group == wanted
        )

    if category is not None:
        wanted_category = category.strip()
        snapshots = tuple(
            snapshot
            for snapshot in snapshots
            if (snapshot.category or "") == wanted_category
        )

    if name_contains:
        needle = name_contains.strip().lower()
        snapshots = tuple(
            snapshot
            for snapshot in snapshots
            if needle in snapshot.name.lower()
        )

    bound = _bounded(limit)
    start = max(0, offset)
    shown = snapshots[start : start + bound]
    next_offset = start + len(shown)
    return {
        "matched": len(snapshots),
        "returned": len(shown),
        "offset": start,
        # `null` when the collection is exhausted, so an agent knows it
        # has seen everything rather than guessing from a count.
        "next_offset": next_offset if next_offset < len(snapshots) else None,
        "limit": bound,
        "torrents": [_brief(snapshot) for snapshot in shown],
    }


def aggregate_stats(
    client: Any,
    *,
    state_group: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Totals over a selection: sizes, transfer, ratio, seeding time.

    Exists so a model never adds bytes by hand. Arithmetic across four
    `uploaded` fields is arithmetic a model will eventually get wrong in
    silence, and nothing downstream would catch it.

    Reuses `collect_torrent_stats`, which is already bounded and already
    computes the aggregate ratio the CLI reports -- so this surface and
    `torrents stats` can never disagree. Fixed size whatever the
    selection.
    """
    filters = build_torrent_filter(
        categories=[category] if category else (),
        states=[state_group] if state_group else (),
    )
    # `select_all` means "no filter at all" in the domain, and refuses to
    # combine with one -- so the request carries either the filter or the
    # flag, never both.
    request = (
        SelectionRequest(filters=filters)
        if not filters.is_empty
        else SelectionRequest(select_all=True, filters=filters)
    )
    report = collect_torrent_stats(client, request)
    library = report.library
    return {
        "torrents": library.torrents,
        "scanned": library.scanned,
        "total_size_bytes": library.total_size_bytes,
        "downloaded_bytes": library.downloaded_bytes,
        "uploaded_bytes": library.uploaded_bytes,
        "aggregate_ratio": library.aggregate_ratio,
        "seeding_time_total_seconds": library.seeding_time_total_seconds,
        "seeding_time_median_seconds": library.seeding_time_median_seconds,
    }


def inspect_torrent(client: Any, torrent_hash: str) -> dict[str, Any] | None:
    """Every field of one explicitly chosen torrent.

    Unbounded on purpose: one torrent is a bounded answer. This is where
    the sixteen snapshot fields belong -- after an agent has narrowed to
    a single item, not before.

    Asks upstream for that hash alone. Fetching the whole library to
    find one torrent kept the *output* bounded while leaving the *input*
    unbounded -- invisible to the model, and very real for the instance.
    """
    wanted = torrent_hash.strip().lower()
    for snapshot in list_torrent_snapshots(client, hashes=[wanted]):
        if snapshot.hash.lower() == wanted:
            return {
                "hash": snapshot.hash,
                "name": snapshot.name,
                "category": snapshot.category,
                "state": snapshot.state,
                "state_group": snapshot.state_group,
                "size": snapshot.size,
                "progress": snapshot.progress,
                "ratio": snapshot.ratio,
                "download_rate": snapshot.download_rate,
                "upload_rate": snapshot.upload_rate,
                "download_limit": snapshot.download_limit,
                "upload_limit": snapshot.upload_limit,
                "downloaded": snapshot.downloaded,
                "uploaded": snapshot.uploaded,
                "seeding_time": snapshot.seeding_time,
                "added_at": _isoformat(snapshot.added_at),
                "completed_at": _isoformat(snapshot.completed_at),
                "last_activity_at": _isoformat(snapshot.last_activity_at),
            }
    return None


def explain(client: Any, torrent_hash: str) -> dict[str, Any] | None:
    """Why one torrent is in its current state.

    Calls `qbit_core.features.explain.explain_torrent` unchanged. There
    is no second diagnostic engine here, and there must never be one:
    two engines would eventually disagree, and the operator would have
    no way to know which answer was true.
    """
    report = explain_torrent(client, torrent_hash)
    if report is None:
        return None
    return {
        "target": report.target_identity,
        "severity": str(report.overall_severity),
        "summary": report.summary,
        "findings": [
            {
                "code": finding.code,
                "severity": str(finding.severity),
                "title": finding.title,
                "explanation": finding.explanation,
                # `limitations` is the field that stops an agent stating
                # a conclusion more firmly than the engine did. Dropping
                # it left the verdict looking unqualified -- on a product
                # whose promise is evidence, never a guess.
                "evidence": [
                    {
                        "code": item.code,
                        "label": item.label,
                        "value": item.value,
                        "source": str(item.source),
                    }
                    for item in finding.evidence
                ],
                "limitations": list(finding.limitations),
                "next_commands": list(finding.next_commands),
            }
            for finding in report.findings
        ],
    }


def _isoformat(value: Any) -> str | None:
    return value.isoformat() if value is not None else None
