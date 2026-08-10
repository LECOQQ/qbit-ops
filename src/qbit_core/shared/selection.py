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

import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from qbit_core.errors import InvalidInputError, QbitCoreError
from qbit_core.qbit.fields import (
    get_field_as_string,
    get_field_as_tag_list,
    get_optional_bool,
    get_optional_float,
    get_optional_int,
)
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

# qBittorrent encodes "unset" as a negative value across its numeric
# fields (`-1` uncomputed, `-2` "use the global setting"). None of the
# V1 filter sources was ever observed carrying one, so this is a guard
# rather than a correction: a real size, ratio or byte count is never
# negative, so nothing legitimate is lost by refusing to compare them.
_UNSET_NUMERIC = (-1, -2)

# Timestamp fields additionally treat `0` as unknown. No torrent is
# really added -- or last active -- at the Unix epoch, so a zero means
# "not recorded", and reading it as 1970 would make `--older-than` or
# `--inactive-for` match it: the dangerous direction on a destructive
# command.
_UNSET_TIMESTAMP = (0, -1, -2)


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
class Range[T: (int, float, datetime)]:
    """An inclusive bound on both sides; `None` means unconstrained.

    One type for every bounded family -- ratio, size, progress,
    transferred bytes, seeding time, added date -- so adding a bounded
    filter never means inventing a comparison rule.
    """

    min: T | None = None
    max: T | None = None

    @property
    def is_unset(self) -> bool:
        """Return whether this range constrains nothing."""
        return self.min is None and self.max is None

    @property
    def is_impossible(self) -> bool:
        """Return whether `min > max` makes this range unsatisfiable."""
        return (
            self.min is not None
            and self.max is not None
            and (self.min > self.max)
        )

    def contains(self, value: T | None) -> bool:
        """Return whether a known value falls inside the bounds.

        `None` means the source field was absent, null, unparsable or a
        qBittorrent "unset" sentinel. It never satisfies a bound that
        was actually posed: coercing an unknown size to `0` would make
        `--size-max` match torrents nobody meant to target. An unset
        range still matches everything, unknown value included -- no
        bound was asked for.
        """
        if self.is_unset:
            return True
        if value is None:
            return False
        if self.min is not None and value < self.min:
            return False
        if self.max is not None and value > self.max:
            return False
        return True


@dataclass(frozen=True)
class TagCriterion:
    """Set membership over a torrent's tags, in the three useful shapes.

    A torrent carries several tags, so "has any of" and "has all of" are
    genuinely different questions -- unlike category or state, which are
    single-valued and need only `any_of`. Expressing both with three
    explicit fields keeps the model free of a boolean expression tree.

    Comparison is case-insensitive, matching how categories are already
    compared.
    """

    any_of: tuple[str, ...] = ()
    all_of: tuple[str, ...] = ()
    none_of: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Return whether this criterion constrains nothing."""
        return not (self.any_of or self.all_of or self.none_of)

    def matches(self, tags: Collection[str]) -> bool:
        """Evaluate the criterion against a torrent's tags."""
        present = {tag.casefold() for tag in tags}

        if self.any_of and not any(
            tag.casefold() in present for tag in self.any_of
        ):
            return False
        if not all(tag.casefold() in present for tag in self.all_of):
            return False
        if any(tag.casefold() in present for tag in self.none_of):
            return False
        return True


