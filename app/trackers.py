"""Manage qBittorrent trackers."""

import logging
from collections.abc import Mapping
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

TrackerMatchMode = Literal["exact", "without-query"]


def normalize_tracker_url(
    url: str,
    match_mode: TrackerMatchMode = "exact",
) -> str:
    """Normalize a tracker URL before comparisons."""
    stripped_url = url.strip()

    if match_mode == "exact":
        return stripped_url.rstrip("/")

    if match_mode == "without-query":
        parsed_url = urlsplit(stripped_url)
        normalized_path = parsed_url.path.rstrip("/")
        return urlunsplit(
            (
                parsed_url.scheme,
                parsed_url.netloc,
                normalized_path,
                "",
                "",
            )
        )

    raise ValueError(f"Unsupported tracker match mode: {match_mode}")


def has_tracker(
    trackers: list[str],
    tracker: str,
    match_mode: TrackerMatchMode = "exact",
) -> bool:
    """Return whether a tracker list contains the requested tracker."""
    normalized_tracker = normalize_tracker_url(tracker, match_mode)
    normalized_trackers = {
        normalize_tracker_url(existing_tracker, match_mode)
        for existing_tracker in trackers
    }

    return normalized_tracker in normalized_trackers


def list_tracker_usage(
    client: Any,
    match_mode: TrackerMatchMode = "exact",
) -> dict[str, int]:
    """List normalized trackers and count torrents using each one."""
    tracker_usage: dict[str, int] = {}

    for torrent in client.torrents_info():
        torrent_hash = _get_torrent_hash(torrent)
        trackers = _get_active_tracker_urls(
            client.torrents_trackers(torrent_hash)
        )
        normalized_trackers = {
            normalized_tracker
            for tracker_url in trackers
            if (
                normalized_tracker := normalize_tracker_url(
                    tracker_url,
                    match_mode,
                )
            )
            != ""
        }

        for normalized_tracker in normalized_trackers:
            tracker_usage[normalized_tracker] = (
                tracker_usage.get(normalized_tracker, 0) + 1
            )

    return dict(sorted(tracker_usage.items()))


def inspect_tracker(
    client: Any,
    tracker: str,
    match_mode: TrackerMatchMode = "exact",
) -> dict[str, Any]:
    """List torrents using a tracker."""
    scanned = 0
    torrents: list[dict[str, Any]] = []

    for torrent in client.torrents_info():
        scanned += 1
        torrent_hash = _get_torrent_hash(torrent)
        torrent_name = _get_torrent_name(torrent)
        trackers = _get_active_tracker_urls(
            client.torrents_trackers(torrent_hash)
        )
        matching_tracker_urls = _get_matching_tracker_urls(
            trackers,
            tracker,
            match_mode,
        )

        if not matching_tracker_urls:
            continue

        torrents.append(
            {
                "hash": torrent_hash,
                "name": torrent_name,
                "state": _get_field_as_string(torrent, "state"),
                "size": _get_field_as_int(torrent, "size"),
                "progress": _get_field_as_float(torrent, "progress"),
                "ratio": _get_field_as_float(torrent, "ratio"),
                "active_tracker_count": len(trackers),
                "matching_tracker_urls": matching_tracker_urls,
            }
        )

    return {
        "tracker": tracker,
        "match": match_mode,
        "scanned": scanned,
        "matched_tracker": len(torrents),
        "torrents": torrents,
    }


def export_tracker_state(
    client: Any,
    match_mode: TrackerMatchMode = "exact",
) -> dict[str, Any]:
    """Export active tracker state for every torrent."""
    torrents: list[dict[str, Any]] = []

    for torrent in client.torrents_info():
        torrent_hash = _get_torrent_hash(torrent)
        torrent_name = _get_torrent_name(torrent)
        trackers = _get_active_tracker_urls(
            client.torrents_trackers(torrent_hash)
        )
        normalized_trackers = sorted(
            {
                normalized_tracker
                for tracker_url in trackers
                if (
                    normalized_tracker := normalize_tracker_url(
                        tracker_url,
                        match_mode,
                    )
                )
                != ""
            }
        )
        torrents.append(
            {
                "hash": torrent_hash,
                "name": torrent_name,
                "trackers": trackers,
                "normalized_trackers": normalized_trackers,
            }
        )

    return {
        "summary": {
            "torrents": len(torrents),
            "match": match_mode,
        },
        "torrents": torrents,
    }


