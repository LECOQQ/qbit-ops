"""Test the `trackers status` domain model: collection and aggregation."""

import pytest

from qbit_core.errors import InvalidInputError
from qbit_core.features.torrents import build_torrent_filter
from qbit_core.features.tracker_status import (
    TrackerHealth,
    classify_raw_tracker_status,
    collect_tracker_status,
    tracker_status_exit_code,
    tracker_status_report_to_csv_rows,
    tracker_status_report_to_dict,
)
from qbit_core.features.trackers import normalize_tracker_host
from tests.support import FakeQbitClient, make_torrent

HASH_A = "a" * 40
HASH_B = "b" * 40
PASSKEY_URL = "https://tracker.example/announce/SUPERSECRETPASSKEY"


# --- Identity normalization -------------------------------------------------


def test_identity_strips_scheme_path_query_and_userinfo() -> None:
    identity = normalize_tracker_host(
        "https://user:pass@tracker.example/announce/PASSKEY?x=1"
    )
    assert identity == "tracker.example"


def test_identity_preserves_non_default_port() -> None:
    identity = normalize_tracker_host("https://tracker.example:6969/announce")
    assert identity == "tracker.example:6969"


def test_identity_does_not_collapse_default_port() -> None:
    """Deliberate: `normalize_tracker_host` is reused unchanged from the
    torrent- filter pipeline (`--tracker`), so it must behave identically
    everywhere.
    """
    assert normalize_tracker_host("https://tracker.example:443/x") == (
        "tracker.example:443"
    )
    assert normalize_tracker_host("https://tracker.example/x") == (
        "tracker.example"
    )


def test_dht_pex_lsd_are_excluded_from_the_report() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1")],
        trackers_by_hash={
            HASH_A: [
                {"url": "** [DHT] **", "status": 0},
                {"url": "** [PeX] **", "status": 0},
                {"url": "** [LSD] **", "status": 0},
            ]
        },
    )

    report = collect_tracker_status(client, build_torrent_filter())

    assert report.trackers == ()
    assert report.overall_health is TrackerHealth.HEALTHY


# --- Raw status mapping ------------------------------------------------------


def test_classify_raw_tracker_status_maps_every_known_code() -> None:
    assert classify_raw_tracker_status(0) == (TrackerHealth.DISABLED, False)
    assert classify_raw_tracker_status(1) == (TrackerHealth.WARNING, True)
    assert classify_raw_tracker_status(2) == (TrackerHealth.HEALTHY, True)
    assert classify_raw_tracker_status(3) == (TrackerHealth.HEALTHY, True)
    assert classify_raw_tracker_status(4) == (TrackerHealth.CRITICAL, True)
    assert classify_raw_tracker_status(5) == (TrackerHealth.CRITICAL, True)
    assert classify_raw_tracker_status(6) == (TrackerHealth.CRITICAL, True)


def test_classify_raw_tracker_status_accepts_string_codes() -> None:
    assert classify_raw_tracker_status("2") == (TrackerHealth.HEALTHY, True)
    assert classify_raw_tracker_status("disabled") == (
        TrackerHealth.DISABLED,
        False,
    )


def test_classify_raw_tracker_status_handles_unknown_values() -> None:
    assert classify_raw_tracker_status(99) == (TrackerHealth.UNKNOWN, None)
    assert classify_raw_tracker_status("garbage") == (
        TrackerHealth.UNKNOWN,
        None,
    )
    assert classify_raw_tracker_status(None) == (TrackerHealth.UNKNOWN, None)


# --- Aggregation --------------------------------------------------------------


def test_deterministic_ordering_is_alphabetical_by_identity() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1")],
        trackers_by_hash={
            HASH_A: [
                {"url": "https://z.example/announce", "status": 2},
                {"url": "https://a.example/announce", "status": 2},
            ]
        },
    )

    report = collect_tracker_status(client, build_torrent_filter())

    assert [t.identity for t in report.trackers] == [
        "a.example",
        "z.example",
    ]


