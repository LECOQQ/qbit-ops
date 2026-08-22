"""Contract tests for authentic `captured-container` payload fixtures.

Runs the production boundary functions the CLI and TUI use against
payloads captured from real qBittorrent containers (see
`tests/integration/_capture.py`). Reads only committed JSON fixtures;
no Docker is required to run this file.
"""

from __future__ import annotations

import json

import pytest

from qbit_core.features.status import (
    build_instance_stats_from_server_state,
)
from qbit_core.features.trackers import classify_raw_tracker_status
from qbit_core.qbit.fields import (
    get_active_tracker_urls,
    get_field_as_float,
    get_field_as_string,
    get_optional_bool,
    get_raw_tracker_status,
    get_transfer_rates,
    is_disabled_tracker,
    pseudo_tracker_label,
)
from qbit_core.shared.torrent_states import (
    build_torrent_snapshot,
    classify_torrent_state,
)
from tests.compatibility._captured_loader import (
    CAPTURED_FIXTURES_ROOT,
    discover_matrix_ids,
    load_all_captured_fixtures,
    load_captured_fixtures,
)
from tests.integration._matrix import load_matrix

_MATRIX_ENTRIES_BY_ID = {entry.id: entry for entry in load_matrix()}


def test_at_least_one_matrix_entry_has_captured_fixtures() -> None:
    assert discover_matrix_ids(), (
        "no captured-container fixtures found; run `make capture-qbit-fixtures "
        "QBIT_MATRIX_ID=<id>` at least once before this test can prove anything"
    )


@pytest.mark.parametrize("matrix_id", discover_matrix_ids())
def test_captured_fixtures_are_labelled_captured_container(
    matrix_id: str,
) -> None:
    for fixture in load_captured_fixtures(matrix_id):
        assert fixture.trust == "captured-container", fixture.path


@pytest.mark.parametrize("matrix_id", discover_matrix_ids())
def test_declared_matrix_id_matches_the_directory_it_lives_in(
    matrix_id: str,
) -> None:
    """A fixture's own `_meta.matrix_id` claim must match the directory
    it is stored in -- otherwise a fixture could claim e.g. 5.2.3 while
    its content silently came from a different capture."""
    for fixture in load_captured_fixtures(matrix_id):
        assert fixture.declared_matrix_id == fixture.matrix_id, (
            f"{fixture.path} declares matrix_id="
            f"{fixture.declared_matrix_id!r} but lives under "
            f"{fixture.matrix_id!r}"
        )


@pytest.mark.parametrize("matrix_id", discover_matrix_ids())
def test_captured_versions_match_the_matrix_manifest_entry(
    matrix_id: str,
) -> None:
    """`fixture.qbittorrent_version`/`web_api_version`/`architecture`
    are values *observed* from the real container
    (`_capture.py::_write_fixture` reads `container.observed_*`, never
    `matrix_entry.expected_*`), so comparing them to the manifest's
    independently-loaded `expected_*` is a real cross-check."""
    entry = _MATRIX_ENTRIES_BY_ID[matrix_id]
    for fixture in load_captured_fixtures(matrix_id):
        if fixture.qbittorrent_version is not None:
            assert (
                fixture.qbittorrent_version == entry.expected_version
            ), fixture.path
        if fixture.web_api_version is not None:
            assert (
                fixture.web_api_version == entry.expected_web_api_version
            ), fixture.path
        if fixture.architecture is not None:
            assert (
                fixture.architecture == entry.expected_architecture
            ), fixture.path
        if fixture.image_digest is not None:
            assert fixture.image_digest == entry.image_digest, fixture.path


def test_every_captured_fixture_has_non_empty_provenance_prose() -> None:
    for fixture in load_all_captured_fixtures():
        assert fixture.sanitization.strip(), fixture.path
        assert fixture.limitations.strip(), fixture.path


def test_captured_torrent_payload_is_read_correctly() -> None:
    found_any = False
    for matrix_id in discover_matrix_ids():
        for fixture in load_captured_fixtures(matrix_id):
            if fixture.name != "torrent_complete":
                continue
            found_any = True
            torrent = fixture.payload
            assert get_field_as_string(torrent, "hash")
            assert get_field_as_float(torrent, "progress") == 1.0
            # Any classification, including "unknown", is a valid
            # non-raising outcome for a real captured state string.
            classify_torrent_state(get_field_as_string(torrent, "state"))
            # Extra real-world fields (e.g. `availability`, `popularity`) must
            # not break reading the fields qbit-ops actually consumes.
            assert "availability" in torrent

    assert found_any, "expected at least one torrent_complete capture"