def analyze_tracker_health(client: Any) -> dict[str, Any]:
    """Analyze tracker health across all torrents."""
    scanned = 0
    active_tracker_occurrences = 0
    disabled_tracker_occurrences = 0
    exact_trackers: set[str] = set()
    logical_trackers: set[str] = set()
    disabled_trackers: set[str] = set()
    query_variants: dict[str, dict[str, set[str]]] = {}

    for torrent in client.torrents_info():
        scanned += 1
        torrent_hash = _get_torrent_hash(torrent)
        torrent_name = _get_torrent_name(torrent)

        for tracker in client.torrents_trackers(torrent_hash):
            tracker_url = _get_field_as_string(tracker, "url")
            if tracker_url == "":
                continue

            if _is_disabled_tracker(tracker):
                disabled_tracker_occurrences += 1
                disabled_trackers.add(tracker_url)
                continue

            active_tracker_occurrences += 1
            exact_tracker = normalize_tracker_url(tracker_url)
            logical_tracker = normalize_tracker_url(
                tracker_url,
                "without-query",
            )
            exact_trackers.add(exact_tracker)
            logical_trackers.add(logical_tracker)

            group = query_variants.setdefault(
                logical_tracker,
                {"variants": set(), "torrents": set()},
            )
            group["variants"].add(exact_tracker)
            group["torrents"].add(f"{torrent_name} ({torrent_hash})")

    query_variant_groups = [
        {
            "tracker": tracker_url,
            "variants": sorted(group["variants"]),
            "torrents": sorted(group["torrents"]),
        }
        for tracker_url, group in sorted(query_variants.items())
        if len(group["variants"]) > 1
    ]

    return {
        "summary": {
            "scanned": scanned,
            "active_tracker_occurrences": active_tracker_occurrences,
            "disabled_tracker_occurrences": disabled_tracker_occurrences,
            "unique_exact_trackers": len(exact_trackers),
            "unique_logical_trackers": len(logical_trackers),
            "query_variant_groups": len(query_variant_groups),
        },
        "disabled_trackers": sorted(disabled_trackers),
        "query_variant_groups": query_variant_groups,
    }


def add_tracker_if_source_present(
    client: Any,
    source_tracker: str,
    target_tracker: str,
    dry_run: bool = True,
    match_mode: TrackerMatchMode = "exact",
    verbose: bool = False,
) -> dict[str, Any]:
    """Add a target tracker to torrents already using the source tracker."""
    scanned = 0
    matched_source = 0
    already_had_target = 0
    modified = 0
    details: list[dict[str, str]] = []

    for torrent in client.torrents_info():
        scanned += 1
        torrent_hash = _get_torrent_hash(torrent)
        torrent_name = _get_torrent_name(torrent)
        trackers = _get_active_tracker_urls(
            client.torrents_trackers(torrent_hash)
        )

        if not has_tracker(trackers, source_tracker, match_mode):
            continue

        matched_source += 1
        if has_tracker(trackers, target_tracker, match_mode):
            already_had_target += 1
            logger.info("Already present: %s", torrent_name)
            if verbose:
                details.append(
                    {
                        "hash": torrent_hash,
                        "name": torrent_name,
                        "action": "already_had_target",
                    }
                )
            continue

        if dry_run:
            logger.info("Would add tracker to: %s", torrent_name)
            action = "would_add"
        else:
            logger.info("Adding tracker to: %s", torrent_name)
            try:
                client.torrents_add_trackers(
                    torrent_hash=torrent_hash,
                    urls=target_tracker,
                )
            except Exception as error:
                raise RuntimeError(
                    "Failed to add tracker to torrent "
                    f"'{torrent_name}' ({torrent_hash}): {error}"
                ) from error
            action = "added"

        modified += 1
        if verbose:
            details.append(
                {
                    "hash": torrent_hash,
                    "name": torrent_name,
                    "action": action,
                }
            )

    summary: dict[str, Any] = {
        "scanned": scanned,
        "matched_source": matched_source,
        "already_had_target": already_had_target,
        "modified": modified,
        "dry_run": dry_run,
    }
    if verbose:
        summary["details"] = details

    return summary


