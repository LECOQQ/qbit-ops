"""List and select qBittorrent torrents.

Turns the pure SELECT stage (`qbit_core.shared.selection`) into
client-facing use cases: fetching torrents, resolving a
`SelectionRequest` against them, and planning/applying bulk actions.
Every result carries `TorrentSnapshot` directly -- display concerns
such as the `(uncategorized)` label belong to the rendering layers.
Kept free of Typer and Rich so both the CLI and the TUI can reuse it
without pulling in presentation concerns.
"""

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Any, Literal

from qbit_core.features.trackers import (
    classify_raw_tracker_status,
    describe_tracker_url,
    has_tracker_host,
    normalize_tracker_host,
    sanitize_tracker_text,
)
from qbit_core.qbit.fields import (
    get_field_as_float,
    get_field_as_int,
    get_field_as_string,
    get_field_as_tag_list,
    get_raw_tracker_status,
    is_disabled_tracker,
)
from qbit_core.shared.inspection import inspect_trackers
from qbit_core.shared.selection import (
    EMPTY_TORRENT_FILTER,
    STATE_FILTER_VALUES,
    InvalidTorrentSelectorError,
    Selection,
    SelectionRequest,
    TorrentFilter,
    TorrentNotFoundError,
    format_category_label,
    resolve_torrent_hash,
    select_from_items,
    validate_selection_request,
)
from qbit_core.shared.torrent_states import (
    TorrentSnapshot,
    TorrentStateGroup,
    build_torrent_snapshot,
    is_stopped_state,
)

TorrentBulkAction = Literal["pause", "resume", "start", "reannounce", "delete"]


def build_torrent_filter(
    *,
    categories: Sequence[str] = (),
    states: Sequence[str] = (),
    tracker: str | None = None,
    completed: bool = False,
    incomplete: bool = False,
    active: bool = False,
    inactive: bool = False,
    stalled: bool = False,
    errored: bool = False,
) -> TorrentFilter:
    """Validate and build a `TorrentFilter` from raw CLI-style inputs.

    Rejects the two locally-provable contradictions (`--completed
    --incomplete`, `--active --inactive`) and any unrecognized `--state`
    value before any qBittorrent API call. Every other combination is
    accepted and combines with AND, even where it can never match
    anything -- not every zero-match combination is a contradiction.
    """
    if completed and incomplete:
        raise ValueError("Use --completed or --incomplete, not both.")
    if active and inactive:
        raise ValueError("Use --active or --inactive, not both.")

    normalized_categories = tuple(
        dict.fromkeys(
            category.strip()
            for category in categories
            if category.strip() != ""
        )
    )

    normalized_states: list[TorrentStateGroup] = []
    for state in states:
        normalized_state = state.strip().lower()
        if normalized_state not in STATE_FILTER_VALUES:
            supported = ", ".join(sorted(STATE_FILTER_VALUES))
            raise ValueError(
                f"Unknown --state value '{state}'. Supported values: "
                f"{supported}."
            )
        if normalized_state not in normalized_states:
            normalized_states.append(normalized_state)  # type: ignore[arg-type]

    normalized_tracker: str | None = None
    if tracker is not None:
        normalized_tracker = normalize_tracker_host(tracker)
        if normalized_tracker == "":
            raise ValueError("--tracker must not be empty or whitespace-only.")

    return TorrentFilter(
        categories=normalized_categories,
        states=tuple(normalized_states),
        tracker=normalized_tracker,
        completed=True if completed else (False if incomplete else None),
        active=True if active else (False if inactive else None),
        stalled=True if stalled else None,
        errored=True if errored else None,
    )


