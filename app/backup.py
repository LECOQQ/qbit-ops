"""Export qBittorrent instance state."""

from datetime import UTC, datetime
from typing import Any

from app.config import QbitConfig
from app.torrents import list_torrents_with_trackers
from app.trackers import (
    TrackerMatchMode,
    list_tracker_usage,
    normalize_tracker_url,
)


def export_instance_state(
    client: Any,
    config: QbitConfig,
    qbit_ops_version: str,
    qbittorrent_version: str,
    web_api_version: str,
    match_mode: TrackerMatchMode = "exact",
) -> dict[str, Any]:
    """Export torrents, trackers and metadata for backup or audit."""
    torrents = _add_normalized_trackers(
        list_torrents_with_trackers(client),
        match_mode,
    )
    tracker_usage = list_tracker_usage(client, match_mode)

    return {
        "metadata": {
            "exported_at": datetime.now(UTC).isoformat(),
            "qbit_ops_version": qbit_ops_version,
            "qbit_host": config.host,
            "qbit_user": config.username,
            "qbittorrent_version": qbittorrent_version,
            "web_api_version": web_api_version,
            "tracker_match": match_mode,
        },
        "summary": {
            "torrents": len(torrents),
            "unique_trackers": len(tracker_usage),
            "tracker_match": match_mode,
        },
        "torrents": torrents,
        "tracker_usage": tracker_usage,
    }


def _add_normalized_trackers(
    torrents: list[dict[str, Any]],
    match_mode: TrackerMatchMode,
) -> list[dict[str, Any]]:
    """Attach normalized active tracker identities to exported torrents."""
    enriched_torrents: list[dict[str, Any]] = []

    for torrent in torrents:
        normalized_trackers = sorted(
            {
                normalized_tracker
                for tracker in torrent["trackers"]
                if not tracker["disabled"]
                and (
                    normalized_tracker := normalize_tracker_url(
                        tracker["url"],
                        match_mode,
                    )
                )
                != ""
            }
        )
        enriched_torrent = dict(torrent)
        enriched_torrent["normalized_trackers"] = normalized_trackers
        enriched_torrents.append(enriched_torrent)

    return enriched_torrents
