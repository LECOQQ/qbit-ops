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


def add_tracker_if_source_present(
    client: Any,
    source_tracker: str,
    target_tracker: str,
    dry_run: bool = True,
    match_mode: TrackerMatchMode = "exact",
) -> dict[str, int | bool]:
    """Add a target tracker to torrents already using the source tracker."""
    scanned = 0
    matched_source = 0
    already_had_target = 0
    modified = 0

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
            continue

        if dry_run:
            logger.info("Would add tracker to: %s", torrent_name)
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

        modified += 1

    return {
        "scanned": scanned,
        "matched_source": matched_source,
        "already_had_target": already_had_target,
        "modified": modified,
        "dry_run": dry_run,
    }


def remove_tracker_from_all(
    client: Any,
    tracker: str,
    dry_run: bool = True,
    match_mode: TrackerMatchMode = "exact",
) -> dict[str, int | bool]:
    """Remove a tracker from every torrent using it."""
    scanned = 0
    matched_tracker = 0
    modified = 0
    removed_urls = 0

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

        modified += 1

    return {
        "scanned": scanned,
        "matched_tracker": matched_tracker,
        "modified": modified,
        "removed_urls": removed_urls,
        "dry_run": dry_run,
    }


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
