"""List and select qBittorrent torrents.

Owns the shared, structured torrent-filter model (`TorrentFilter`,
`SelectedTorrent`, `TorrentSelection`) and its one filtering pipeline
(`select_torrents`), reused by every read command and bulk mutation that
targets more than a single hash. Kept free of Typer and Rich so it can be
reused by any future interface (CLI, TUI) without pulling in presentation
concerns -- mirrors `qbit_ops.selectors` for hash resolution and
`qbit_ops.status`/`qbit_ops.doctor` for their own collection/render splits.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Literal

from qbit_ops.qbit.fields import (
    get_active_tracker_urls,
    get_field_as_float,
    get_field_as_int,
    get_field_as_string,
    get_raw_tracker_status,
    is_disabled_tracker,
)
from qbit_ops.selectors import TorrentNotFoundError, resolve_torrent_hash
from qbit_ops.torrent_states import (
    TorrentStateGroup,
    classify_torrent_state,
    is_completed_torrent,
    is_stopped_state,
)
from qbit_ops.trackers import (
    classify_raw_tracker_status,
    describe_tracker_url,
    has_tracker_host,
    normalize_tracker_host,
    sanitize_tracker_text,
)

TorrentBulkAction = Literal["pause", "resume", "start", "reannounce"]

UNCATEGORIZED_LABEL = "(uncategorized)"
UNCATEGORIZED_FILTER_TOKEN = "uncategorized"

# The public `--state` vocabulary: exactly the values `classify_torrent_state`
# can return, so a filter value can never name a group the classifier
# itself would never produce. `completed`/`active`/`inactive` are
# deliberately NOT part of this vocabulary -- they have their own
# dedicated `TorrentFilter` fields (and CLI flags) instead of a second,
# overlapping spelling here.
STATE_FILTER_VALUES: frozenset[TorrentStateGroup] = frozenset(
    {"downloading", "seeding", "checking", "stalled", "errored", "unknown"}
)


@dataclass(frozen=True)
class TorrentFilter:
    """Structured, Typer/Rich-free torrent selection criteria.

    Repeated `--category`/`--state` values combine with OR within the
    same field; different fields combine with AND. `tracker`, when set,
    is always already normalized to `host` or `host:port`
    (`qbit_ops.trackers.normalize_tracker_host`) by `build_torrent_filter` --
    never a full URL, so a passkey embedded in a tracker's path or query
    string can never reach this model. `categories` holds the raw
    requested tokens (not display-normalized); `states` holds only
    values from `STATE_FILTER_VALUES`.
    """

    categories: tuple[str, ...] = ()
    states: tuple[TorrentStateGroup, ...] = ()
    tracker: str | None = None
    completed: bool | None = None
    active: bool | None = None
    stalled: bool | None = None
    errored: bool | None = None

    @property
    def is_empty(self) -> bool:
        """Return whether this filter would select every torrent."""
        return (
            not self.categories
            and not self.states
            and self.tracker is None
            and self.completed is None
            and self.active is None
            and self.stalled is None
            and self.errored is None
        )

    @property
    def requires_tracker_data(self) -> bool:
        """Return whether this filter needs a per-torrent tracker lookup."""
        return self.tracker is not None


_EMPTY_TORRENT_FILTER = TorrentFilter()


@dataclass(frozen=True)
class SelectedTorrent:
    """One torrent selected by `select_torrents`.

    Carries the complete infohash (never truncated) plus enough fields
    for list rendering, mutation plans, previews, and future `explain`/
    TUI consumers. `tracker_count` is `None` when tracker data was not
    collected for this selection (no `--tracker` filter was present) --
    distinct from `0`, which means tracker data *was* collected and the
    torrent legitimately has no active trackers. `download_rate`/
    `upload_rate` (bytes/second, from qBittorrent's own `dlspeed`/
    `upspeed` fields already present on every `torrents_info()` item --
    no extra API call) were added for the TUI's per-row rate column
    (pre-1.0 additive field, see docs/DECISIONS.md).
    """

    hash: str
    name: str
    category: str
    state: str
    size: int
    progress: float
    ratio: float
    tracker_count: int | None
    download_rate: int
    upload_rate: int


@dataclass(frozen=True)
class TorrentSelection:
    """The deterministic result of applying a `TorrentFilter`."""

    scanned: int
    matched: tuple[SelectedTorrent, ...]
    filters: TorrentFilter
    tracker_data_collected: bool


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

    The single seam between CLI options (independent boolean flags for
    each polarity) and the structured tri-state model. Rejects the two
    locally-provable contradictions (`--completed --incomplete`,
    `--active --inactive`) and any unrecognized `--state` value before
    any qBittorrent API call; every other combination is accepted and
    combines with AND, even where it can never match anything (e.g.
    `--state downloading --stalled`) -- not every combination that
    yields zero matches is a contradiction worth rejecting.
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
) -> TorrentSelection:
    """Select torrents by structured filter criteria.

    The shared filtering pipeline: load once via `torrents_info()`, apply
    every cheap (torrent-info-only) filter first, and only then call
    `client.torrents_trackers()` -- at most once per surviving candidate,
    and only when `filters.tracker` is set. A filter-less or
    non-tracker-filtered selection never calls `torrents_trackers()` at
    all. `on_progress` reports real, known progress over exactly the
    calls actually made: a single (total, total) completion when no
    tracker lookup is needed, or one advance per candidate tracker
    lookup otherwise.
    """
    all_torrents = list(client.torrents_info())

    if not filters.requires_tracker_data:
        selection = select_torrents_from_items(all_torrents, filters)
        if on_progress is not None:
            on_progress(selection.scanned, selection.scanned)
        return selection

    total = len(all_torrents)
    candidates = [
        torrent
        for torrent in all_torrents
        if _matches_cheap_filters(torrent, filters)
    ]

    selected: list[SelectedTorrent] = []
    candidate_total = len(candidates)
    for index, torrent in enumerate(candidates, start=1):
        torrent_hash = get_field_as_string(torrent, "hash")
        active_trackers = get_active_tracker_urls(
            client.torrents_trackers(torrent_hash)
        )
        if on_progress is not None:
            on_progress(index, candidate_total)

        # requires_tracker_data implies filters.tracker is not None
        assert filters.tracker is not None
        if not has_tracker_host(active_trackers, filters.tracker):
            continue

        selected.append(
            _build_selected_torrent(torrent, tracker_count=len(active_trackers))
        )

    selected.sort(key=lambda item: item.name.casefold())

    return TorrentSelection(
        scanned=total,
        matched=tuple(selected),
        filters=filters,
        tracker_data_collected=True,
    )


def select_torrents_from_items(
    torrents: Sequence[Any],
    filters: TorrentFilter,
) -> TorrentSelection:
    """Apply only the cheap, `torrents_info()`-shaped filters in memory.

    Never calls the qBittorrent API -- the caller has already fetched
    `torrents` (typically once per refresh cycle, e.g. a TUI's periodic
    tick, see `qbit_ops.app_services`) and wants to (re-)apply filters to it
    without a second `torrents_info()` round-trip. This never resolves a
    `--tracker` filter, which needs a per-torrent `torrents_trackers()`
    lookup this function deliberately cannot perform: pass a filter with
    `tracker=None` here, and use `select_torrents` (with a client)
    instead when a tracker filter is required.
    """
    if filters.requires_tracker_data:
        raise ValueError(
            "select_torrents_from_items cannot resolve a --tracker filter "
            "without a qBittorrent client; use select_torrents instead."
        )

    total = len(torrents)
    selected = [
        _build_selected_torrent(torrent, tracker_count=None)
        for torrent in torrents
        if _matches_cheap_filters(torrent, filters)
    ]
    selected.sort(key=lambda item: item.name.casefold())

    return TorrentSelection(
        scanned=total,
        matched=tuple(selected),
        filters=filters,
        tracker_data_collected=False,
    )


def torrent_filter_to_dict(filters: TorrentFilter) -> dict[str, Any]:
    """Build a stable, JSON-safe representation of a torrent filter."""
    return {
        "categories": list(filters.categories),
        "states": list(filters.states),
        "tracker": filters.tracker,
        "completed": filters.completed,
        "active": filters.active,
        "stalled": filters.stalled,
        "errored": filters.errored,
    }


def describe_torrent_filter(filters: TorrentFilter) -> str:
    """Build a concise, deterministic human description of a torrent filter.

    Never renders anything beyond what `TorrentFilter` itself carries --
    `tracker` is already host-only by construction, so this can never
    leak a passkey.
    """
    parts: list[str] = []
    if filters.categories:
        parts.append("category=" + "|".join(filters.categories))
    if filters.states:
        parts.append("state=" + "|".join(filters.states))
    if filters.tracker is not None:
        parts.append(f"tracker={filters.tracker}")
    if filters.completed is not None:
        parts.append("completed" if filters.completed else "incomplete")
    if filters.active is not None:
        parts.append("active" if filters.active else "inactive")
    if filters.stalled is not None:
        parts.append("stalled")
    if filters.errored is not None:
        parts.append("errored")

    return ", ".join(parts) if parts else "none"


def _matches_cheap_filters(torrent: Any, filters: TorrentFilter) -> bool:
    """Return whether a torrent matches every `torrents_info()`-only filter."""
    if filters.categories:
        torrent_category = get_field_as_string(torrent, "category")
        if not any(
            _category_matches(torrent_category, category)
            for category in filters.categories
        ):
            return False

    state = get_field_as_string(torrent, "state")

    if filters.states and classify_torrent_state(state) not in filters.states:
        return False

    if (
        filters.completed is not None
        and is_completed_torrent(torrent) != filters.completed
    ):
        return False

    if filters.active is not None and (not is_stopped_state(state)) != (
        filters.active
    ):
        return False

    if filters.stalled is not None:
        if (classify_torrent_state(state) == "stalled") != filters.stalled:
            return False

    if filters.errored is not None:
        if (classify_torrent_state(state) == "errored") != filters.errored:
            return False

    return True


def _build_selected_torrent(
    torrent: Any, *, tracker_count: int | None
) -> SelectedTorrent:
    """Build a `SelectedTorrent` from a raw qBittorrent torrent object."""
    return SelectedTorrent(
        hash=get_field_as_string(torrent, "hash"),
        name=get_field_as_string(torrent, "name"),
        category=_format_category_label(
            get_field_as_string(torrent, "category")
        ),
        state=get_field_as_string(torrent, "state"),
        size=get_field_as_int(torrent, "size"),
        progress=get_field_as_float(torrent, "progress"),
        ratio=get_field_as_float(torrent, "ratio"),
        tracker_count=tracker_count,
        download_rate=get_field_as_int(torrent, "dlspeed"),
        upload_rate=get_field_as_int(torrent, "upspeed"),
    )


def _category_matches(torrent_category: str, requested_category: str) -> bool:
    """Return whether a torrent category matches the requested filter.

    Accepts either public uncategorized token: the bare word
    (`uncategorized`, the filter value users type) or the display label
    (`(uncategorized)`, what commands render) -- both are documented,
    interchangeable ways to request uncategorized torrents.
    """
    normalized_request = requested_category.strip().casefold()
    if normalized_request in {
        UNCATEGORIZED_LABEL.casefold(),
        UNCATEGORIZED_FILTER_TOKEN,
    }:
        return torrent_category.strip() == ""

    return torrent_category.casefold() == normalized_request


def _format_category_label(category: str) -> str:
    """Normalize category labels for display and comparison."""
    if category.strip() == "":
        return UNCATEGORIZED_LABEL

    return category.strip()


def list_category_usage(client: Any) -> dict[str, int]:
    """List categories and count torrents in each one."""
    category_usage: dict[str, int] = {}

    for torrent in client.torrents_info():
        category = _format_category_label(
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

    `changes` and `skipped` are collected unconditionally (not gated by a
    `verbose` flag) since the CLI layer needs full detail to render a
    confirmation preview; whether to *print* that detail is a rendering
    decision, not a planning one. `torrent_hash` is the resolved full
    hash when `--hash` selected the target, always `None` otherwise;
    `filters` is always the exact `TorrentFilter` used (empty when
    `--hash` or `--all` was used instead).
    """

    action: TorrentBulkAction
    torrent_hash: str | None
    select_all: bool
    filters: TorrentFilter
    scanned: int
    matched: int
    changes: tuple[BulkTorrentChange, ...]
    skipped: tuple[BulkTorrentSkip, ...]