@dataclass(frozen=True)
class TorrentFilter:
    """Structured, Typer/Rich-free torrent selection criteria.

    Composition: repeated values inside one field combine with OR,
    different fields combine with AND, and every `*_excluded` field is
    an AND NOT applied after inclusion. Bounded fields are `Range`
    objects, inclusive on both sides.

    `tracker`, when set, is always pre-normalized to `host` or
    `host:port` -- never a full URL, so a passkey can never reach this
    model. `added` holds *resolved* datetimes: a relative duration like
    `90d` is user intent, translated at the CLI boundary, so the filter
    itself stays comparable and serializable.
    """

    # --- identity and organisation
    categories: tuple[str, ...] = ()
    categories_excluded: tuple[str, ...] = ()
    tags: TagCriterion = TagCriterion()
    save_path_prefixes: tuple[str, ...] = ()
    save_paths_excluded: tuple[str, ...] = ()
    name_contains: tuple[str, ...] = ()
    name_excluded: tuple[str, ...] = ()
    name_regex: str | None = None

    # --- state
    states: tuple[TorrentStateGroup, ...] = ()
    states_excluded: tuple[TorrentStateGroup, ...] = ()
    completed: bool | None = None
    active: bool | None = None
    stalled: bool | None = None
    errored: bool | None = None
    private: bool | None = None

    # --- measures
    ratio: Range[float] = Range()
    size: Range[int] = Range()
    progress: Range[float] = Range()
    uploaded: Range[int] = Range()
    seeding_time: Range[int] = Range()
    added: Range[datetime] = Range()
    completed_at: Range[datetime] = Range()
    last_activity: Range[datetime] = Range()

    # --- trackers
    trackers: tuple[str, ...] = ()
    trackers_excluded: tuple[str, ...] = ()
    has_trackers: bool | None = None

    @property
    def is_empty(self) -> bool:
        """Return whether this filter would select every torrent.

        Compared against a default instance rather than enumerated
        field by field: an enumeration silently stops being true the
        day a field is added and forgotten, and "no filter" is exactly
        what must never be mistaken for "every torrent" on a mutation.
        """
        return self == TorrentFilter()

    @property
    def requires_inspection(self) -> bool:
        """Return whether resolving this filter needs the INSPECT stage.

        `has_trackers` is deliberately absent: `trackers_count` comes
        from the same bulk listing as everything else, so asking "does
        this torrent have any tracker at all" costs nothing.
        """
        return bool(self.trackers or self.trackers_excluded)


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
    def requires_inspection(self) -> bool:
        """Return whether resolving this request needs an INSPECT pass."""
        return self.filters.requires_inspection


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


_RANGE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("ratio", "--ratio"),
    ("size", "--size"),
    ("progress", "--progress"),
    ("uploaded", "--uploaded"),
    ("seeding_time", "--seeded-for"),
)

_EXCLUSION_OPTIONS: tuple[tuple[str, str, str, str], ...] = (
    ("categories", "categories_excluded", "--category", "--exclude-category"),
    ("states", "states_excluded", "--state", "--exclude-state"),
    (
        "save_path_prefixes",
        "save_paths_excluded",
        "--save-path",
        "--exclude-save-path",
    ),
    ("name_contains", "name_excluded", "--name-contains", "--exclude-name"),
)


def validate_torrent_filter(filters: TorrentFilter) -> None:
    """Reject a filter that cannot match anything, before any API call.

    Only *provably* empty combinations are refused. A combination that
    merely happens to match nothing on this instance (`--state seeding
    --incomplete`) is legitimate and left to return zero results: the
    difference is whether the contradiction is in the request itself or
    in the data.
    """
    for attribute, option in _RANGE_OPTIONS:
        bounds: Range[Any] = getattr(filters, attribute)
        if bounds.is_impossible:
            raise InvalidInputError(
                f"{option}-min ({bounds.min}) is greater than {option}-max "
                f"({bounds.max}); no torrent can satisfy both."
            )

    if filters.added.is_impossible:
        raise InvalidInputError(
            "--newer-than and --older-than describe an empty time window; "
            "no torrent can satisfy both."
        )

    if filters.last_activity.is_impossible:
        raise InvalidInputError(
            "--inactive-for and --active-within describe an empty time "
            "window; no torrent can satisfy both."
        )

    if filters.completed_at.is_impossible:
        raise InvalidInputError(
            "--completed-before and --completed-within describe an empty "
            "time window; no torrent can satisfy both."
        )

    for included, excluded, option, exclude_option in _EXCLUSION_OPTIONS:
        _reject_overlap(
            getattr(filters, included),
            getattr(filters, excluded),
            option=option,
            exclude_option=exclude_option,
        )

    _reject_overlap(
        filters.tags.any_of + filters.tags.all_of,
        filters.tags.none_of,
        option="--tag/--tag-all",
        exclude_option="--exclude-tag",
    )

    if filters.trackers:
        _reject_overlap(
            filters.trackers,
            filters.trackers_excluded,
            option="--tracker",
            exclude_option="--exclude-tracker",
        )
        if filters.has_trackers is False:
            raise InvalidInputError(
                "--no-tracker cannot be combined with --tracker: a torrent "
                "cannot both have no tracker and announce to one."
            )

    if filters.has_trackers is False and filters.trackers_excluded:
        raise InvalidInputError(
            "--exclude-tracker is redundant with --no-tracker: a torrent "
            "with no tracker announces to none of them."
        )

    if filters.name_regex is not None:
        try:
            re.compile(filters.name_regex)
        except re.error as error:
            raise InvalidInputError(
                f"--name-regex is not a valid regular expression: {error}."
            ) from None


