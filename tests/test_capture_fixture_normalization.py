"""Unit tests for captured-fixture volatile-field normalization.

Exercises `tests.integration._capture`'s pure normalization and
serialization logic directly, against constructed data -- no Docker
required.
"""

from __future__ import annotations

import json

from tests.integration._capture import (
    VOLATILE_TORRENT_FIELDS,
    _write_fixture,
    normalize_volatile_torrent_fields,
)
from tests.integration._harness import RunningQbitContainer
from tests.integration._matrix import load_matrix_entry


def _fake_container() -> RunningQbitContainer:
    entry = load_matrix_entry("qbit-5.2.3")
    return RunningQbitContainer(
        matrix_entry=entry,
        run_id="unit-test",
        container_name="qbit-ops-it-unit-test-qbit-5.2.3",
        network_name="qbit-ops-it-unit-test-qbit-5.2.3",
        config_dir=None,  # type: ignore[arg-type]
        downloads_dir=None,  # type: ignore[arg-type]
        host="http://127.0.0.1:18113",
        username="admin",
        password="unit-test-password",
        observed_version=entry.expected_version,
        observed_web_api_version=entry.expected_web_api_version,
        observed_architecture=entry.expected_architecture,
    )


def test_normalize_replaces_only_present_volatile_fields() -> None:
    payload = {
        "hash": "a" * 40,
        "added_on": 1785179490,
        "completion_on": 1785179491,
        "last_activity": 1785179490,
        "seeding_time": 2,
        "progress": 1.0,
    }
    normalized = normalize_volatile_torrent_fields(payload)

    for field_name in VOLATILE_TORRENT_FIELDS:
        assert normalized[field_name] == 0
    assert set(normalized) == set(payload)
    assert normalized["hash"] == payload["hash"]
    assert normalized["progress"] == payload["progress"]


def test_normalize_does_not_mutate_the_input_payload() -> None:
    payload = {"added_on": 123, "hash": "b" * 40}
    normalize_volatile_torrent_fields(payload)
    assert payload["added_on"] == 123


def test_normalize_skips_fields_that_are_absent() -> None:
    payload = {"hash": "c" * 40}
    normalized = normalize_volatile_torrent_fields(payload)
    assert normalized == payload


def test_two_captures_differing_only_in_volatile_fields_are_byte_identical(
    tmp_path,
) -> None:
    """Re-running the matrix against an unchanged instance must not dirty
    the working tree."""
    container = _fake_container()

    capture_one = {
        "hash": "d" * 40,
        "name": "qbit-ops-sample-complete.bin",
        "added_on": 1785179490,
        "completion_on": 1785179491,
        "last_activity": 1785179490,
        "seeding_time": 1,
        "progress": 1.0,
    }
    capture_two = {
        **capture_one,
        "added_on": 1785181431,
        "completion_on": 1785181433,
        "last_activity": 1785181431,
        "seeding_time": 2,
    }
    assert capture_one != capture_two

    directory_one = tmp_path / "run-one"
    directory_two = tmp_path / "run-two"
    directory_one.mkdir()
    directory_two.mkdir()

    path_one = _write_fixture(
        directory_one,
        "torrent_complete",
        payload=normalize_volatile_torrent_fields(capture_one),
        description="test",
        container=container,
        sanitization="n/a",
        fields_removed=[],
        fields_normalized=sorted(VOLATILE_TORRENT_FIELDS),
        limitations="test",
    )
    path_two = _write_fixture(
        directory_two,
        "torrent_complete",
        payload=normalize_volatile_torrent_fields(capture_two),
        description="test",
        container=container,
        sanitization="n/a",
        fields_removed=[],
        fields_normalized=sorted(VOLATILE_TORRENT_FIELDS),
        limitations="test",
    )

    text_one = path_one.read_text(encoding="utf-8")
    text_two = path_two.read_text(encoding="utf-8")
    # `capture_date` is the one field allowed to differ -- both ran
    # "today" in this test, so strip it explicitly rather than relying
    # on that coincidence.
    doc_one = json.loads(text_one)
    doc_two = json.loads(text_two)
    del doc_one["_meta"]["capture_date"]
    del doc_two["_meta"]["capture_date"]
    assert doc_one == doc_two
