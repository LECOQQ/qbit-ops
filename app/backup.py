"""Export qBittorrent instance state."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import QbitConfig
from app.torrents import list_torrents_with_trackers
from app.trackers import (
    TrackerMatchMode,
    list_tracker_usage,
    normalize_tracker_url,
)


class BackupExportError(ValueError):
    """Report invalid backup export payloads."""


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


def load_export_file(path: Path) -> dict[str, Any]:
    """Load a backup or tracker export JSON file."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise BackupExportError(
            f"Unable to read export file {path}: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise BackupExportError(
            f"Invalid JSON in export file {path}: {error.msg}"
        ) from error

    if not isinstance(payload, dict):
        raise BackupExportError(
            f"Export file {path} must contain a JSON object at the root."
        )

    torrents = payload.get("torrents")
    if not isinstance(torrents, list):
        raise BackupExportError(
            f"Export file {path} is missing a 'torrents' list."
        )

    tracker_usage = payload.get("tracker_usage", {})
    if not isinstance(tracker_usage, dict):
        raise BackupExportError(
            f"Export file {path} has an invalid 'tracker_usage' object."
        )

    return payload


def diff_backup_exports(
    baseline: dict[str, Any],
    target: dict[str, Any],
    *,
    baseline_source: str = "baseline",
    target_source: str = "target",
) -> dict[str, Any]:
    """Compare two export payloads and report tracker-related differences."""
    baseline_torrents = _index_torrents_by_hash(baseline["torrents"])
    target_torrents = _index_torrents_by_hash(target["torrents"])
    baseline_usage = _normalize_tracker_usage(baseline.get("tracker_usage", {}))
    target_usage = _normalize_tracker_usage(target.get("tracker_usage", {}))

    added_torrents = _list_torrent_refs_from_index(
        (
            hash_value
            for hash_value in target_torrents
            if hash_value not in baseline_torrents
        ),
        target_torrents,
    )
    removed_torrents = _list_torrent_refs_from_index(
        (
            hash_value
            for hash_value in baseline_torrents
            if hash_value not in target_torrents
        ),
        baseline_torrents,
    )

    changed_torrents: list[dict[str, Any]] = []
    for hash_value, baseline_torrent in baseline_torrents.items():
        target_torrent = target_torrents.get(hash_value)
        if target_torrent is None:
            continue

        tracker_changes = _diff_sorted_values(
            baseline_torrent.get("normalized_trackers", []),
            target_torrent.get("normalized_trackers", []),
        )
        if not tracker_changes["added"] and not tracker_changes["removed"]:
            continue

        changed_torrents.append(
            {
                "hash": baseline_torrent["hash"],
                "name": baseline_torrent["name"],
                "normalized_trackers": tracker_changes,
            }
        )

    tracker_usage_diff = _diff_tracker_usage(baseline_usage, target_usage)
    identical = (
        not added_torrents
        and not removed_torrents
        and not changed_torrents
        and not tracker_usage_diff["added"]
        and not tracker_usage_diff["removed"]
        and not tracker_usage_diff["changed"]
    )

    return {
        "summary": {
            "identical": identical,
            "baseline": {
                "source": baseline_source,
                "torrents": len(baseline_torrents),
                "unique_trackers": len(baseline_usage),
            },
            "target": {
                "source": target_source,
                "torrents": len(target_torrents),
                "unique_trackers": len(target_usage),
            },
            "added_torrents": len(added_torrents),
            "removed_torrents": len(removed_torrents),
            "changed_torrents": len(changed_torrents),
            "tracker_usage_added": len(tracker_usage_diff["added"]),
            "tracker_usage_removed": len(tracker_usage_diff["removed"]),
            "tracker_usage_changed": len(tracker_usage_diff["changed"]),
        },
        "added_torrents": added_torrents,
        "removed_torrents": removed_torrents,
        "changed_torrents": changed_torrents,
        "tracker_usage": tracker_usage_diff,
    }


def has_backup_diff(report: dict[str, Any]) -> bool:
    """Return whether a backup diff report contains any difference."""
    return not report["summary"]["identical"]


def _index_torrents_by_hash(
    torrents: list[Any],
) -> dict[str, dict[str, Any]]:
    """Index export torrents by lowercase hash."""
    indexed_torrents: dict[str, dict[str, Any]] = {}

    for torrent in torrents:
        if not isinstance(torrent, dict):
            raise BackupExportError(
                "Export torrent entries must be JSON objects."
            )

        hash_value = str(torrent.get("hash", "")).strip()
        if hash_value == "":
            raise BackupExportError(
                "Export torrent entries must include a hash."
            )

        indexed_torrents[hash_value.lower()] = {
            "hash": hash_value,
            "name": str(torrent.get("name", hash_value)),
            "normalized_trackers": _as_string_list(
                torrent.get("normalized_trackers", [])
            ),
        }

    return indexed_torrents


def _normalize_tracker_usage(tracker_usage: Any) -> dict[str, int]:
    """Normalize tracker usage counts from an export payload."""
    if not isinstance(tracker_usage, dict):
        return {}

    normalized_usage: dict[str, int] = {}
    for tracker_url, torrent_count in tracker_usage.items():
        try:
            normalized_usage[str(tracker_url)] = int(torrent_count)
        except (TypeError, ValueError):
            continue

    return normalized_usage


def _list_torrent_refs_from_index(
    hash_values: Any,
    torrents_by_hash: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """Build sorted torrent references using indexed torrent metadata."""
    torrent_refs: list[dict[str, str]] = []

    for hash_value in hash_values:
        torrent = torrents_by_hash[hash_value]
        torrent_refs.append(
            {
                "hash": torrent["hash"],
                "name": torrent["name"],
            }
        )

    return sorted(torrent_refs, key=lambda item: item["hash"])


def _diff_sorted_values(
    baseline_values: list[str],
    target_values: list[str],
) -> dict[str, list[str]]:
    """Return added and removed values between two sorted lists."""
    baseline_set = set(baseline_values)
    target_set = set(target_values)

    return {
        "added": sorted(target_set - baseline_set),
        "removed": sorted(baseline_set - target_set),
    }


def _diff_tracker_usage(
    baseline_usage: dict[str, int],
    target_usage: dict[str, int],
) -> dict[str, Any]:
    """Compare tracker usage maps between two exports."""
    added = {
        tracker_url: target_usage[tracker_url]
        for tracker_url in sorted(target_usage)
        if tracker_url not in baseline_usage
    }
    removed = {
        tracker_url: baseline_usage[tracker_url]
        for tracker_url in sorted(baseline_usage)
        if tracker_url not in target_usage
    }
    changed = [
        {
            "tracker": tracker_url,
            "baseline": baseline_usage[tracker_url],
            "target": target_usage[tracker_url],
        }
        for tracker_url in sorted(baseline_usage)
        if tracker_url in target_usage
        and baseline_usage[tracker_url] != target_usage[tracker_url]
    ]

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def _as_string_list(values: Any) -> list[str]:
    """Convert export list values to strings."""
    if not isinstance(values, list):
        return []

    return [str(value) for value in values]


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
