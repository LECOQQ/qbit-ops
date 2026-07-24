"""List qBittorrent torrents."""

from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Literal

from app.qbit_fields import (
    get_field_as_float,
    get_field_as_int,
    get_field_as_string,
)
from app.selectors import TorrentNotFoundError, resolve_torrent_hash
from app.trackers import TrackerMatchMode, has_tracker

TorrentBulkAction = Literal["pause", "resume", "start", "reannounce"]


@dataclass(frozen=True)
class BulkTorrentChange:
    """One torrent that a bulk action will act on."""

    hash: str
    name: str


@dataclass(frozen=True)
class BulkTorrentSkip:
    """One torrent excluded from a bulk action because it would be a no-op."""

    hash: str
    name: str
    reason: str


@dataclass(frozen=True)
class BulkTorrentActionPlan:
    """The result of planning a bulk torrent action, before it is applied.

    `changes` and `skipped` are collected unconditionally (not gated by a
    `verbose` flag) since the CLI layer needs full detail to render a
    confirmation preview; whether to *print* that detail is a rendering
    decision, not a planning one.
    """

    action: TorrentBulkAction
    selection: dict[str, str]
    scanned: int
    matched: int
    changes: tuple[BulkTorrentChange, ...]
    skipped: tuple[BulkTorrentSkip, ...]


def list_torrents(client: Any) -> list[dict[str, Any]]:
    """List torrents with useful audit fields."""
    torrents = [
        _build_torrent_audit_entry(client, torrent)
        for torrent in client.torrents_info()
    ]
    torrents.sort(key=lambda item: item["name"].casefold())
    return torrents


def list_torrents_by_category(client: Any, category: str) -> dict[str, Any]:
    """List torrents belonging to a category."""
    scanned = 0
    torrents: list[dict[str, Any]] = []
    normalized_category = category.strip()

    for torrent in client.torrents_info():
        scanned += 1
        torrent_category = get_field_as_string(torrent, "category")
        if not _category_matches(torrent_category, normalized_category):
            continue

        torrents.append(_build_torrent_audit_entry(client, torrent))

    torrents.sort(key=lambda item: item["name"].casefold())

    return {
        "category": _format_category_label(normalized_category),
        "scanned": scanned,
        "matched": len(torrents),
        "torrents": torrents,
    }


def list_category_usage(client: Any) -> dict[str, int]:
    """List categories and count torrents in each one."""
    category_usage: dict[str, int] = {}

    for torrent in client.torrents_info():
        category = _format_category_label(
            get_field_as_string(torrent, "category")
        )
        category_usage[category] = category_usage.get(category, 0) + 1

    return dict(sorted(category_usage.items()))


UNCATEGORIZED_LABEL = "(uncategorized)"


def _build_torrent_audit_entry(client: Any, torrent: Any) -> dict[str, Any]:
    """Build standard audit fields for one torrent."""
    torrent_hash = get_field_as_string(torrent, "hash")
    trackers = _get_active_tracker_urls(client.torrents_trackers(torrent_hash))

    return {
        "hash": torrent_hash,
        "name": get_field_as_string(torrent, "name"),
        "category": _format_category_label(
            get_field_as_string(torrent, "category")
        ),
        "state": get_field_as_string(torrent, "state"),
        "size": get_field_as_int(torrent, "size"),
        "progress": get_field_as_float(torrent, "progress"),
        "ratio": get_field_as_float(torrent, "ratio"),
        "tracker_count": len(trackers),
    }


def _category_matches(torrent_category: str, requested_category: str) -> bool:
    """Return whether a torrent category matches the requested filter."""
    if requested_category.casefold() == UNCATEGORIZED_LABEL.casefold():
        return torrent_category.strip() == ""

    return torrent_category.casefold() == requested_category.casefold()


def _format_category_label(category: str) -> str:
    """Normalize category labels for display and comparison."""
    if category.strip() == "":
        return UNCATEGORIZED_LABEL

    return category.strip()


