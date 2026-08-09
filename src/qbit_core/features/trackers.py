"""Manage qBittorrent trackers.

Every tracker-facing surface must route through the sanitization
primitives defined here (`redact_tracker_identity`,
`normalize_tracker_host`, `describe_tracker_url`,
`sanitize_tracker_text`) before rendering anything derived from a
tracker announce URL or tracker-provided message. Sanitization is
always structural (strip anything shaped like a credential-bearing
URL), never specific to a known password or passkey value.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from qbit_core.qbit.fields import (
    get_field_as_string,
    get_raw_tracker_status,
)
from qbit_core.shared.inspection import Inspection, select_and_inspect
from qbit_core.shared.selection import SelectionRequest

TrackerMatchMode = Literal["exact", "without-query"]

# Matches a scheme-prefixed URL (`https://...`, `udp://...`), the primary
# shape a raw announce URL takes inside an otherwise-safe message.
_URL_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://\S+")

# Matches the same shape percent-encoded, as an HTTP client's exception
# text can echo a request URL that itself embeds a tracker announce URL.
_PERCENT_ENCODED_URL_PATTERN = re.compile(r"[a-zA-Z0-9]*%3[Aa]%2[Ff]%2[Ff]\S+")

# Matches a bare `userinfo@host[:port]` fragment with no scheme prefix,
# narrower than a generic `\S+:\S+@\S+` so it doesn't fire on ordinary
# prose containing a colon and an "@".
_BARE_USERINFO_PATTERN = re.compile(
    r"\b[\w.%+-]+:[\w.%+-]+@[\w.-]+(?::\d+)?\S*"
)


def sanitize_tracker_text(text: str) -> str:
    """Strip URLs, percent-encoded URLs, and bare userinfo from free text.

    Purely structural -- recognizes URL/userinfo shape, never a
    specific password or passkey value.
    """
    sanitized = _URL_PATTERN.sub("<redacted-url>", text)
    sanitized = _PERCENT_ENCODED_URL_PATTERN.sub("<redacted-url>", sanitized)
    sanitized = _BARE_USERINFO_PATTERN.sub("<redacted-userinfo>", sanitized)
    return sanitized.strip()


@dataclass(frozen=True)
class SafeTrackerIdentity:
    """A tracker announce URL reduced to secret-free structural fields.

    `path_shape` never reveals path content: every non-empty segment
    becomes a uniform `<secret>` placeholder, since qbit-ops cannot
    reliably distinguish a fixed path element from a secret one.
    `query_keys` carries parameter names only, never values.
    """

    identity: str
    scheme: str | None
    host: str | None
    port: int | None
    path_shape: str | None
    query_keys: tuple[str, ...]


_PSEUDO_TRACKER_PATTERN = re.compile(r"^\*\*\s*\[(.+?)\]\s*\*\*$")


def is_pseudo_tracker_marker(url: str) -> bool:
    """Return whether `url` is a DHT/PeX/LSD pseudo-tracker marker.

    These are not real announce URLs -- never a target for a tracker
    mutation (add/remove/replace/restore).
    """
    return _PSEUDO_TRACKER_PATTERN.match(url.strip()) is not None


def describe_tracker_url(url: str) -> SafeTrackerIdentity:
    """Reduce a tracker announce URL to a `SafeTrackerIdentity`.

    Handles qBittorrent's DHT/PeX/LSD pseudo-tracker markers (e.g.
    `"** [DHT] **"`) specially. A malformed or unparsable value degrades
    to `identity="unknown"` with every other field `None`/empty, rather
    than raising.
    """
    stripped = url.strip()

    pseudo_match = _PSEUDO_TRACKER_PATTERN.match(stripped)
    if pseudo_match is not None:
        return SafeTrackerIdentity(
            identity=pseudo_match.group(1).strip(),
            scheme=None,
            host=None,
            port=None,
            path_shape=None,
            query_keys=(),
        )

    try:
        identity = normalize_tracker_host(stripped)
    except ValueError:
        return SafeTrackerIdentity(
            identity="unknown",
            scheme=None,
            host=None,
            port=None,
            path_shape=None,
            query_keys=(),
        )

    parseable = stripped if "://" in stripped else f"//{stripped}"
    try:
        parsed = urlsplit(parseable)
    except ValueError:
        return SafeTrackerIdentity(
            identity=identity,
            scheme=None,
            host=None,
            port=None,
            path_shape=None,
            query_keys=(),
        )

    segments = [segment for segment in parsed.path.split("/") if segment]
    path_shape = (
        "/" + "/".join("<secret>" for _ in segments) if segments else None
    )
    query_keys = tuple(
        sorted(
            {
                key
                for key, _value in parse_qsl(
                    parsed.query, keep_blank_values=True
                )
            }
        )
    )

    return SafeTrackerIdentity(
        identity=identity,
        scheme=parsed.scheme or None,
        host=(parsed.hostname or "").lower() or None,
        port=parsed.port,
        path_shape=path_shape,
        query_keys=query_keys,
    )


class TrackerHealth(StrEnum):
    """Classify the health of one tracker endpoint or aggregate."""

    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    DISABLED = "disabled"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


# Maps qBittorrent's `TrackerStatus` int codes to a `TrackerHealth`.
# UPDATING -> HEALTHY (not WARNING): an announce in flight is transient
# and a healthy instance can be caught mid-announce at any moment.
# NOT_CONTACTED -> WARNING (not UNKNOWN): a recognized, expected state.
_RAW_STATUS_HEALTH: dict[int, TrackerHealth] = {
    0: TrackerHealth.DISABLED,  # DISABLED
    1: TrackerHealth.WARNING,  # NOT_CONTACTED
    2: TrackerHealth.HEALTHY,  # WORKING
    3: TrackerHealth.HEALTHY,  # UPDATING
    4: TrackerHealth.CRITICAL,  # NOT_WORKING
    5: TrackerHealth.CRITICAL,  # TRACKER_ERROR
    6: TrackerHealth.CRITICAL,  # UNREACHABLE
}


def classify_raw_tracker_status(
    raw_status: int | str | None,
) -> tuple[TrackerHealth, bool | None]:
    """Map one raw qBittorrent tracker status to `(health, enabled)`.

    `enabled` is `None` when the raw value could not be classified at
    all -- never guessed. Unrecognized values map to
    `TrackerHealth.UNKNOWN` rather than inventing a severity.
    """
    if isinstance(raw_status, str) and raw_status.strip().lower() == "disabled":
        return TrackerHealth.DISABLED, False

    try:
        code = int(raw_status)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return TrackerHealth.UNKNOWN, None

    health = _RAW_STATUS_HEALTH.get(code, TrackerHealth.UNKNOWN)
    if health is TrackerHealth.UNKNOWN:
        return health, None

    return health, health is not TrackerHealth.DISABLED


def compute_tracker_aggregate_health(
    *,
    healthy: int,
    warning: int,
    critical: int,
    disabled: int,
    unknown: int,
) -> TrackerHealth:
    """Compute one tracker identity's aggregate health from endpoint counts.

    Disabled endpoints are excluded from every comparison, so an
    intentionally-disabled tracker never pushes a working tracker's
    health toward WARNING/CRITICAL. All-CRITICAL -> CRITICAL; a mixed
    critical/healthy set is WARNING, not CRITICAL, so one failing
    endpoint never drowns out others that are working.
    """
    enabled_total = healthy + warning + critical + unknown
    if enabled_total == 0:
        return TrackerHealth.DISABLED
    if critical == enabled_total:
        return TrackerHealth.CRITICAL
    if healthy == enabled_total:
        return TrackerHealth.HEALTHY
    if unknown == enabled_total:
        return TrackerHealth.UNKNOWN
    return TrackerHealth.WARNING


@dataclass(frozen=True)
class TrackerAdditionChange:
    """One torrent that will gain the target tracker."""

    hash: str
    name: str


@dataclass(frozen=True)
class TrackerAdditionPlan:
    """Plan for `add_tracker_if_source_present`.

    `scanned` counts every torrent in the instance, `matched` those the
    torrent filters kept, and `matched_source` those among them actually
    using the source tracker. Without a filter, `matched == scanned`.
    """

    source_tracker: str
    target_tracker: str
    match: TrackerMatchMode
    scanned: int
    matched: int
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

    `stale_urls` embeds the new passkey and must never be rendered:
    `repr=False` keeps it out of default dataclass repr/logging.
    Callers must use `stale_url_count` for any user-facing preview.
    """

    hash: str
    name: str
    stale_url_count: int
    stale_urls: tuple[tuple[str, str], ...] = field(repr=False)