def test_captured_tracker_payload_is_sanitized_and_readable() -> None:
    found_any = False
    for matrix_id in discover_matrix_ids():
        for fixture in load_captured_fixtures(matrix_id):
            if fixture.name != "trackers_tracked_torrent":
                continue
            found_any = True
            for tracker in fixture.payload:
                raw_status = get_raw_tracker_status(tracker)
                classify_raw_tracker_status(raw_status)  # must not raise
                is_disabled_tracker(tracker)  # must not raise
                url = tracker.get("url", "")
                assert (
                    "qbit-ops-tracker" not in url
                ), f"unsanitized tracker hostname leaked into {fixture.path}"

    assert found_any, "expected at least one trackers_tracked_torrent capture"


def test_pseudo_trackers_are_excluded_on_every_captured_version() -> None:
    """The counting rule, proven against real payloads rather than test
    doubles: DHT/PeX/LSD are peer-discovery mechanisms, so on every
    supported version the only announce URL left is the real tracker --
    whatever status that version reports the pseudo entries with (`0` on
    an idle capture, `2` once they are working)."""
    found_any = False
    for matrix_id in discover_matrix_ids():
        for fixture in load_captured_fixtures(matrix_id):
            if fixture.name != "trackers_tracked_torrent":
                continue
            found_any = True
            labels = [
                pseudo_tracker_label(tracker.get("url", ""))
                for tracker in fixture.payload
            ]
            assert [label for label in labels if label is not None] == [
                "DHT",
                "PeX",
                "LSD",
            ], f"unexpected pseudo-tracker set in {fixture.path}"
            assert get_active_tracker_urls(fixture.payload) == [
                "http://tracker.example:6969/announce"
            ], f"a pseudo-tracker reached the active URLs in {fixture.path}"

    assert found_any, "expected at least one trackers_tracked_torrent capture"


def test_captured_transfer_payload_is_read_correctly() -> None:
    found_any = False
    for matrix_id in discover_matrix_ids():
        for fixture in load_captured_fixtures(matrix_id):
            if fixture.name != "transfer_info":
                continue
            found_any = True
            rates = get_transfer_rates(fixture.payload)
            assert rates == (
                fixture.payload.get("dl_info_speed", 0),
                fixture.payload.get("up_info_speed", 0),
            )

    assert found_any, "expected at least one transfer_info capture"


def test_captured_application_version_strings_are_read_correctly() -> None:
    from qbit_core.features.doctor import CheckStatus, _version_parsable_check

    found_any = False
    for matrix_id in discover_matrix_ids():
        for fixture in load_captured_fixtures(matrix_id):
            if fixture.name != "app_version":
                continue
            found_any = True
            parsed, check = _version_parsable_check(fixture.payload)
            assert parsed is not None
            assert check.status is CheckStatus.PASS

    assert found_any, "expected at least one app_version capture"


