"""Test the status snapshot collection service."""

from datetime import UTC, datetime

import pytest

from qbit_core.features.status import (
    Health,
    StatusAlert,
    StatusSnapshot,
    TransferCounts,
    TransferRates,
    build_instance_stats_from_server_state,
    build_status_snapshot_from_data,
    build_unavailable_snapshot,
    classify_torrent_state,
    collect_instance_stats,
    collect_status_snapshot,
    snapshot_to_csv_rows,
    snapshot_to_json_dict,
    status_exit_code,
    watch_status,
)
from qbit_ops.cli.rendering import format_byte_rate
from tests.support import FakeQbitClient, make_torrent


class _StopWatch(Exception):
    """Sentinel raised by a fake `collect`/`sleep` to end a test's loop."""


class _FakeClock:
    """Deterministic monotonic clock + sleep, so tests never really wait."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleep_calls: list[float] = []

    def monotonic(self) -> float:
        """Return the current fake time."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Record the requested sleep and advance the fake clock by it."""
        self.sleep_calls.append(seconds)
        self.now += seconds


def test_classify_torrent_state_groups_qbit4_and_qbit5_active_states() -> None:
    assert classify_torrent_state("downloading") == "downloading"
    assert classify_torrent_state("metaDL") == "downloading"
    assert classify_torrent_state("forcedDL") == "downloading"
    assert classify_torrent_state("queuedDL") == "downloading"
    assert classify_torrent_state("uploading") == "seeding"
    assert classify_torrent_state("forcedUP") == "seeding"
    assert classify_torrent_state("queuedUP") == "seeding"


def test_classify_torrent_state_groups_paused_and_stopped_equally() -> None:
    assert classify_torrent_state("pausedDL") == "downloading"
    assert classify_torrent_state("stoppedDL") == "downloading"
    assert classify_torrent_state("pausedUP") == "seeding"
    assert classify_torrent_state("stoppedUP") == "seeding"


def test_classify_torrent_state_groups_stalled_checking_and_errored() -> None:
    assert classify_torrent_state("stalledDL") == "stalled"
    assert classify_torrent_state("stalledUP") == "stalled"
    assert classify_torrent_state("checkingDL") == "checking"
    assert classify_torrent_state("checkingUP") == "checking"
    assert classify_torrent_state("checkingResumeData") == "checking"
    assert classify_torrent_state("allocating") == "checking"
    assert classify_torrent_state("moving") == "checking"
    assert classify_torrent_state("error") == "errored"
    assert classify_torrent_state("missingFiles") == "errored"


def test_classify_torrent_state_keeps_unrecognized_states_as_unknown() -> None:
    assert classify_torrent_state("somethingNew") == "unknown"


def test_collect_status_snapshot_reports_healthy_with_no_alerts() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a", state="uploading", progress=1.0),
            make_torrent(hash="b", state="downloading", progress=0.4),
        ],
        download_speed=1024,
        upload_speed=2048,
    )

    snapshot = collect_status_snapshot(client, host="http://localhost:8080")

    assert snapshot.health == Health.HEALTHY
    assert snapshot.alerts == ()
    assert snapshot.counts == TransferCounts(
        total=2,
        downloading=1,
        seeding=1,
        completed=1,
        stalled=0,
        checking=0,
        errored=0,
        unknown=0,
    )
    assert snapshot.rates == TransferRates(1024, 2048)


def test_collect_status_snapshot_reports_warning_for_stalled_torrents() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a", state="stalledDL"),
            make_torrent(hash="b", state="uploading"),
        ],
    )

    snapshot = collect_status_snapshot(client)

    assert snapshot.health == Health.WARNING
    assert len(snapshot.alerts) == 1
    assert snapshot.alerts[0].code == "torrents_stalled"
    assert snapshot.alerts[0].severity == Health.WARNING
    assert snapshot.alerts[0].count == 1


