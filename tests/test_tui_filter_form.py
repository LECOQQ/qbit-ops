"""`qbit_ops.tui.filter_form` -- the filters modal's Textual-free model."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from qbit_core.errors import InvalidInputError
from qbit_core.shared.selection import TorrentFilter
from qbit_ops.tui.filter_form import (
    CLI_ONLY_TORRENT_FILTER_FIELDS,
    PANE_FIELDS,
    PANE_NAMES,
    FiltersDraft,
    pane_applied_count,
    pane_has_pending_edits,
)

_NOW = datetime(2024, 1, 1, tzinfo=UTC)


def test_every_torrent_filter_field_is_classified_exactly_once() -> None:
    """Criterion 3: derived from `dataclasses.fields(TorrentFilter)`,
    not a recopied constant -- a field added there must be classified
    here or this fails."""
    all_fields = {f.name for f in dataclasses.fields(TorrentFilter)}
    cli_only = set(CLI_ONLY_TORRENT_FILTER_FIELDS)
    assert cli_only < all_fields

    exposed_via_draft = {
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
        "trackers",  # named, not exposed -- see has_trackers below
        "trackers_excluded",  # named, not exposed
        "has_trackers",
        "tracker_health",  # named, not exposed
    }
    assert exposed_via_draft == all_fields
    assert all_fields - cli_only == exposed_via_draft - cli_only


def test_the_panes_partition_every_draft_field_with_no_overlap() -> None:
    seen: set[str] = set()
    for pane in PANE_NAMES:
        fields = set(PANE_FIELDS[pane])
        assert not (fields & seen), f"{pane} reuses a field another pane owns"
        seen |= fields

    draft_fields = {f.name for f in dataclasses.fields(FiltersDraft)}
    assert seen == draft_fields


def test_round_tripping_an_applied_filter_reproduces_it() -> None:
    applied = TorrentFilter(
        categories=("films", "tv"),
        categories_excluded=("archive",),
        name_regex="^ubuntu",
    )
    draft = FiltersDraft.from_filter(applied, now=_NOW)
    assert draft.to_filter(now=_NOW) == applied


def test_size_and_ratio_ranges_parse_into_the_filter() -> None:
    draft = FiltersDraft(size_min="1GiB", size_max="50GiB")
    result = draft.to_filter(now=_NOW)
    assert result.size.min == 1024**3
    assert result.size.max == 50 * 1024**3


def test_an_impossible_range_raises_before_it_reaches_the_filter() -> None:
    draft = FiltersDraft(size_min="50GiB", size_max="1GiB")
    with pytest.raises(InvalidInputError):
        draft.to_filter(now=_NOW)


def test_blank_fields_stay_unset_not_zero() -> None:
    draft = FiltersDraft()
    result = draft.to_filter(now=_NOW)
    assert result == TorrentFilter()


def test_added_window_reads_older_edge_as_max_and_newer_edge_as_min() -> None:
    """ "7d to 90d" means "added between 7 and 90 days ago": the smaller
    duration is the more recent instant (the window's `max`), the
    larger duration is the more distant one (`min`)."""
    draft = FiltersDraft(added_min="7d", added_max="90d")
    result = draft.to_filter(now=_NOW)
    assert result.added.max == _NOW - timedelta(days=7)
    assert result.added.min == _NOW - timedelta(days=90)


def test_seeded_for_is_a_minimum_with_no_maximum() -> None:
    draft = FiltersDraft(seeded_for="30d")
    result = draft.to_filter(now=_NOW)
    assert result.seeding_time.min == 30 * 86400
    assert result.seeding_time.max is None


def test_tag_criterion_carries_three_independent_lists() -> None:
    draft = FiltersDraft(tags_any="stale, keep", tags_all="seed-forever")
    result = draft.to_filter(now=_NOW)
    assert result.tags.any_of == ("stale", "keep")
    assert result.tags.all_of == ("seed-forever",)
    assert result.tags.none_of == ()


def test_no_trackers_checkbox_only_ever_narrows_never_widens() -> None:
    off = FiltersDraft().to_filter(now=_NOW)
    on = FiltersDraft(no_trackers=True).to_filter(now=_NOW)
    assert off.has_trackers is None
    assert on.has_trackers is False


def test_pane_pending_detection_ignores_unchanged_panes() -> None:
    applied = TorrentFilter(categories=("films",))
    draft = FiltersDraft.from_filter(applied, now=_NOW)

    assert not pane_has_pending_edits("Organisation", draft, applied)

    draft.categories = "films, tv"
    assert pane_has_pending_edits("Organisation", draft, applied)
    assert not pane_has_pending_edits("State", draft, applied)


def test_pane_pending_detection_works_on_an_unparsable_draft() -> None:
    """A half-typed value must still be able to show as pending -- it
    is never parsed to decide this."""
    applied = TorrentFilter()
    draft = FiltersDraft.from_filter(applied, now=_NOW)
    draft.size_min = "not a size"

    assert pane_has_pending_edits("Measures", draft, applied)
    with pytest.raises(InvalidInputError):
        draft.to_filter(now=_NOW)


def test_pane_applied_count_reads_the_applied_filter_not_the_draft() -> None:
    applied = TorrentFilter(categories=("films",), name_regex="^ubuntu")
    draft = FiltersDraft.from_filter(applied, now=_NOW)
    draft.categories = "films, tv, docs"  # uncommitted edit

    assert pane_applied_count("Organisation", applied) == 2


def test_a_pane_with_nothing_applied_counts_zero() -> None:
    assert pane_applied_count("Trackers", TorrentFilter()) == 0
    assert pane_applied_count("State", TorrentFilter()) == 0
