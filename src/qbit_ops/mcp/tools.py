"""Bounded, read-only views over `qbit_core`, shaped for an agent.

The problem this package exists to solve was measured, not assumed: a
`torrents list --format json` over a 1000-torrent library serialises
~346 KiB. An MCP tool result cannot be piped through `jq` or `head` --
it lands in the model's context whole. So the bound lives here, on the
server, never in the model's good intentions.

The shape is drill-down, and it is deliberate:

    summary  ->  find (bounded)  ->  inspect one  ->  explain one

Each step returns just enough to choose the next one. `find_torrents`
returns the handful of fields `_brief` names, not every field
`inspect_torrent` carries through `_full`, because a hash, a name and a
state are what a next step is chosen from.

No tool mutates. No tool holds a rule of its own: every classification
comes from `qbit_core`, so this surface cannot drift from the CLI's
answers -- and it can be deleted without taking a verdict with it.
"""

from __future__ import annotations

from typing import Any

from qbit_core.features.explain import build_torrent_explanation
from qbit_core.features.stats import collect_torrent_stats
from qbit_core.features.status import collect_status_snapshot
from qbit_core.features.torrents import (
    build_torrent_filter,
    get_safe_tracker_details,
    list_torrent_snapshots,
    select_torrents,
)
from qbit_core.qbit.fields import get_field_as_string
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

    Filters go through `build_torrent_filter` and `select_torrents`,
    the same engine `aggregate_stats` and every CLI selector use: this
    tool used to compare `category` with `==` and `state_group` with a
    hand-rolled lowercase check, so it silently disagreed with
    `aggregate_stats` on the same arguments (case, and the
    `uncategorized` token). One engine, one answer.
    """
    filters = build_torrent_filter(
        categories=[category] if category else (),
        states=[state_group] if state_group else (),
        name_contains=[name_contains] if name_contains else (),
    )
    selection, _ = select_torrents(client, filters)
    snapshots = selection.matched

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


def _full(snapshot: TorrentSnapshot) -> dict[str, Any]:
    """Every `TorrentSnapshot` field, once an agent has narrowed to a
    single torrent -- `tags` included, the same raw membership list the
    model itself carries it for: a caller that already holds this
    projection never needs a second call just to read it.
    """
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
        "tags": list(snapshot.tags),
        "downloaded": snapshot.downloaded,
        "uploaded": snapshot.uploaded,
        "seeding_time": snapshot.seeding_time,
        "added_at": _isoformat(snapshot.added_at),
        "completed_at": _isoformat(snapshot.completed_at),
        "last_activity_at": _isoformat(snapshot.last_activity_at),
    }


def inspect_torrent(client: Any, torrent_hash: str) -> dict[str, Any] | None:
    """Every field of one explicitly chosen torrent.

    Unbounded on purpose: one torrent is a bounded answer. This is where
    every snapshot field belongs -- see `_full` -- after an agent has
    narrowed to a single item, not before.

    Asks upstream for that hash alone. Fetching the whole library to
    find one torrent kept the *output* bounded while leaving the *input*
    unbounded -- invisible to the model, and very real for the instance.
    """
    wanted = torrent_hash.strip().lower()
    for snapshot in list_torrent_snapshots(client, hashes=[wanted]):
        if snapshot.hash.lower() == wanted:
            return _full(snapshot)
    return None


def explain(client: Any, torrent_hash: str) -> dict[str, Any] | None:
    """Why one torrent is in its current state.

    Resolves the hash narrowly instead of calling
    `qbit_core.features.explain.explain_torrent`: that entry point
    scans the whole library because the CLI's `explain` accepts a hash
    *prefix*, a capability this signature -- always a full infohash --
    never uses. Calls `build_torrent_explanation`, the same pure rule
    catalogue `explain_torrent` itself delegates to once it has
    resolved a torrent: there is still exactly one diagnostic engine,
    never a second one here.
    """
    wanted = torrent_hash.strip().lower()
    matches = client.torrents_info(torrent_hashes=[wanted]) if wanted else []
    torrent = next(
        (
            item
            for item in matches
            if get_field_as_string(item, "hash").lower() == wanted
        ),
        None,
    )
    if torrent is None:
        return None

    resolved_hash = get_field_as_string(torrent, "hash")
    try:
        raw_trackers = list(client.torrents_trackers(resolved_hash))
        safe_tracker_details: list[dict[str, Any]] | None = (
            get_safe_tracker_details(raw_trackers)
        )
    except Exception:
        safe_tracker_details = None

    report = build_torrent_explanation(torrent, safe_tracker_details)
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