def select_torrents(
    client: Any,
    filters: TorrentFilter,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[Selection, dict[str, int] | None]:
    """Select torrents by structured filter criteria.

    Loads once via `torrents_info()`, applies every cheap filter first,
    and only then calls `client.torrents_trackers()` -- at most once per
    surviving candidate, and only when `filters.tracker` is set.
    `on_progress` reports real progress over the calls actually made.

    Returns the selection plus, when the INSPECT stage ran, the active
    tracker count per infohash. `None` means no inspection happened at
    all -- never confuse it with an empty mapping, which would mean
    "inspected, nothing found".
    """
    all_torrents = list(client.torrents_info())

    if not filters.requires_tracker_data:
        selection = select_torrents_from_items(all_torrents, filters)
        if on_progress is not None:
            on_progress(selection.scanned, selection.scanned)
        return selection, None

    # SELECT on the cheap criteria first, INSPECT only the survivors:
    # this ordering is what bounds the `torrents_trackers()` call count.
    candidates = select_torrents_from_items(
        all_torrents, replace(filters, tracker=None)
    )
    inspection = inspect_trackers(client, candidates, on_progress=on_progress)

    # requires_tracker_data implies filters.tracker is not None
    assert filters.tracker is not None
    matched = [
        torrent
        for torrent in inspection.torrents
        if has_tracker_host(list(torrent.active_tracker_urls), filters.tracker)
    ]

    return (
        Selection(
            scanned=candidates.scanned,
            matched=tuple(torrent.snapshot for torrent in matched),
            request=SelectionRequest(filters=filters),
        ),
        {torrent.snapshot.hash: torrent.tracker_count for torrent in matched},
    )


def select_torrents_from_items(
    torrents: Sequence[Any],
    filters: TorrentFilter,
) -> Selection:
    """Apply only the cheap, `torrents_info()`-shaped filters in memory.

    Never calls the qBittorrent API -- for callers re-applying filters to
    an already-fetched snapshot. Cannot resolve a `--tracker` filter
    (needs a per-torrent `torrents_trackers()` lookup); pass
    `tracker=None` here and use `select_torrents` instead when a tracker
    filter is required.
    """
    if filters.requires_tracker_data:
        raise ValueError(
            "select_torrents_from_items cannot resolve a --tracker filter "
            "without a qBittorrent client; use select_torrents instead."
        )

    return select_from_items(torrents, SelectionRequest(filters=filters))


def list_torrent_snapshots(client: Any) -> tuple[TorrentSnapshot, ...]:
    """List every torrent as a `TorrentSnapshot`, unfiltered, one
    `torrents_info()` call -- the non-CLI counterpart to `select_torrents`."""
    return tuple(
        build_torrent_snapshot(torrent) for torrent in client.torrents_info()
    )


def list_category_usage(client: Any) -> dict[str, int]:
    """List categories and count torrents in each one."""
    category_usage: dict[str, int] = {}

    for torrent in client.torrents_info():
        category = format_category_label(
            get_field_as_string(torrent, "category")
        )
        category_usage[category] = category_usage.get(category, 0) + 1

    return dict(sorted(category_usage.items()))


def list_torrents_with_trackers(
    client: Any,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]]:
    """List torrents with tracker details for export and audit.

    Calls `client.torrents_trackers()` once per torrent, so
    `on_progress(completed, total)` reports real, known progress through
    that per-torrent work.
    """
    all_torrents = list(client.torrents_info())
    total = len(all_torrents)
    torrents: list[dict[str, Any]] = []

    for index, torrent in enumerate(all_torrents, start=1):
        torrent_hash = get_field_as_string(torrent, "hash")
        trackers = _get_tracker_details(client.torrents_trackers(torrent_hash))
        active_tracker_count = sum(
            1 for tracker in trackers if not tracker["disabled"]
        )
        torrents.append(
            {
                "hash": torrent_hash,
                "name": get_field_as_string(torrent, "name"),
                "state": get_field_as_string(torrent, "state"),
                "size": get_field_as_int(torrent, "size"),
                "progress": get_field_as_float(torrent, "progress"),
                "ratio": get_field_as_float(torrent, "ratio"),
                "save_path": get_field_as_string(torrent, "save_path"),
                "category": get_field_as_string(torrent, "category"),
                "tags": get_field_as_tag_list(torrent),
                "added_on": get_field_as_int(torrent, "added_on"),
                "trackers": trackers,
                "active_tracker_count": active_tracker_count,
            }
        )
        if on_progress is not None:
            on_progress(index, total)

    return torrents


