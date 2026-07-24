"""Manage qBittorrent trackers."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TrackerMatchMode = Literal["exact", "without-query"]


@dataclass(frozen=True)
class TrackerAdditionChange:
    """One torrent that will gain the target tracker."""

    hash: str
    name: str


@dataclass(frozen=True)
class TrackerAdditionPlan:
    """Plan for `add_tracker_if_source_present`.

    `changes` and `already_had_target` are collected unconditionally so
    the CLI can render a full confirmation preview regardless of
    `--verbose`.
    """

    source_tracker: str
    target_tracker: str
    match: TrackerMatchMode
    scanned: int
    matched_source: int
    already_had_target: tuple[TrackerAdditionChange, ...]
    changes: tuple[TrackerAdditionChange, ...]


@dataclass(frozen=True)
class TrackerRemovalChange:
    """One torrent that will have matching tracker URLs removed."""

    hash: str
    name: str
    urls: tuple[str, ...]


@dataclass(frozen=True)
class TrackerRemovalPlan:
    """Plan for `remove_tracker_from_all`."""

    tracker: str
    match: TrackerMatchMode
    scanned: int
    matched_tracker: int
    changes: tuple[TrackerRemovalChange, ...]

    @property
    def removed_url_count(self) -> int:
        """Total tracker URLs that would be/were removed across all torrents."""
        return sum(len(change.urls) for change in self.changes)


@dataclass(frozen=True)
class TrackerReplacementChange:
    """One torrent's replace/remove operation for a tracker replacement."""

    hash: str
    name: str
    replace_url: str | None
    remove_urls: tuple[str, ...]
    already_had_target: bool


@dataclass(frozen=True)
class TrackerReplacementPlan:
    """Plan for `replace_tracker_in_all`."""

    source_tracker: str
    target_tracker: str
    match: TrackerMatchMode
    scanned: int
    matched_source: int
    changes: tuple[TrackerReplacementChange, ...]

    @property
    def replaced_url_count(self) -> int:
        """Torrents that get an edited (not just removed) tracker URL."""
        return sum(
            1 for change in self.changes if change.replace_url is not None
        )

    @property
    def removed_url_count(self) -> int:
        """Total duplicate/source tracker URLs removed across all torrents."""
        return sum(len(change.remove_urls) for change in self.changes)


@dataclass(frozen=True)
class PasskeyReplacementChange:
    """One torrent's passkey update.

    `stale_urls` (old URL -> new URL, with the new passkey embedded) is
    required by `apply_tracker_passkey_replacement` but must never be
    rendered: `repr=False` keeps it out of default dataclass repr/logging,
    and callers must only use `stale_url_count` for any user-facing
    preview or summary.
    """

    hash: str
    name: str
    stale_url_count: int
    stale_urls: tuple[tuple[str, str], ...] = field(repr=False)


@dataclass(frozen=True)
class PasskeyReplacementPlan:
    """Plan for `replace_tracker_passkey`.

    Never carries the raw new passkey or new URLs outside of each
    change's `stale_urls` (see `PasskeyReplacementChange`); the tracker
    template itself does not contain the passkey value.
    """

    tracker_template: str
    scanned: int
    matched_source: int
    already_up_to_date: int
    changes: tuple[PasskeyReplacementChange, ...]

    @property
    def replaced_url_count(self) -> int:
        """Total tracker URLs whose passkey would be/was updated."""
        return sum(change.stale_url_count for change in self.changes)


def redact_tracker_identity(url: str) -> str:
    """Return a tracker URL reduced to scheme and host for safe display.

    Private trackers commonly embed a passkey or other per-user secret
    in the path (e.g. `/announce/<passkey>`) or the query string (e.g.
    `?passkey=<value>`). Guessing which path segment is secret is
    unreliable, so any tracker identity shown in a confirmation prompt
    or preview is reduced to scheme + host only; the raw URL is still
    used for the actual API calls.
    """
    parsed = urlsplit(url.strip())
    return f"{parsed.scheme}://{parsed.netloc}"


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
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    """List normalized trackers and count torrents using each one.

    Calls `client.torrents_trackers()` once per torrent, so
    `on_progress(completed, total)` reports real, known progress through
    that per-torrent work.
    """
    all_torrents = list(client.torrents_info())
    total = len(all_torrents)
    tracker_usage: dict[str, int] = {}

    for index, torrent in enumerate(all_torrents, start=1):
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

        if on_progress is not None:
            on_progress(index, total)

    return dict(sorted(tracker_usage.items()))


