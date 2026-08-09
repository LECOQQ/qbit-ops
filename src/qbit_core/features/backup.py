"""Export and restore qBittorrent instance state."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qbit_core.config import QbitConfig
from qbit_core.features.torrents import list_torrents_with_trackers
from qbit_core.features.trackers import (
    TrackerMatchMode,
    describe_tracker_url,
    is_pseudo_tracker_marker,
    list_tracker_usage,
    normalize_tracker_url,
    redact_tracker_identity,
    sanitize_tracker_text,
)
from qbit_core.qbit.fields import (
    get_field_as_string,
    get_field_as_tag_list,
    is_disabled_tracker,
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
    """Export torrents, trackers and metadata for backup or audit.

    qbit-ops's one deliberately sensitive export: unlike `trackers
    list`/`inspect`/`status`/`export`, this legitimately needs raw
    tracker announce URLs (including any passkey) to restore trackers
    later. Treat the resulting file as a credential: never echo it to a
    terminal summary, and store it with restrictive permissions.
    `metadata.qbit_host` is reduced to scheme + host[:port] since
    `QBIT_HOST` can itself embed the qBittorrent password as userinfo.
    """
    torrents = _add_normalized_trackers(
        list_torrents_with_trackers(client),
        match_mode,
    )
    tracker_usage = list_tracker_usage(client, match_mode)

    return {
        "metadata": {
            "exported_at": datetime.now(UTC).isoformat(),
            "qbit_ops_version": qbit_ops_version,
            "qbit_host": redact_tracker_identity(config.host),
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


def redact_backup_diff(report: dict[str, Any]) -> dict[str, Any]:
    """Reduce a diff report's tracker fields to safe `host[:port]` identities.

    `backup diff`'s default rendering: `report` (from
    `diff_backup_exports`) is computed against raw, exact tracker URLs
    (so a passkey rotation is still detected as a real difference), but
    nothing derived from it reaches output without passing through here
    first -- `--reveal-sensitive` bypasses this and renders `report`
    directly. Never mutates `report`; fields with no raw tracker data
    are passed through unchanged.
    """
    redacted_changed_torrents = [
        {
            "hash": torrent["hash"],
            "name": torrent["name"],
            "normalized_trackers": {
                "added": _redact_identity_list(
                    torrent["normalized_trackers"]["added"]
                ),
                "removed": _redact_identity_list(
                    torrent["normalized_trackers"]["removed"]
                ),
            },
        }
        for torrent in report["changed_torrents"]
    ]

    tracker_usage = report["tracker_usage"]
    redacted_usage = {
        "added": _redact_usage_counts(tracker_usage["added"]),
        "removed": _redact_usage_counts(tracker_usage["removed"]),
        "changed": _redact_usage_changed(tracker_usage["changed"]),
    }

    return {
        "summary": report["summary"],
        "added_torrents": report["added_torrents"],
        "removed_torrents": report["removed_torrents"],
        "changed_torrents": redacted_changed_torrents,
        "tracker_usage": redacted_usage,
    }


def _redact_identity_list(urls: list[str]) -> list[str]:
    """Reduce a list of raw tracker URLs to sorted, deduplicated identities."""
    return sorted({describe_tracker_url(url).identity for url in urls})


def _redact_usage_counts(usage: dict[str, int]) -> dict[str, int]:
    """Re-key a raw-URL-keyed usage dict by identity, summing collisions."""
    redacted: dict[str, int] = {}
    for tracker_url, count in usage.items():
        identity = describe_tracker_url(tracker_url).identity
        redacted[identity] = redacted.get(identity, 0) + count

    return dict(sorted(redacted.items()))


def _redact_usage_changed(
    changed: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Re-key raw-URL `tracker_usage.changed` entries by identity."""
    merged: dict[str, dict[str, int]] = {}
    for entry in changed:
        identity = describe_tracker_url(entry["tracker"]).identity
        bucket = merged.setdefault(identity, {"baseline": 0, "target": 0})
        bucket["baseline"] += entry["baseline"]
        bucket["target"] += entry["target"]

    return [
        {
            "tracker": identity,
            "baseline": counts["baseline"],
            "target": counts["target"],
        }
        for identity, counts in sorted(merged.items())
    ]


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


# --- Restore: additive-only replay of an export onto the live instance ----
#
# Never touches a torrent absent from the live instance (no torrent is
# ever created here -- that is `torrents import`'s job). Every mutation
# is additive: an existing category is never overwritten, an existing
# tag or tracker is never removed. A field missing from an older export
# (e.g. no "tags" key before this was added) naturally yields zero
# changes for that field -- "not captured" is indistinguishable from
# "nothing to add", which is exactly the safe behavior wanted.


@dataclass(frozen=True)
class BackupRestoreCategoryChange:
    hash: str
    name: str
    category: str


@dataclass(frozen=True)
class BackupRestoreTagChange:
    hash: str
    name: str
    added_tags: tuple[str, ...]


@dataclass(frozen=True)
class BackupRestoreTrackerChange:
    """`added_trackers` holds raw announce URLs (may carry a passkey) --
    needed by `apply_backup_restore`, but never meant to be rendered.
    Callers must only ever display `len(added_trackers)`."""

    hash: str
    name: str
    added_trackers: tuple[str, ...]


@dataclass(frozen=True)
class BackupRestorePlan:
    matched: int
    unmatched_hashes: tuple[str, ...]
    categories_to_create: tuple[str, ...]
    category_changes: tuple[BackupRestoreCategoryChange, ...]
    tag_changes: tuple[BackupRestoreTagChange, ...]
    tracker_changes: tuple[BackupRestoreTrackerChange, ...]

    @property
    def has_changes(self) -> bool:
        return bool(
            self.category_changes or self.tag_changes or self.tracker_changes
        )


@dataclass(frozen=True)
class BackupRestoreFailure:
    hash: str
    name: str
    action: str
    message: str


@dataclass(frozen=True)
class BackupRestoreResult:
    categories_created: tuple[str, ...]
    categories_restored: int
    tags_restored: int
    trackers_restored: int
    failures: tuple[BackupRestoreFailure, ...]


def _real_tracker_urls_from_export(trackers: Any) -> set[str]:
    """Extract real (non-disabled, non-pseudo) announce URLs from an
    export torrent's raw `trackers` list."""
    if not isinstance(trackers, list):
        return set()

    urls: set[str] = set()
    for tracker in trackers:
        if not isinstance(tracker, dict):
            continue
        if tracker.get("disabled"):
            continue
        url = str(tracker.get("url", "")).strip()
        if url == "" or is_pseudo_tracker_marker(url):
            continue
        urls.add(url)

    return urls


def _real_tracker_urls_live(trackers: Any) -> set[str]:
    """Extract real (non-disabled, non-pseudo) announce URLs from a live
    `client.torrents_trackers()` response."""
    urls: set[str] = set()
    for tracker in trackers:
        if is_disabled_tracker(tracker):
            continue
        url = get_field_as_string(tracker, "url")
        if url == "" or is_pseudo_tracker_marker(url):
            continue
        urls.add(url)

    return urls


def plan_backup_restore(
    client: Any, export: dict[str, Any]
) -> BackupRestorePlan:
    """Plan an additive restore of category/tags/trackers from `export`.

    Only affects torrents already present locally (matched by hash);
    never adds a torrent absent locally, never overwrites an existing
    category, and never removes a tag or tracker.
    """
    live_torrents = {
        get_field_as_string(torrent, "hash").lower(): torrent
        for torrent in client.torrents_info()
    }
    existing_categories = set(client.torrents_categories().keys())

    matched = 0
    unmatched: list[str] = []
    categories_needed: set[str] = set()
    category_changes: list[BackupRestoreCategoryChange] = []
    tag_changes: list[BackupRestoreTagChange] = []
    tracker_changes: list[BackupRestoreTrackerChange] = []

    for entry in export.get("torrents", []):
        if not isinstance(entry, dict):
            continue
        export_hash = str(entry.get("hash", "")).strip()
        if export_hash == "":
            continue

        live_torrent = live_torrents.get(export_hash.lower())
        if live_torrent is None:
            unmatched.append(export_hash)
            continue
        matched += 1

        torrent_hash = get_field_as_string(live_torrent, "hash")
        name = get_field_as_string(live_torrent, "name")

        desired_category = str(entry.get("category", "")).strip()
        current_category = get_field_as_string(live_torrent, "category").strip()
        if desired_category != "" and current_category == "":
            category_changes.append(
                BackupRestoreCategoryChange(
                    hash=torrent_hash, name=name, category=desired_category
                )
            )
            if desired_category not in existing_categories:
                categories_needed.add(desired_category)

        desired_tags = {str(tag) for tag in entry.get("tags", []) or []}
        current_tags = set(get_field_as_tag_list(live_torrent))
        missing_tags = sorted(desired_tags - current_tags)
        if missing_tags:
            tag_changes.append(
                BackupRestoreTagChange(
                    hash=torrent_hash,
                    name=name,
                    added_tags=tuple(missing_tags),
                )
            )

        desired_trackers = _real_tracker_urls_from_export(entry.get("trackers"))
        current_trackers = _real_tracker_urls_live(
            client.torrents_trackers(torrent_hash)
        )
        missing_trackers = sorted(desired_trackers - current_trackers)
        if missing_trackers:
            tracker_changes.append(
                BackupRestoreTrackerChange(
                    hash=torrent_hash,
                    name=name,
                    added_trackers=tuple(missing_trackers),
                )
            )

    return BackupRestorePlan(
        matched=matched,
        unmatched_hashes=tuple(sorted(unmatched)),
        categories_to_create=tuple(sorted(categories_needed)),
        category_changes=tuple(category_changes),
        tag_changes=tuple(tag_changes),
        tracker_changes=tuple(tracker_changes),
    )


def apply_backup_restore(
    client: Any, plan: BackupRestorePlan
) -> BackupRestoreResult:
    """Apply `plan`. Every action's outcome is recorded independently --
    one failure (e.g. a category that can't be created) never hides or
    blocks the others."""
    categories_created: list[str] = []
    failures: list[BackupRestoreFailure] = []

    for category in plan.categories_to_create:
        try:
            client.torrents_create_category(name=category)
            categories_created.append(category)
        except Exception as error:
            failures.append(
                BackupRestoreFailure(
                    hash="",
                    name=category,
                    action="create_category",
                    message=str(error),
                )
            )

    categories_restored = 0
    for change in plan.category_changes:
        try:
            client.torrents_set_category(
                torrent_hashes=change.hash, category=change.category
            )
            categories_restored += 1
        except Exception as error:
            failures.append(
                BackupRestoreFailure(
                    hash=change.hash,
                    name=change.name,
                    action="set_category",
                    message=str(error),
                )
            )

    tags_restored = 0
    for change in plan.tag_changes:
        try:
            client.torrents_add_tags(
                torrent_hashes=change.hash, tags=list(change.added_tags)
            )
            tags_restored += 1
        except Exception as error:
            failures.append(
                BackupRestoreFailure(
                    hash=change.hash,
                    name=change.name,
                    action="add_tags",
                    message=str(error),
                )
            )

    trackers_restored = 0
    for change in plan.tracker_changes:
        try:
            client.torrents_add_trackers(
                torrent_hash=change.hash, urls=list(change.added_trackers)
            )
            trackers_restored += 1
        except Exception as error:
            failures.append(
                BackupRestoreFailure(
                    hash=change.hash,
                    name=change.name,
                    action="add_trackers",
                    message=sanitize_tracker_text(str(error)),
                )
            )

    return BackupRestoreResult(
        categories_created=tuple(categories_created),
        categories_restored=categories_restored,
        tags_restored=tags_restored,
        trackers_restored=trackers_restored,
        failures=tuple(failures),
    )
