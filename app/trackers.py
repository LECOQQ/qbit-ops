"""Manage qBittorrent trackers."""

import logging
from collections.abc import Callable, Mapping
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Add a target tracker to torrents already using the source tracker."""
    all_torrents = list(client.torrents_info())
    total = len(all_torrents)
    scanned = 0
    matched_source = 0
    already_had_target = 0
    modified = 0
    details: list[dict[str, str]] = []

    for torrent in all_torrents:
        scanned += 1
        if on_progress is not None:
            on_progress(scanned, total)

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
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Remove a tracker from every torrent using it."""
    all_torrents = list(client.torrents_info())
    total = len(all_torrents)
    scanned = 0
    matched_tracker = 0
    modified = 0
    removed_urls = 0
    details: list[dict[str, Any]] = []

    for torrent in all_torrents:
        scanned += 1
        if on_progress is not None:
            on_progress(scanned, total)

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
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Replace a source tracker with a target on matching torrents."""
    _ensure_distinct_tracker_identity(
        source_tracker,
        target_tracker,
        match_mode,
    )

    all_torrents = list(client.torrents_info())
    total = len(all_torrents)
    scanned = 0
    matched_source = 0
    already_had_target = 0
    modified = 0
    replaced_urls = 0
    removed_urls = 0
    details: list[dict[str, Any]] = []

    for torrent in all_torrents:
        scanned += 1
        if on_progress is not None:
            on_progress(scanned, total)

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


PASSKEY_PLACEHOLDER = "{passkey}"


def _parse_passkey_template(
    template: str,
) -> tuple[str, str, Literal["query", "path"], str | int]:
    """Locate the passkey placeholder in a tracker URL template.

    Returns the template's scheme, netloc, placeholder mode, and
    position: a query parameter name in "query" mode, or a zero-based
    path segment index in "path" mode.
    """
    stripped_template = template.strip()
    if stripped_template.count(PASSKEY_PLACEHOLDER) != 1:
        raise RuntimeError(
            "Tracker template must contain exactly one "
            f"'{PASSKEY_PLACEHOLDER}' placeholder marking the passkey "
            "position."
        )

    parsed_template = urlsplit(stripped_template)

    if PASSKEY_PLACEHOLDER in parsed_template.query:
        query_params = parse_qsl(parsed_template.query, keep_blank_values=True)
        placeholder_keys = [
            key for key, value in query_params if value == PASSKEY_PLACEHOLDER
        ]
        if len(placeholder_keys) == 1:
            return (
                parsed_template.scheme,
                parsed_template.netloc,
                "query",
                placeholder_keys[0],
            )

    path_segments = parsed_template.path.split("/")
    placeholder_indexes = [
        index
        for index, segment in enumerate(path_segments)
        if segment == PASSKEY_PLACEHOLDER
    ]
    if len(placeholder_indexes) == 1:
        return (
            parsed_template.scheme,
            parsed_template.netloc,
            "path",
            placeholder_indexes[0],
        )

    raise RuntimeError(
        f"Tracker template must place the '{PASSKEY_PLACEHOLDER}' "
        "placeholder either as a query parameter value "
        "(e.g. '?passkey={passkey}') or as a full path segment "
        "(e.g. '/announce/{passkey}')."
    )


def _build_query_passkey_url(
    url: str,
    passkey_param: str,
    new_passkey: str,
) -> str:
    """Return a tracker URL with a specific query parameter replaced."""
    parsed_url = urlsplit(url)
    query_params = parse_qsl(parsed_url.query, keep_blank_values=True)

    replaced = False
    updated_params: list[tuple[str, str]] = []
    for key, value in query_params:
        if key == passkey_param:
            updated_params.append((key, new_passkey))
            replaced = True
        else:
            updated_params.append((key, value))

    if not replaced:
        updated_params.append((passkey_param, new_passkey))

    return urlunsplit(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            urlencode(updated_params),
            parsed_url.fragment,
        )
    )


def _match_path_passkey(
    actual_url: str,
    scheme: str,
    netloc: str,
    template_segments: list[str],
    placeholder_index: int,
) -> str | None:
    """Return the current passkey value if a URL matches the path template."""
    parsed_actual = urlsplit(actual_url)
    if parsed_actual.scheme != scheme or parsed_actual.netloc != netloc:
        return None

    actual_segments = parsed_actual.path.split("/")
    if len(actual_segments) != len(template_segments):
        return None

    for index, (actual_segment, template_segment) in enumerate(
        zip(actual_segments, template_segments, strict=True)
    ):
        if index == placeholder_index:
            if not actual_segment:
                return None
        elif actual_segment != template_segment:
            return None

    return actual_segments[placeholder_index]


def _build_path_passkey_url(
    actual_url: str,
    placeholder_index: int,
    new_passkey: str,
) -> str:
    """Return a tracker URL with its passkey path segment replaced."""
    parsed_actual = urlsplit(actual_url)
    actual_segments = parsed_actual.path.split("/")
    actual_segments[placeholder_index] = new_passkey

    return urlunsplit(
        (
            parsed_actual.scheme,
            parsed_actual.netloc,
            "/".join(actual_segments),
            parsed_actual.query,
            parsed_actual.fragment,
        )
    )


def replace_tracker_passkey(
    client: Any,
    tracker_template: str,
    new_passkey: str,
    dry_run: bool = True,
    verbose: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Replace a tracker's passkey on every torrent using that tracker.

    The tracker template locates the passkey with a literal
    '{passkey}' placeholder, either as a query parameter value
    (e.g. '?passkey={passkey}') or as a full path segment
    (e.g. '/announce/{passkey}'). Torrents are matched on the
    tracker's fixed host/path shape, so the caller does not need to
    know each torrent's current passkey value.
    """
    scheme, netloc, mode, position = _parse_passkey_template(tracker_template)
    template_path_segments = (
        urlsplit(tracker_template.strip()).path.split("/")
        if mode == "path"
        else []
    )
    query_base_url = (
        urlunsplit(
            (scheme, netloc, urlsplit(tracker_template.strip()).path, "", "")
        )
        if mode == "query"
        else ""
    )

    all_torrents = list(client.torrents_info())
    total = len(all_torrents)
    scanned = 0
    matched_source = 0
    already_had_target = 0
    modified = 0
    replaced_urls = 0
    removed_urls = 0
    details: list[dict[str, Any]] = []

    for torrent in all_torrents:
        scanned += 1
        if on_progress is not None:
            on_progress(scanned, total)

        torrent_hash = _get_torrent_hash(torrent)
        torrent_name = _get_torrent_name(torrent)
        trackers = _get_active_tracker_urls(
            client.torrents_trackers(torrent_hash)
        )

        if mode == "query":
            assert isinstance(position, str)
            matching_urls = _get_matching_tracker_urls(
                trackers, query_base_url, "without-query"
            )
            stale_urls = {
                matching_url: updated_url
                for matching_url in matching_urls
                if (
                    updated_url := _build_query_passkey_url(
                        matching_url, position, new_passkey
                    )
                )
                != matching_url
            }
        else:
            assert isinstance(position, int)
            matching_urls = []
            stale_urls = {}
            for tracker_entry_url in trackers:
                current_passkey = _match_path_passkey(
                    tracker_entry_url,
                    scheme,
                    netloc,
                    template_path_segments,
                    position,
                )
                if current_passkey is None:
                    continue

                matching_urls.append(tracker_entry_url)
                if current_passkey == new_passkey:
                    continue

                stale_urls[tracker_entry_url] = _build_path_passkey_url(
                    tracker_entry_url,
                    position,
                    new_passkey,
                )

        if not matching_urls:
            continue

        matched_source += 1

        if not stale_urls:
            already_had_target += 1
            if verbose:
                details.append(
                    {
                        "hash": torrent_hash,
                        "name": torrent_name,
                        "action": "already_up_to_date",
                        "matching_tracker_urls": matching_urls,
                    }
                )
            continue

        log_prefix = "Would update" if dry_run else "Updating"
        logger.info("%s passkey on: %s", log_prefix, torrent_name)

        if not dry_run:
            try:
                for source_url, target_url in stale_urls.items():
                    client.torrents_edit_tracker(
                        torrent_hash=torrent_hash,
                        original_url=source_url,
                        new_url=target_url,
                    )
            except Exception as error:
                raise RuntimeError(
                    "Failed to update tracker passkey on torrent "
                    f"'{torrent_name}' ({torrent_hash}): {error}"
                ) from error

        replaced_urls += len(stale_urls)
        modified += 1

        if verbose:
            details.append(
                {
                    "hash": torrent_hash,
                    "name": torrent_name,
                    "action": "would_replace" if dry_run else "replaced",
                    "replaced_tracker_urls": stale_urls,
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