def test_multiple_endpoints_for_the_same_tracker_on_one_torrent_merge() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1")],
        trackers_by_hash={
            HASH_A: [
                {"url": "https://tracker.example/announce1", "status": 2},
                {"url": "https://tracker.example/announce2", "status": 2},
            ]
        },
    )

    report = collect_tracker_status(client, build_torrent_filter())

    assert len(report.trackers) == 1
    aggregate = report.trackers[0]
    assert aggregate.torrent_count == 1
    assert aggregate.endpoint_count == 2
    assert aggregate.healthy_count == 2


def test_same_tracker_across_multiple_torrents_counts_each_torrent_once() -> (
    None
):
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="T1"),
            make_torrent(hash=HASH_B, name="T2"),
        ],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 2}],
            HASH_B: [{"url": "https://tracker.example/announce", "status": 2}],
        },
    )

    report = collect_tracker_status(client, build_torrent_filter())

    assert len(report.trackers) == 1
    assert report.trackers[0].torrent_count == 2
    assert report.trackers[0].endpoint_count == 2


def test_mixed_healthy_and_failing_endpoints_produce_warning() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="T1"),
            make_torrent(hash=HASH_B, name="T2"),
        ],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 2}],
            HASH_B: [{"url": "https://tracker.example/announce", "status": 4}],
        },
    )

    report = collect_tracker_status(client, build_torrent_filter())

    assert report.trackers[0].health is TrackerHealth.WARNING
    assert report.overall_health is TrackerHealth.WARNING


def test_all_failing_endpoints_produce_critical() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1")],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 4}],
        },
    )

    report = collect_tracker_status(client, build_torrent_filter())

    assert report.trackers[0].health is TrackerHealth.CRITICAL
    assert report.overall_health is TrackerHealth.CRITICAL


def test_disabled_only_tracker_is_disabled_not_critical() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1")],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 0}],
        },
    )

    report = collect_tracker_status(client, build_torrent_filter())

    assert report.trackers[0].health is TrackerHealth.DISABLED
    assert report.overall_health is TrackerHealth.HEALTHY
    assert tracker_status_exit_code(report.overall_health) == 0


def test_disabled_endpoint_does_not_drag_down_a_working_tracker() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="T1"),
            make_torrent(hash=HASH_B, name="T2"),
        ],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 2}],
            HASH_B: [{"url": "https://tracker.example/announce", "status": 0}],
        },
    )

    report = collect_tracker_status(client, build_torrent_filter())

    assert report.trackers[0].health is TrackerHealth.HEALTHY
    assert report.trackers[0].disabled_count == 1


def test_all_unknown_endpoints_produce_unknown() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1")],
        trackers_by_hash={
            HASH_A: [
                {"url": "https://tracker.example/announce", "status": "weird"}
            ],
        },
    )

    report = collect_tracker_status(client, build_torrent_filter())

    assert report.trackers[0].health is TrackerHealth.UNKNOWN
    assert report.overall_health is TrackerHealth.WARNING


# --- Partial collection failures ---------------------------------------------


def test_partial_collection_failure_keeps_other_observations() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="T1"),
            make_torrent(hash=HASH_B, name="T2"),
        ],
        trackers_by_hash={
            HASH_B: [{"url": "https://tracker.example/announce", "status": 2}],
        },
        tracker_error_hashes={HASH_A},
    )

    report = collect_tracker_status(client, build_torrent_filter())

    assert report.collection_errors == 1
    assert len(report.trackers) == 1
    assert report.trackers[0].health is TrackerHealth.HEALTHY
    assert report.overall_health is TrackerHealth.WARNING
    assert tracker_status_exit_code(report.overall_health) == 1


def test_total_collection_failure_is_unavailable() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1")],
        tracker_error_hashes={HASH_A},
    )

    report = collect_tracker_status(client, build_torrent_filter())

    assert report.collection_errors == 1
    assert report.overall_health is TrackerHealth.UNAVAILABLE
    assert tracker_status_exit_code(report.overall_health) == 3