def inspect_torrent(client: Any, torrent_hash: str) -> dict[str, Any] | None:
    """Return detailed torrent information for a hash or unique prefix.

    Returns `None` when nothing matches. Propagates
    `AmbiguousTorrentHashError` (and `InvalidTorrentSelectorError` for an
    empty selector) so the caller can present candidates instead of
    silently picking one torrent.
    """
    all_torrents = list(client.torrents_info())

    try:
        resolved = resolve_torrent_hash(all_torrents, torrent_hash)
    except TorrentNotFoundError:
        return None

    for torrent in all_torrents:
        current_hash = get_field_as_string(torrent, "hash")
        if current_hash.lower() == resolved.hash.lower():
            return _build_torrent_details(client, torrent, resolved.hash)

    return None  # pragma: no cover - resolved hash always exists


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
        torrent_name = get_field_as_string(torrent, "name")
        match_score = _score_name_match(torrent_name, normalized_query)
        if match_score < min_score:
            continue

        torrent_hash = get_field_as_string(torrent, "hash")
        matches.append(
            {
                "hash": torrent_hash,
                "name": torrent_name,
                "state": get_field_as_string(torrent, "state"),
                "progress": get_field_as_float(torrent, "progress"),
                "ratio": get_field_as_float(torrent, "ratio"),
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


@dataclass(frozen=True)
class BulkTorrentChange:
    """One torrent that a bulk action will act on."""

    hash: str
    name: str


@dataclass(frozen=True)
class BulkTorrentSkip:
    """One torrent excluded from a bulk action because it would be a no-op."""

    hash: str
    name: str
    reason: str


@dataclass(frozen=True)
class BulkTorrentActionPlan:
    """The result of planning a bulk torrent action, before it is applied.

    `changes`/`skipped` are always collected in full -- the CLI needs
    full detail for a confirmation preview, and whether to print it is a
    rendering decision, not a planning one. `torrent_hash` is the
    resolved full hash when `--hash` selected the target, `None`
    otherwise; `filters` is empty when `--hash` or `--all` was used.
    `delete_files` only matters for `action == "delete"`; ignored by
    every other action.
    """

    action: TorrentBulkAction
    torrent_hash: str | None
    select_all: bool
    filters: TorrentFilter
    scanned: int
    matched: int
    changes: tuple[BulkTorrentChange, ...]
    skipped: tuple[BulkTorrentSkip, ...]
    delete_files: bool = False


def validate_torrent_selector(
    *,
    torrent_hash: str | None,
    select_all: bool,
    filters: TorrentFilter,
) -> None:
    """Ensure a bulk torrent selector is safe and unambiguous.

    Thin adapter over `validate_selection_request`, kept for callers
    that still pass loose selector arguments rather than a
    `SelectionRequest`.
    """
    validate_selection_request(
        SelectionRequest(
            torrent_hash=torrent_hash,
            select_all=select_all,
            filters=filters,
        )
    )


def select_torrents_for_mutation(
    client: Any,
    *,
    torrent_hash: str | None,
    select_all: bool,
    filters: TorrentFilter,
    on_progress: Callable[[int, int], None] | None = None,
) -> Selection:
    """Resolve a full bulk-mutation selector to a `Selection`.

    Validates the selector first (see `validate_selection_request`), so
    an unsafe combination is rejected before any qBittorrent API call.
    `Selection.resolved_hash` carries the complete infohash when
    `--hash` was used, and is `None` for `--all`, a filter-based
    selection, or a hash matching nothing.
    """
    request = SelectionRequest(
        torrent_hash=torrent_hash, select_all=select_all, filters=filters
    )
    validate_selection_request(request)

    if torrent_hash is not None:
        all_torrents = list(client.torrents_info())
        if on_progress is not None:
            on_progress(len(all_torrents), len(all_torrents))
        return select_from_items(all_torrents, request)

    effective_filters = TorrentFilter() if select_all else filters
    selection, _ = select_torrents(
        client, effective_filters, on_progress=on_progress
    )
    return selection


def plan_bulk_torrent_action(
    client: Any,
    action: TorrentBulkAction,
    *,
    torrent_hash: str | None = None,
    select_all: bool = False,
    filters: TorrentFilter = EMPTY_TORRENT_FILTER,
    delete_files: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
) -> BulkTorrentActionPlan:
    """Plan a bulk torrent action against a filtered torrent selection.

    Read-only: never mutates the qBittorrent instance. `torrent_hash`
    accepts a complete hash or an unambiguous prefix; an ambiguous
    prefix raises `AmbiguousTorrentHashError` before any plan is built,
    while an unmatched hash resolves to zero selected torrents, same as
    any other filter that matches nothing. `delete_files` is only
    meaningful for `action="delete"`.
    """
    selection = select_torrents_for_mutation(
        client,
        torrent_hash=torrent_hash,
        select_all=select_all,
        filters=filters,
        on_progress=on_progress,
    )

    changes: list[BulkTorrentChange] = []
    skips: list[BulkTorrentSkip] = []

    for torrent in selection.matched:
        skip_reason = _bulk_action_skip_reason(action, torrent.state)
        if skip_reason is not None:
            skips.append(
                BulkTorrentSkip(
                    hash=torrent.hash, name=torrent.name, reason=skip_reason
                )
            )
            continue

        changes.append(BulkTorrentChange(hash=torrent.hash, name=torrent.name))

    return BulkTorrentActionPlan(
        action=action,
        torrent_hash=selection.resolved_hash,
        select_all=select_all,
        filters=filters,
        scanned=selection.scanned,
        matched=len(selection.matched),
        changes=tuple(changes),
        skipped=tuple(skips),
        delete_files=delete_files,
    )


def build_bulk_action_plan_from_snapshot(
    raw_torrents: Sequence[Any],
    action: TorrentBulkAction,
    selected_hashes: Sequence[str],
) -> BulkTorrentActionPlan:
    """Build a `BulkTorrentActionPlan` from an explicit hash selection
    against an already-fetched torrent snapshot -- zero API calls.

    The TUI's counterpart to `plan_bulk_torrent_action`, for callers
    that already hold a snapshot and a known hash set instead of doing a
    fresh `torrents_info()` scan. A selected hash missing from
    `raw_torrents` is reported as a skip (`"not_found"`), never dropped
    or substituted. `torrent_hash`/`select_all`/`filters` on the result
    are placeholders -- they describe a CLI selector, which doesn't
    apply to an explicit hash set.
    """
    by_hash: dict[str, Any] = {
        get_field_as_string(item, "hash").lower(): item for item in raw_torrents
    }

    changes: list[BulkTorrentChange] = []
    skips: list[BulkTorrentSkip] = []

    for torrent_hash in sorted(dict.fromkeys(selected_hashes)):
        torrent = by_hash.get(torrent_hash.lower())
        if torrent is None:
            skips.append(
                BulkTorrentSkip(
                    hash=torrent_hash, name=torrent_hash, reason="not_found"
                )
            )
            continue

        name = get_field_as_string(torrent, "name")
        state = get_field_as_string(torrent, "state")
        skip_reason = _bulk_action_skip_reason(action, state)
        if skip_reason is not None:
            skips.append(
                BulkTorrentSkip(
                    hash=torrent_hash, name=name, reason=skip_reason
                )
            )
            continue

        changes.append(BulkTorrentChange(hash=torrent_hash, name=name))

    return BulkTorrentActionPlan(
        action=action,
        torrent_hash=None,
        select_all=False,
        filters=EMPTY_TORRENT_FILTER,
        scanned=len(raw_torrents),
        matched=len(changes) + len(skips),
        changes=tuple(changes),
        skipped=tuple(skips),
    )


def apply_bulk_torrent_action(client: Any, plan: BulkTorrentActionPlan) -> None:
    """Apply a previously built plan. Mutates exactly `plan.changes`.

    Never rescans torrents: the plan is the sole source of truth for what
    gets mutated, so preview and execution can never diverge.
    """
    if not plan.changes:
        return

    hashes = [change.hash for change in plan.changes]
    try:
        _call_bulk_torrent_action(
            client, plan.action, hashes, delete_files=plan.delete_files
        )
    except Exception as error:
        raise RuntimeError(
            f"Failed to {plan.action} selected torrents: {error}"
        ) from error


class HashActionStatus(StrEnum):
    """Per-torrent outcome of a hash-targeted bulk action.

    `CHANGED`: needed the action, and the (unconfirmed -- no
    per-torrent API feedback) call succeeded. `UNCHANGED`: already in
    the requested state, no call made. `NOT_FOUND`: hash absent from
    the snapshot. Never treat `UNCHANGED` as `CHANGED` -- both are
    "successful" but only `CHANGED` means state actually moved.
    """

    CHANGED = "changed"
    UNCHANGED = "unchanged"
    NOT_FOUND = "not_found"
    FAILED = "failed"


@dataclass(frozen=True)
class HashActionOutcome:
    """One torrent's result within a `BulkHashActionResult`."""

    torrent_hash: str
    status: HashActionStatus
    previous_state: str | None = None
    reason: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class BulkHashActionResult:
    """One deduplicated `HashActionOutcome` per requested hash, sorted by
    hash. To later reverse only what this call changed, use
    `changed_hashes` -- never `successful_hashes`, which also includes
    `UNCHANGED`."""

    action: TorrentBulkAction
    outcomes: tuple[HashActionOutcome, ...]

    @property
    def changed_hashes(self) -> tuple[str, ...]:
        """Hashes whose state actually changed as a result of this call."""
        return tuple(
            outcome.torrent_hash
            for outcome in self.outcomes
            if outcome.status is HashActionStatus.CHANGED
        )

    @property
    def unchanged_hashes(self) -> tuple[str, ...]:
        """Hashes already in the requested state -- no API call was made."""
        return tuple(
            outcome.torrent_hash
            for outcome in self.outcomes
            if outcome.status is HashActionStatus.UNCHANGED
        )

    @property
    def not_found_hashes(self) -> tuple[str, ...]:
        """Requested hashes absent from the torrent snapshot."""
        return tuple(
            outcome.torrent_hash
            for outcome in self.outcomes
            if outcome.status is HashActionStatus.NOT_FOUND
        )

    @property
    def failed_hashes(self) -> tuple[str, ...]:
        """Hashes that needed the action but the API call raised."""
        return tuple(
            outcome.torrent_hash
            for outcome in self.outcomes
            if outcome.status is HashActionStatus.FAILED
        )

    @property
    def successful_hashes(self) -> tuple[str, ...]:
        """`CHANGED` union `UNCHANGED` -- not for deciding what to
        reverse later; use `changed_hashes`."""
        return tuple(
            outcome.torrent_hash
            for outcome in self.outcomes
            if outcome.status
            in (HashActionStatus.CHANGED, HashActionStatus.UNCHANGED)
        )


def _apply_bulk_action_to_hashes(
    client: Any,
    action: TorrentBulkAction,
    hashes: Collection[str],
) -> BulkHashActionResult:
    """Classify `hashes` from one `torrents_info()` call, then issue at
    most one bulk mutating call for those that need it. On failure every
    attempted hash is reported `FAILED` with the same `error` --
    qBittorrent's bulk endpoints give no per-torrent status. Hashes are
    deduplicated case-insensitively (deterministic, sorted order); a
    blank hash raises `InvalidTorrentSelectorError` before any API call.
    """
    normalized_hashes = sorted(
        {_normalize_requested_hash(value) for value in hashes}
    )
    if not normalized_hashes:
        return BulkHashActionResult(action=action, outcomes=())

    raw_torrents = list(client.torrents_info())
    by_hash: dict[str, Any] = {
        get_field_as_string(item, "hash").lower(): item for item in raw_torrents
    }

    outcomes: dict[str, HashActionOutcome] = {}
    to_apply: list[str] = []

    for normalized_hash in normalized_hashes:
        torrent = by_hash.get(normalized_hash)
        if torrent is None:
            outcomes[normalized_hash] = HashActionOutcome(
                torrent_hash=normalized_hash,
                status=HashActionStatus.NOT_FOUND,
                reason="not_found",
            )
            continue

        # Use qBittorrent's own casing, not the caller's -- two requested
        # hashes for the same torrent already collapsed above, so this
        # can never collide with another entry.
        canonical_hash = get_field_as_string(torrent, "hash")
        state = get_field_as_string(torrent, "state")
        skip_reason = _bulk_action_skip_reason(action, state)
        if skip_reason is not None:
            outcomes[canonical_hash] = HashActionOutcome(
                torrent_hash=canonical_hash,
                status=HashActionStatus.UNCHANGED,
                previous_state=state,
                reason=skip_reason,
            )
            continue

        outcomes[canonical_hash] = HashActionOutcome(
            torrent_hash=canonical_hash,
            status=HashActionStatus.CHANGED,  # provisional, see below
            previous_state=state,
        )
        to_apply.append(canonical_hash)

    if to_apply:
        try:
            _call_bulk_torrent_action(client, action, to_apply)
        except Exception as error:
            error_message = f"Failed to {action} selected torrents: {error}"
            for torrent_hash in to_apply:
                previous = outcomes[torrent_hash]
                outcomes[torrent_hash] = HashActionOutcome(
                    torrent_hash=torrent_hash,
                    status=HashActionStatus.FAILED,
                    previous_state=previous.previous_state,
                    error=error_message,
                )

    return BulkHashActionResult(
        action=action, outcomes=tuple(outcomes.values())
    )


def _normalize_requested_hash(value: str) -> str:
    """Lowercase and validate one requested hash before any API call."""
    normalized = value.strip().lower()
    if normalized == "":
        raise InvalidTorrentSelectorError("Provide a non-empty torrent hash.")
    return normalized


def pause_torrents_by_hash(
    client: Any,
    hashes: Collection[str],
) -> BulkHashActionResult:
    """Pause exactly `hashes`. Track `.changed_hashes` to reverse later."""
    return _apply_bulk_action_to_hashes(client, "pause", hashes)


def resume_torrents_by_hash(
    client: Any,
    hashes: Collection[str],
) -> BulkHashActionResult:
    """Resume exactly `hashes`. Pass a prior pause's `changed_hashes`,
    never `successful_hashes`."""
    return _apply_bulk_action_to_hashes(client, "resume", hashes)


def _call_bulk_torrent_action(
    client: Any,
    action: TorrentBulkAction,
    torrent_hashes: list[str],
    *,
    delete_files: bool = False,
) -> None:
    """Call the qBittorrent API for a bulk torrent action.

    Calls `torrents_start` directly for "resume"/"start": the installed
    qbittorrent-api aliases `torrents_resume = torrents_start`, so the
    method is never absent on a real client (verified in
    `tests/test_qbit_library_http_boundary.py`). `delete_files` is only
    read for `action="delete"`.
    """
    if action == "pause":
        client.torrents_pause(torrent_hashes)
        return

    if action in ("resume", "start"):
        client.torrents_start(torrent_hashes)
        return

    if action == "delete":
        client.torrents_delete(
            delete_files=delete_files, torrent_hashes=torrent_hashes
        )
        return

    client.torrents_reannounce(torrent_hashes)


def _bulk_action_skip_reason(
    action: TorrentBulkAction,
    state: str,
) -> str | None:
    """Return a skip reason when a bulk action would be a no-op.

    "reannounce" and "delete" have no no-op state -- always a change.
    """
    if action == "pause" and is_stopped_state(state):
        return "already_stopped"

    if action in ("resume", "start") and not is_stopped_state(state):
        return "already_running"

    return None


def _build_torrent_details(
    client: Any,
    torrent: Any,
    torrent_hash: str,
) -> dict[str, Any]:
    """Build a detailed torrent report with tracker information.

    Uses `get_safe_tracker_details`, not `_get_tracker_details`: this
    feeds `torrents inspect`, an ordinary read command, so trackers must
    be secret-free. Raw announce URLs are only ever returned by
    `list_torrents_with_trackers`, for the sensitive `backup export`.
    """
    trackers = get_safe_tracker_details(client.torrents_trackers(torrent_hash))
    active_tracker_count = sum(1 for tracker in trackers if tracker["enabled"])

    return {
        "hash": torrent_hash,
        "name": get_field_as_string(torrent, "name"),
        "state": get_field_as_string(torrent, "state"),
        "size": get_field_as_int(torrent, "size"),
        "progress": get_field_as_float(torrent, "progress"),
        "ratio": get_field_as_float(torrent, "ratio"),
        "save_path": get_field_as_string(torrent, "save_path"),
        "category": get_field_as_string(torrent, "category"),
        "added_on": get_field_as_int(torrent, "added_on"),
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
    """Extract tracker URLs and status from qBittorrent tracker objects.

    Returns the literal announce URL, so it is only safe for
    `list_torrents_with_trackers` (the `backup export` artifact), never
    for an ordinary command's rendered output. Use
    `get_safe_tracker_details` for anything user-facing.
    """
    tracker_details: list[dict[str, Any]] = []

    for tracker in trackers:
        tracker_url = get_field_as_string(tracker, "url")
        if tracker_url == "":
            continue

        tracker_details.append(
            {
                "url": tracker_url,
                "status": get_field_as_string(tracker, "status"),
                "disabled": is_disabled_tracker(tracker),
            }
        )

    return tracker_details


def get_safe_tracker_details(trackers: Any) -> list[dict[str, Any]]:
    """Extract secret-free structural tracker details for display.

    Mirrors the endpoint shape `inspect_tracker` in
    `qbit_core.features.trackers` produces: a normalized identity plus
    structural URL fields, never a raw announce URL, passkey, or query
    value.
    """
    tracker_details: list[dict[str, Any]] = []

    for tracker in trackers:
        tracker_url = get_field_as_string(tracker, "url")
        if tracker_url == "":
            continue

        safe_identity = describe_tracker_url(tracker_url)
        raw_status = get_raw_tracker_status(tracker)
        health, enabled = classify_raw_tracker_status(raw_status)
        raw_message = get_field_as_string(tracker, "msg")
        message = sanitize_tracker_text(raw_message) if raw_message else None

        tracker_details.append(
            {
                "tracker": safe_identity.identity,
                "health": health.value,
                "enabled": enabled,
                "scheme": safe_identity.scheme,
                "path_shape": safe_identity.path_shape,
                "query_keys": list(safe_identity.query_keys),
                "message": message,
            }
        )

    return tracker_details
