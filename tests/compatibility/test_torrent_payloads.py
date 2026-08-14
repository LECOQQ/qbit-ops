"""Compatibility contract tests: torrent payload fixtures.

Exercises the exact production boundary functions the CLI and TUI use
(`qbit_core.qbit.fields.get_field_as_*`,
`qbit_core.shared.torrent_states.classify_torrent_state`) against fixture
payloads -- never a separate fixture-only parsing path.
"""

from __future__ import annotations

from datetime import UTC, datetime

from qbit_core.qbit.fields import (
    get_field_as_float,
    get_field_as_int,
    get_field_as_string,
    get_optional_float,
    get_optional_int,
)
from qbit_core.shared.torrent_states import (
    build_torrent_snapshot,
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


def test_missing_fields_are_unknown_to_a_bounded_predicate() -> None:
    """Same fixture, opposite reading: `get_field_as_*` answers "what do
    I display" with `0`, `get_optional_*` answers "what do I know" with
    `None`. A predicate must use the second, or `--size-max` would match
    a torrent whose size qBittorrent never sent.
    """
    fixture = load_fixture("torrents", "missing_optional_fields")
    torrent = fixture.payload

    assert get_optional_float(torrent, "ratio") is None
    assert get_optional_int(torrent, "added_on") is None
    assert get_optional_int(torrent, "uploaded") is None
    assert get_optional_int(torrent, "seeding_time") is None
    assert get_optional_int(torrent, "trackers_count") is None
    # Present in this fixture, so genuinely known.
    assert get_optional_int(torrent, "size") == 4700372992
    assert get_optional_float(torrent, "progress") == 0.5


def test_explicit_null_fields_are_unknown_too() -> None:
    fixture = load_fixture("torrents", "explicit_none_fields")
    torrent = fixture.payload

    assert get_optional_float(torrent, "ratio") is None
    assert get_optional_int(torrent, "added_on") is None


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


# --- TorrentSnapshot: the same fixtures, through the qbit_core model -------


def test_snapshot_of_a_4x_paused_download_is_paused_not_downloading() -> None:
    fixture = load_fixture("torrents", "paused_state_4x")
    snapshot = build_torrent_snapshot(fixture.payload)

    assert snapshot.is_paused is True
    assert snapshot.is_downloading is False
    assert snapshot.is_active is False


def test_snapshot_of_a_5x_stopped_torrent_is_paused() -> None:
    fixture = load_fixture("torrents", "stopped_state_5x")
    snapshot = build_torrent_snapshot(fixture.payload)

    assert snapshot.is_paused is True
    assert snapshot.is_active is False


def test_snapshot_of_an_ordinary_downloading_torrent() -> None:
    fixture = load_fixture("torrents", "ordinary_downloading")
    snapshot = build_torrent_snapshot(fixture.payload)

    assert snapshot.is_downloading is True
    assert snapshot.is_paused is False
    assert snapshot.has_error is False


def test_snapshot_of_an_ordinary_seeding_torrent() -> None:
    fixture = load_fixture("torrents", "ordinary_seeding")
    snapshot = build_torrent_snapshot(fixture.payload)

    assert snapshot.is_uploading is True
    assert snapshot.is_paused is False


def test_snapshot_of_a_stalled_torrent_is_active_not_transferring() -> None:
    fixture = load_fixture("torrents", "stalled")
    snapshot = build_torrent_snapshot(fixture.payload)

    assert snapshot.is_active is True
    assert snapshot.is_downloading is False
    assert snapshot.is_uploading is False


def test_snapshot_of_an_errored_torrent() -> None:
    fixture = load_fixture("torrents", "errored")
    snapshot = build_torrent_snapshot(fixture.payload)

    assert snapshot.has_error is True


def test_snapshot_of_an_unknown_state_torrent_has_every_boolean_false() -> None:
    """`forcedMetaDL`: absent from every classification set, must not raise."""
    fixture = load_fixture("torrents", "unknown_state")
    snapshot = build_torrent_snapshot(fixture.payload)

    assert snapshot.state_group == "unknown"
    assert snapshot.is_paused is False
    assert snapshot.is_downloading is False
    assert snapshot.is_uploading is False
    assert snapshot.is_checking is False
    assert snapshot.has_error is False


def test_snapshot_tolerates_missing_optional_fields() -> None:
    fixture = load_fixture("torrents", "missing_optional_fields")
    snapshot = build_torrent_snapshot(fixture.payload)

    assert snapshot.category == ""
    assert snapshot.ratio == 0.0
    assert snapshot.download_rate == 0
    assert snapshot.upload_rate == 0


def test_snapshot_ignores_an_extra_future_field() -> None:
    fixture = load_fixture("torrents", "extra_future_field")
    snapshot = build_torrent_snapshot(fixture.payload)

    assert snapshot.hash != ""
    assert snapshot.is_downloading is True


# --- individual measures: the six fields the model consumes ---------------


def test_the_measures_of_a_seeding_torrent_reach_the_central_model() -> None:
    fixture = load_fixture("torrents", "ordinary_seeding")
    snapshot = build_torrent_snapshot(fixture.payload)

    assert snapshot.downloaded == 4700372992
    assert snapshot.uploaded == 11750932480
    assert snapshot.seeding_time == 2592000
    assert snapshot.added_at == datetime(2023, 11, 3, 8, 26, 40, tzinfo=UTC)
    assert snapshot.completed_at == datetime(2023, 11, 3, 9, 26, 40, tzinfo=UTC)
    assert snapshot.last_activity_at == datetime(
        2023, 11, 26, 12, 0, tzinfo=UTC
    )


def test_missing_measures_reach_the_model_as_unknown_not_as_zero() -> None:
    """The same fixture the coercing accessors read as `0` above: the
    model must report absence, or an aggregate would count a torrent
    qBittorrent said nothing about."""
    fixture = load_fixture("torrents", "missing_optional_fields")
    snapshot = build_torrent_snapshot(fixture.payload)

    assert snapshot.seeding_time is None
    assert snapshot.added_at is None
    assert snapshot.completed_at is None
    assert snapshot.last_activity_at is None


def test_explicitly_null_measures_are_unknown_to_the_model_too() -> None:
    fixture = load_fixture("torrents", "explicit_none_fields")
    snapshot = build_torrent_snapshot(fixture.payload)

    assert snapshot.seeding_time is None
    assert snapshot.added_at is None
