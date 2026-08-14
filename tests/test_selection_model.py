"""Test the composable selection model (`Range`, `TagCriterion`, filter
validation) defined in `qbit_core.shared.selection`.

These types decide what a destructive command targets, so the tests
concentrate on the two properties that make a wrong selection silent:
an unknown value must never satisfy a bound, and a filter must never be
mistaken for "no filter".
"""

from dataclasses import fields
from datetime import datetime, timedelta
from typing import Any

import pytest

from qbit_core.errors import InvalidInputError
from qbit_core.shared.selection import (
    Range,
    SelectionRequest,
    TagCriterion,
    TorrentFilter,
    validate_torrent_filter,
    without_inspection_criteria,
)

# --- Range ------------------------------------------------------------------


def test_unset_range_matches_everything_including_unknown() -> None:
    """No bound was asked for, so nothing is excluded -- not even a
    torrent whose field qBittorrent never sent."""
    unset: Range[int] = Range()

    assert unset.is_unset
    assert unset.contains(0)
    assert unset.contains(10**12)
    assert unset.contains(None)


@pytest.mark.parametrize(
    ("bounds", "value", "expected"),
    [
        (Range(min=1), 1, True),
        (Range(min=1), 0, False),
        (Range(max=4), 4, True),
        (Range(max=4), 5, False),
        (Range(min=1, max=4), 1, True),
        (Range(min=1, max=4), 4, True),
        (Range(min=1, max=4), 0, False),
        (Range(min=1, max=4), 5, False),
    ],
)
def test_bounds_are_inclusive_on_both_sides(
    bounds: Range[int], value: int, expected: bool
) -> None:
    assert bounds.contains(value) is expected


@pytest.mark.parametrize(
    "bounds",
    [Range(min=1), Range(max=4), Range(min=1, max=4)],
)
def test_unknown_never_satisfies_a_bound_that_was_posed(
    bounds: Range[int],
) -> None:
    """Decision M1. Coercing an unknown size to `0` would make
    `--size-max` match torrents nobody meant to target -- and a max
    bound is where the coercion turns into a false positive."""
    assert bounds.contains(None) is False


def test_a_max_bound_does_not_swallow_unknown_values() -> None:
    """The specific asymmetry that motivated M1: with the coercing
    accessors, a missing size read as `0` and satisfied any max."""
    assert Range(max=1_000_000).contains(None) is False
    assert Range(max=1_000_000).contains(0) is True


def test_range_detects_an_impossible_window() -> None:
    assert Range(min=4, max=1).is_impossible
    assert not Range(min=1, max=4).is_impossible
    assert not Range(min=1, max=1).is_impossible
    assert not Range(min=4).is_impossible


def test_range_works_over_datetimes() -> None:
    now = datetime(2026, 8, 10, 12, 0, 0)
    window: Range[datetime] = Range(min=now - timedelta(days=7), max=now)

    assert window.contains(now - timedelta(days=1))
    assert not window.contains(now - timedelta(days=8))
    assert not window.contains(None)


# --- TagCriterion -----------------------------------------------------------


def test_empty_criterion_matches_any_tag_set() -> None:
    criterion = TagCriterion()

    assert criterion.is_empty
    assert criterion.matches([])
    assert criterion.matches(["anything"])


def test_any_of_is_or() -> None:
    criterion = TagCriterion(any_of=("archive", "long-term"))

    assert criterion.matches(["archive"])
    assert criterion.matches(["long-term"])
    assert criterion.matches(["archive", "unrelated"])
    assert not criterion.matches(["unrelated"])
    assert not criterion.matches([])


def test_all_of_is_and() -> None:
    """The distinction that justifies TagCriterion existing: a torrent
    carries several tags, so "any" and "all" are different questions."""
    criterion = TagCriterion(all_of=("archive", "long-term"))

    assert criterion.matches(["archive", "long-term"])
    assert criterion.matches(["archive", "long-term", "extra"])
    assert not criterion.matches(["archive"])
    assert not criterion.matches([])


def test_none_of_is_and_not() -> None:
    criterion = TagCriterion(none_of=("protected",))

    assert criterion.matches(["archive"])
    assert not criterion.matches(["protected"])
    assert not criterion.matches(["archive", "protected"])