def validate_torrent_selector(
    *,
    torrent_hash: str | None,
    select_all: bool,
    filters: TorrentFilter,
) -> None:
    """Ensure a bulk torrent selector is safe and unambiguous.

    `--hash` always resolves to a single torrent, so it never combines
    with `--all` or any filter. `--all` is an explicit acknowledgement of
    whole-instance scope, so it never combines with a filter either
    (there is no validation-only meaning that would justify it). One or
    more filters may otherwise define a bulk selection on their own --
    but at least one of `--hash`, `--all`, or a filter is always
    required, so no selector can ever silently mean the whole seedbox.
    """
    if torrent_hash is not None:
        if select_all or not filters.is_empty:
            raise ValueError(
                "Use --hash alone, without --all or any other filter."
            )
        return

    if select_all:
        if not filters.is_empty:
            raise ValueError("Use --all alone, without any other filter.")
        return

    if filters.is_empty:
        raise ValueError(
            "Provide --hash, --all, or at least one filter (--category, "
            "--state, --tracker, --completed, --incomplete, --active, "
            "--inactive, --stalled, --errored)."
        )


def select_torrents_for_mutation(
    client: Any,
    *,
    torrent_hash: str | None,
    select_all: bool,
    filters: TorrentFilter,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[TorrentSelection, str | None]:
    """Resolve a full bulk-mutation selector to a `TorrentSelection`.

    Validates the selector first (see `validate_torrent_selector`), so an
    unsafe combination is rejected before any qBittorrent API call.
    Returns `(selection, resolved_hash)`: `resolved_hash` is the
    complete infohash when `--hash` was used (even on a no-match), and
    `None` for `--all` or filter-based selections.
    """
    validate_torrent_selector(
        torrent_hash=torrent_hash, select_all=select_all, filters=filters
    )

    if torrent_hash is not None:
        return _select_torrents_by_hash(client, torrent_hash, on_progress)

    effective_filters = TorrentFilter() if select_all else filters
    selection = select_torrents(
        client, effective_filters, on_progress=on_progress
    )
    return selection, None


def _select_torrents_by_hash(
    client: Any,
    torrent_hash: str,
    on_progress: Callable[[int, int], None] | None,
) -> tuple[TorrentSelection, str | None]:
    """Resolve a hash selector to at most one torrent.

    An unmatched hash resolves to zero selected torrents rather than
    raising, so it flows through the same no-match path as any other
    bulk selector. An ambiguous prefix raises `AmbiguousTorrentHashError`
    so the caller can reject it before any mutation is attempted.
    """
    all_torrents = list(client.torrents_info())
    total = len(all_torrents)
    if on_progress is not None:
        on_progress(total, total)

    try:
        resolved = resolve_torrent_hash(all_torrents, torrent_hash)
    except TorrentNotFoundError:
        return (
            TorrentSelection(
                scanned=total,
                matched=(),
                filters=TorrentFilter(),
                tracker_data_collected=False,
            ),
            None,
        )

    matched = tuple(
        _build_selected_torrent(torrent, tracker_count=None)
        for torrent in all_torrents
        if get_field_as_string(torrent, "hash").lower() == resolved.hash.lower()
    )

    return (
        TorrentSelection(
            scanned=total,
            matched=matched,
            filters=TorrentFilter(),
            tracker_data_collected=False,
        ),
        resolved.hash,
    )


def plan_bulk_torrent_action(
    client: Any,
    action: TorrentBulkAction,
    *,
    torrent_hash: str | None = None,
    select_all: bool = False,
    filters: TorrentFilter = _EMPTY_TORRENT_FILTER,
    on_progress: Callable[[int, int], None] | None = None,
) -> BulkTorrentActionPlan:
    """Plan a bulk torrent action against a filtered torrent selection.

    Pure with respect to the qBittorrent instance: this only reads state
    and never mutates it. `torrent_hash` accepts a complete hash or an
    unambiguous prefix, resolved via `qbit_ops.selectors.resolve_torrent_hash`.
    An ambiguous prefix raises `AmbiguousTorrentHashError` before any plan
    is built; an unmatched hash resolves to zero selected torrents, same
    as any other filter that matches nothing.
    """
    selection, resolved_hash = select_torrents_for_mutation(
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
        torrent_hash=resolved_hash,
        select_all=select_all,
        filters=filters,
        scanned=selection.scanned,
        matched=len(selection.matched),
        changes=tuple(changes),
        skipped=tuple(skips),
    )


def build_bulk_action_plan_from_snapshot(
    raw_torrents: Sequence[Any],
    action: TorrentBulkAction,
    selected_hashes: Sequence[str],
) -> BulkTorrentActionPlan:
    """Build a `BulkTorrentActionPlan` from an explicit hash selection
    against an already-fetched torrent snapshot -- zero API calls.

    The TUI's multi-selection counterpart to `plan_bulk_torrent_action`:
    that function always resolves its own selector (`--hash`/`--all`/
    filters) via a fresh `torrents_info()` scan, which does not fit an
    explicit, already-known set of full hashes the caller (e.g. a TUI
    that just refreshed) already has in memory. Reuses the exact same
    skip-reason rule (`_bulk_action_skip_reason`) and result shapes
    (`BulkTorrentChange`/`BulkTorrentSkip`/`BulkTorrentActionPlan`) as
    the CLI planner -- there is only one skip-reason rule catalogue.

    A selected hash no longer present in `raw_torrents` is reported as
    a skip (reason `"not_found"`), never silently dropped and never
    substituted -- the caller can tell "excluded because satisfied"
    apart from "excluded because it disappeared". `torrent_hash`/
    `select_all`/`filters` on the returned plan are placeholders
    (`None`/`False`/empty): they describe *how* a CLI selector was
    built, which does not apply to an explicit hash set, and
    `apply_bulk_torrent_action` never reads them anyway (only
    `action`/`changes`).
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
        filters=_EMPTY_TORRENT_FILTER,
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
        _call_bulk_torrent_action(client, plan.action, hashes)
    except Exception as error:
        raise RuntimeError(
            f"Failed to {plan.action} selected torrents: {error}"
        ) from error


def _call_bulk_torrent_action(
    client: Any,
    action: TorrentBulkAction,
    torrent_hashes: list[str],
) -> None:
    """Call the qBittorrent API for a bulk torrent action.

    Calls `torrents_start` directly for "resume"/"start" rather than
    probing for it with `getattr(client, "torrents_start", None)` and
    falling back to `torrents_resume` (constat P-4): the installed
    qbittorrent-api aliases `torrents_resume = torrents_start` (the same
    bound method, verified in `tests/test_qbit_library_http_boundary.py`),
    so `torrents_start` is never absent on a real client and the
    fallback branch was unreachable dead code. qbittorrent-api itself
    already negotiates the underlying `start`/`resume` endpoint by Web
    API version internally -- this project does not duplicate that
    negotiation.
    """
    if action == "pause":
        client.torrents_pause(torrent_hashes)
        return

    if action in ("resume", "start"):
        client.torrents_start(torrent_hashes)
        return

    client.torrents_reannounce(torrent_hashes)


def _bulk_action_skip_reason(
    action: TorrentBulkAction,
    state: str,
) -> str | None:
    """Return a skip reason when a bulk action would be a no-op."""
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
    be reduced to secret-free structural fields the same way
    `trackers inspect` does. Raw announce URLs are only ever returned by
    `list_torrents_with_trackers`, which feeds the sensitive `backup
    export` artifact.
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

    Mirrors the endpoint shape `inspect_tracker` in `qbit_ops.trackers`
    produces: a normalized identity plus structural URL fields, never a
    raw announce URL, passkey, or query value.
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