def inspect_tracker(
    client: Any,
    tracker: str,
    match_mode: TrackerMatchMode = "exact",
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """List torrents using a tracker.

    Calls `client.torrents_trackers()` once per scanned torrent, so
    `on_progress(completed, total)` reports real, known progress through
    that per-torrent work.
    """
    all_torrents = list(client.torrents_info())
    total = len(all_torrents)
    torrents: list[dict[str, Any]] = []

    for index, torrent in enumerate(all_torrents, start=1):
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

        if matching_tracker_urls:
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

        if on_progress is not None:
            on_progress(index, total)

    return {
        "tracker": tracker,
        "match": match_mode,
        "scanned": total,
        "matched_tracker": len(torrents),
        "torrents": torrents,
    }


def export_tracker_state(
    client: Any,
    match_mode: TrackerMatchMode = "exact",
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Export active tracker state for every torrent.

    Calls `client.torrents_trackers()` once per torrent, so
    `on_progress(completed, total)` reports real, known progress through
    that per-torrent work.
    """
    all_torrents = list(client.torrents_info())
    total = len(all_torrents)
    torrents: list[dict[str, Any]] = []

    for index, torrent in enumerate(all_torrents, start=1):
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

        if on_progress is not None:
            on_progress(index, total)

    return {
        "summary": {
            "torrents": len(torrents),
            "match": match_mode,
        },
        "torrents": torrents,
    }


def analyze_tracker_health(
    client: Any,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Analyze tracker health across all torrents.

    Calls `client.torrents_trackers()` once per torrent, so
    `on_progress(completed, total)` reports real, known progress through
    that per-torrent work.
    """
    all_torrents = list(client.torrents_info())
    total = len(all_torrents)
    active_tracker_occurrences = 0
    disabled_tracker_occurrences = 0
    exact_trackers: set[str] = set()
    logical_trackers: set[str] = set()
    disabled_trackers: set[str] = set()
    query_variants: dict[str, dict[str, set[str]]] = {}

    for index, torrent in enumerate(all_torrents, start=1):
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

        if on_progress is not None:
            on_progress(index, total)

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
            "scanned": total,
            "active_tracker_occurrences": active_tracker_occurrences,
            "disabled_tracker_occurrences": disabled_tracker_occurrences,
            "unique_exact_trackers": len(exact_trackers),
            "unique_logical_trackers": len(logical_trackers),
            "query_variant_groups": len(query_variant_groups),
        },
        "disabled_trackers": sorted(disabled_trackers),
        "query_variant_groups": query_variant_groups,
    }


def plan_tracker_addition(
    client: Any,
    source_tracker: str,
    target_tracker: str,
    match_mode: TrackerMatchMode = "exact",
    on_progress: Callable[[int, int], None] | None = None,
) -> TrackerAdditionPlan:
    """Plan adding a target tracker to torrents already using the source."""
    all_torrents = list(client.torrents_info())
    total = len(all_torrents)
    scanned = 0
    already_had_target: list[TrackerAdditionChange] = []
    changes: list[TrackerAdditionChange] = []

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

        if has_tracker(trackers, target_tracker, match_mode):
            already_had_target.append(
                TrackerAdditionChange(hash=torrent_hash, name=torrent_name)
            )
            continue

        changes.append(
            TrackerAdditionChange(hash=torrent_hash, name=torrent_name)
        )

    return TrackerAdditionPlan(
        source_tracker=source_tracker,
        target_tracker=target_tracker,
        match=match_mode,
        scanned=scanned,
        matched_source=len(already_had_target) + len(changes),
        already_had_target=tuple(already_had_target),
        changes=tuple(changes),
    )


def apply_tracker_addition(client: Any, plan: TrackerAdditionPlan) -> None:
    """Apply a previously built plan. Mutates exactly `plan.changes`."""
    for change in plan.changes:
        try:
            client.torrents_add_trackers(
                torrent_hash=change.hash,
                urls=plan.target_tracker,
            )
        except Exception as error:
            raise RuntimeError(
                f"Failed to add tracker to torrent '{change.name}' "
                f"({change.hash}): {error}"
            ) from error


def plan_tracker_removal(
    client: Any,
    tracker: str,
    match_mode: TrackerMatchMode = "exact",
    on_progress: Callable[[int, int], None] | None = None,
) -> TrackerRemovalPlan:
    """Plan removing a tracker from every torrent using it."""
    all_torrents = list(client.torrents_info())
    total = len(all_torrents)
    scanned = 0
    changes: list[TrackerRemovalChange] = []

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

        changes.append(
            TrackerRemovalChange(
                hash=torrent_hash,
                name=torrent_name,
                urls=tuple(matching_tracker_urls),
            )
        )

    return TrackerRemovalPlan(
        tracker=tracker,
        match=match_mode,
        scanned=scanned,
        matched_tracker=len(changes),
        changes=tuple(changes),
    )