def _reject_overlap(
    included: Collection[str],
    excluded: Collection[str],
    *,
    option: str,
    exclude_option: str,
) -> None:
    """Reject a value asked for and excluded at once.

    Never a deliberate request, and silently returning zero results
    would hide the typo behind a plausible-looking empty selection.
    """
    overlap = sorted(
        {value.casefold() for value in included}
        & {value.casefold() for value in excluded}
    )
    if overlap:
        raise InvalidInputError(
            f"'{overlap[0]}' is required by {option} and refused by "
            f"{exclude_option}; no torrent can satisfy both."
        )


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

    Excludes `tracker`/`trackers_excluded`, which need a per-torrent
    lookup -- callers that set them must narrow the result after the
    INSPECT stage.
    """
    return (
        _matches_organisation(torrent, filters)
        and _matches_name(torrent, filters)
        and _matches_state(torrent, filters)
        and _matches_measures(torrent, filters)
    )


def _matches_organisation(torrent: Any, filters: TorrentFilter) -> bool:
    """Category, tags, save path and their exclusions."""
    if filters.categories or filters.categories_excluded:
        torrent_category = get_field_as_string(torrent, "category")
        if filters.categories and not any(
            category_matches(torrent_category, category)
            for category in filters.categories
        ):
            return False
        if any(
            category_matches(torrent_category, category)
            for category in filters.categories_excluded
        ):
            return False

    if not filters.tags.is_empty and not filters.tags.matches(
        get_field_as_tag_list(torrent)
    ):
        return False

    if filters.save_path_prefixes or filters.save_paths_excluded:
        save_path = get_field_as_string(torrent, "save_path")
        if filters.save_path_prefixes and not any(
            _is_under_path(save_path, prefix)
            for prefix in filters.save_path_prefixes
        ):
            return False
        if any(
            _is_under_path(save_path, prefix)
            for prefix in filters.save_paths_excluded
        ):
            return False

    return True


def _matches_name(torrent: Any, filters: TorrentFilter) -> bool:
    """Substring and regular-expression matching over the torrent name.

    Substrings are case-insensitive (a torrent name's capitalization is
    not something an operator should have to reproduce); the regular
    expression is not, so a pattern stays exactly what the user wrote --
    inline `(?i)` remains available.
    """
    if not (
        filters.name_contains or filters.name_excluded or filters.name_regex
    ):
        return True

    name = get_field_as_string(torrent, "name")
    folded = name.casefold()

    if filters.name_contains and not any(
        needle.casefold() in folded for needle in filters.name_contains
    ):
        return False
    if any(needle.casefold() in folded for needle in filters.name_excluded):
        return False
    if filters.name_regex is not None and (
        re.search(filters.name_regex, name) is None
    ):
        return False

    return True


def _matches_state(torrent: Any, filters: TorrentFilter) -> bool:
    """State group, its exclusions, and the derived state aliases.

    `completed`/`active`/`stalled`/`errored` keep their long-standing
    reading, coercion included: `--active` means "not stopped", not "is
    transferring", and `--completed` reads a missing progress as `0`.
    Only the new bounded predicates apply the unknown-never-matches
    rule -- changing these would be a silent contract change.
    """
    state = get_field_as_string(torrent, "state")
    group = classify_torrent_state(state)

    if filters.states and group not in filters.states:
        return False
    if filters.states_excluded and group in filters.states_excluded:
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

    if filters.stalled is not None and (group == "stalled") != filters.stalled:
        return False

    if filters.errored is not None and (group == "errored") != filters.errored:
        return False

    if filters.private is not None and (
        get_optional_bool(torrent, "private") is not filters.private
    ):
        return False

    return True


def _matches_measures(torrent: Any, filters: TorrentFilter) -> bool:
    """Bounded numeric predicates and the cheap tracker-presence check.

    Every value is read through the optional accessors, so an absent,
    null, unparsable or sentinel field reads as unknown and satisfies no
    bound (decision M1).
    """
    if not filters.ratio.contains(
        get_optional_float(torrent, "ratio", sentinels=_UNSET_NUMERIC)
    ):
        return False

    if not filters.size.contains(
        get_optional_int(torrent, "size", sentinels=_UNSET_NUMERIC)
    ):
        return False

    if not filters.progress.contains(
        get_optional_float(torrent, "progress", sentinels=_UNSET_NUMERIC)
    ):
        return False

    if not filters.uploaded.contains(
        get_optional_int(torrent, "uploaded", sentinels=_UNSET_NUMERIC)
    ):
        return False

    if not filters.seeding_time.contains(
        get_optional_int(torrent, "seeding_time", sentinels=_UNSET_NUMERIC)
    ):
        return False

    if not filters.added.is_unset:
        added_on = get_optional_int(
            torrent, "added_on", sentinels=_UNSET_TIMESTAMP
        )
        added_at = (
            None
            if added_on is None
            else datetime.fromtimestamp(added_on, tz=UTC)
        )
        if not filters.added.contains(added_at):
            return False

    if not filters.completed_at.is_unset:
        # `-1` is qBittorrent's "never completed" marker, observed on
        # every tested version -- so an unfinished torrent is unknown
        # here and matches no completion bound, rather than pretending
        # to have finished at the epoch.
        completed_on = get_optional_int(
            torrent, "completion_on", sentinels=_UNSET_TIMESTAMP
        )
        finished_at = (
            None
            if completed_on is None
            else datetime.fromtimestamp(completed_on, tz=UTC)
        )
        if not filters.completed_at.contains(finished_at):
            return False

    if not filters.last_activity.is_unset:
        last_activity = get_optional_int(
            torrent, "last_activity", sentinels=_UNSET_TIMESTAMP
        )
        moment = (
            None
            if last_activity is None
            else datetime.fromtimestamp(last_activity, tz=UTC)
        )
        if not filters.last_activity.contains(moment):
            return False

    if filters.has_trackers is not None:
        count = get_optional_int(
            torrent, "trackers_count", sentinels=_UNSET_NUMERIC
        )
        if count is None or (count > 0) != filters.has_trackers:
            return False

    return True


def _is_under_path(save_path: str, prefix: str) -> bool:
    """Return whether `save_path` is `prefix` or lives beneath it.

    Case-sensitive, and no normalization beyond a trailing separator:
    these are real POSIX paths, and rewriting them would make the filter
    disagree with what qBittorrent reports. The separator check is what
    stops `/downloads` from matching `/downloads-old`.
    """
    if prefix == "":
        return False
    boundary = prefix.rstrip("/")
    return save_path == prefix or save_path.startswith(f"{boundary}/")


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
    """Build a stable, JSON-safe representation of a torrent filter.

    `tracker` is kept for compatibility as a *projection* of `trackers`,
    which is authoritative: the single requested host when exactly one
    was given, `null` otherwise. It is deliberately `null` rather than
    the first of several -- a script reading the old key then sees
    either the value it expected or nothing, never a partial answer
    that looks like a complete one.

    Durations serialize as whole seconds and sizes as bytes, matching
    how the filter stores them; `added` serializes as resolved ISO-8601
    instants, because a relative `90d` is user intent that belongs to
    the layer holding the request, not to the resolved filter.
    """
    return {
        "categories": list(filters.categories),
        "categories_excluded": list(filters.categories_excluded),
        "tags": {
            "any_of": list(filters.tags.any_of),
            "all_of": list(filters.tags.all_of),
            "none_of": list(filters.tags.none_of),
        },
        "save_paths": list(filters.save_path_prefixes),
        "save_paths_excluded": list(filters.save_paths_excluded),
        "name_contains": list(filters.name_contains),
        "name_excluded": list(filters.name_excluded),
        "name_regex": filters.name_regex,
        "states": list(filters.states),
        "states_excluded": list(filters.states_excluded),
        "completed": filters.completed,
        "active": filters.active,
        "stalled": filters.stalled,
        "errored": filters.errored,
        "private": filters.private,
        "ratio": _range_to_dict(filters.ratio),
        "size": _range_to_dict(filters.size),
        "progress": _range_to_dict(filters.progress),
        "uploaded": _range_to_dict(filters.uploaded),
        "seeding_time": _range_to_dict(filters.seeding_time),
        "added": _range_to_dict(filters.added),
        "completed_at": _range_to_dict(filters.completed_at),
        "last_activity": _range_to_dict(filters.last_activity),
        "tracker": filters.trackers[0] if len(filters.trackers) == 1 else None,
        "trackers": list(filters.trackers),
        "trackers_excluded": list(filters.trackers_excluded),
        "has_trackers": filters.has_trackers,
    }


def _range_to_dict(bounds: Range[Any]) -> dict[str, Any]:
    """Serialize one range, rendering datetimes as ISO-8601 instants."""
    return {
        "min": _serializable_bound(bounds.min),
        "max": _serializable_bound(bounds.max),
    }


def _serializable_bound(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def describe_torrent_filter(filters: TorrentFilter) -> str:
    """Build a concise, deterministic human description of a torrent filter.

    Renders only the families actually set, in a fixed order, so the
    same filter always reads the same way. Never renders anything beyond
    what `TorrentFilter` carries -- tracker values are host-only by
    construction, so this can never leak a passkey.

    Bounds render their stored value (bytes, seconds, ISO instants)
    rather than the operator's `10GiB`/`90d` shorthand: this line is an
    echo of what will actually be matched, and the resolved value is the
    one that decides.
    """
    parts: list[str] = []

    _append_values(parts, "category", filters.categories)
    _append_values(parts, "exclude-category", filters.categories_excluded)
    _append_values(parts, "tag", filters.tags.any_of)
    _append_values(parts, "tag-all", filters.tags.all_of)
    _append_values(parts, "exclude-tag", filters.tags.none_of)
    _append_values(parts, "save-path", filters.save_path_prefixes)
    _append_values(parts, "exclude-save-path", filters.save_paths_excluded)
    _append_values(parts, "name-contains", filters.name_contains)
    _append_values(parts, "exclude-name", filters.name_excluded)
    if filters.name_regex is not None:
        parts.append(f"name-regex={filters.name_regex}")
    _append_values(parts, "state", filters.states)
    _append_values(parts, "exclude-state", filters.states_excluded)

    if filters.completed is not None:
        parts.append("completed" if filters.completed else "incomplete")
    if filters.active is not None:
        parts.append("active" if filters.active else "inactive")
    if filters.stalled is not None:
        parts.append("stalled")
    if filters.errored is not None:
        parts.append("errored")
    if filters.private is not None:
        parts.append("private" if filters.private else "public")

    _append_bounds(parts, "ratio", filters.ratio)
    _append_bounds(parts, "size", filters.size)
    _append_bounds(parts, "progress", filters.progress)
    _append_bounds(parts, "uploaded", filters.uploaded)
    _append_bounds(parts, "seeding-time", filters.seeding_time)
    _append_bounds(parts, "added", filters.added)
    _append_bounds(parts, "completed-at", filters.completed_at)
    _append_bounds(parts, "last-activity", filters.last_activity)

    _append_values(parts, "tracker", filters.trackers)
    _append_values(parts, "exclude-tracker", filters.trackers_excluded)
    if filters.has_trackers is not None:
        parts.append("has-tracker" if filters.has_trackers else "no-tracker")

    return ", ".join(parts) if parts else "none"


def _append_values(parts: list[str], label: str, values: Sequence[str]) -> None:
    """Render one repeatable family as `label=a|b`, or nothing when unset."""
    if values:
        parts.append(f"{label}=" + "|".join(values))


def _append_bounds(parts: list[str], label: str, bounds: Range[Any]) -> None:
    """Render each posed bound separately, so `>=` and `<=` stay explicit."""
    if bounds.min is not None:
        parts.append(f"{label}>={_serializable_bound(bounds.min)}")
    if bounds.max is not None:
        parts.append(f"{label}<={_serializable_bound(bounds.max)}")


def _sorted_by_name(
    snapshots: Sequence[TorrentSnapshot],
) -> tuple[TorrentSnapshot, ...]:
    return tuple(sorted(snapshots, key=lambda item: item.name.casefold()))