def test_the_three_shapes_combine() -> None:
    criterion = TagCriterion(
        any_of=("sonarr", "radarr"),
        all_of=("verified",),
        none_of=("protected",),
    )

    assert criterion.matches(["sonarr", "verified"])
    assert not criterion.matches(["sonarr"])
    assert not criterion.matches(["sonarr", "verified", "protected"])


def test_tag_matching_is_case_insensitive() -> None:
    """Matches how categories are already compared."""
    assert TagCriterion(any_of=("Archive",)).matches(["archive"])
    assert TagCriterion(none_of=("PROTECTED",)).matches(["keep"])
    assert not TagCriterion(none_of=("PROTECTED",)).matches(["protected"])


# --- TorrentFilter.is_empty -------------------------------------------------


def test_default_filter_is_empty() -> None:
    assert TorrentFilter().is_empty


@pytest.mark.parametrize(
    "filters",
    [
        TorrentFilter(categories=("movies",)),
        TorrentFilter(categories_excluded=("movies",)),
        TorrentFilter(tags=TagCriterion(any_of=("keep",))),
        TorrentFilter(tags=TagCriterion(all_of=("keep",))),
        TorrentFilter(tags=TagCriterion(none_of=("keep",))),
        TorrentFilter(save_path_prefixes=("/downloads",)),
        TorrentFilter(save_paths_excluded=("/downloads",)),
        TorrentFilter(name_contains=("debian",)),
        TorrentFilter(name_excluded=("debian",)),
        TorrentFilter(name_regex="^deb"),
        TorrentFilter(states=("seeding",)),
        TorrentFilter(states_excluded=("seeding",)),
        TorrentFilter(completed=True),
        TorrentFilter(active=False),
        TorrentFilter(stalled=True),
        TorrentFilter(errored=True),
        TorrentFilter(private=True),
        TorrentFilter(ratio=Range(min=1.0)),
        TorrentFilter(size=Range(max=1024)),
        TorrentFilter(progress=Range(min=0.99)),
        TorrentFilter(uploaded=Range(min=1024)),
        TorrentFilter(seeding_time=Range(min=60)),
        TorrentFilter(added=Range(max=datetime(2026, 1, 1))),
        TorrentFilter(completed_at=Range(max=datetime(2026, 1, 1))),
        TorrentFilter(last_activity=Range(max=datetime(2026, 1, 1))),
        TorrentFilter(trackers=("tracker.example",)),
        TorrentFilter(trackers_excluded=("tracker.example",)),
        TorrentFilter(has_trackers=False),
        TorrentFilter(tracker_health=("critical",)),
    ],
)
def test_every_single_field_makes_a_filter_non_empty(
    filters: TorrentFilter,
) -> None:
    """`is_empty` is what stands between "no selector" and "every
    torrent" on a mutation. It compares against a default instance
    rather than enumerating fields, so this holds for any field added
    later -- this test proves it holds for every field present today.
    """
    assert not filters.is_empty


def test_is_empty_covers_every_declared_field() -> None:
    """Guard the guard: if a field is added and this file is not
    updated, the count mismatch says so."""
    from dataclasses import fields

    covered = {
        "categories",
        "categories_excluded",
        "tags",
        "save_path_prefixes",
        "save_paths_excluded",
        "name_contains",
        "name_excluded",
        "name_regex",
        "states",
        "states_excluded",
        "completed",
        "active",
        "stalled",
        "errored",
        "private",
        "ratio",
        "size",
        "progress",
        "uploaded",
        "seeding_time",
        "added",
        "completed_at",
        "last_activity",
        "trackers",
        "trackers_excluded",
        "has_trackers",
        "tracker_health",
    }

    assert {f.name for f in fields(TorrentFilter)} == covered


# --- requires_inspection ----------------------------------------------------


def test_only_tracker_derived_filters_require_inspection() -> None:
    assert not TorrentFilter().requires_inspection
    assert TorrentFilter(trackers=("tracker.example",)).requires_inspection
    assert TorrentFilter(
        trackers_excluded=("tracker.example",)
    ).requires_inspection
    assert TorrentFilter(tracker_health=("critical",)).requires_inspection