def list_torrents_with_trackers(client: Any) -> list[dict[str, Any]]:
    """List torrents with tracker details for export and audit."""
    torrents: list[dict[str, Any]] = []

    for torrent in client.torrents_info():
        torrent_hash = get_field_as_string(torrent, "hash")
        trackers = _get_tracker_details(client.torrents_trackers(torrent_hash))
        active_tracker_count = sum(
            1 for tracker in trackers if not tracker["disabled"]
        )
        torrents.append(
            {
                "hash": torrent_hash,
                "name": get_field_as_string(torrent, "name"),
                "state": get_field_as_string(torrent, "state"),
                "size": get_field_as_int(torrent, "size"),
                "progress": get_field_as_float(torrent, "progress"),
                "ratio": get_field_as_float(torrent, "ratio"),
                "save_path": get_field_as_string(torrent, "save_path"),
                "category": get_field_as_string(torrent, "category"),
                "added_on": get_field_as_int(torrent, "added_on"),
                "trackers": trackers,
                "active_tracker_count": active_tracker_count,
            }
        )

    return torrents


def inspect_torrent(client: Any, torrent_hash: str) -> dict[str, Any] | None:
    """Return detailed torrent information for a hash or unique prefix.

    Returns `None` when nothing matches. Propagates
    `AmbiguousTorrentHashError` (and `InvalidTorrentSelectorError` for an
    empty selector) so the caller can present candidates instead of
    silently picking one torrent.
    """
    all_torrents = list(client.torrents_info())

    try:
        resolved = resolve_torrent_hash(all_torrents, torrent_hash)
    except TorrentNotFoundError:
        return None

    for torrent in all_torrents:
        current_hash = get_field_as_string(torrent, "hash")
        if current_hash.lower() == resolved.hash.lower():
            return _build_torrent_details(client, torrent, resolved.hash)

    return None  # pragma: no cover - resolved hash always exists


def search_torrents_by_name(
    client: Any,
    query: str,
    *,
    limit: int = 20,
    min_score: float = 0.5,
) -> dict[str, Any]:
    """Search torrents by name and rank matches by relevance."""
    normalized_query = query.strip()
    matches: list[dict[str, Any]] = []

    for torrent in client.torrents_info():
        torrent_name = get_field_as_string(torrent, "name")
        match_score = _score_name_match(torrent_name, normalized_query)
        if match_score < min_score:
            continue

        torrent_hash = get_field_as_string(torrent, "hash")
        matches.append(
            {
                "hash": torrent_hash,
                "name": torrent_name,
                "state": get_field_as_string(torrent, "state"),
                "progress": get_field_as_float(torrent, "progress"),
                "ratio": get_field_as_float(torrent, "ratio"),
                "match_score": round(match_score, 4),
            }
        )

    matches.sort(
        key=lambda item: (-item["match_score"], item["name"].casefold()),
    )
    if limit > 0:
        matches = matches[:limit]

    return {
        "query": normalized_query,
        "summary": {
            "matched": len(matches),
            "limit": limit,
        },
        "matches": matches,
    }


def plan_bulk_torrent_action(
    client: Any,
    action: TorrentBulkAction,
    *,
    torrent_hash: str | None = None,
    category: str | None = None,
    tracker: str | None = None,
    match_mode: TrackerMatchMode = "exact",
    select_all: bool = False,
    completed_only: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
) -> BulkTorrentActionPlan:
    """Plan a bulk torrent action against a filtered torrent selection.

    Pure with respect to the qBittorrent instance: this only reads state
    and never mutates it. `torrent_hash` accepts a complete hash or an
    unambiguous prefix, resolved via `app.selectors.resolve_torrent_hash`.
    An ambiguous prefix raises `AmbiguousTorrentHashError` before any plan
    is built; an unmatched hash resolves to zero selected torrents, same
    as any other filter that matches nothing.
    """
    selection = select_torrents_for_bulk_action(
        client=client,
        torrent_hash=torrent_hash,
        category=category,
        tracker=tracker,
        match_mode=match_mode,
        select_all=select_all,
        completed_only=completed_only,
        on_progress=on_progress,
    )
    changes: list[BulkTorrentChange] = []
    skips: list[BulkTorrentSkip] = []

    for torrent in selection["torrents"]:
        matched_hash = torrent["hash"]
        torrent_name = torrent["name"]
        torrent_state = torrent["state"]

        skip_reason = _bulk_action_skip_reason(action, torrent_state)
        if skip_reason is not None:
            skips.append(
                BulkTorrentSkip(
                    hash=matched_hash, name=torrent_name, reason=skip_reason
                )
            )
            continue

        changes.append(BulkTorrentChange(hash=matched_hash, name=torrent_name))

    return BulkTorrentActionPlan(
        action=action,
        selection=selection["selection"],
        scanned=selection["scanned"],
        matched=selection["matched"],
        changes=tuple(changes),
        skipped=tuple(skips),
    )


