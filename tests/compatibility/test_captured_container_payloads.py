"""Contract tests for authentic `captured-container` payload fixtures.

Runs the production boundary functions the CLI and TUI use against
payloads captured from real qBittorrent containers (see
`tests/integration/_capture.py`). Reads only committed JSON fixtures;
no Docker is required to run this file.
"""

from __future__ import annotations

import pytest

from qbit_core.features.trackers import classify_raw_tracker_status
from qbit_core.qbit.fields import (
    get_field_as_float,
    get_field_as_string,
    get_optional_bool,
    get_raw_tracker_status,
    get_transfer_rates,
    is_disabled_tracker,
)
from qbit_core.shared.torrent_states import classify_torrent_state
from tests.compatibility._captured_loader import (
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