def test_no_tracker_is_cheap_because_trackers_count_is_in_the_listing() -> None:
    """`trackers_count` ships with `torrents_info()` on every tested
    version, so asking "does it have any tracker" costs no extra call."""
    assert not TorrentFilter(has_trackers=False).requires_inspection
    assert not TorrentFilter(has_trackers=True).requires_inspection


# --- the cheap-filter teardown ----------------------------------------------

# One non-default value per declared field, so the teardown can be
# exercised field by field instead of on a hand-picked subset.
_NON_DEFAULT_VALUE_BY_FIELD: dict[str, Any] = {
    "categories": ("movies",),
    "categories_excluded": ("movies",),
    "tags": TagCriterion(any_of=("keep",)),
    "save_path_prefixes": ("/downloads",),
    "save_paths_excluded": ("/downloads",),
    "name_contains": ("debian",),
    "name_excluded": ("debian",),
    "name_regex": "^deb",
    "states": ("seeding",),
    "states_excluded": ("seeding",),
    "completed": True,
    "active": False,
    "stalled": True,
    "errored": True,
    "private": True,
    "ratio": Range(min=1.0),
    "size": Range(max=1024),
    "progress": Range(min=0.99),
    "uploaded": Range(min=1024),
    "seeding_time": Range(min=60),
    "added": Range(max=datetime(2026, 1, 1)),
    "completed_at": Range(max=datetime(2026, 1, 1)),
    "last_activity": Range(max=datetime(2026, 1, 1)),
    "trackers": ("tracker.example",),
    "trackers_excluded": ("tracker.example",),
    "has_trackers": False,
    "tracker_health": ("critical",),
}


def test_every_declared_field_has_a_non_default_sample() -> None:
    """Guard the guard: a field added without a sample here would make
    the teardown tests below silently stop covering it."""
    defaults = TorrentFilter()

    assert set(_NON_DEFAULT_VALUE_BY_FIELD) == {
        declared.name for declared in fields(TorrentFilter)
    }
    for name, value in _NON_DEFAULT_VALUE_BY_FIELD.items():
        assert value != getattr(defaults, name)


@pytest.mark.parametrize("field_name", sorted(_NON_DEFAULT_VALUE_BY_FIELD))
def test_the_cheap_filter_never_requires_inspection(field_name: str) -> None:
    """Whichever field carries a value, the cheap counterpart must be
    resolvable from `torrents_info()` alone.

    A field the predicate calls expensive but the teardown forgets to
    clear is not a cosmetic mismatch: `select_torrents_from_items`
    raises on it, and `collect_tracker_status` pays a second INSPECT
    pass over the whole selection.
    """
    filters = TorrentFilter(
        **{field_name: _NON_DEFAULT_VALUE_BY_FIELD[field_name]}
    )

    assert not without_inspection_criteria(filters).requires_inspection


def test_the_cheap_filter_preserves_every_criterion_it_can_resolve() -> None:
    """The teardown drops the expensive criteria and nothing else."""
    filters = TorrentFilter(**_NON_DEFAULT_VALUE_BY_FIELD)
    cheap = without_inspection_criteria(filters)

    assert not cheap.requires_inspection
    for name, value in _NON_DEFAULT_VALUE_BY_FIELD.items():
        if TorrentFilter(**{name: value}).requires_inspection:
            continue
        assert getattr(cheap, name) == value


# --- has_selector -----------------------------------------------------------


def test_a_bare_request_narrows_nothing() -> None:
    assert not SelectionRequest().has_selector


@pytest.mark.parametrize(
    "request_",
    [
        SelectionRequest(torrent_hash="abc123"),
        SelectionRequest(select_all=True),
        SelectionRequest(filters=TorrentFilter(categories=("movies",))),
        SelectionRequest(filters=TorrentFilter(ratio=Range(min=1.0))),
    ],
)
def test_hash_all_and_any_filter_all_count_as_selectors(
    request_: SelectionRequest,
) -> None:
    """`--all` included: it names the whole library, but it is an
    explicit request about the torrents *present*, so a reader must be
    able to treat it as a narrowing."""
    assert request_.has_selector