def apply_bulk_torrent_action(client: Any, plan: BulkTorrentActionPlan) -> None:
    """Apply a previously built plan. Mutates exactly `plan.changes`.

    Never rescans torrents: the plan is the sole source of truth for what
    gets mutated, so preview and execution can never diverge.
    """
    if not plan.changes:
        return

    hashes = [change.hash for change in plan.changes]
    try:
        _call_bulk_torrent_action(client, plan.action, hashes)
    except Exception as error:
        raise RuntimeError(
            f"Failed to {plan.action} selected torrents: {error}"
        ) from error


def select_torrents_for_bulk_action(
    client: Any,
    *,
    torrent_hash: str | None = None,
    category: str | None = None,
    tracker: str | None = None,
    match_mode: TrackerMatchMode = "exact",
    select_all: bool = False,
    completed_only: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Select torrents for a bulk action using one filter."""
    validate_bulk_torrent_selection(
        torrent_hash=torrent_hash,
        category=category,
        tracker=tracker,
        select_all=select_all,
        completed_only=completed_only,
    )

    all_torrents = list(client.torrents_info())
    total = len(all_torrents)

    if torrent_hash is not None:
        return _select_torrents_by_hash(all_torrents, torrent_hash, on_progress)

    selected_torrents: list[dict[str, Any]] = []
    scanned = 0

    for torrent in all_torrents:
        scanned += 1
        if on_progress is not None:
            on_progress(scanned, total)

        if completed_only and not is_completed_torrent(torrent):
            continue

        if select_all or (
            completed_only and category is None and tracker is None
        ):
            selected_torrents.append(_build_bulk_torrent_entry(torrent))
            continue

        if not _torrent_matches_bulk_filter(
            client=client,
            torrent=torrent,
            category=category,
            tracker=tracker,
            match_mode=match_mode,
        ):
            continue

        selected_torrents.append(_build_bulk_torrent_entry(torrent))

    selected_torrents.sort(key=lambda item: item["name"].casefold())

    return {
        "selection": _build_bulk_selection_metadata(
            torrent_hash=None,
            category=category,
            tracker=tracker,
            match_mode=match_mode,
            select_all=select_all,
            completed_only=completed_only,
        ),
        "scanned": scanned,
        "matched": len(selected_torrents),
        "torrents": selected_torrents,
    }


def _select_torrents_by_hash(
    all_torrents: list[Any],
    torrent_hash: str,
    on_progress: Callable[[int, int], None] | None,
) -> dict[str, Any]:
    """Resolve a hash selector to at most one torrent.

    An unmatched hash resolves to zero selected torrents rather than
    raising, so it flows through the same no-match path as any other
    bulk filter. An ambiguous prefix raises `AmbiguousTorrentHashError`
    so the caller can reject it before any mutation is attempted.
    """
    total = len(all_torrents)
    if on_progress is not None:
        on_progress(total, total)

    try:
        resolved = resolve_torrent_hash(all_torrents, torrent_hash)
    except TorrentNotFoundError:
        return {
            "selection": _build_bulk_selection_metadata(
                torrent_hash=torrent_hash,
                category=None,
                tracker=None,
                match_mode="exact",
                select_all=False,
                completed_only=False,
            ),
            "scanned": total,
            "matched": 0,
            "torrents": [],
        }

    selected_torrents = [
        _build_bulk_torrent_entry(torrent)
        for torrent in all_torrents
        if get_field_as_string(torrent, "hash").lower() == resolved.hash.lower()
    ]

    return {
        "selection": _build_bulk_selection_metadata(
            torrent_hash=resolved.hash,
            category=None,
            tracker=None,
            match_mode="exact",
            select_all=False,
            completed_only=False,
        ),
        "scanned": total,
        "matched": len(selected_torrents),
        "torrents": selected_torrents,
    }


def _build_bulk_torrent_entry(torrent: Any) -> dict[str, Any]:
    """Build torrent fields used by bulk actions."""
    return {
        "hash": get_field_as_string(torrent, "hash"),
        "name": get_field_as_string(torrent, "name"),
        "state": get_field_as_string(torrent, "state"),
        "category": _format_category_label(
            get_field_as_string(torrent, "category")
        ),
    }


def _build_bulk_selection_metadata(
    *,
    torrent_hash: str | None,
    category: str | None,
    tracker: str | None,
    match_mode: TrackerMatchMode,
    select_all: bool = False,
    completed_only: bool = False,
) -> dict[str, str]:
    """Describe which filter was used for a bulk torrent action."""
    if torrent_hash is not None:
        return {
            "filter": "hash",
            "value": torrent_hash,
        }

    if select_all:
        return {
            "filter": "all",
            "value": "*",
        }

    if completed_only:
        if category is not None:
            return {
                "filter": "completed+category",
                "value": _format_category_label(category.strip()),
            }

        if tracker is not None:
            return {
                "filter": "completed+tracker",
                "value": tracker.strip(),
                "match": match_mode,
            }

        return {
            "filter": "completed",
            "value": "*",
        }

    if category is not None:
        return {
            "filter": "category",
            "value": _format_category_label(category.strip()),
        }

    if tracker is not None:
        return {
            "filter": "tracker",
            "value": tracker.strip(),
            "match": match_mode,
        }

    raise AssertionError(
        "No bulk selection filter was provided; "
        "validate_bulk_torrent_selection should have rejected this."
    )


def _torrent_matches_bulk_filter(
    client: Any,
    torrent: Any,
    *,
    category: str | None,
    tracker: str | None,
    match_mode: TrackerMatchMode,
) -> bool:
    """Return whether a torrent matches the requested bulk filter."""
    if category is not None:
        torrent_category = get_field_as_string(torrent, "category")
        return _category_matches(torrent_category, category.strip())

    if tracker is not None:
        torrent_hash = get_field_as_string(torrent, "hash")
        trackers = _get_active_tracker_urls(
            client.torrents_trackers(torrent_hash)
        )
        return has_tracker(trackers, tracker.strip(), match_mode)

    return False


def _call_bulk_torrent_action(
    client: Any,
    action: TorrentBulkAction,
    torrent_hashes: list[str],
) -> None:
    """Call the qBittorrent API for a bulk torrent action."""
    if action == "pause":
        client.torrents_pause(torrent_hashes)
        return

    if action in ("resume", "start"):
        start_method = getattr(client, "torrents_start", None)
        if start_method is not None:
            start_method(torrent_hashes)
            return

        client.torrents_resume(torrent_hashes)
        return

    client.torrents_reannounce(torrent_hashes)


def validate_bulk_torrent_selection(
    *,
    torrent_hash: str | None,
    category: str | None,
    tracker: str | None,
    select_all: bool,
    completed_only: bool,
) -> None:
    """Ensure bulk torrent filters are mutually consistent.

    `--hash` is always exclusive: it resolves to a single torrent, so it
    never combines with `--category`, `--tracker`, `--all`, or
    `--completed`.
    """
    if torrent_hash is not None:
        conflicts_with_hash = (
            category is not None
            or tracker is not None
            or select_all
            or completed_only
        )
        if conflicts_with_hash:
            raise ValueError(
                "Use --hash alone, without --category, --tracker, --all, "
                "or --completed."
            )
        return

    named_filters = sum(1 for value in (category, tracker) if value is not None)

    if completed_only:
        if select_all:
            raise ValueError(
                "Use --completed alone or with --category or --tracker."
            )
        if named_filters > 1:
            raise ValueError(
                "Provide at most one of --category or --tracker with "
                "--completed."
            )
        return

    if select_all:
        if named_filters > 0:
            raise ValueError(
                "Use --all alone, without --category or --tracker."
            )
        return

    if named_filters != 1:
        raise ValueError(
            "Provide exactly one of --hash, --category, --tracker, --all, "
            "or --completed."
        )


def _bulk_action_skip_reason(
    action: TorrentBulkAction,
    state: str,
) -> str | None:
    """Return a skip reason when a bulk action would be a no-op."""
    if action == "pause" and is_stopped_state(state):
        return "already_stopped"

    if action in ("resume", "start") and not is_stopped_state(state):
        return "already_running"

    return None


def is_stopped_state(state: str) -> bool:
    """Return whether qBittorrent reports a torrent as stopped."""
    normalized_state = state.casefold()
    return normalized_state.startswith("paused") or normalized_state.startswith(
        "stopped"
    )


def is_completed_torrent(torrent: Any) -> bool:
    """Return whether qBittorrent reports a torrent as completed."""
    return get_field_as_float(torrent, "progress") >= 1.0


def _build_torrent_details(
    client: Any,
    torrent: Any,
    torrent_hash: str,
) -> dict[str, Any]:
    """Build a detailed torrent report with tracker information."""
    trackers = _get_tracker_details(client.torrents_trackers(torrent_hash))
    active_tracker_count = sum(
        1 for tracker in trackers if not tracker["disabled"]
    )

    return {
        "hash": torrent_hash,
        "name": get_field_as_string(torrent, "name"),
        "state": get_field_as_string(torrent, "state"),
        "size": get_field_as_int(torrent, "size"),
        "progress": get_field_as_float(torrent, "progress"),
        "ratio": get_field_as_float(torrent, "ratio"),
        "save_path": get_field_as_string(torrent, "save_path"),
        "category": get_field_as_string(torrent, "category"),
        "added_on": get_field_as_int(torrent, "added_on"),
        "trackers": trackers,
        "active_tracker_count": active_tracker_count,
    }


def _score_name_match(name: str, query: str) -> float:
    """Score how closely a torrent name matches a search query."""
    normalized_name = name.casefold()
    normalized_query = query.casefold().strip()
    if normalized_query == "":
        return 0.0
    if normalized_name == normalized_query:
        return 1.0
    if normalized_name.startswith(normalized_query):
        return 0.95
    if normalized_query in normalized_name:
        return 0.85

    return SequenceMatcher(
        None,
        normalized_name,
        normalized_query,
    ).ratio()


def _get_tracker_details(trackers: Any) -> list[dict[str, Any]]:
    """Extract tracker URLs and status from qBittorrent tracker objects."""
    tracker_details: list[dict[str, Any]] = []

    for tracker in trackers:
        tracker_url = get_field_as_string(tracker, "url")
        if tracker_url == "":
            continue

        tracker_details.append(
            {
                "url": tracker_url,
                "status": get_field_as_string(tracker, "status"),
                "disabled": _is_disabled_tracker(tracker),
            }
        )

    return tracker_details


def _get_active_tracker_urls(trackers: Any) -> list[str]:
    """Extract non-disabled tracker URLs from qBittorrent tracker objects."""
    return [
        tracker_url
        for tracker in trackers
        if not _is_disabled_tracker(tracker)
        and (tracker_url := get_field_as_string(tracker, "url")) != ""
    ]


def _is_disabled_tracker(tracker: Any) -> bool:
    """Return whether qBittorrent reports a tracker as disabled."""
    status = get_field_as_string(tracker, "status").strip().lower()
    return status in {"0", "disabled"}