def remove_tracker_from_all(
    client: Any,
    tracker: str,
    dry_run: bool = True,
    match_mode: TrackerMatchMode = "exact",
    verbose: bool = False,
) -> dict[str, Any]:
    """Remove a tracker from every torrent using it."""
    scanned = 0
    matched_tracker = 0
    modified = 0
    removed_urls = 0
    details: list[dict[str, Any]] = []

    for torrent in client.torrents_info():
        scanned += 1
        torrent_hash = _get_torrent_hash(torrent)
        torrent_name = _get_torrent_name(torrent)
        trackers = _get_active_tracker_urls(
            client.torrents_trackers(torrent_hash)
        )
        matching_tracker_urls = _get_matching_tracker_urls(
            trackers,
            tracker,
            match_mode,
        )

        if not matching_tracker_urls:
            continue

        matched_tracker += 1
        removed_urls += len(matching_tracker_urls)

        if dry_run:
            logger.info(
                "Would remove tracker from: %s (%s URL(s))",
                torrent_name,
                len(matching_tracker_urls),
            )
            action = "would_remove"
        else:
            logger.info(
                "Removing tracker from: %s (%s URL(s))",
                torrent_name,
                len(matching_tracker_urls),
            )
            try:
                client.torrents_remove_trackers(
                    torrent_hash=torrent_hash,
                    urls=matching_tracker_urls,
                )
            except Exception as error:
                raise RuntimeError(
                    "Failed to remove tracker from torrent "
                    f"'{torrent_name}' ({torrent_hash}): {error}"
                ) from error
            action = "removed"

        modified += 1
        if verbose:
            details.append(
                {
                    "hash": torrent_hash,
                    "name": torrent_name,
                    "action": action,
                    "matching_tracker_urls": matching_tracker_urls,
                }
            )

    summary = {
        "scanned": scanned,
        "matched_tracker": matched_tracker,
        "modified": modified,
        "removed_urls": removed_urls,
        "dry_run": dry_run,
    }
    if verbose:
        summary["details"] = details

    return summary


def replace_tracker_in_all(
    client: Any,
    source_tracker: str,
    target_tracker: str,
    dry_run: bool = True,
    match_mode: TrackerMatchMode = "exact",
    verbose: bool = False,
) -> dict[str, Any]:
    """Replace a source tracker with a target on matching torrents."""
    _ensure_distinct_tracker_identity(
        source_tracker,
        target_tracker,
        match_mode,
    )

    scanned = 0
    matched_source = 0
    already_had_target = 0
    modified = 0
    replaced_urls = 0
    removed_urls = 0
    details: list[dict[str, Any]] = []

    for torrent in client.torrents_info():
        scanned += 1
        torrent_hash = _get_torrent_hash(torrent)
        torrent_name = _get_torrent_name(torrent)
        trackers = _get_active_tracker_urls(
            client.torrents_trackers(torrent_hash)
        )
        matching_source_urls = _get_matching_tracker_urls(
            trackers,
            source_tracker,
            match_mode,
        )

        if not matching_source_urls:
            continue

        matched_source += 1
        target_already_present = has_tracker(
            trackers,
            target_tracker,
            match_mode,
        )
        source_urls_to_remove = matching_source_urls
        source_url_to_replace = ""
        action = "would_remove_source"

        if target_already_present:
            already_had_target += 1
            log_prefix = "Would remove" if dry_run else "Removing"
            logger.info(
                "%s source tracker from %s because target is present",
                log_prefix,
                torrent_name,
            )
        else:
            source_url_to_replace = matching_source_urls[0]
            source_urls_to_remove = matching_source_urls[1:]
            action = "would_replace"
            log_prefix = "Would replace" if dry_run else "Replacing"
            logger.info("%s tracker on: %s", log_prefix, torrent_name)

        if not dry_run:
            try:
                if source_url_to_replace:
                    client.torrents_edit_tracker(
                        torrent_hash=torrent_hash,
                        original_url=source_url_to_replace,
                        new_url=target_tracker,
                    )
                    action = "replaced"

                if source_urls_to_remove:
                    client.torrents_remove_trackers(
                        torrent_hash=torrent_hash,
                        urls=source_urls_to_remove,
                    )
                    if not source_url_to_replace:
                        action = "removed_source"
            except Exception as error:
                raise RuntimeError(
                    "Failed to replace tracker on torrent "
                    f"'{torrent_name}' ({torrent_hash}): {error}"
                ) from error

        replaced_urls += 1 if source_url_to_replace else 0
        removed_urls += len(source_urls_to_remove)
        modified += 1

        if verbose:
            details.append(
                {
                    "hash": torrent_hash,
                    "name": torrent_name,
                    "action": action,
                    "replaced_tracker_url": source_url_to_replace,
                    "matching_tracker_urls": matching_source_urls,
                    "removed_tracker_urls": source_urls_to_remove,
                }
            )

    summary: dict[str, Any] = {
        "scanned": scanned,
        "matched_source": matched_source,
        "already_had_target": already_had_target,
        "modified": modified,
        "replaced_urls": replaced_urls,
        "removed_urls": removed_urls,
        "dry_run": dry_run,
    }
    if verbose:
        summary["details"] = details

    return summary


