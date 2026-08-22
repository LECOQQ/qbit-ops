"""The Overview's derived models, checked without a running app.

Tracker attribution, the library breakdown and the rate window are all
computed from the torrent list the periodic refresh already fetched.
None of them needs Textual, and none of them may need a second API call.
"""

from __future__ import annotations

import pytest

from qbit_ops.tui.state import (
    GRAPH_SAMPLE_INTERVAL_SECONDS,
    NO_TRACKER_KEY,
    NO_TRACKER_LABEL,
    TRACKER_SPARKLINE_SLOTS,
    RateHistory,
    TrackerActivityKind,
    build_library_breakdown,
    build_tracker_breakdown,
)
from tests.support import make_torrent

pytestmark = pytest.mark.tui


def _torrent(index: int, **overrides: object) -> dict[str, object]:
    return make_torrent(hash=f"{index:040x}", **overrides)


# --- the window is a span of time, not a count of refreshes --------------


def test_the_sampler_runs_on_its_own_second() -> None:
    """One sample per second and one column per sample: the panel's
    width decides the window, and the label is read back off it."""
    assert GRAPH_SAMPLE_INTERVAL_SECONDS == 1.0


def test_a_fresh_window_has_measured_nothing_rather_than_measured_zero() -> (
    None
):
    history = RateHistory()

    assert history.measured == 0
    assert history.downloads == ()
    # Asked for a window, it pads with unmeasured slots rather than
    # returning a shorter one: the plot is always as wide as the panel.
    downloads, uploads = history.window(10)
    assert downloads == [None] * 10
    assert uploads == [None] * 10


def test_a_recorded_zero_counts_as_a_measurement() -> None:
    history = RateHistory()
    history.record_transfer(download=0, upload=0)

    assert history.measured == 1
    assert history.downloads == (0,)


def test_seconds_nobody_watched_are_recorded_as_unmeasured() -> None:
    """The operator was on the Torrents page and the sampler was
    stopped. Those seconds are not zero traffic -- nobody looked."""
    history = RateHistory()
    history.record_transfer(download=5, upload=5)
    history.skip(4)
    history.record_transfer(download=7, upload=7)

    downloads, _ = history.window(6)
    assert downloads == [5, None, None, None, None, 7]
    assert history.measured == 2


def test_a_skip_can_never_outgrow_the_window() -> None:
    history = RateHistory(slots=8)
    history.skip(10_000)

    assert len(history.downloads) == 8
    assert history.measured == 0


def test_the_window_never_grows_past_its_slots() -> None:
    history = RateHistory(slots=8)
    for value in range(20):
        history.record_transfer(download=value, upload=0)

    assert history.measured == 8
    assert history.downloads[-1] == 19


def test_a_negative_reading_never_enters_the_window() -> None:
    history = RateHistory()
    history.record_transfer(download=-1, upload=-5)

    assert history.downloads == (0,)
    assert history.uploads == (0,)


def test_every_tracker_sparkline_shares_one_peak() -> None:
    history = RateHistory()
    history.record_trackers({"a": 100, "b": 4_000})

    assert history.tracker_peak == 4_000
    assert history.tracker("a") == (100,)
    assert history.tracker("b") == (4_000,)


def test_a_tracker_quiet_for_a_whole_window_stops_costing_a_series() -> None:
    history = RateHistory()
    history.record_trackers({"gone": 500})
    assert history.tracker_keys == ("gone",)

    for _ in range(TRACKER_SPARKLINE_SLOTS):
        history.record_trackers({})

    assert history.tracker_keys == ()


# --- tracker attribution -------------------------------------------------


def test_a_tracker_row_is_keyed_by_host_never_by_announce_url() -> None:
    """A raw announce URL carries a passkey; the host does not, and the
    host is all this window shows."""
    breakdown = build_tracker_breakdown(
        [
            _torrent(
                1,
                tracker="https://bt.private.tld:443/announce/s3cr3tpasskey",
                upspeed=1_000,
            )
        ]
    )

    (row,) = breakdown.rows
    assert row.label == "bt.private.tld:443"
    assert "s3cr3tpasskey" not in row.label
    assert "announce" not in row.label


