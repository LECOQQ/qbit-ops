"""Test the status snapshot collection service."""

from app.status import (
    Health,
    StatusAlert,
    StatusSnapshot,
    TransferCounts,
    TransferRates,
    build_unavailable_snapshot,
    classify_torrent_state,
    collect_status_snapshot,
    snapshot_to_csv_rows,
    snapshot_to_json_dict,
    status_exit_code,
)
from app.ui import format_byte_rate
from tests.support import FakeQbitClient, make_torrent


def test_classify_torrent_state_groups_qbit4_and_qbit5_active_states() -> None:
    """Ensure active DL/UP states classify without relying on paused/stopped."""
    assert classify_torrent_state("downloading") == "downloading"
    assert classify_torrent_state("metaDL") == "downloading"
    assert classify_torrent_state("forcedDL") == "downloading"
    assert classify_torrent_state("queuedDL") == "downloading"
    assert classify_torrent_state("uploading") == "seeding"
    assert classify_torrent_state("forcedUP") == "seeding"
    assert classify_torrent_state("queuedUP") == "seeding"


def test_classify_torrent_state_groups_paused_and_stopped_equally() -> None:
    """Ensure qBittorrent 4 `paused*` and 5 `stopped*` map identically."""
    assert classify_torrent_state("pausedDL") == "downloading"
    assert classify_torrent_state("stoppedDL") == "downloading"
    assert classify_torrent_state("pausedUP") == "seeding"
    assert classify_torrent_state("stoppedUP") == "seeding"


def test_classify_torrent_state_groups_stalled_checking_and_errored() -> None:
    """Ensure stalled, checking and error states map to their own groups."""
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
    """Ensure a future/unrecognized state is tracked, not silently dropped."""
    assert classify_torrent_state("somethingNew") == "unknown"


def test_collect_status_snapshot_reports_healthy_with_no_alerts() -> None:
    """Ensure a clean instance reports healthy with an empty alert list."""
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
    """Ensure stalled torrents produce a warning, not a critical, health."""
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


def test_collect_status_snapshot_reports_critical_for_errored() -> None:
    """Ensure errored torrents win over stalled torrents (most severe)."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a", state="error"),
            make_torrent(hash="b", state="stalledUP"),
        ],
    )

    snapshot = collect_status_snapshot(client)

    assert snapshot.health == Health.CRITICAL
    codes = {alert.code for alert in snapshot.alerts}
    assert codes == {"torrents_errored", "torrents_stalled"}


def test_collect_status_snapshot_counts_unknown_states_as_warning() -> None:
    """Ensure unrecognized states are counted and raise a warning alert."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a", state="futureState")],
    )

    snapshot = collect_status_snapshot(client)

    assert snapshot.health == Health.WARNING
    assert snapshot.counts.unknown == 1
    alert_codes = {alert.code for alert in snapshot.alerts}
    assert "torrents_unknown_state" in alert_codes


def test_collect_status_snapshot_uses_a_bounded_number_of_api_calls() -> None:
    """Ensure `status` never calls `torrents_trackers()` per torrent."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=str(index)) for index in range(50)],
    )

    collect_status_snapshot(client)

    assert client.torrents_info_calls == 1
    assert client.transfer_info_calls == 1
    assert client.torrents_trackers_calls == 0


def test_collect_status_snapshot_redacts_credentials_from_host() -> None:
    """Ensure embedded userinfo never leaks into the snapshot."""
    client = FakeQbitClient(torrents=[])

    snapshot = collect_status_snapshot(
        client,
        host="http://admin:super-secret@localhost:8080",
    )

    assert snapshot.host == "http://localhost:8080"
    assert "super-secret" not in (snapshot.host or "")


def test_build_unavailable_snapshot_reports_unavailable_health() -> None:
    """Ensure connection failures build an unavailable snapshot."""
    snapshot = build_unavailable_snapshot(
        code="qbittorrent_unavailable",
        message="Unable to connect to qBittorrent at http://localhost:8080.",
    )

    assert snapshot.health == Health.UNAVAILABLE
    assert snapshot.connected is False
    assert snapshot.alerts[0].code == "qbittorrent_unavailable"


def test_status_exit_code_maps_every_health_value() -> None:
    """Ensure every health value has a documented exit code."""
    assert status_exit_code(Health.HEALTHY) == 0
    assert status_exit_code(Health.WARNING) == 1
    assert status_exit_code(Health.CRITICAL) == 2
    assert status_exit_code(Health.UNAVAILABLE) == 3


def test_snapshot_to_json_dict_has_the_documented_shape() -> None:
    """Ensure the JSON representation matches the documented schema."""
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
    """Ensure CSV rows expose numeric, unformatted rate and count values."""
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
    """Ensure byte rates render with binary (1024-based) units."""
    assert format_byte_rate(0) == "0 B/s"
    assert format_byte_rate(512) == "512 B/s"
    assert format_byte_rate(1536) == "1.5 KiB/s"
    assert format_byte_rate(13_002_342) == "12.4 MiB/s"
    assert format_byte_rate(5_368_709_120) == "5.0 GiB/s"