def _get_active_tracker_urls(trackers: Any) -> list[str]:
    """Extract non-disabled tracker URLs from qBittorrent tracker objects."""
    return [
        tracker_url
        for tracker in trackers
        if not _is_disabled_tracker(tracker)
        and (tracker_url := _get_field_as_string(tracker, "url")) != ""
    ]


def _get_matching_tracker_urls(
    trackers: list[str],
    tracker: str,
    match_mode: TrackerMatchMode,
) -> list[str]:
    """Return raw tracker URLs matching a normalized tracker."""
    normalized_tracker = normalize_tracker_url(tracker, match_mode)

    return [
        tracker_url
        for tracker_url in trackers
        if normalize_tracker_url(tracker_url, match_mode) == normalized_tracker
    ]


def _is_disabled_tracker(tracker: Any) -> bool:
    """Return whether qBittorrent reports a tracker as disabled."""
    status = _get_field_as_string(tracker, "status").strip().lower()
    return status in {"0", "disabled"}


def _ensure_distinct_tracker_identity(
    source_tracker: str,
    target_tracker: str,
    match_mode: TrackerMatchMode,
) -> None:
    """Ensure source and target trackers are distinct for replacement."""
    normalized_source = normalize_tracker_url(source_tracker, match_mode)
    normalized_target = normalize_tracker_url(target_tracker, match_mode)

    if normalized_source == normalized_target:
        raise RuntimeError(
            "Source and target trackers resolve to the same tracker identity."
        )


def _get_torrent_hash(torrent: Any) -> str:
    """Extract the torrent hash from a qBittorrent torrent object."""
    torrent_hash = _get_field_as_string(torrent, "hash")
    if torrent_hash == "":
        raise RuntimeError("Unable to read torrent hash from qBittorrent data.")

    return torrent_hash


def _get_torrent_name(torrent: Any) -> str:
    """Extract the torrent name from a qBittorrent torrent object."""
    torrent_name = _get_field_as_string(torrent, "name")
    if torrent_name == "":
        return _get_torrent_hash(torrent)

    return torrent_name


def _get_field_as_int(item: Any, field_name: str) -> int:
    """Read an integer field from an object or mapping."""
    value: Any
    if isinstance(item, Mapping):
        value = item.get(field_name, 0)
    else:
        value = getattr(item, field_name, 0)

    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _get_field_as_float(item: Any, field_name: str) -> float:
    """Read a float field from an object or mapping."""
    value: Any
    if isinstance(item, Mapping):
        value = item.get(field_name, 0.0)
    else:
        value = getattr(item, field_name, 0.0)

    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _get_field_as_string(item: Any, field_name: str) -> str:
    """Read a string field from an object or mapping."""
    value: Any
    if isinstance(item, Mapping):
        value = item.get(field_name, "")
    else:
        value = getattr(item, field_name, "")

    if value is None:
        return ""

    return str(value)