# --- validation (docs/SELECTION.md section E) -------------------------------


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (TorrentFilter(ratio=Range(min=4.0, max=1.0)), "--ratio"),
        (TorrentFilter(size=Range(min=100, max=10)), "--size"),
        (TorrentFilter(progress=Range(min=0.9, max=0.1)), "--progress"),
        (TorrentFilter(uploaded=Range(min=100, max=10)), "--uploaded"),
        (TorrentFilter(seeding_time=Range(min=100, max=10)), "--seeded-for"),
    ],
)
def test_an_inverted_range_is_rejected(
    filters: TorrentFilter, expected: str
) -> None:
    with pytest.raises(InvalidInputError, match=expected):
        validate_torrent_filter(filters)


def test_an_empty_time_window_is_rejected() -> None:
    filters = TorrentFilter(
        added=Range(min=datetime(2026, 8, 1), max=datetime(2026, 7, 1))
    )

    with pytest.raises(InvalidInputError, match="empty time window"):
        validate_torrent_filter(filters)


@pytest.mark.parametrize(
    "filters",
    [
        TorrentFilter(categories=("movies",), categories_excluded=("movies",)),
        TorrentFilter(states=("seeding",), states_excluded=("seeding",)),
        TorrentFilter(
            save_path_prefixes=("/data",), save_paths_excluded=("/data",)
        ),
        TorrentFilter(name_contains=("deb",), name_excluded=("deb",)),
        TorrentFilter(tags=TagCriterion(any_of=("keep",), none_of=("keep",))),
        TorrentFilter(tags=TagCriterion(all_of=("keep",), none_of=("keep",))),
        TorrentFilter(
            trackers=("tracker.example",),
            trackers_excluded=("tracker.example",),
        ),
    ],
)
def test_requiring_and_refusing_the_same_value_is_rejected(
    filters: TorrentFilter,
) -> None:
    """Never a deliberate request; returning zero results silently would
    hide the typo behind a plausible-looking empty selection."""
    with pytest.raises(InvalidInputError, match="can satisfy both"):
        validate_torrent_filter(filters)


def test_overlap_detection_is_case_insensitive() -> None:
    filters = TorrentFilter(
        categories=("Movies",), categories_excluded=("movies",)
    )

    with pytest.raises(InvalidInputError):
        validate_torrent_filter(filters)


def test_no_tracker_conflicts_with_a_tracker_host() -> None:
    filters = TorrentFilter(trackers=("tracker.example",), has_trackers=False)

    with pytest.raises(InvalidInputError, match="--no-tracker"):
        validate_torrent_filter(filters)


def test_no_tracker_is_redundant_with_an_excluded_tracker() -> None:
    filters = TorrentFilter(
        has_trackers=False, trackers_excluded=("tracker.example",)
    )

    with pytest.raises(InvalidInputError, match="redundant"):
        validate_torrent_filter(filters)


def test_an_invalid_regex_is_rejected_before_any_api_call() -> None:
    with pytest.raises(InvalidInputError, match="not a valid regular"):
        validate_torrent_filter(TorrentFilter(name_regex="a["))


def test_a_valid_regex_passes() -> None:
    validate_torrent_filter(TorrentFilter(name_regex=r"^S\d+E\d+"))


@pytest.mark.parametrize(
    "filters",
    [
        TorrentFilter(),
        TorrentFilter(states=("seeding",), completed=False),
        TorrentFilter(categories=("a",), categories_excluded=("b",)),
        TorrentFilter(ratio=Range(min=1.0, max=1.0)),
        TorrentFilter(save_path_prefixes=("/a", "/b")),
        TorrentFilter(tags=TagCriterion(any_of=("a",), all_of=("b",))),
    ],
)
def test_combinations_that_are_merely_narrow_are_accepted(
    filters: TorrentFilter,
) -> None:
    """Only provably empty requests are refused. A combination that
    happens to match nothing on this instance is legitimate -- the
    contradiction must be in the request, not in the data."""
    validate_torrent_filter(filters)