def test_no_captured_payload_carries_the_instance_public_address() -> None:
    """qBittorrent 5.1 added `last_external_address_v4`/`_v6` to
    `server_state`: the operator's own public IP.

    The capture drops them by key (`tests/integration/_capture.py`),
    which is exact. The text scan deliberately does **not** look for
    bare addresses: `2.0.10.0` is a real Qt version string in
    `app_build_info`, and nothing in text distinguishes a four-part
    version from an IPv4. An ambiguous pattern stays out of a blocking
    gate -- see AGENTS.md, "rien de probabiliste".
    """
    offenders = []
    for path in sorted(CAPTURED_FIXTURES_ROOT.rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8")).get("payload")
        if not isinstance(payload, dict):
            continue
        leaked = [key for key in payload if "external_address" in key]
        if leaked:
            offenders.append((path.name, leaked))

    assert not offenders, f"captured payload(s) carry an address: {offenders}"


def test_no_captured_fixture_leaks_a_real_secret() -> None:
    from tests.compatibility._security_scan import scan_text

    for fixture in load_all_captured_fixtures():
        text = fixture.path.read_text(encoding="utf-8")
        violations = scan_text(text)
        assert not violations, f"{fixture.path}: {violations}"


def test_private_reads_unknown_on_captures_that_do_not_expose_it() -> None:
    """`private` ships from qBittorrent 5.0. On a 4.6.7 capture the field
    is genuinely absent, so it must read unknown -- never `False`, which
    would make `--public` match every torrent on an older instance.

    Asserted against whichever captures exist rather than a hardcoded
    version list, so this stays true as the matrix grows.
    """
    seen_absent = False
    seen_present = False

    for matrix_id in discover_matrix_ids():
        for fixture in load_captured_fixtures(matrix_id):
            if fixture.name != "torrent_complete":
                continue
            value = get_optional_bool(fixture.payload, "private")
            if "private" in fixture.payload:
                assert isinstance(value, bool), fixture.path
                seen_present = True
            else:
                assert value is None, fixture.path
                seen_absent = True

    assert seen_absent, "no capture without `private` -- 4.6.7 evidence lost"
    assert seen_present, "no capture with `private` -- 5.x evidence lost"


_INDIVIDUAL_MEASURE_FIELDS = (
    "downloaded",
    "uploaded",
    "seeding_time",
    "added_on",
    "completion_on",
    "last_activity",
)


@pytest.mark.parametrize("matrix_id", discover_matrix_ids())
def test_every_captured_version_reports_the_individual_measures(
    matrix_id: str,
) -> None:
    """The six measures the central model carries are present, and
    integer-shaped, on every captured qBittorrent version -- so reading
    them rests on no version assumption.

    `seeding_time`/`added_on`/`completion_on`/`last_activity` are
    normalized to `0` at capture time (see each fixture's
    `fields_normalized`), which is evidence of their shape, never of
    real elapsed time -- hence no assertion on their magnitude here.
    """
    found_any = False

    for fixture in load_captured_fixtures(matrix_id):
        if fixture.name != "torrent_complete":
            continue
        found_any = True
        for field_name in _INDIVIDUAL_MEASURE_FIELDS:
            assert field_name in fixture.payload, (fixture.path, field_name)
            assert isinstance(fixture.payload[field_name], int), (
                fixture.path,
                field_name,
            )

    assert found_any, f"no torrent_complete capture for {matrix_id}"


@pytest.mark.parametrize("matrix_id", discover_matrix_ids())
def test_a_captured_torrent_builds_a_complete_central_model(
    matrix_id: str,
) -> None:
    for fixture in load_captured_fixtures(matrix_id):
        if fixture.name != "torrent_complete":
            continue
        snapshot = build_torrent_snapshot(fixture.payload)

        assert snapshot.downloaded == fixture.payload["downloaded"]
        assert snapshot.uploaded == fixture.payload["uploaded"]
        # Normalized to 0 at capture: a timestamp of 0 is qBittorrent's
        # "not recorded", so the model must report absence rather than
        # 1970 -- the exact reading a bounded filter also performs.
        assert snapshot.added_at is None
        assert snapshot.completed_at is None
        assert snapshot.last_activity_at is None


# The eight `server_state` fields the Overview's Session window reads,
# beyond the lifetime totals `InstanceStats` already carried. Listed as
# a fixed baseline so a capture that stopped reporting one is caught
# here rather than as a blank cell on screen.
_SERVER_STATE_SESSION_FIELDS = (
    "dl_info_data",
    "up_info_data",
    "dht_nodes",
    "dl_rate_limit",
    "up_rate_limit",
    "use_alt_speed_limits",
    "free_space_on_disk",
    "queueing",
)


@pytest.mark.parametrize("matrix_id", discover_matrix_ids())
def test_every_captured_version_reports_the_session_fields(
    matrix_id: str,
) -> None:
    found_any = False

    for fixture in load_captured_fixtures(matrix_id):
        if fixture.name != "sync_maindata_server_state":
            continue
        found_any = True
        for field_name in _SERVER_STATE_SESSION_FIELDS:
            assert field_name in fixture.payload, (fixture.path, field_name)

    assert found_any, f"no server_state capture for {matrix_id}"


@pytest.mark.parametrize("matrix_id", discover_matrix_ids())
def test_a_captured_server_state_builds_complete_instance_stats(
    matrix_id: str,
) -> None:
    for fixture in load_captured_fixtures(matrix_id):
        if fixture.name != "sync_maindata_server_state":
            continue
        stats = build_instance_stats_from_server_state(fixture.payload)

        assert stats.session_downloaded_bytes == fixture.payload["dl_info_data"]
        assert stats.session_uploaded_bytes == fixture.payload["up_info_data"]
        assert stats.dht_nodes == fixture.payload["dht_nodes"]
        assert stats.download_rate_limit == fixture.payload["dl_rate_limit"]
        assert stats.upload_rate_limit == fixture.payload["up_rate_limit"]
        assert (
            stats.alternative_limits_enabled
            == fixture.payload["use_alt_speed_limits"]
        )
        assert stats.queueing_enabled == fixture.payload["queueing"]


@pytest.mark.parametrize("matrix_id", discover_matrix_ids())
def test_the_three_sentinels_never_become_a_measure(matrix_id: str) -> None:
    """A fresh instance reports `-1` free space, `'-'` ratio and a
    `firewalled` connection on every captured version. None of the three
    may reach a caller as a number it could format."""
    for fixture in load_captured_fixtures(matrix_id):
        if fixture.name != "sync_maindata_server_state":
            continue
        payload = fixture.payload
        stats = build_instance_stats_from_server_state(payload)

        assert payload["free_space_on_disk"] < 0, fixture.path
        assert stats.free_space_bytes is None

        assert isinstance(payload["global_ratio"], str), fixture.path
        assert stats.all_time_ratio is None

        assert payload["connection_status"] == "firewalled", fixture.path
        assert stats.connection_status == "firewalled"
