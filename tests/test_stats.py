"""Test library statistics: the aggregates, the two blocks, the budget."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from qbit_core.features.stats import (
    MeasureTotals,
    aggregate_library_stats,
    aggregate_measure_totals,
    collect_torrent_stats,
    stats_report_to_csv_rows,
    stats_report_to_dict,
)
from qbit_core.shared.selection import (
    Range,
    SelectionRequest,
    TorrentFilter,
)
from qbit_core.shared.torrent_states import (
    TorrentSnapshot,
    build_torrent_snapshot,
)
from tests.support import FakeQbitClient, make_torrent

TRACKER_URL = "https://tracker.example/announce"


def _snapshot(**overrides: Any) -> TorrentSnapshot:
    """Build a central-model snapshot from a raw torrent, as production does."""
    return build_torrent_snapshot(make_torrent(**overrides))


def _aggregate(*snapshots: TorrentSnapshot, scanned: int | None = None):
    return aggregate_library_stats(
        snapshots, scanned=len(snapshots) if scanned is None else scanned
    )


# --- aggregates, without any client -----------------------------------------


def test_aggregation_needs_no_client_only_central_model_snapshots() -> None:
    stats = _aggregate(
        _snapshot(size=1_000, downloaded=400, uploaded=800),
        _snapshot(size=3_000, downloaded=600, uploaded=400),
        scanned=9,
    )

    assert stats.torrents == 2
    assert stats.scanned == 9
    assert stats.total_size_bytes == 4_000
    assert stats.average_size_bytes == 2_000
    assert stats.largest_size_bytes == 3_000
    assert stats.downloaded_bytes == 1_000
    assert stats.uploaded_bytes == 1_200


def test_aggregate_ratio_is_the_ratio_of_the_totals() -> None:
    """Not the mean of the per-torrent ratios, which would weigh a tiny
    torrent exactly like a huge one."""
    stats = _aggregate(
        _snapshot(size=50_000_000, downloaded=1_000, uploaded=10_000, ratio=10),
        _snapshot(
            size=80_000_000_000,
            downloaded=1_000_000,
            uploaded=1_000_000,
            ratio=1,
        ),
    )

    assert stats.aggregate_ratio == 1_010_000 / 1_001_000


def test_a_ratio_is_null_when_nothing_was_downloaded() -> None:
    stats = _aggregate(_snapshot(downloaded=0, uploaded=5_000))

    assert stats.aggregate_ratio is None
    assert stats.uploaded_bytes == 5_000


def test_an_unknown_byte_counter_is_excluded_never_subtracted() -> None:
    """qBittorrent's "unset" marker is negative, so counting it would
    take bytes away from the total and quietly distort the ratio."""
    stats = _aggregate(
        _snapshot(downloaded=1_000, uploaded=4_000),
        _snapshot(downloaded=-1, uploaded=-1),
        _snapshot(),
    )

    assert stats.downloaded_bytes == 1_000
    assert stats.uploaded_bytes == 4_000
    assert stats.aggregate_ratio == 4.0


def test_a_selection_whose_transfer_is_wholly_unknown_stays_at_zero() -> None:
    stats = _aggregate(_snapshot(downloaded=-1, uploaded=-1))

    assert stats.downloaded_bytes == 0
    assert stats.uploaded_bytes == 0
    assert stats.aggregate_ratio is None


def test_an_unknown_seeding_time_leaves_its_aggregates_alone() -> None:
    stats = _aggregate(
        _snapshot(seeding_time=100),
        _snapshot(seeding_time=300),
        _snapshot(seeding_time=-1),
        _snapshot(),
    )

    assert stats.seeding_time_total_seconds == 400
    assert stats.seeding_time_median_seconds == 200


def test_an_unknown_added_date_is_never_read_as_the_epoch() -> None:
    stats = _aggregate(
        _snapshot(added_on=1_700_000_000),
        _snapshot(added_on=0),
        _snapshot(added_on=1_500_000_000),
    )

    assert stats.oldest_added_at == datetime(2017, 7, 14, 2, 40, tzinfo=UTC)
    assert stats.newest_added_at == datetime(
        2023, 11, 14, 22, 13, 20, tzinfo=UTC
    )


def test_an_empty_selection_reports_zeros_and_undefined_aggregates() -> None:
    stats = _aggregate(scanned=1_105)

    assert stats.torrents == 0
    assert stats.scanned == 1_105
    assert stats.total_size_bytes == 0
    assert stats.downloaded_bytes == 0
    assert stats.uploaded_bytes == 0
    assert stats.seeding_time_total_seconds == 0
    assert stats.average_size_bytes is None
    assert stats.largest_size_bytes is None
    assert stats.aggregate_ratio is None
    assert stats.seeding_time_median_seconds is None
    assert stats.oldest_added_at is None
    assert stats.newest_added_at is None


# --- one predicate decides the instance block and the call that feeds it ----


def test_no_selector_reads_the_all_time_counters_once() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent()],
        all_time_downloaded=8_000,
        all_time_uploaded=24_000,
        global_ratio="3.0",
    )

    report = collect_torrent_stats(client, SelectionRequest())

    assert client.torrents_info_calls == 1
    assert client.sync_maindata_calls == 1
    assert client.torrents_trackers_calls == 0
    assert report.instance is not None
    assert report.instance.all_time_downloaded_bytes == 8_000
    assert report.instance.all_time_uploaded_bytes == 24_000
    assert report.instance.all_time_ratio == 3.0


def test_a_selector_without_a_tracker_filter_costs_one_listing() -> None:
    client = FakeQbitClient(torrents=[make_torrent(category="movies")])

    report = collect_torrent_stats(
        client,
        SelectionRequest(filters=TorrentFilter(categories=("movies",))),
    )

    assert client.torrents_info_calls == 1
    assert client.sync_maindata_calls == 0
    assert client.torrents_trackers_calls == 0
    assert report.instance is None


def test_a_tracker_filter_inspects_only_the_cheap_filter_survivors() -> None:
    """The cost `torrents list --tracker` already pays, reused as-is: one
    lookup per surviving candidate, never one per torrent in the
    instance."""
    kept = make_torrent(hash="a" * 40, name="A", category="movies")
    dropped = make_torrent(hash="b" * 40, name="B", category="music")
    client = FakeQbitClient(
        torrents=[kept, dropped],
        trackers_by_hash={"a" * 40: [{"url": TRACKER_URL, "status": "2"}]},
    )

    report = collect_torrent_stats(
        client,
        SelectionRequest(
            filters=TorrentFilter(
                categories=("movies",), trackers=("tracker.example",)
            )
        ),
    )

    # 1 SELECT + 1 bulk `include_trackers` probe this fake ignores --
    # see `qbit_core.shared.inspection._fetch_trackers_in_bulk`.
    assert client.torrents_info_calls == 2
    assert client.torrents_trackers_calls == 1
    assert client.sync_maindata_calls == 0
    assert report.library.torrents == 1
    assert report.instance is None


def test_selecting_by_hash_or_by_all_still_drops_the_instance_block() -> None:
    torrents = [make_torrent(hash="a" * 40), make_torrent(hash="b" * 40)]

    for request in (
        SelectionRequest(torrent_hash="a" * 8),
        SelectionRequest(select_all=True),
    ):
        client = FakeQbitClient(torrents=torrents)

        report = collect_torrent_stats(client, request)

        assert report.instance is None, request
        assert client.sync_maindata_calls == 0, request


def test_the_report_echoes_the_request_the_operator_made() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    request = SelectionRequest(select_all=True)

    report = collect_torrent_stats(client, request)

    assert report.request == request


def test_an_unsafe_selector_combination_is_refused_before_any_call() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])

    try:
        collect_torrent_stats(
            client,
            SelectionRequest(torrent_hash="a" * 8, select_all=True),
        )
    except ValueError:
        assert client.torrents_info_calls == 0
    else:  # pragma: no cover - the call must not be accepted
        raise AssertionError("expected --hash with --all to be refused")


# --- serialization ----------------------------------------------------------


def test_the_payload_carries_both_blocks_and_the_serialized_filters() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(size=2_000, downloaded=1_000, uploaded=3_000)],
        all_time_downloaded=10,
        all_time_uploaded=40,
        global_ratio="4.0",
    )

    payload = stats_report_to_dict(
        collect_torrent_stats(client, SelectionRequest())
    )

    assert payload["schema_version"] == "1"
    assert datetime.fromisoformat(payload["generated_at"]).tzinfo is not None
    assert payload["filters"]["categories"] == []
    assert payload["library"]["total_size_bytes"] == 2_000
    assert payload["library"]["aggregate_ratio"] == 3.0
    assert payload["instance"] == {
        "all_time_downloaded_bytes": 10,
        "all_time_uploaded_bytes": 40,
        "all_time_ratio": 4.0,
    }


def test_the_payload_serializes_a_filtered_selection_without_an_instance() -> (
    None
):
    client = FakeQbitClient(torrents=[make_torrent(category="movies")])

    payload = stats_report_to_dict(
        collect_torrent_stats(
            client,
            SelectionRequest(
                filters=TorrentFilter(
                    categories=("movies",), ratio=Range(min=1.0)
                )
            ),
        )
    )

    assert payload["instance"] is None
    assert payload["filters"]["categories"] == ["movies"]
    assert payload["filters"]["ratio"] == {"min": 1.0, "max": None}


def test_csv_rows_are_long_form_and_omit_an_absent_instance_block() -> None:
    client = FakeQbitClient(torrents=[make_torrent(category="movies")])

    with_instance = stats_report_to_csv_rows(
        collect_torrent_stats(client, SelectionRequest())
    )
    filtered = stats_report_to_csv_rows(
        collect_torrent_stats(
            client,
            SelectionRequest(filters=TorrentFilter(categories=("movies",))),
        )
    )

    assert all(len(row) == 3 for row in with_instance)
    assert ("library", "torrents", "1") in with_instance
    assert any(section == "instance" for section, _, _ in with_instance)
    assert not any(section == "instance" for section, _, _ in filtered)


def test_an_undefined_aggregate_serializes_as_null_never_as_zero() -> None:
    client = FakeQbitClient(torrents=[])

    report = collect_torrent_stats(client, SelectionRequest())
    payload = stats_report_to_dict(report)

    assert payload["library"]["aggregate_ratio"] is None
    assert payload["library"]["average_size_bytes"] is None
    assert payload["library"]["oldest_added_at"] is None
    assert ("library", "aggregate_ratio", "") in stats_report_to_csv_rows(
        report
    )


# --- the shared measure arithmetic ------------------------------------------


def test_measure_totals_sum_only_the_measures_qbittorrent_reported() -> None:
    """`-1` is qBittorrent's "never set" marker, written literally rather
    than imported. Summed as a value it would take bytes *away* from the
    total, which is why it must never reach the sum.
    """
    totals = aggregate_measure_totals(
        [
            _snapshot(
                size=1_000, downloaded=100, uploaded=200, seeding_time=60
            ),
            _snapshot(size=500, downloaded=-1, uploaded=-1, seeding_time=-1),
        ]
    )

    assert totals == MeasureTotals(
        total_size_bytes=1_500,
        downloaded_bytes=100,
        uploaded_bytes=200,
        aggregate_ratio=2.0,
        seeding_time_total_seconds=60,
    )


def test_measure_totals_ratio_is_the_ratio_of_the_totals() -> None:
    """The mean of these two ratios is 1.0; the ratio of the totals is
    0.2. Weighing a tiny torrent like a huge one is the whole reason the
    two commands must share this arithmetic.
    """
    totals = aggregate_measure_totals(
        [
            _snapshot(downloaded=100, uploaded=200, ratio=2.0),
            _snapshot(downloaded=900, uploaded=0, ratio=0.0),
        ]
    )

    assert totals.aggregate_ratio == 0.2


def test_measure_totals_leave_the_ratio_undefined_rather_than_zero() -> None:
    assert aggregate_measure_totals([]).aggregate_ratio is None
    assert (
        aggregate_measure_totals(
            [_snapshot(downloaded=0, uploaded=5)]
        ).aggregate_ratio
        is None
    )


def test_library_stats_read_their_shared_measures_from_one_place() -> None:
    """`torrents stats` and the per-tracker breakdown must never sum the
    same bytes differently."""
    snapshots = [
        _snapshot(size=1_000, downloaded=100, uploaded=200, seeding_time=60),
        _snapshot(size=500, downloaded=-1, uploaded=900, seeding_time=30),
    ]
    library = _aggregate(*snapshots)
    totals = aggregate_measure_totals(snapshots)

    assert library.total_size_bytes == totals.total_size_bytes
    assert library.downloaded_bytes == totals.downloaded_bytes
    assert library.uploaded_bytes == totals.uploaded_bytes
    assert library.aggregate_ratio == totals.aggregate_ratio
    assert (
        library.seeding_time_total_seconds == totals.seeding_time_total_seconds
    )
