"""List qBittorrent torrents."""

import logging
from collections.abc import Mapping
from difflib import SequenceMatcher
from typing import Any, Literal

from app.trackers import TrackerMatchMode, has_tracker

logger = logging.getLogger(__name__)

TorrentBulkAction = Literal["pause", "resume", "reannounce"]


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
        torrent_category = _get_field_as_string(torrent, "category")
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
            _get_field_as_string(torrent, "category")
        )
        category_usage[category] = category_usage.get(category, 0) + 1

    return dict(sorted(category_usage.items()))


UNCATEGORIZED_LABEL = "(uncategorized)"


def _build_torrent_audit_entry(client: Any, torrent: Any) -> dict[str, Any]:
    """Build standard audit fields for one torrent."""
    torrent_hash = _get_field_as_string(torrent, "hash")
    trackers = _get_active_tracker_urls(client.torrents_trackers(torrent_hash))

    return {
        "hash": torrent_hash,
        "name": _get_field_as_string(torrent, "name"),
        "category": _format_category_label(
            _get_field_as_string(torrent, "category")
        ),
        "state": _get_field_as_string(torrent, "state"),
        "size": _get_field_as_int(torrent, "size"),
        "progress": _get_field_as_float(torrent, "progress"),
        "ratio": _get_field_as_float(torrent, "ratio"),
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
        torrent_hash = _get_field_as_string(torrent, "hash")
        trackers = _get_tracker_details(client.torrents_trackers(torrent_hash))
        active_tracker_count = sum(
            1 for tracker in trackers if not tracker["disabled"]
        )
        torrents.append(
            {
                "hash": torrent_hash,
                "name": _get_field_as_string(torrent, "name"),
                "state": _get_field_as_string(torrent, "state"),
                "size": _get_field_as_int(torrent, "size"),
                "progress": _get_field_as_float(torrent, "progress"),
                "ratio": _get_field_as_float(torrent, "ratio"),
                "save_path": _get_field_as_string(torrent, "save_path"),
                "category": _get_field_as_string(torrent, "category"),
                "added_on": _get_field_as_int(torrent, "added_on"),
                "trackers": trackers,
                "active_tracker_count": active_tracker_count,
            }
        )

    return torrents


def inspect_torrent(client: Any, torrent_hash: str) -> dict[str, Any] | None:
    """Return detailed torrent information when a hash matches."""
    normalized_hash = torrent_hash.strip().lower()

    for torrent in client.torrents_info():
        current_hash = _get_field_as_string(torrent, "hash")
        if current_hash.lower() != normalized_hash:
            continue

        return _build_torrent_details(client, torrent, current_hash)

    return None


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
        torrent_name = _get_field_as_string(torrent, "name")
        match_score = _score_name_match(torrent_name, normalized_query)
        if match_score < min_score:
            continue

        torrent_hash = _get_field_as_string(torrent, "hash")
        matches.append(
            {
                "hash": torrent_hash,
                "name": torrent_name,
                "state": _get_field_as_string(torrent, "state"),
                "progress": _get_field_as_float(torrent, "progress"),
                "ratio": _get_field_as_float(torrent, "ratio"),
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


def apply_bulk_torrent_action(
    client: Any,
    action: TorrentBulkAction,
    *,
    category: str | None = None,
    tracker: str | None = None,
    match_mode: TrackerMatchMode = "exact",
    name: str | None = None,
    dry_run: bool = True,
    verbose: bool = False,
) -> dict[str, Any]:
    """Apply a bulk torrent action to a filtered torrent selection."""
    selection = select_torrents_for_bulk_action(
        client=client,
        category=category,
        tracker=tracker,
        match_mode=match_mode,
        name=name,
    )
    modified = 0
    skipped = 0
    modified_hashes: list[str] = []
    details: list[dict[str, str]] = []

    for torrent in selection["torrents"]:
        torrent_hash = torrent["hash"]
        torrent_name = torrent["name"]
        torrent_state = torrent["state"]

        if action == "pause" and _is_paused_state(torrent_state):
            skipped += 1
            if verbose:
                details.append(
                    {
                        "hash": torrent_hash,
                        "name": torrent_name,
                        "action": "already_paused",
                    }
                )
            continue

        if action == "resume" and not _is_paused_state(torrent_state):
            skipped += 1
            if verbose:
                details.append(
                    {
                        "hash": torrent_hash,
                        "name": torrent_name,
                        "action": "not_paused",
                    }
                )
            continue

        log_prefix = _bulk_action_log_prefix(action, dry_run)
        logger.info("%s: %s", log_prefix, torrent_name)
        modified_hashes.append(torrent_hash)
        modified += 1
        if verbose:
            details.append(
                {
                    "hash": torrent_hash,
                    "name": torrent_name,
                    "action": _bulk_action_result_name(action, dry_run),
                }
            )

    if not dry_run and modified_hashes:
        try:
            _call_bulk_torrent_action(client, action, modified_hashes)
        except Exception as error:
            raise RuntimeError(
                f"Failed to {action} selected torrents: {error}"
            ) from error

    summary: dict[str, Any] = {
        "action": action,
        "selection": selection["selection"],
        "scanned": selection["scanned"],
        "matched": selection["matched"],
        "modified": modified,
        "skipped": skipped,
        "dry_run": dry_run,
    }
    if verbose:
        summary["details"] = details

    return summary


def select_torrents_for_bulk_action(
    client: Any,
    *,
    category: str | None = None,
    tracker: str | None = None,
    match_mode: TrackerMatchMode = "exact",
    name: str | None = None,
) -> dict[str, Any]:
    """Select torrents for a bulk action using one filter."""
    active_filters = [
        filter_name
        for filter_name, filter_value in (
            ("category", category),
            ("tracker", tracker),
            ("name", name),
        )
        if filter_value is not None
    ]
    if len(active_filters) != 1:
        raise ValueError("Provide exactly one of category, tracker, or name.")

    selected_torrents: list[dict[str, Any]] = []
    scanned = 0

    for torrent in client.torrents_info():
        scanned += 1
        if not _torrent_matches_bulk_filter(
            client=client,
            torrent=torrent,
            category=category,
            tracker=tracker,
            match_mode=match_mode,
            name=name,
        ):
            continue

        selected_torrents.append(_build_bulk_torrent_entry(torrent))

    selected_torrents.sort(key=lambda item: item["name"].casefold())

    return {
        "selection": _build_bulk_selection_metadata(
            category=category,
            tracker=tracker,
            match_mode=match_mode,
            name=name,
        ),
        "scanned": scanned,
        "matched": len(selected_torrents),
        "torrents": selected_torrents,
    }


def _build_bulk_torrent_entry(torrent: Any) -> dict[str, Any]:
    """Build torrent fields used by bulk actions."""
    return {
        "hash": _get_field_as_string(torrent, "hash"),
        "name": _get_field_as_string(torrent, "name"),
        "state": _get_field_as_string(torrent, "state"),
        "category": _format_category_label(
            _get_field_as_string(torrent, "category")
        ),
    }


def _build_bulk_selection_metadata(
    *,
    category: str | None,
    tracker: str | None,
    match_mode: TrackerMatchMode,
    name: str | None,
) -> dict[str, str]:
    """Describe which filter was used for a bulk torrent action."""
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

    return {
        "filter": "name",
        "value": (name or "").strip(),
    }


def _torrent_matches_bulk_filter(
    client: Any,
    torrent: Any,
    *,
    category: str | None,
    tracker: str | None,
    match_mode: TrackerMatchMode,
    name: str | None,
) -> bool:
    """Return whether a torrent matches the requested bulk filter."""
    if category is not None:
        torrent_category = _get_field_as_string(torrent, "category")
        return _category_matches(torrent_category, category.strip())

    if tracker is not None:
        torrent_hash = _get_field_as_string(torrent, "hash")
        trackers = _get_active_tracker_urls(
            client.torrents_trackers(torrent_hash)
        )
        return has_tracker(trackers, tracker.strip(), match_mode)

    if name is not None:
        torrent_name = _get_field_as_string(torrent, "name")
        return _score_name_match(torrent_name, name) >= 0.5

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

    if action == "resume":
        client.torrents_resume(torrent_hashes)
        return

    client.torrents_reannounce(torrent_hashes)


def _bulk_action_log_prefix(action: TorrentBulkAction, dry_run: bool) -> str:
    """Return a readable log prefix for a bulk torrent action."""
    if dry_run:
        return f"Would {action}"

    return action.capitalize()


def _bulk_action_result_name(
    action: TorrentBulkAction,
    dry_run: bool,
) -> str:
    """Return a stable action label for verbose bulk summaries."""
    if dry_run:
        return f"would_{action}"

    return action


def _is_paused_state(state: str) -> bool:
    """Return whether qBittorrent reports a torrent as paused."""
    return state.casefold().startswith("paused")


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
        "name": _get_field_as_string(torrent, "name"),
        "state": _get_field_as_string(torrent, "state"),
        "size": _get_field_as_int(torrent, "size"),
        "progress": _get_field_as_float(torrent, "progress"),
        "ratio": _get_field_as_float(torrent, "ratio"),
        "save_path": _get_field_as_string(torrent, "save_path"),
        "category": _get_field_as_string(torrent, "category"),
        "added_on": _get_field_as_int(torrent, "added_on"),
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
        tracker_url = _get_field_as_string(tracker, "url")
        if tracker_url == "":
            continue

        tracker_details.append(
            {
                "url": tracker_url,
                "status": _get_field_as_string(tracker, "status"),
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
        and (tracker_url := _get_field_as_string(tracker, "url")) != ""
    ]


def _is_disabled_tracker(tracker: Any) -> bool:
    """Return whether qBittorrent reports a tracker as disabled."""
    status = _get_field_as_string(tracker, "status").strip().lower()
    return status in {"0", "disabled"}


def _get_field_as_string(item: Any, field_name: str) -> str:
    """Read a string field from an object or mapping."""
    value = _get_field(item, field_name, "")
    if value is None:
        return ""

    return str(value)


def _get_field_as_int(item: Any, field_name: str) -> int:
    """Read an integer field from an object or mapping."""
    value = _get_field(item, field_name, 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _get_field_as_float(item: Any, field_name: str) -> float:
    """Read a float field from an object or mapping."""
    value = _get_field(item, field_name, 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _get_field(item: Any, field_name: str, default: Any) -> Any:
    """Read a field from an object or mapping."""
    if isinstance(item, Mapping):
        return item.get(field_name, default)

    return getattr(item, field_name, default)