@dataclass(frozen=True)
class PasskeyReplacementPlan:
    """Plan for `replace_tracker_passkey`.

    Never carries the raw new passkey or new URLs outside of each
    change's `stale_urls` (see `PasskeyReplacementChange`).
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
    """Return a tracker URL reduced to scheme and host[:port] for safe display.

    Private trackers commonly embed a passkey in the path or query
    string, and guessing which segment is secret is unreliable, so any
    identity shown in a prompt or preview is reduced to scheme +
    host[:port]. Rebuilds from `.hostname`/`.port` rather than
    `.netloc`, which would include userinfo (`user:pass@host`).
    """
    parsed = urlsplit(url.strip())
    host = (parsed.hostname or "").lower()
    netloc = f"{host}:{parsed.port}" if parsed.port is not None else host
    if parsed.scheme:
        return f"{parsed.scheme}://{netloc}"
    return netloc


def normalize_tracker_host(value: str) -> str:
    """Normalize a tracker filter value to a bare `host` or `host:port`.

    Accepts either a bare host or a full announce URL and extracts only
    the host and port; scheme, path, and query string (where a passkey
    would live) are always discarded. Does not collapse a scheme's
    default port (`https://host:443` stays `host:443`, distinct from
    `host`). Does not accept qBittorrent's DHT/PeX/LSD pseudo-tracker
    markers (e.g. `"** [DHT] **"`) -- callers must filter those out first.
    """
    stripped = value.strip()
    parseable = stripped if "://" in stripped else f"//{stripped}"
    parsed = urlsplit(parseable)
    host = (parsed.hostname or "").lower()

    if host == "":
        # Not parseable as a netloc at all (e.g. a bare word with no dot
        # and no port) -- fall back to the raw value with any path
        # dropped, so a plain hostname-looking string still matches.
        return stripped.split("/", 1)[0].lower()

    if parsed.port is not None:
        return f"{host}:{parsed.port}"

    return host


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


def has_tracker_host(tracker_urls: list[str], normalized_host: str) -> bool:
    """Return whether any tracker URL's host[:port] matches a normalized host.

    `normalized_host` must already be `normalize_tracker_host`'s output.
    """
    return any(
        normalize_tracker_host(url) == normalized_host for url in tracker_urls
    )


def list_tracker_usage(
    client: Any,
    match_mode: TrackerMatchMode = "exact",
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    """List normalized-but-raw trackers and count torrents using each one.

    Deliberately not safe for display: with `match_mode="exact"` a
    result key can still carry a passkey. Exists only to back `backup
    export`'s `tracker_usage` field; no CLI command renders this
    directly.
    """
    inspection = select_and_inspect(
        client, SelectionRequest(), on_progress=on_progress
    )
    tracker_usage: dict[str, int] = {}

    for inspected in inspection.torrents:
        trackers = list(inspected.active_tracker_urls)
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
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """List torrents using a tracker, matched by normalized host[:port].

    Every endpoint reported is reduced to secret-free structural fields
    (`SafeTrackerIdentity`, `TrackerHealth`) -- a full announce URL
    never reaches the returned data. Considers every tracker entry,
    including disabled ones.
    """
    normalized_identity = describe_tracker_url(tracker).identity
    inspection = select_and_inspect(
        client, SelectionRequest(), on_progress=on_progress
    )
    torrents: list[dict[str, Any]] = []

    for inspected in inspection.torrents:
        torrent = inspected.snapshot
        raw_trackers = list(inspected.raw_trackers)
        active_tracker_count = inspected.tracker_count

        matching_endpoints: list[dict[str, Any]] = []
        for raw_tracker in raw_trackers:
            url = get_field_as_string(raw_tracker, "url")
            if url == "":
                continue

            safe_identity = describe_tracker_url(url)
            if safe_identity.identity != normalized_identity:
                continue

            raw_status = get_raw_tracker_status(raw_tracker)
            health, enabled = classify_raw_tracker_status(raw_status)
            raw_message = get_field_as_string(raw_tracker, "msg")
            message = (
                sanitize_tracker_text(raw_message) if raw_message else None
            )

            matching_endpoints.append(
                {
                    "raw_status": raw_status,
                    "health": health.value,
                    "enabled": enabled,
                    "message": message,
                    "scheme": safe_identity.scheme,
                    "path_shape": safe_identity.path_shape,
                    "query_keys": list(safe_identity.query_keys),
                }
            )

        if matching_endpoints:
            torrents.append(
                {
                    "hash": torrent.hash,
                    "name": torrent.name,
                    "state": torrent.state,
                    "size": torrent.size,
                    "progress": torrent.progress,
                    "ratio": torrent.ratio,
                    "active_tracker_count": active_tracker_count,
                    "matching_endpoints": matching_endpoints,
                }
            )

    return {
        "tracker": normalized_identity,
        "scanned": inspection.selection.scanned,
        "matched_tracker": len(torrents),
        "torrents": torrents,
    }


def export_tracker_state(
    client: Any,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Export normalized tracker identities for every torrent.

    Safe by default: trackers are reduced to `host[:port]` identities,
    never a full announce URL or passkey. Distinct from `backup export`,
    which legitimately carries raw announce URLs.
    """
    inspection = select_and_inspect(
        client, SelectionRequest(), on_progress=on_progress
    )
    torrents: list[dict[str, Any]] = []
    all_identities: set[str] = set()

    for inspected in inspection.torrents:
        trackers = list(inspected.active_tracker_urls)
        normalized_trackers = sorted(
            {
                safe.identity
                for tracker_url in trackers
                if (safe := describe_tracker_url(tracker_url)).host is not None
            }
        )
        all_identities.update(normalized_trackers)
        torrents.append(
            {
                "hash": inspected.snapshot.hash,
                "name": inspected.snapshot.name,
                "normalized_trackers": normalized_trackers,
            }
        )

    return {
        "summary": {
            "torrents": len(torrents),
            "unique_trackers": len(all_identities),
        },
        "torrents": torrents,
    }


