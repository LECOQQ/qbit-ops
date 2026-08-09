"""Decide which torrents an operation targets -- the SELECT stage.

Owns the whole selection vocabulary: the structured `TorrentFilter`,
the `SelectionRequest` every caller passes around instead of a dozen
loose arguments, the `Selection` it resolves to, and the hash resolver
that turns a full infohash or unambiguous prefix into one torrent. The
infohash is the primary identifier: an operation must never silently
affect several torrents because a selector was ambiguous.

Pure by construction -- no qBittorrent client, no I/O, no Typer/Rich.
`select_from_items` applies a filter to torrents a caller already
holds; fetching them is the caller's job (`features.torrents`), and so
is anything needing a per-torrent tracker lookup (the INSPECT stage).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from qbit_core.errors import QbitCoreError
from qbit_core.qbit.fields import get_field_as_string
from qbit_core.shared.torrent_states import (
    TorrentSnapshot,
    TorrentStateGroup,
    build_torrent_snapshot,
    classify_torrent_state,
    is_completed_torrent,
    is_stopped_state,
)

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
class ResolvedTorrent:
    """Identify one torrent resolved from a hash selector."""

    hash: str
    name: str


class TorrentSelectorError(QbitCoreError):
    """Base class for torrent hash selector errors."""


class InvalidTorrentSelectorError(TorrentSelectorError, ValueError):
    """Report an empty or otherwise malformed hash selector."""


class TorrentNotFoundError(TorrentSelectorError):
    """Report that no torrent matches the given hash selector."""

    def __init__(self, value: str) -> None:
        """Store the selector value that matched no torrent."""
        self.value = value
        super().__init__(f"No torrent matches hash '{value}'.")


class AmbiguousTorrentHashError(TorrentSelectorError):
    """Report that a hash prefix matches more than one torrent."""

    def __init__(
        self,
        value: str,
        candidates: tuple[ResolvedTorrent, ...],
    ) -> None:
        """Store the ambiguous selector and its sorted candidates."""
        self.value = value
        self.candidates = candidates
        super().__init__(
            f"Hash prefix '{value}' matches {len(candidates)} torrents."
        )


def resolve_torrent_hash(
    torrents: Iterable[Any],
    value: str,
) -> ResolvedTorrent:
    """Resolve a complete hash or unique hash prefix to one torrent.

    Matching is case-insensitive. Raises `InvalidTorrentSelectorError`
    for an empty selector, `TorrentNotFoundError` when nothing matches,
    and `AmbiguousTorrentHashError` when several torrents share the
    prefix (candidates sorted by hash, then name).
    """
    normalized_value = value.strip().lower()
    if normalized_value == "":
        raise InvalidTorrentSelectorError(
            "Provide a non-empty torrent hash or hash prefix."
        )

    candidates: list[ResolvedTorrent] = []
    for torrent in torrents:
        torrent_hash = get_field_as_string(torrent, "hash")
        if torrent_hash == "":
            continue
        if torrent_hash.lower().startswith(normalized_value):
            candidates.append(
                ResolvedTorrent(
                    hash=torrent_hash,
                    name=get_field_as_string(torrent, "name"),
                )
            )

    if not candidates:
        raise TorrentNotFoundError(value)

    if len(candidates) > 1:
        candidates.sort(
            key=lambda candidate: (
                candidate.hash.lower(),
                candidate.name.casefold(),
            )
        )
        raise AmbiguousTorrentHashError(value, tuple(candidates))

    return candidates[0]


@dataclass(frozen=True)
class TorrentFilter:
    """Structured, Typer/Rich-free torrent selection criteria.

    Repeated `--category`/`--state` values combine with OR within the
    same field; different fields combine with AND. `tracker`, when set,
    is always pre-normalized to `host` or `host:port` -- never a full
    URL, so a passkey can never reach this model. `categories` holds raw
    requested tokens; `states` holds only `STATE_FILTER_VALUES` values.
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


EMPTY_TORRENT_FILTER = TorrentFilter()


@dataclass(frozen=True)
class SelectionRequest:
    """What the operator asked to target, before any qBittorrent call.

    The single selection argument every layer passes around, so a
    future change to how criteria are expressed touches one type
    instead of every command signature. `filters` stays deliberately
    opaque here: combination semantics belong to `TorrentFilter`.
    """

    torrent_hash: str | None = None
    select_all: bool = False
    filters: TorrentFilter = field(default_factory=TorrentFilter)

    @property
    def requires_tracker_data(self) -> bool:
        """Return whether resolving this request needs an INSPECT pass."""
        return self.filters.requires_tracker_data


