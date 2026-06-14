"""List qBittorrent torrents."""

from collections.abc import Mapping
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