def plan_tracker_addition(
    inspection: Inspection,
    source_tracker: str,
    target_tracker: str,
    match_mode: TrackerMatchMode = "exact",
) -> TrackerAdditionPlan:
    """Plan adding a target tracker to torrents already using the source.

    Pure: consumes an already-collected `Inspection` and makes no
    qBittorrent call, so planning is testable without a client and the
    scan it plans from can be scoped by any `SelectionRequest`.
    """
    already_had_target: list[TrackerAdditionChange] = []
    changes: list[TrackerAdditionChange] = []

    for inspected in inspection.torrents:
        trackers = list(inspected.active_tracker_urls)

        if not has_tracker(trackers, source_tracker, match_mode):
            continue

        change = TrackerAdditionChange(
            hash=inspected.snapshot.hash, name=inspected.snapshot.name
        )
        if has_tracker(trackers, target_tracker, match_mode):
            already_had_target.append(change)
            continue

        changes.append(change)

    return TrackerAdditionPlan(
        source_tracker=source_tracker,
        target_tracker=target_tracker,
        match=match_mode,
        scanned=inspection.selection.scanned,
        matched=len(inspection.torrents),
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
                f"({change.hash}): {sanitize_tracker_text(str(error))}"
            ) from error


def plan_tracker_removal(
    inspection: Inspection,
    tracker: str,
    match_mode: TrackerMatchMode = "exact",
) -> TrackerRemovalPlan:
    """Plan removing a tracker from every torrent using it.

    Pure: consumes an already-collected `Inspection` and makes no
    qBittorrent call.
    """
    changes: list[TrackerRemovalChange] = []

    for inspected in inspection.torrents:
        matching_tracker_urls = _get_matching_tracker_urls(
            list(inspected.active_tracker_urls),
            tracker,
            match_mode,
        )

        if not matching_tracker_urls:
            continue

        changes.append(
            TrackerRemovalChange(
                hash=inspected.snapshot.hash,
                name=inspected.snapshot.name,
                urls=tuple(matching_tracker_urls),
            )
        )

    return TrackerRemovalPlan(
        tracker=tracker,
        match=match_mode,
        scanned=inspection.selection.scanned,
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
                f"({change.hash}): {sanitize_tracker_text(str(error))}"
            ) from error


