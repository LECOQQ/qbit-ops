"""List qBittorrent torrents."""

from collections.abc import Mapping
from difflib import SequenceMatcher
from typing import Any


def list_torrents(client: Any) -> list[dict[str, Any]]:
    """List torrents with useful audit fields."""
    torrents: list[dict[str, Any]] = []

    for torrent in client.torrents_info():
        torrent_hash = _get_field_as_string(torrent, "hash")
        trackers = _get_active_tracker_urls(
            client.torrents_trackers(torrent_hash)
        )
        torrents.append(
            {
                "hash": torrent_hash,
                "name": _get_field_as_string(torrent, "name"),
                "state": _get_field_as_string(torrent, "state"),
                "size": _get_field_as_int(torrent, "size"),
                "progress": _get_field_as_float(torrent, "progress"),
                "ratio": _get_field_as_float(torrent, "ratio"),
                "tracker_count": len(trackers),
            }
        )

    return torrents


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