def apply_tracker_removal(client: Any, plan: TrackerRemovalPlan) -> None:
    """Apply a previously built plan. Mutates exactly `plan.changes`."""
    for change in plan.changes:
        try:
            client.torrents_remove_trackers(
                torrent_hash=change.hash,
                urls=list(change.urls),
            )
        except Exception as error:
            raise RuntimeError(
                f"Failed to remove tracker from torrent '{change.name}' "
                f"({change.hash}): {error}"
            ) from error


def plan_tracker_replacement(
    client: Any,
    source_tracker: str,
    target_tracker: str,
    match_mode: TrackerMatchMode = "exact",
    on_progress: Callable[[int, int], None] | None = None,
) -> TrackerReplacementPlan:
    """Plan replacing a source tracker with a target on matching torrents."""
    _ensure_distinct_tracker_identity(
        source_tracker,
        target_tracker,
        match_mode,
    )

    all_torrents = list(client.torrents_info())
    total = len(all_torrents)
    scanned = 0
    changes: list[TrackerReplacementChange] = []

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

        target_already_present = has_tracker(
            trackers,
            target_tracker,
            match_mode,
        )

        if target_already_present:
            changes.append(
                TrackerReplacementChange(
                    hash=torrent_hash,
                    name=torrent_name,
                    replace_url=None,
                    remove_urls=tuple(matching_source_urls),
                    already_had_target=True,
                )
            )
        else:
            changes.append(
                TrackerReplacementChange(
                    hash=torrent_hash,
                    name=torrent_name,
                    replace_url=matching_source_urls[0],
                    remove_urls=tuple(matching_source_urls[1:]),
                    already_had_target=False,
                )
            )

    return TrackerReplacementPlan(
        source_tracker=source_tracker,
        target_tracker=target_tracker,
        match=match_mode,
        scanned=scanned,
        matched_source=len(changes),
        changes=tuple(changes),
    )


def apply_tracker_replacement(
    client: Any, plan: TrackerReplacementPlan
) -> None:
    """Apply a previously built plan. Mutates exactly `plan.changes`."""
    for change in plan.changes:
        try:
            if change.replace_url is not None:
                client.torrents_edit_tracker(
                    torrent_hash=change.hash,
                    original_url=change.replace_url,
                    new_url=plan.target_tracker,
                )

            if change.remove_urls:
                client.torrents_remove_trackers(
                    torrent_hash=change.hash,
                    urls=list(change.remove_urls),
                )
        except Exception as error:
            raise RuntimeError(
                f"Failed to replace tracker on torrent '{change.name}' "
                f"({change.hash}): {error}"
            ) from error


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


def plan_tracker_passkey_replacement(
    client: Any,
    tracker_template: str,
    new_passkey: str,
    on_progress: Callable[[int, int], None] | None = None,
) -> PasskeyReplacementPlan:
    """Plan replacing a tracker's passkey on every torrent using it.

    The tracker template locates the passkey with a literal
    '{passkey}' placeholder, either as a query parameter value
    (e.g. '?passkey={passkey}') or as a full path segment
    (e.g. '/announce/{passkey}'). Torrents are matched on the
    tracker's fixed host/path shape, so the caller does not need to
    know each torrent's current passkey value.

    Neither `tracker_template` nor the returned plan's summary fields
    ever carry `new_passkey`; it only ever appears inside each change's
    `stale_urls`, which callers must never render (see
    `PasskeyReplacementChange`).
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
    already_up_to_date = 0
    changes: list[PasskeyReplacementChange] = []

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
            already_up_to_date += 1
            continue

        changes.append(
            PasskeyReplacementChange(
                hash=torrent_hash,
                name=torrent_name,
                stale_url_count=len(stale_urls),
                stale_urls=tuple(stale_urls.items()),
            )
        )

    return PasskeyReplacementPlan(
        tracker_template=tracker_template,
        scanned=scanned,
        matched_source=matched_source,
        already_up_to_date=already_up_to_date,
        changes=tuple(changes),
    )


def apply_tracker_passkey_replacement(
    client: Any,
    plan: PasskeyReplacementPlan,
) -> None:
    """Apply a previously built plan. Mutates exactly `plan.changes`."""
    for change in plan.changes:
        try:
            for source_url, target_url in change.stale_urls:
                client.torrents_edit_tracker(
                    torrent_hash=change.hash,
                    original_url=source_url,
                    new_url=target_url,
                )
        except Exception as error:
            raise RuntimeError(
                "Failed to update tracker passkey on torrent "
                f"'{change.name}' ({change.hash}): {error}"
            ) from error


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
