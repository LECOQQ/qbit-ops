"""Compatibility contract tests: torrent payload fixtures.

Exercises the exact production boundary functions the CLI and TUI use
(`qbit_core.qbit.fields.get_field_as_*`,
`qbit_core.shared.torrent_states.classify_torrent_state`) against fixture
payloads -- never a separate fixture-only parsing path.
"""

from __future__ import annotations

from qbit_core.qbit.fields import (
    get_field_as_float,
    get_field_as_int,
    get_field_as_string,
)
from qbit_core.shared.torrent_states import (
    classify_torrent_state,
    is_stopped_state,
)
from tests.compatibility._fixture_loader import load_fixture


def test_ordinary_downloading_torrent_fields_are_read_correctly() -> None:
    fixture = load_fixture("torrents", "ordinary_downloading")
    torrent = fixture.payload

    assert get_field_as_string(torrent, "hash") == (
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    assert get_field_as_string(torrent, "state") == "downloading"
    assert get_field_as_float(torrent, "progress") == 0.42
    assert get_field_as_string(torrent, "category") == "linux"
    assert get_field_as_int(torrent, "size") == 4700372992
    assert classify_torrent_state("downloading") == "downloading"


def test_ordinary_seeding_torrent_classifies_as_seeding() -> None:
    fixture = load_fixture("torrents", "ordinary_seeding")
    torrent = fixture.payload

    state = get_field_as_string(torrent, "state")
    assert classify_torrent_state(state) == "seeding"
    assert is_stopped_state(state) is False


def test_stopped_5x_vocabulary_classifies_as_stopped() -> None:
    fixture = load_fixture("torrents", "stopped_state_5x")
    state = get_field_as_string(fixture.payload, "state")

    assert is_stopped_state(state) is True


def test_paused_4x_vocabulary_classifies_as_stopped() -> None:
    """4.x's `paused*` and 5.x's `stopped*` vocabularies both map to
    the same qbit-ops concept."""
    fixture = load_fixture("torrents", "paused_state_4x")
    state = get_field_as_string(fixture.payload, "state")

    assert is_stopped_state(state) is True


def test_stalled_torrent_classifies_as_stalled() -> None:
    fixture = load_fixture("torrents", "stalled")
    state = get_field_as_string(fixture.payload, "state")

    assert classify_torrent_state(state) == "stalled"


def test_errored_torrent_classifies_as_errored() -> None:
    fixture = load_fixture("torrents", "errored")
    state = get_field_as_string(fixture.payload, "state")

    assert classify_torrent_state(state) == "errored"


def test_completed_torrent_has_full_progress() -> None:
    fixture = load_fixture("torrents", "completed")
    assert get_field_as_float(fixture.payload, "progress") == 1.0


def test_missing_optional_fields_use_documented_fallbacks() -> None:
    fixture = load_fixture("torrents", "missing_optional_fields")
    torrent = fixture.payload

    assert get_field_as_string(torrent, "category") == ""
    assert get_field_as_string(torrent, "save_path") == ""
    assert get_field_as_float(torrent, "ratio") == 0.0
    assert get_field_as_int(torrent, "dlspeed") == 0
    assert get_field_as_int(torrent, "upspeed") == 0
    assert get_field_as_int(torrent, "added_on") == 0
    # Mandatory-in-practice fields, present here, must still read fine.
    assert get_field_as_string(torrent, "hash") != ""
    assert get_field_as_string(torrent, "state") == "downloading"


def test_explicit_none_fields_use_the_same_fallbacks_as_missing_fields() -> (
    None
):
    fixture = load_fixture("torrents", "explicit_none_fields")
    torrent = fixture.payload

    assert get_field_as_string(torrent, "category") == ""
    assert get_field_as_float(torrent, "ratio") == 0.0
    assert get_field_as_int(torrent, "dlspeed") == 0
    assert get_field_as_int(torrent, "upspeed") == 0
    assert get_field_as_string(torrent, "save_path") == ""
    assert get_field_as_int(torrent, "added_on") == 0


def test_unknown_state_classifies_as_unknown_not_an_exception() -> None:
    """`forcedMetaDL` is a real qBittorrent state absent from every
    classification set."""
    fixture = load_fixture("torrents", "unknown_state")
    state = get_field_as_string(fixture.payload, "state")

    assert classify_torrent_state(state) == "unknown"


def test_extra_future_field_is_silently_ignored() -> None:
    fixture = load_fixture("torrents", "extra_future_field")
    torrent = fixture.payload

    assert get_field_as_string(torrent, "hash") != ""
    assert "a_field_from_a_future_qbittorrent_release" in torrent
    assert classify_torrent_state(get_field_as_string(torrent, "state")) == (
        "downloading"
    )


def test_v1_infohash_shape_is_read_as_an_opaque_string() -> None:
    fixture = load_fixture("torrents", "v1_hash")
    torrent_hash = get_field_as_string(fixture.payload, "hash")

    assert len(torrent_hash) == 40
    assert torrent_hash == fixture.payload["hash"]


def test_v2_or_hybrid_infohash_shape_is_read_as_an_opaque_string() -> None:
    """qbit-ops treats 'hash' as an opaque string -- a 64-character
    v2/hybrid-shaped value must work identically to a 40-character
    v1 hash."""
    fixture = load_fixture("torrents", "v2_hash")
    torrent_hash = get_field_as_string(fixture.payload, "hash")

    assert len(torrent_hash) == 64
    assert torrent_hash == fixture.payload["hash"]
    assert len(torrent_hash) != len(
        get_field_as_string(load_fixture("torrents", "v1_hash").payload, "hash")
    )