def plan_tracker_replacement(
    inspection: Inspection,
    source_tracker: str,
    target_tracker: str,
    match_mode: TrackerMatchMode = "exact",
) -> TrackerReplacementPlan:
    """Plan replacing a source tracker with a target on matching torrents.

    Pure: consumes an already-collected `Inspection` and makes no
    qBittorrent call.
    """
    _ensure_distinct_tracker_identity(
        source_tracker,
        target_tracker,
        match_mode,
    )

    changes: list[TrackerReplacementChange] = []

    for inspected in inspection.torrents:
        torrent_hash = inspected.snapshot.hash
        torrent_name = inspected.snapshot.name
        trackers = list(inspected.active_tracker_urls)
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
        scanned=inspection.selection.scanned,
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
                f"({change.hash}): {sanitize_tracker_text(str(error))}"
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
    inspection: Inspection,
    tracker_template: str,
    new_passkey: str,
) -> PasskeyReplacementPlan:
    """Plan replacing a tracker's passkey on every torrent using it.

    Pure: consumes an already-collected `Inspection` and makes no
    qBittorrent call.

    The tracker template locates the passkey with a literal
    '{passkey}' placeholder, either as a query parameter value or a
    full path segment. Neither `tracker_template` nor the plan's
    summary fields carry `new_passkey`; it only appears inside each
    change's `stale_urls` (see `PasskeyReplacementChange`).
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

    matched_source = 0
    already_up_to_date = 0
    changes: list[PasskeyReplacementChange] = []

    for inspected in inspection.torrents:
        torrent_hash = inspected.snapshot.hash
        torrent_name = inspected.snapshot.name
        trackers = list(inspected.active_tracker_urls)

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
        scanned=inspection.selection.scanned,
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
                f"'{change.name}' ({change.hash}): "
                f"{sanitize_tracker_text(str(error))}"
            ) from error


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
    torrent_hash = get_field_as_string(torrent, "hash")
    if torrent_hash == "":
        raise RuntimeError("Unable to read torrent hash from qBittorrent data.")

    return torrent_hash


def _get_torrent_name(torrent: Any) -> str:
    """Extract the torrent name from a qBittorrent torrent object."""
    torrent_name = get_field_as_string(torrent, "name")
    if torrent_name == "":
        return _get_torrent_hash(torrent)

    return torrent_name
