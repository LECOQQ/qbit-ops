"""Test the cheap predicates (`matches_cheap_filters`).

One test class of concerns per filter family, plus the property that
motivated the whole model: a field qBittorrent did not send is unknown,
and unknown satisfies no bound -- so a bounded filter can never widen a
destructive selection by accident.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from qbit_core.shared.selection import (
    Range,
    TagCriterion,
    TorrentFilter,
    matches_cheap_filters,
)
from qbit_core.shared.torrent_states import build_torrent_snapshot


def _torrent(**overrides: Any) -> dict[str, Any]:
    """Build a raw `torrents_info()`-shaped item with sane defaults."""
    defaults: dict[str, Any] = {
        "hash": "a" * 40,
        "name": "Debian 12 ISO",
        "state": "uploading",
        "category": "linux",
        "tags": "archive, verified",
        "save_path": "/downloads/linux",
        "size": 4_700_000_000,
        "progress": 1.0,
        "ratio": 2.5,
        "uploaded": 9_400_000_000,
        "seeding_time": 86_400 * 30,
        "added_on": 1_700_000_000,
        "completion_on": 1_700_000_100,
        "trackers_count": 2,
        "private": True,
    }
    defaults.update(overrides)
    return defaults


def _matches(filters: TorrentFilter, **overrides: Any) -> bool:
    return matches_cheap_filters(_torrent(**overrides), filters)


# --- unknown values never satisfy a bound (decision M1) ---------------------


@pytest.mark.parametrize(
    ("filters", "field"),
    [
        (TorrentFilter(size=Range(max=10**12)), "size"),
        (TorrentFilter(ratio=Range(max=100.0)), "ratio"),
        (TorrentFilter(uploaded=Range(max=10**12)), "uploaded"),
        (TorrentFilter(seeding_time=Range(max=10**9)), "seeding_time"),
        (TorrentFilter(progress=Range(max=1.0)), "progress"),
    ],
)
def test_a_missing_field_never_satisfies_a_max_bound(
    filters: TorrentFilter, field: str
) -> None:
    """The exact false positive the model exists to prevent: with the
    coercing accessors a missing field read as `0` and satisfied every
    max bound, silently widening `torrents delete`."""
    torrent = _torrent()
    del torrent[field]

    assert not matches_cheap_filters(torrent, filters)


@pytest.mark.parametrize(
    "field", ["size", "ratio", "uploaded", "seeding_time", "added_on"]
)
def test_an_explicit_null_is_unknown_too(field: str) -> None:
    filters = TorrentFilter(
        size=Range(max=10**12),
        ratio=Range(max=100.0),
        uploaded=Range(max=10**12),
        seeding_time=Range(max=10**9),
        added=Range(max=datetime.now(tz=UTC)),
    )

    assert not _matches(filters, **{field: None})


def test_a_sentinel_reads_as_unknown() -> None:
    assert not _matches(TorrentFilter(ratio=Range(max=100.0)), ratio=-1)
    assert not _matches(TorrentFilter(size=Range(max=10**12)), size=-1)


def test_a_torrent_with_every_field_still_matches() -> None:
    """Guard against the rule excluding everything: the same bounds that
    reject an unknown value must accept a known one."""
    filters = TorrentFilter(
        size=Range(max=10**12),
        ratio=Range(max=100.0),
        uploaded=Range(max=10**12),
        seeding_time=Range(max=10**9),
    )

    assert _matches(filters)


# --- categories -------------------------------------------------------------


def test_categories_combine_with_or() -> None:
    filters = TorrentFilter(categories=("linux", "movies"))

    assert _matches(filters, category="linux")
    assert _matches(filters, category="movies")
    assert not _matches(filters, category="music")


def test_excluded_category_is_and_not() -> None:
    assert not _matches(
        TorrentFilter(categories_excluded=("linux",)), category="linux"
    )
    assert _matches(
        TorrentFilter(categories_excluded=("linux",)), category="movies"
    )


def test_include_and_exclude_categories_combine() -> None:
    filters = TorrentFilter(
        categories=("linux", "movies"), categories_excluded=("movies",)
    )

    assert _matches(filters, category="linux")
    assert not _matches(filters, category="movies")


def test_uncategorized_token_still_works() -> None:
    assert _matches(TorrentFilter(categories=("uncategorized",)), category="")


# --- tags -------------------------------------------------------------------


def test_tag_any_all_and_none() -> None:
    assert _matches(TorrentFilter(tags=TagCriterion(any_of=("archive",))))
    assert _matches(
        TorrentFilter(tags=TagCriterion(all_of=("archive", "verified")))
    )
    assert not _matches(
        TorrentFilter(tags=TagCriterion(all_of=("archive", "missing")))
    )
    assert not _matches(TorrentFilter(tags=TagCriterion(none_of=("archive",))))


def test_tags_are_matched_case_insensitively() -> None:
    assert _matches(
        TorrentFilter(tags=TagCriterion(any_of=("ARCHIVE",))),
        tags="Archive",
    )


def test_a_torrent_without_tags_matches_only_none_of() -> None:
    assert not _matches(
        TorrentFilter(tags=TagCriterion(any_of=("archive",))), tags=""
    )
    assert _matches(
        TorrentFilter(tags=TagCriterion(none_of=("archive",))), tags=""
    )


# --- save path --------------------------------------------------------------


def test_save_path_matches_the_directory_and_anything_beneath() -> None:
    filters = TorrentFilter(save_path_prefixes=("/downloads",))

    assert _matches(filters, save_path="/downloads")
    assert _matches(filters, save_path="/downloads/linux")
    assert _matches(filters, save_path="/downloads/linux/iso")


def test_save_path_respects_the_separator_boundary() -> None:
    """A plain `startswith` would make `/downloads` match
    `/downloads-old`, silently pulling in a whole other volume."""
    filters = TorrentFilter(save_path_prefixes=("/downloads",))

    assert not _matches(filters, save_path="/downloads-old/linux")
    assert not _matches(filters, save_path="/downloads2")


def test_save_path_is_case_sensitive() -> None:
    """These are real POSIX paths; folding case would make the filter
    disagree with what qBittorrent reports."""
    filters = TorrentFilter(save_path_prefixes=("/Downloads",))

    assert not _matches(filters, save_path="/downloads/linux")
    assert _matches(filters, save_path="/Downloads/linux")


def test_a_trailing_separator_in_the_prefix_is_tolerated() -> None:
    filters = TorrentFilter(save_path_prefixes=("/downloads/",))

    assert _matches(filters, save_path="/downloads/linux")


def test_excluded_save_path_is_and_not() -> None:
    filters = TorrentFilter(save_paths_excluded=("/downloads/linux",))

    assert not _matches(filters, save_path="/downloads/linux/iso")
    assert _matches(filters, save_path="/downloads/movies")


# --- name -------------------------------------------------------------------


def test_name_contains_is_case_insensitive_and_ors() -> None:
    filters = TorrentFilter(name_contains=("DEBIAN", "ubuntu"))

    assert _matches(filters, name="Debian 12 ISO")
    assert _matches(filters, name="Ubuntu 24.04")
    assert not _matches(filters, name="Fedora 40")


def test_excluded_name_is_and_not() -> None:
    filters = TorrentFilter(name_excluded=("sample",))

    assert not _matches(filters, name="Movie.2024.SAMPLE.mkv")
    assert _matches(filters, name="Movie.2024.mkv")


def test_name_regex_is_a_search_not_a_full_match() -> None:
    assert _matches(TorrentFilter(name_regex=r"\d{2} ISO"))
    assert not _matches(TorrentFilter(name_regex=r"^Ubuntu"))


def test_name_regex_stays_case_sensitive_but_inline_flags_work() -> None:
    """The pattern is used exactly as written, so `(?i)` remains the
    explicit way to fold case."""
    assert not _matches(TorrentFilter(name_regex="debian"))
    assert _matches(TorrentFilter(name_regex="(?i)debian"))


def test_name_predicates_combine_with_and() -> None:
    filters = TorrentFilter(
        name_contains=("debian",), name_regex=r"ISO$", name_excluded=("beta",)
    )

    assert _matches(filters, name="Debian 12 ISO")
    assert not _matches(filters, name="Debian 12 beta ISO")
    assert not _matches(filters, name="Debian 12 img")


# --- state ------------------------------------------------------------------


def test_state_group_include_and_exclude() -> None:
    assert _matches(TorrentFilter(states=("seeding",)), state="uploading")
    assert not _matches(
        TorrentFilter(states=("downloading",)), state="uploading"
    )
    assert not _matches(
        TorrentFilter(states_excluded=("seeding",)), state="uploading"
    )


def test_derived_state_aliases_keep_their_existing_reading() -> None:
    """`--active` has always meant "not stopped", never "transferring".
    A stalled seed at 0 B/s is active, and that stays true."""
    assert _matches(TorrentFilter(active=True), state="stalledUP")
    assert not _matches(TorrentFilter(active=True), state="pausedUP")
    assert _matches(TorrentFilter(active=False), state="stoppedUP")


def test_a_missing_progress_matches_neither_completed_nor_incomplete() -> None:
    """Reversed after review: this used to read a missing `progress` as
    `0`, so `--incomplete` selected a torrent whose progress qBittorrent
    never reported. That is M1's exact failure mode -- an unreported
    value widening a destructive selection -- so both directions now
    fail closed."""
    torrent = _torrent()
    del torrent["progress"]

    assert not matches_cheap_filters(torrent, TorrentFilter(completed=False))
    assert not matches_cheap_filters(torrent, TorrentFilter(completed=True))


def test_a_missing_state_matches_no_state_criterion() -> None:
    """`is_stopped_state("")` is False, so an absent state used to read
    as "not stopped" and satisfy `--active`."""
    torrent = _torrent()
    del torrent["state"]

    for filters in (
        TorrentFilter(active=True),
        TorrentFilter(active=False),
        TorrentFilter(stalled=True),
        TorrentFilter(errored=True),
        TorrentFilter(states=("unknown",)),
        TorrentFilter(states=("seeding",)),
    ):
        assert not matches_cheap_filters(torrent, filters), filters


def test_an_explicitly_null_state_or_progress_is_unknown_too() -> None:
    assert not _matches(TorrentFilter(active=True), state=None)
    assert not _matches(TorrentFilter(completed=False), progress=None)


def test_a_blank_state_is_treated_as_absent() -> None:
    """qBittorrent never reports an empty state, so a blank one means
    the field was missing rather than naming a real state."""
    assert not _matches(TorrentFilter(active=True), state="")


def test_an_excluded_state_does_not_fire_on_an_unknown_state() -> None:
    """Exclusions stay "the criterion did not match": an unknown state
    is not proof of the state being refused."""
    torrent = _torrent()
    del torrent["state"]

    assert matches_cheap_filters(
        torrent, TorrentFilter(states_excluded=("seeding",))
    )


def test_a_reported_state_and_progress_still_match_normally() -> None:
    """Guard against the rule excluding everything."""
    assert _matches(TorrentFilter(active=True), state="uploading")
    assert _matches(TorrentFilter(completed=True), progress=1.0)
    assert _matches(TorrentFilter(completed=False), progress=0.4)


def test_private_matches_only_a_real_boolean() -> None:
    assert _matches(TorrentFilter(private=True), private=True)
    assert _matches(TorrentFilter(private=False), private=False)
    assert not _matches(TorrentFilter(private=True), private=False)


def test_private_never_matches_when_the_version_omits_the_field() -> None:
    """`private` ships from qBittorrent 5.0. On an older instance both
    `--private` and `--public` select nothing, which is NO_MATCH -- never
    a mutation that is wider than intended (decision M4)."""
    torrent = _torrent()
    del torrent["private"]

    assert not matches_cheap_filters(torrent, TorrentFilter(private=True))
    assert not matches_cheap_filters(torrent, TorrentFilter(private=False))


# --- measures ---------------------------------------------------------------


def test_ratio_and_size_bounds() -> None:
    assert _matches(TorrentFilter(ratio=Range(min=1.0, max=4.0)), ratio=2.5)
    assert not _matches(TorrentFilter(ratio=Range(min=3.0)), ratio=2.5)
    assert _matches(TorrentFilter(size=Range(min=1_000_000_000)))
    assert not _matches(TorrentFilter(size=Range(max=1_000_000)))


def test_seeding_time_zero_is_a_value_not_unknown() -> None:
    """A freshly added torrent has genuinely seeded for zero seconds."""
    assert _matches(TorrentFilter(seeding_time=Range(max=60)), seeding_time=0)
    assert not _matches(
        TorrentFilter(seeding_time=Range(min=1)), seeding_time=0
    )


def test_added_window_is_evaluated_in_utc() -> None:
    added_on = 1_700_000_000
    moment = datetime.fromtimestamp(added_on, tz=UTC)

    assert _matches(TorrentFilter(added=Range(min=moment - timedelta(days=1))))
    assert _matches(TorrentFilter(added=Range(max=moment + timedelta(days=1))))
    assert not _matches(
        TorrentFilter(added=Range(min=moment + timedelta(days=1)))
    )


def test_added_on_zero_is_unknown_not_the_unix_epoch() -> None:
    """Reading a zero as 1970 would make `--older-than` match it, which
    is the dangerous direction on a destructive command."""
    filters = TorrentFilter(added=Range(max=datetime.now(tz=UTC)))

    assert not _matches(filters, added_on=0)


def test_has_trackers_is_evaluated_without_any_extra_call() -> None:
    assert _matches(TorrentFilter(has_trackers=True), trackers_count=2)
    assert _matches(TorrentFilter(has_trackers=False), trackers_count=0)
    assert not _matches(TorrentFilter(has_trackers=True), trackers_count=0)
    assert not TorrentFilter(has_trackers=False).requires_inspection


def test_has_trackers_never_matches_when_the_count_is_unknown() -> None:
    torrent = _torrent()
    del torrent["trackers_count"]

    assert not matches_cheap_filters(torrent, TorrentFilter(has_trackers=True))
    assert not matches_cheap_filters(torrent, TorrentFilter(has_trackers=False))


# --- composition ------------------------------------------------------------


def test_different_families_combine_with_and() -> None:
    """The "cleanup" use case: inactive-ish, well-seeded, not protected."""
    filters = TorrentFilter(
        categories=("linux",),
        ratio=Range(min=2.0),
        seeding_time=Range(min=86_400 * 7),
        tags=TagCriterion(none_of=("keep",)),
    )

    assert _matches(filters)
    assert not _matches(filters, category="movies")
    assert not _matches(filters, ratio=1.0)
    assert not _matches(filters, seeding_time=60)
    assert not _matches(filters, tags="keep")


def test_an_empty_filter_matches_everything() -> None:
    assert _matches(TorrentFilter())
    assert matches_cheap_filters({}, TorrentFilter())


# --- inactivity -------------------------------------------------------------
#
# `last_activity` semantics were observed on real containers (qBittorrent
# 4.6.7 and 5.2.3, see docs/SELECTION.md): it is the timestamp of the
# last byte transferred, it equals `added_on` while nothing has ever
# transferred, and it does not move on announce, on a state change, or
# with the passage of time.


def test_inactivity_window_uses_last_activity_not_added_on() -> None:
    """The whole point of the filter: a torrent added long ago but still
    transferring must not read as inactive."""
    now = datetime.now(tz=UTC)
    long_ago = int((now - timedelta(days=200)).timestamp())
    recent = int((now - timedelta(minutes=5)).timestamp())

    inactive_30d = TorrentFilter(
        last_activity=Range(max=now - timedelta(days=30))
    )

    assert not _matches(inactive_30d, added_on=long_ago, last_activity=recent)
    assert _matches(inactive_30d, added_on=long_ago, last_activity=long_ago)


def test_active_within_is_the_other_bound() -> None:
    now = datetime.now(tz=UTC)
    recent = int((now - timedelta(hours=1)).timestamp())
    stale = int((now - timedelta(days=10)).timestamp())

    active_24h = TorrentFilter(
        last_activity=Range(min=now - timedelta(hours=24))
    )

    assert _matches(active_24h, last_activity=recent)
    assert not _matches(active_24h, last_activity=stale)


def test_a_torrent_that_never_transferred_counts_from_when_it_was_added() -> (
    None
):
    """Observed on both tested versions: `last_activity` is initialized
    to `added_on`, so a torrent that never moved a byte becomes inactive
    as its age grows -- which is the intuitive reading."""
    now = datetime.now(tz=UTC)
    added = int((now - timedelta(days=60)).timestamp())

    filters = TorrentFilter(last_activity=Range(max=now - timedelta(days=30)))

    assert _matches(filters, added_on=added, last_activity=added)


def test_an_unknown_last_activity_never_matches_an_inactivity_bound() -> None:
    torrent = _torrent()
    torrent.pop("last_activity", None)
    filters = TorrentFilter(last_activity=Range(max=datetime.now(tz=UTC)))

    assert not matches_cheap_filters(torrent, filters)
    assert not _matches(filters, last_activity=0)


# --- completion date --------------------------------------------------------
#
# `completion_on` is `-1` for a torrent that never finished downloading,
# observed on qBittorrent 4.6.7 and 5.2.3 (see docs/SELECTION.md). That
# sentinel is what makes this filter safe to expose.


def test_completion_window_selects_on_when_the_download_finished() -> None:
    now = datetime.now(tz=UTC)
    long_ago = int((now - timedelta(days=200)).timestamp())
    recent = int((now - timedelta(days=2)).timestamp())

    old_completions = TorrentFilter(
        completed_at=Range(max=now - timedelta(days=30))
    )

    assert _matches(old_completions, completion_on=long_ago)
    assert not _matches(old_completions, completion_on=recent)


def test_completed_within_is_the_other_bound() -> None:
    now = datetime.now(tz=UTC)
    recent = int((now - timedelta(days=2)).timestamp())
    long_ago = int((now - timedelta(days=200)).timestamp())

    fresh = TorrentFilter(completed_at=Range(min=now - timedelta(days=7)))

    assert _matches(fresh, completion_on=recent)
    assert not _matches(fresh, completion_on=long_ago)


def test_an_unfinished_torrent_matches_no_completion_bound() -> None:
    """`-1` means "never completed", so an in-progress download is
    unknown here -- never a torrent that finished at the epoch."""
    now = datetime.now(tz=UTC)

    assert not _matches(
        TorrentFilter(completed_at=Range(max=now)), completion_on=-1
    )
    assert not _matches(
        TorrentFilter(completed_at=Range(min=now - timedelta(days=1))),
        completion_on=-1,
    )


def test_completion_and_age_are_different_questions() -> None:
    """A torrent added long ago can have finished yesterday."""
    now = datetime.now(tz=UTC)
    added = int((now - timedelta(days=200)).timestamp())
    finished = int((now - timedelta(days=1)).timestamp())

    assert _matches(
        TorrentFilter(added=Range(max=now - timedelta(days=30))),
        added_on=added,
        completion_on=finished,
    )
    assert not _matches(
        TorrentFilter(completed_at=Range(max=now - timedelta(days=30))),
        added_on=added,
        completion_on=finished,
    )


def test_a_trailing_slash_names_the_same_directory() -> None:
    """`/data` and `/data/` are the same directory, and an operator
    should not have to guess which form qBittorrent stores. Reported by
    review: `--exclude-save-path /data/` missed a torrent at `/data`.
    """
    for prefix in ("/data", "/data/"):
        included = TorrentFilter(save_path_prefixes=(prefix,))
        excluded = TorrentFilter(save_paths_excluded=(prefix,))

        for save_path in ("/data", "/data/"):
            assert _matches(included, save_path=save_path), (prefix, save_path)
            assert not _matches(excluded, save_path=save_path), (
                prefix,
                save_path,
            )


def test_a_trailing_slash_does_not_widen_the_boundary() -> None:
    """Normalizing the separator must not turn `/data` into a prefix of
    `/data-old`."""
    filters = TorrentFilter(save_path_prefixes=("/data/",))

    assert _matches(filters, save_path="/data/movies")
    assert not _matches(filters, save_path="/data-old")
    assert not _matches(filters, save_path="/database")


def test_a_root_only_prefix_is_refused_rather_than_matching_everything() -> (
    None
):
    """`/` names no meaningful subtree; treating it as one would make a
    single stray character select the whole instance."""
    assert not _matches(TorrentFilter(save_path_prefixes=("/",)))
    assert not _matches(TorrentFilter(save_path_prefixes=("",)))


# --- both layers agree on what is known -------------------------------------

_FAR_FUTURE = datetime(2100, 1, 1, tzinfo=UTC)

# Every measure the filters bound *and* the central model carries -- no
# exception, which is the point: one row per pair of readings that must
# never disagree. Each row is the raw field qBittorrent sends, a filter
# bounding it, the model attribute reading it, and every marker meaning
# "never reported" for that field. `0` is a marker for a timestamp
# (nothing happens at the epoch) but a real value for a byte count or a
# duration, which is why the marker sets differ.
_SHARED_MEASURES: tuple[
    tuple[str, TorrentFilter, str, tuple[Any, ...]], ...
] = (
    (
        "uploaded",
        TorrentFilter(uploaded=Range(max=10**12)),
        "uploaded",
        (None, -1, -2),
    ),
    (
        "seeding_time",
        TorrentFilter(seeding_time=Range(max=10**9)),
        "seeding_time",
        (None, -1, -2),
    ),
    (
        "added_on",
        TorrentFilter(added=Range(max=_FAR_FUTURE)),
        "added_at",
        (None, 0, -1, -2),
    ),
    (
        "completion_on",
        TorrentFilter(completed_at=Range(max=_FAR_FUTURE)),
        "completed_at",
        (None, 0, -1, -2),
    ),
    (
        "last_activity",
        TorrentFilter(last_activity=Range(max=_FAR_FUTURE)),
        "last_activity_at",
        (None, 0, -1, -2),
    ),
)

_EVERY_MEASURE_KNOWN: dict[str, Any] = {
    "uploaded": 9_400_000_000,
    "seeding_time": 86_400 * 30,
    "added_on": 1_700_000_000,
    "completion_on": 1_700_000_100,
    "last_activity": 1_700_000_200,
}


@pytest.mark.parametrize(
    ("filters", "model_field"),
    [(row[1], row[2]) for row in _SHARED_MEASURES],
)
def test_a_bound_that_matches_means_the_model_carries_the_measure(
    filters: TorrentFilter, model_field: str
) -> None:
    torrent = _torrent(**_EVERY_MEASURE_KNOWN)

    assert matches_cheap_filters(torrent, filters)
    assert getattr(build_torrent_snapshot(torrent), model_field) is not None


@pytest.mark.parametrize(
    ("raw_field", "filters", "model_field", "unknown_markers"),
    _SHARED_MEASURES,
)
def test_an_unknown_measure_is_unknown_to_the_filter_and_to_the_model(
    raw_field: str,
    filters: TorrentFilter,
    model_field: str,
    unknown_markers: tuple[Any, ...],
) -> None:
    """The invariant that makes one canonical representation real: for
    the same raw object, a value the filtering layer treats as never
    reported is also absent from the central model. Were the model to
    read it with a coercing accessor, a policy could fire on a `0` that
    `--seeded-for` would never have matched."""
    absent = _torrent(**_EVERY_MEASURE_KNOWN)
    del absent[raw_field]

    candidates = [absent] + [
        _torrent(**{**_EVERY_MEASURE_KNOWN, raw_field: marker})
        for marker in unknown_markers
    ]

    for torrent in candidates:
        reported = torrent.get(raw_field, "absent")
        assert not matches_cheap_filters(torrent, filters), reported
        snapshot = build_torrent_snapshot(torrent)
        assert getattr(snapshot, model_field) is None, reported
