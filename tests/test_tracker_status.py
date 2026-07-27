"""Test the `trackers status` domain model: collection and aggregation."""

from qbit_ops.torrents import build_torrent_filter
from qbit_ops.tracker_status import (
    TrackerHealth,
    classify_raw_tracker_status,
    collect_tracker_status,
    tracker_status_exit_code,
    tracker_status_report_to_csv_rows,
    tracker_status_report_to_dict,
)
from qbit_ops.trackers import normalize_tracker_host
from tests.support import FakeQbitClient, make_torrent

HASH_A = "a" * 40
HASH_B = "b" * 40
PASSKEY_URL = "https://tracker.example/announce/SUPERSECRETPASSKEY"


# --- Identity normalization -------------------------------------------------


def test_identity_strips_scheme_path_query_and_userinfo() -> None:
    """Ensure identity normalization never leaks path/query/userinfo."""
    identity = normalize_tracker_host(
        "https://user:pass@tracker.example/announce/PASSKEY?x=1"
    )
    assert identity == "tracker.example"


def test_identity_preserves_non_default_port() -> None:
    """Ensure a non-default port is kept in the identity."""
    identity = normalize_tracker_host("https://tracker.example:6969/announce")
    assert identity == "tracker.example:6969"


def test_identity_does_not_collapse_default_port() -> None:
    """Ensure a scheme's default port is preserved, not stripped.

    Deliberate: `normalize_tracker_host` is reused unchanged from the
    torrent-filter pipeline (`--tracker`), so it must behave identically
    everywhere -- see docs/DECISIONS.md, 2026-07-25.
    """
    assert normalize_tracker_host("https://tracker.example:443/x") == (
        "tracker.example:443"
    )
    assert normalize_tracker_host("https://tracker.example/x") == (
        "tracker.example"
    )


def test_dht_pex_lsd_are_excluded_from_the_report() -> None:
    """Ensure pseudo-tracker entries never appear as tracker identities."""
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
    """Ensure every real `TrackerStatus` code maps to the documented health."""
    assert classify_raw_tracker_status(0) == (TrackerHealth.DISABLED, False)
    assert classify_raw_tracker_status(1) == (TrackerHealth.WARNING, True)
    assert classify_raw_tracker_status(2) == (TrackerHealth.HEALTHY, True)
    assert classify_raw_tracker_status(3) == (TrackerHealth.HEALTHY, True)
    assert classify_raw_tracker_status(4) == (TrackerHealth.CRITICAL, True)
    assert classify_raw_tracker_status(5) == (TrackerHealth.CRITICAL, True)
    assert classify_raw_tracker_status(6) == (TrackerHealth.CRITICAL, True)


def test_classify_raw_tracker_status_accepts_string_codes() -> None:
    """Ensure string-encoded status codes (as fixtures commonly use) work."""
    assert classify_raw_tracker_status("2") == (TrackerHealth.HEALTHY, True)
    assert classify_raw_tracker_status("disabled") == (
        TrackerHealth.DISABLED,
        False,
    )


def test_classify_raw_tracker_status_handles_unknown_values() -> None:
    """Ensure unrecognized/unparsable values map to UNKNOWN, never guessed."""
    assert classify_raw_tracker_status(99) == (TrackerHealth.UNKNOWN, None)
    assert classify_raw_tracker_status("garbage") == (
        TrackerHealth.UNKNOWN,
        None,
    )
    assert classify_raw_tracker_status(None) == (TrackerHealth.UNKNOWN, None)


# --- Aggregation --------------------------------------------------------------


def test_deterministic_ordering_is_alphabetical_by_identity() -> None:
    """Ensure tracker aggregates are always sorted by identity."""
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
    """Ensure two announce URLs on one torrent for the same host merge."""
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
    """Ensure `torrent_count` counts distinct torrents, not endpoints."""
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
    """Ensure a genuine mix of healthy/critical endpoints is WARNING."""
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
    """Ensure a tracker failing on every enabled endpoint is CRITICAL."""
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
    """Ensure a tracker with only disabled endpoints is DISABLED, and the
    report overall stays HEALTHY (a disabled tracker is not a finding)."""
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
    """Ensure one disabled endpoint next to healthy ones stays HEALTHY."""
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
    """Ensure a tracker with only unclassifiable endpoints is UNKNOWN."""
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
    """Ensure one torrent's tracker-lookup failure does not erase others."""
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
    """Ensure tracker collection failing for every matched torrent is
    UNAVAILABLE, distinct from a partial failure."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1")],
        tracker_error_hashes={HASH_A},
    )

    report = collect_tracker_status(client, build_torrent_filter())

    assert report.collection_errors == 1
    assert report.overall_health is TrackerHealth.UNAVAILABLE
    assert tracker_status_exit_code(report.overall_health) == 3


def test_empty_selection_is_healthy_not_an_error() -> None:
    """Ensure a filter matching no torrents produces a HEALTHY empty report."""
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
    """Ensure `--tracker` filters the aggregates, scanning every survivor."""
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
        client, build_torrent_filter(tracker="tracker.example")
    )

    assert [t.identity for t in report.trackers] == ["tracker.example"]
    assert client.torrents_trackers_calls == 2


def test_tracker_filter_matching_no_identity_is_healthy_empty() -> None:
    """Ensure `--tracker` matching no observed identity is an empty, healthy
    report -- not the same exit code as CRITICAL."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1")],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 4}],
        },
    )

    report = collect_tracker_status(
        client, build_torrent_filter(tracker="unrelated.example")
    )

    assert report.trackers == ()
    assert report.overall_health is TrackerHealth.HEALTHY


# --- Representative message sanitization --------------------------------------


def test_representative_message_strips_embedded_urls() -> None:
    """Ensure a tracker message containing a full URL is sanitized."""
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
    """Ensure a WARNING aggregate surfaces the failing endpoint's message.

    `torrent_a` (scanned first, by name order) is healthy with its own
    message; `torrent_b` is critical with a different message. The
    aggregate is WARNING (a mix, not all-critical) -- but the message an
    operator sees must come from the endpoint that is actually the
    problem, not whichever endpoint happened to be read first.
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
    """Ensure no serialization ever renders a full announce URL or passkey."""
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
    """Ensure a cheap filter narrows candidates before any tracker call."""
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
    """Ensure no duplicate `torrents_trackers()` calls per torrent."""
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