def test_stalled_up_completed_from_local_data_does_not_warn() -> None:
    """A `stalledUP` torrent with `downloaded == 0` and `progress == 1`
    was assembled from local data, not the network: it needs no action,
    so it must not turn the library's health to warning nor raise the
    `torrents_stalled` alert."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(
                hash="a", state="stalledUP", progress=1.0, downloaded=0
            ),
            make_torrent(
                hash="b", state="stalledUP", progress=1.0, downloaded=0
            ),
        ],
    )

    snapshot = collect_status_snapshot(client)

    assert snapshot.health == Health.HEALTHY
    assert snapshot.alerts == ()
    # The raw per-state breakdown still reports both torrents as stalled.
    assert snapshot.counts.stalled == 2


def test_one_unexplained_stall_among_explained_ones_still_warns() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(
                hash="a", state="stalledUP", progress=1.0, downloaded=0
            ),
            make_torrent(
                hash="b", state="stalledUP", progress=1.0, downloaded=1
            ),
        ],
    )

    snapshot = collect_status_snapshot(client)

    assert snapshot.health == Health.WARNING
    assert len(snapshot.alerts) == 1
    assert snapshot.alerts[0].code == "torrents_stalled"
    assert snapshot.alerts[0].count == 1
    assert snapshot.counts.stalled == 2


def test_collect_status_snapshot_reports_critical_for_errored() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a", state="error"),
            # `downloaded > 0`: an actionable stall, not a torrent
            # completed from local data alone (see
            # `is_stalled_up_without_network_load`).
            make_torrent(hash="b", state="stalledUP", downloaded=1),
        ],
    )

    snapshot = collect_status_snapshot(client)

    assert snapshot.health == Health.CRITICAL
    codes = {alert.code for alert in snapshot.alerts}
    assert codes == {"torrents_errored", "torrents_stalled"}


def test_collect_status_snapshot_counts_unknown_states_as_warning() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a", state="futureState")],
    )

    snapshot = collect_status_snapshot(client)

    assert snapshot.health == Health.WARNING
    assert snapshot.counts.unknown == 1
    alert_codes = {alert.code for alert in snapshot.alerts}
    assert "torrents_unknown_state" in alert_codes


def test_collect_status_snapshot_uses_a_bounded_number_of_api_calls() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=str(index)) for index in range(50)],
    )

    collect_status_snapshot(client)

    assert client.torrents_info_calls == 1
    assert client.transfer_info_calls == 1
    assert client.torrents_trackers_calls == 0
    assert client.sync_maindata_calls == 0


def test_collect_status_snapshot_redacts_credentials_from_host() -> None:
    client = FakeQbitClient(torrents=[])

    snapshot = collect_status_snapshot(
        client,
        host="http://admin:super-secret@localhost:8080",
    )

    assert snapshot.host == "http://localhost:8080"
    assert "super-secret" not in (snapshot.host or "")


# --- InstanceStats: lifetime instance-wide totals ---------------------------


def test_build_instance_stats_from_server_state() -> None:
    stats = build_instance_stats_from_server_state(
        {
            "alltime_dl": 123_456,
            "alltime_ul": 654_321,
            "global_ratio": "1.75",
            "total_peer_connections": 42,
        }
    )

    assert stats.all_time_downloaded_bytes == 123_456
    assert stats.all_time_uploaded_bytes == 654_321
    assert stats.all_time_ratio == pytest.approx(1.75)
    assert stats.connected_peers == 42
    assert stats.instance_id is None


def test_build_instance_stats_normalizes_no_ratio_sentinel_to_none() -> None:
    """qBittorrent's `-1` means "no ratio computed yet"."""
    stats = build_instance_stats_from_server_state(
        {
            "alltime_dl": 0,
            "alltime_ul": 0,
            "global_ratio": "-1",
            "total_peer_connections": 0,
        }
    )

    assert stats.all_time_ratio is None


def test_build_instance_stats_normalizes_unparsable_ratio_to_none() -> None:
    stats = build_instance_stats_from_server_state({"global_ratio": "n/a"})

    assert stats.all_time_ratio is None
    assert stats.all_time_downloaded_bytes == 0
    assert stats.connected_peers == 0


def test_build_instance_stats_carries_the_given_instance_id() -> None:
    stats = build_instance_stats_from_server_state(
        {}, instance_id="http://localhost:8080"
    )

    assert stats.instance_id == "http://localhost:8080"


def test_collect_instance_stats_uses_exactly_one_api_call() -> None:
    client = FakeQbitClient(
        all_time_downloaded=10,
        all_time_uploaded=20,
        global_ratio="2.00",
        connected_peers=7,
    )

    stats = collect_instance_stats(client, instance_id="http://x")

    assert client.sync_maindata_calls == 1
    assert stats.all_time_downloaded_bytes == 10
    assert stats.all_time_uploaded_bytes == 20
    assert stats.all_time_ratio == pytest.approx(2.00)
    assert stats.connected_peers == 7
    assert stats.instance_id == "http://x"


def test_build_unavailable_snapshot_reports_unavailable_health() -> None:
    snapshot = build_unavailable_snapshot(
        code="qbittorrent_unavailable",
        message="Unable to connect to qBittorrent at http://localhost:8080.",
    )

    assert snapshot.health == Health.UNAVAILABLE
    assert snapshot.connected is False
    assert snapshot.alerts[0].code == "qbittorrent_unavailable"