def test_empty_selection_is_healthy_not_an_error() -> None:
    client = FakeQbitClient(torrents=[])

    report = collect_tracker_status(
        client, build_torrent_filter(categories=["nonexistent"])
    )

    assert report.matched_torrents == 0
    assert report.trackers == ()
    assert report.overall_health is TrackerHealth.HEALTHY
    assert tracker_status_exit_code(report.overall_health) == 0


# --- `--tracker` filtering ----------------------------------------------------


def test_tracker_filter_restricts_the_report_not_the_scan() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="T1"),
            make_torrent(hash=HASH_B, name="T2"),
        ],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 2}],
            HASH_B: [{"url": "https://other.example/announce", "status": 2}],
        },
    )

    report = collect_tracker_status(
        client, build_torrent_filter(trackers=("tracker.example",))
    )

    assert [t.identity for t in report.trackers] == ["tracker.example"]
    assert client.torrents_trackers_calls == 2


def test_exclude_tracker_fails_explicitly_rather_than_being_dropped() -> None:
    """`collect_tracker_status` strips the tracker criteria from its cheap
    pass, because it inspects every selected torrent anyway. `--tracker`
    survives as a *report* restriction; `--exclude-tracker` has no such
    meaning here, so it must fail loudly instead of silently selecting
    more torrents than the operator asked for."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1")],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 2}],
        },
    )

    with pytest.raises(InvalidInputError) as error:
        collect_tracker_status(
            client,
            build_torrent_filter(trackers_excluded=["tracker.example"]),
        )

    assert "--exclude-tracker" in str(error.value)
    assert client.torrents_info_calls == 0


def test_tracker_filter_matching_no_identity_is_healthy_empty() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1")],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 4}],
        },
    )

    report = collect_tracker_status(
        client, build_torrent_filter(trackers=("unrelated.example",))
    )

    assert report.trackers == ()
    assert report.overall_health is TrackerHealth.HEALTHY


# --- Representative message sanitization --------------------------------------


def test_representative_message_strips_embedded_urls() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1")],
        trackers_by_hash={
            HASH_A: [
                {
                    "url": "https://tracker.example/announce",
                    "status": 4,
                    "msg": f"Failure connecting to {PASSKEY_URL}",
                }
            ],
        },
    )

    report = collect_tracker_status(client, build_torrent_filter())

    message = report.trackers[0].representative_message
    assert message is not None
    assert "SUPERSECRETPASSKEY" not in message
    assert "<redacted-url>" in message


def test_representative_message_prefers_the_most_severe_endpoint() -> None:
    """`torrent_a` (scanned first, by name order) is healthy with its own
    message; `torrent_b` is critical with a different message. The aggregate
    is WARNING (a mix, not all-critical) -- but the message an operator sees
    must come from the endpoint that is actually the problem, not whichever
    endpoint happened to be read first.
    """
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="A"),
            make_torrent(hash=HASH_B, name="B"),
        ],
        trackers_by_hash={
            HASH_A: [
                {
                    "url": "https://tracker.example/announce",
                    "status": 2,
                    "msg": "all good here",
                }
            ],
            HASH_B: [
                {
                    "url": "https://tracker.example/announce",
                    "status": 4,
                    "msg": "connection refused",
                }
            ],
        },
    )

    report = collect_tracker_status(client, build_torrent_filter())

    aggregate = report.trackers[0]
    assert aggregate.health is TrackerHealth.WARNING
    assert aggregate.representative_message == "connection refused"


# --- Secret-freedom across every serialization --------------------------------


def test_no_passkey_leaks_in_json_dict_or_csv_rows() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1")],
        trackers_by_hash={
            HASH_A: [
                {
                    "url": PASSKEY_URL,
                    "status": 4,
                    "msg": f"error at {PASSKEY_URL}",
                }
            ],
        },
    )

    report = collect_tracker_status(client, build_torrent_filter())

    payload = tracker_status_report_to_dict(report)
    csv_rows = tracker_status_report_to_csv_rows(report)

    assert "SUPERSECRETPASSKEY" not in str(payload)
    assert all(
        "SUPERSECRETPASSKEY" not in cell for row in csv_rows for cell in row
    )


# --- API-call behavior --------------------------------------------------------


def test_no_calls_for_torrents_filtered_out_by_cheap_filters() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="T1", category="movies"),
            make_torrent(hash=HASH_B, name="T2", category="tv"),
        ],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 2}],
            HASH_B: [{"url": "https://tracker.example/announce", "status": 2}],
        },
    )

    collect_tracker_status(client, build_torrent_filter(categories=["movies"]))

    assert client.torrents_trackers_calls == 1


def test_at_most_one_lookup_per_surviving_torrent() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="T1"),
            make_torrent(hash=HASH_B, name="T2"),
        ],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 2}],
            HASH_B: [{"url": "https://tracker.example/announce", "status": 2}],
        },
    )

    collect_tracker_status(client, build_torrent_filter())

    assert client.torrents_info_calls == 1
    assert client.torrents_trackers_calls == 2


# --- Per-tracker volume -----------------------------------------------------

TRACKER_ONE = "https://one.example/announce"
TRACKER_TWO = "https://two.example/announce"


def _healthy(url: str) -> dict[str, object]:
    return {"url": url, "status": 2}


def test_an_unknown_measure_is_left_out_of_its_tracker_aggregate() -> None:
    """`-1` is qBittorrent's "never set" marker, written literally here
    rather than imported, so this assertion cannot drift along with the
    code it checks. Read as a value it would take bytes *away* from the
    total, which is why the marker must never reach the sum.
    """
    client = FakeQbitClient(
        torrents=[
            make_torrent(
                hash=HASH_A,
                name="T1",
                downloaded=1_000,
                uploaded=2_000,
                seeding_time=3_600,
            ),
            make_torrent(
                hash=HASH_B,
                name="T2",
                downloaded=-1,
                uploaded=-1,
                seeding_time=-1,
            ),
        ],
        trackers_by_hash={
            HASH_A: [_healthy(TRACKER_ONE)],
            HASH_B: [_healthy(TRACKER_ONE)],
        },
    )

    aggregate = collect_tracker_status(client, build_torrent_filter()).trackers[
        0
    ]

    assert aggregate.torrent_count == 2
    assert aggregate.downloaded_bytes == 1_000
    assert aggregate.uploaded_bytes == 2_000
    assert aggregate.seeding_time_total_seconds == 3_600


def test_tracker_ratio_is_the_ratio_of_the_totals() -> None:
    """Never the mean of the per-torrent ratios: that would weigh a tiny
    torrent like a huge one. Here the two answers are far apart -- the
    mean is 1.0, the ratio of the totals is 0.2.
    """
    client = FakeQbitClient(
        torrents=[
            make_torrent(
                hash=HASH_A, name="T1", downloaded=100, uploaded=200, ratio=2.0
            ),
            make_torrent(
                hash=HASH_B, name="T2", downloaded=900, uploaded=0, ratio=0.0
            ),
        ],
        trackers_by_hash={
            HASH_A: [_healthy(TRACKER_ONE)],
            HASH_B: [_healthy(TRACKER_ONE)],
        },
    )

    aggregate = collect_tracker_status(client, build_torrent_filter()).trackers[
        0
    ]

    assert aggregate.downloaded_bytes == 1_000
    assert aggregate.uploaded_bytes == 200
    assert aggregate.aggregate_ratio == pytest.approx(0.2)


def test_tracker_ratio_is_undefined_rather_than_zero_when_nothing_read() -> (
    None
):
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="T1", downloaded=0, uploaded=5)
        ],
        trackers_by_hash={HASH_A: [_healthy(TRACKER_ONE)]},
    )

    aggregate = collect_tracker_status(client, build_torrent_filter()).trackers[
        0
    ]

    assert aggregate.aggregate_ratio is None


def test_excl_counts_the_torrents_a_tracker_is_the_only_identity_of() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="Shared"),
            make_torrent(hash=HASH_B, name="Sole"),
        ],
        trackers_by_hash={
            HASH_A: [_healthy(TRACKER_ONE), _healthy(TRACKER_TWO)],
            HASH_B: [_healthy(TRACKER_ONE)],
        },
    )

    by_identity = {
        aggregate.identity: aggregate
        for aggregate in collect_tracker_status(
            client, build_torrent_filter()
        ).trackers
    }

    assert by_identity["one.example"].torrent_count == 2
    assert by_identity["one.example"].exclusive_torrent_count == 1
    assert by_identity["two.example"].torrent_count == 1
    assert by_identity["two.example"].exclusive_torrent_count == 0


def test_a_disabled_endpoint_still_counts_as_another_identity_for_excl() -> (
    None
):
    """The operator disabled that tracker, they did not remove it, and
    they can re-enable it -- so the torrent is not exclusive to the
    other one.
    """
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1")],
        trackers_by_hash={
            HASH_A: [_healthy(TRACKER_ONE), {"url": TRACKER_TWO, "status": 0}]
        },
    )

    aggregates = collect_tracker_status(client, build_torrent_filter()).trackers

    assert {aggregate.identity for aggregate in aggregates} == {
        "one.example",
        "two.example",
    }
    assert all(
        aggregate.exclusive_torrent_count == 0 for aggregate in aggregates
    )


def test_summing_a_column_over_trackers_can_exceed_the_library_total() -> None:
    """A torrent announcing to two trackers counts entirely in both, so
    the per-tracker columns do not partition the library. `EXCL` is what
    distinguishes the double-counted torrents from the rest.
    """
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="Shared", size=1_000),
            make_torrent(hash=HASH_B, name="Sole", size=500),
        ],
        trackers_by_hash={
            HASH_A: [_healthy(TRACKER_ONE), _healthy(TRACKER_TWO)],
            HASH_B: [_healthy(TRACKER_ONE)],
        },
    )

    aggregates = collect_tracker_status(client, build_torrent_filter()).trackers
    library_total = 1_500

    assert sum(a.total_size_bytes for a in aggregates) == 2_500
    assert sum(a.total_size_bytes for a in aggregates) > library_total
    assert sum(a.exclusive_torrent_count for a in aggregates) == 1


def test_volume_costs_no_call_beyond_the_health_collection() -> None:
    """The measures ride along with the pass `trackers status` already
    made: one listing, one tracker lookup per surviving torrent.
    """
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="T1", downloaded=10, uploaded=20),
            make_torrent(hash=HASH_B, name="T2", downloaded=30, uploaded=40),
        ],
        trackers_by_hash={
            HASH_A: [_healthy(TRACKER_ONE)],
            HASH_B: [_healthy(TRACKER_ONE)],
        },
    )

    aggregate = collect_tracker_status(client, build_torrent_filter()).trackers[
        0
    ]

    assert aggregate.downloaded_bytes == 40
    assert client.torrents_info_calls == 1
    assert client.torrents_trackers_calls == 2


def test_two_endpoints_of_one_host_count_the_torrent_and_its_bytes_once() -> (
    None
):
    """Private trackers commonly list several announce endpoints for one
    host. They merge into one identity, so the torrent must not be
    counted twice, and neither must its bytes.
    """
    client = FakeQbitClient(
        torrents=[
            make_torrent(
                hash=HASH_A,
                name="T1",
                size=1_000,
                downloaded=100,
                uploaded=200,
                seeding_time=60,
            )
        ],
        trackers_by_hash={
            HASH_A: [
                _healthy("https://one.example/announce"),
                _healthy("https://one.example/announce2"),
            ]
        },
    )

    aggregates = collect_tracker_status(client, build_torrent_filter()).trackers

    assert len(aggregates) == 1
    aggregate = aggregates[0]
    assert aggregate.identity == "one.example"
    assert aggregate.endpoint_count == 2
    assert aggregate.torrent_count == 1
    assert aggregate.exclusive_torrent_count == 1
    assert aggregate.total_size_bytes == 1_000
    assert aggregate.downloaded_bytes == 100
    assert aggregate.uploaded_bytes == 200
    assert aggregate.seeding_time_total_seconds == 60