def test_torrents_without_a_host_land_in_one_unknown_bucket() -> None:
    breakdown = build_tracker_breakdown(
        [
            _torrent(1, tracker="** [DHT] **", upspeed=26_000),
            _torrent(2, tracker=""),
            _torrent(3, tracker="not a url at all"),
        ]
    )

    (row,) = breakdown.rows
    assert row.key == NO_TRACKER_KEY
    assert row.label == NO_TRACKER_LABEL
    assert row.kind is TrackerActivityKind.UNKNOWN
    assert row.torrents == 3


def test_activity_is_derived_from_rates_and_partitions_the_rows() -> None:
    breakdown = build_tracker_breakdown(
        [
            _torrent(1, tracker="http://both.tld/a", dlspeed=10, upspeed=10),
            _torrent(2, tracker="http://seed.tld/a", upspeed=10),
            _torrent(3, tracker="http://leech.tld/a", dlspeed=10),
            _torrent(4, tracker="http://idle.tld/a"),
            _torrent(5, tracker="http://bad.tld/a", state="error"),
            _torrent(6, tracker="** [PeX] **"),
        ]
    )

    kinds = {row.label: row.kind for row in breakdown.rows}
    assert kinds["both.tld"] is TrackerActivityKind.BOTH
    assert kinds["seed.tld"] is TrackerActivityKind.SEEDING
    assert kinds["leech.tld"] is TrackerActivityKind.LEECHING
    assert kinds["idle.tld"] is TrackerActivityKind.IDLE
    assert kinds["bad.tld"] is TrackerActivityKind.ERRORED
    assert kinds[NO_TRACKER_LABEL] is TrackerActivityKind.UNKNOWN

    counted = sum(breakdown.count_by_kind(kind) for kind in TrackerActivityKind)
    assert counted == len(breakdown.rows) == 6


def test_rates_of_torrents_sharing_a_tracker_are_summed() -> None:
    breakdown = build_tracker_breakdown(
        [
            _torrent(1, tracker="http://one.tld/a", dlspeed=100, upspeed=1),
            _torrent(2, tracker="http://one.tld/a", dlspeed=200, upspeed=2),
        ]
    )

    (row,) = breakdown.rows
    assert row.download_rate == 300
    assert row.upload_rate == 3
    assert row.torrents == 2
    assert row.total_rate == 303


def test_rows_are_ordered_by_how_much_they_are_moving() -> None:
    breakdown = build_tracker_breakdown(
        [
            _torrent(1, tracker="http://quiet.tld/a", upspeed=1),
            _torrent(2, tracker="http://busy.tld/a", upspeed=9_000),
            _torrent(3, tracker="http://middling.tld/a", upspeed=500),
        ]
    )

    assert [row.label for row in breakdown.rows] == [
        "busy.tld",
        "middling.tld",
        "quiet.tld",
    ]


def test_exclusive_and_shared_read_the_count_each_torrent_carries() -> None:
    """`trackers_count` is on every captured version, so this needs no
    per-torrent `torrents_trackers()` call."""
    breakdown = build_tracker_breakdown(
        [
            _torrent(1, tracker="http://one.tld/a", trackers_count=1),
            _torrent(2, tracker="http://one.tld/a", trackers_count=3),
            _torrent(3, tracker="http://one.tld/a", trackers_count=0),
        ]
    )

    assert breakdown.exclusive == 1
    assert breakdown.shared == 1
    assert breakdown.torrents == 3


# --- the library breakdown -----------------------------------------------


def test_the_library_breakdown_counts_sizes_categories_and_tags() -> None:
    breakdown = build_library_breakdown(
        [
            _torrent(1, size=1_000, category="sonarr", tags="keep,stale"),
            _torrent(2, size=2_000, category="sonarr", tags="keep"),
            _torrent(3, size=3_000, category="", tags=""),
        ]
    )

    assert breakdown.total_size_bytes == 6_000
    assert breakdown.categories[0] == ("sonarr", 2)
    assert dict(breakdown.tags) == {"keep": 2, "stale": 1}
    assert breakdown.untagged == 1


def test_an_uncategorized_torrent_uses_the_shared_display_label() -> None:
    from qbit_core.shared.selection import format_category_label

    breakdown = build_library_breakdown([_torrent(1, category="")])

    assert breakdown.categories == ((format_category_label(""), 1),)


def test_an_empty_library_breaks_down_to_nothing_not_to_zeroes() -> None:
    breakdown = build_library_breakdown([])

    assert breakdown.total_size_bytes == 0
    assert breakdown.categories == ()
    assert breakdown.tags == ()
    assert breakdown.untagged == 0