def test_status_exit_code_maps_every_health_value() -> None:
    assert status_exit_code(Health.HEALTHY) == 0
    assert status_exit_code(Health.WARNING) == 1
    assert status_exit_code(Health.CRITICAL) == 2
    assert status_exit_code(Health.UNAVAILABLE) == 3


def test_snapshot_to_json_dict_has_the_documented_shape() -> None:
    snapshot = StatusSnapshot(
        schema_version="1",
        generated_at=collect_status_snapshot(FakeQbitClient()).generated_at,
        health=Health.WARNING,
        connected=True,
        host="http://localhost:8080",
        qbittorrent_version="5.0.1",
        api_version="2.9.3",
        counts=TransferCounts(1, 0, 0, 0, 1, 0, 0, 0),
        rates=TransferRates(100, 200),
        alerts=(
            StatusAlert(
                code="torrents_stalled",
                severity=Health.WARNING,
                message="1 stalled torrent(s)",
                count=1,
            ),
        ),
    )

    payload = snapshot_to_json_dict(snapshot)

    assert payload["schema_version"] == "1"
    assert payload["health"] == "warning"
    assert payload["connection"] == {
        "connected": True,
        "host": "http://localhost:8080",
        "qbittorrent_version": "5.0.1",
        "api_version": "2.9.3",
    }
    assert payload["transfers"]["stalled"] == 1
    assert payload["rates"] == {
        "download_bytes_per_second": 100,
        "upload_bytes_per_second": 200,
    }
    assert payload["alerts"] == [
        {
            "code": "torrents_stalled",
            "severity": "warning",
            "message": "1 stalled torrent(s)",
            "count": 1,
        }
    ]


def test_snapshot_to_csv_rows_is_a_stable_key_value_table() -> None:
    snapshot = build_unavailable_snapshot(
        code="qbittorrent_unavailable",
        message="Unable to connect.",
        host="http://localhost:8080",
    )

    rows = snapshot_to_csv_rows(snapshot)

    assert ("connection", "health", "unavailable") in rows
    assert ("connection", "host", "http://localhost:8080") in rows
    assert ("transfers", "total", "0") in rows
    assert ("rates", "download_bytes_per_second", "0") in rows
    assert ("alerts", "qbittorrent_unavailable", "Unable to connect.") in rows


def test_format_byte_rate_uses_binary_units() -> None:
    assert format_byte_rate(0) == "0 B/s"
    assert format_byte_rate(512) == "512 B/s"
    assert format_byte_rate(1536) == "1.5 KiB/s"
    assert format_byte_rate(13_002_342) == "12.4 MiB/s"
    assert format_byte_rate(5_368_709_120) == "5.0 GiB/s"


# --- watch_status() loop -------------------------------------------------


def _healthy_snapshot(**overrides: object) -> StatusSnapshot:
    defaults: dict[str, object] = dict(
        schema_version="1",
        generated_at=None,
        health=Health.HEALTHY,
        connected=True,
        host=None,
        qbittorrent_version="5.0.1",
        api_version="2.9.3",
        counts=TransferCounts(1, 0, 1, 1, 0, 0, 0, 0),
        rates=TransferRates(0, 0),
        alerts=(),
    )
    defaults.update(overrides)
    if defaults["generated_at"] is None:
        defaults["generated_at"] = datetime.now(UTC)
    return StatusSnapshot(**defaults)  # type: ignore[arg-type]


def test_watch_status_reuses_the_injected_collector_every_iteration() -> None:
    clock = _FakeClock()
    call_count = 0

    def collect() -> StatusSnapshot:
        nonlocal call_count
        call_count += 1
        if call_count > 4:
            raise _StopWatch
        return _healthy_snapshot()

    with pytest.raises(_StopWatch):
        watch_status(
            collect=collect,
            emit=lambda snapshot: None,
            interval=1.0,
            sleep=clock.sleep,
            now=clock.monotonic,
        )

    assert call_count == 5


def test_watch_status_emits_snapshots_in_order() -> None:
    clock = _FakeClock()
    emitted: list[Health] = []
    healths = [Health.HEALTHY, Health.WARNING, Health.CRITICAL]

    def collect() -> StatusSnapshot:
        if not healths:
            raise _StopWatch
        return _healthy_snapshot(health=healths.pop(0))

    with pytest.raises(_StopWatch):
        watch_status(
            collect=collect,
            emit=lambda snapshot: emitted.append(snapshot.health),
            interval=1.0,
            sleep=clock.sleep,
            now=clock.monotonic,
        )

    assert emitted == [Health.HEALTHY, Health.WARNING, Health.CRITICAL]