@dataclass(frozen=True)
class Selection:
    """The deterministic result of resolving a `SelectionRequest`.

    `matched` carries `TorrentSnapshot` -- the central torrent model --
    sorted by canonical name. `resolved_hash` is the complete infohash
    when `torrent_hash` selected the target (`None` otherwise, and also
    `None` when the hash matched nothing).
    """

    scanned: int
    matched: tuple[TorrentSnapshot, ...]
    request: SelectionRequest
    resolved_hash: str | None = None


def validate_selection_request(request: SelectionRequest) -> None:
    """Ensure a mutation selector is safe and unambiguous.

    `--hash` never combines with `--all` or a filter; `--all` (an
    explicit acknowledgement of whole-instance scope) never combines
    with a filter either. Otherwise one or more filters may define the
    selection, but at least one of `--hash`, `--all`, or a filter is
    always required -- no selector can silently mean the whole seedbox.
    """
    if request.torrent_hash is not None:
        if request.select_all or not request.filters.is_empty:
            raise ValueError(
                "Use --hash alone, without --all or any other filter."
            )
        return

    if request.select_all:
        if not request.filters.is_empty:
            raise ValueError("Use --all alone, without any other filter.")
        return

    if request.filters.is_empty:
        raise ValueError(
            "Provide --hash, --all, or at least one filter (--category, "
            "--state, --tracker, --completed, --incomplete, --active, "
            "--inactive, --stalled, --errored)."
        )


def select_from_items(
    torrents: Sequence[Any],
    request: SelectionRequest,
) -> Selection:
    """Apply the cheap, `torrents_info()`-shaped criteria in memory.

    Never calls the qBittorrent API. Resolves `torrent_hash` when set
    (an unmatched hash yields zero matches rather than raising, so it
    flows through the same no-match path as any other selector; an
    ambiguous prefix raises `AmbiguousTorrentHashError` before anything
    can be planned). Cannot resolve a `tracker` criterion -- that needs
    the INSPECT stage; callers must narrow afterwards.
    """
    if request.torrent_hash is not None:
        return _select_by_hash(torrents, request)

    effective = EMPTY_TORRENT_FILTER if request.select_all else request.filters
    matched = [
        build_torrent_snapshot(torrent)
        for torrent in torrents
        if matches_cheap_filters(torrent, effective)
    ]

    return Selection(
        scanned=len(torrents),
        matched=_sorted_by_name(matched),
        request=request,
    )


def _select_by_hash(
    torrents: Sequence[Any],
    request: SelectionRequest,
) -> Selection:
    """Resolve a hash selector to at most one torrent."""
    assert request.torrent_hash is not None
    try:
        resolved = resolve_torrent_hash(torrents, request.torrent_hash)
    except TorrentNotFoundError:
        return Selection(scanned=len(torrents), matched=(), request=request)

    matched = tuple(
        build_torrent_snapshot(torrent)
        for torrent in torrents
        if get_field_as_string(torrent, "hash").lower() == resolved.hash.lower()
    )

    return Selection(
        scanned=len(torrents),
        matched=matched,
        request=request,
        resolved_hash=resolved.hash,
    )


def matches_cheap_filters(torrent: Any, filters: TorrentFilter) -> bool:
    """Return whether a torrent matches every `torrents_info()`-only filter.

    Excludes `tracker`, which needs a per-torrent lookup -- callers that
    set it must narrow the result after the INSPECT stage.
    """
    if filters.categories:
        torrent_category = get_field_as_string(torrent, "category")
        if not any(
            category_matches(torrent_category, category)
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


def category_matches(torrent_category: str, requested_category: str) -> bool:
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


def format_category_label(category: str) -> str:
    """Normalize category labels for display and comparison."""
    if category.strip() == "":
        return UNCATEGORIZED_LABEL

    return category.strip()


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


def _sorted_by_name(
    snapshots: Sequence[TorrentSnapshot],
) -> tuple[TorrentSnapshot, ...]:
    return tuple(sorted(snapshots, key=lambda item: item.name.casefold()))