def test_watch_status_uses_the_injected_sleep() -> None:
    clock = _FakeClock()
    count = 0

    def collect() -> StatusSnapshot:
        nonlocal count
        count += 1
        if count > 2:
            raise _StopWatch
        return _healthy_snapshot()

    with pytest.raises(_StopWatch):
        watch_status(
            collect=collect,
            emit=lambda snapshot: None,
            interval=1.0,
            sleep=clock.sleep,
            now=clock.monotonic,
        )

    assert clock.sleep_calls
    assert all(delay == pytest.approx(1.0) for delay in clock.sleep_calls)


def test_watch_status_never_requests_a_negative_sleep() -> None:
    clock = _FakeClock()
    count = 0

    def collect() -> StatusSnapshot:
        nonlocal count
        count += 1
        clock.now += 5.0  # slower than the 1.0s interval
        if count > 3:
            raise _StopWatch
        return _healthy_snapshot()

    with pytest.raises(_StopWatch):
        watch_status(
            collect=collect,
            emit=lambda snapshot: None,
            interval=1.0,
            sleep=clock.sleep,
            now=clock.monotonic,
        )

    assert clock.sleep_calls == []


def test_watch_status_targets_cycle_start_to_avoid_drift() -> None:
    clock = _FakeClock()
    count = 0

    def collect() -> StatusSnapshot:
        nonlocal count
        count += 1
        clock.now += 0.4
        if count > 1:
            raise _StopWatch
        return _healthy_snapshot()

    with pytest.raises(_StopWatch):
        watch_status(
            collect=collect,
            emit=lambda snapshot: None,
            interval=1.0,
            sleep=clock.sleep,
            now=clock.monotonic,
        )

    assert clock.sleep_calls == [pytest.approx(0.6)]


def test_watch_status_never_overlaps_collection() -> None:
    """The loop is a plain synchronous `while True`, so overlap is
    structurally impossible; this pins that invariant down with an
    explicit re-entrancy guard so a future change introducing real
    concurrency would be caught here.
    """
    clock = _FakeClock()
    in_progress = False
    count = 0

    def collect() -> StatusSnapshot:
        nonlocal in_progress, count
        assert not in_progress, "collect() re-entered before it returned"
        in_progress = True
        count += 1
        snapshot = _healthy_snapshot()
        in_progress = False
        if count > 3:
            raise _StopWatch
        return snapshot

    with pytest.raises(_StopWatch):
        watch_status(
            collect=collect,
            emit=lambda snapshot: None,
            interval=1.0,
            sleep=clock.sleep,
            now=clock.monotonic,
        )


def test_watch_status_continues_through_unavailable_snapshots() -> None:
    clock = _FakeClock()
    emitted: list[Health] = []
    count = 0

    def collect() -> StatusSnapshot:
        nonlocal count
        count += 1
        if count > 4:
            raise _StopWatch
        return build_unavailable_snapshot(
            code="qbittorrent_unavailable", message="temporary blip"
        )

    with pytest.raises(_StopWatch):
        watch_status(
            collect=collect,
            emit=lambda snapshot: emitted.append(snapshot.health),
            interval=1.0,
            sleep=clock.sleep,
            now=clock.monotonic,
        )

    assert emitted == [Health.UNAVAILABLE] * 4


def test_watch_status_recovers_to_healthy_after_unavailable() -> None:
    clock = _FakeClock()
    emitted: list[Health] = []
    outcomes = [
        build_unavailable_snapshot(
            code="qbittorrent_unavailable", message="temporary blip"
        ),
        _healthy_snapshot(health=Health.HEALTHY),
    ]

    def collect() -> StatusSnapshot:
        if not outcomes:
            raise _StopWatch
        return outcomes.pop(0)

    with pytest.raises(_StopWatch):
        watch_status(
            collect=collect,
            emit=lambda snapshot: emitted.append(snapshot.health),
            interval=1.0,
            sleep=clock.sleep,
            now=clock.monotonic,
        )

    assert emitted == [Health.UNAVAILABLE, Health.HEALTHY]


def test_malformed_non_mapping_transfer_info_fails_explicitly() -> None:
    """`build_status_snapshot_from_data` does not bypass
    `qbit_core.qbit.fields.get_transfer_rates` with a direct `.get()`
    call: a non-mapping payload must fail with an explicit `TypeError`,
    not an accidental `AttributeError`.
    """

    class _NotAMapping:
        pass

    with pytest.raises(TypeError, match="mapping"):
        build_status_snapshot_from_data(
            qbittorrent_version="5.0.1",
            api_version="2.11.4",
            transfer_info_data=_NotAMapping(),
            torrents=[],
        )
