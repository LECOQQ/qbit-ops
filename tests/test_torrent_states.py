"""Test torrent state normalization and the `TorrentSnapshot` model."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from qbit_core.shared.torrent_states import (
    TorrentSnapshot,
    build_torrent_snapshot,
    is_actively_downloading_state,
    is_actively_uploading_state,
    is_checking_state,
    is_errored_state,
)
from tests.support import make_torrent


def test_build_torrent_snapshot_reads_every_field() -> None:
    torrent = make_torrent(
        hash="a" * 40,
        name="Debian netinst",
        category="linux",
        state="downloading",
        progress=0.42,
        ratio=1.5,
        size=4_700_372_992,
        dlspeed=1_024,
        upspeed=512,
        dl_limit=500_000,
        up_limit=0,
        tags="stale, keep",
        downloaded=3_000_000_000,
        uploaded=4_500_000_000,
        seeding_time=86_400,
        added_on=1_700_000_000,
        completion_on=1_700_086_400,
        last_activity=1_700_090_000,
    )

    snapshot = build_torrent_snapshot(torrent)

    assert snapshot == TorrentSnapshot(
        hash="a" * 40,
        name="Debian netinst",
        category="linux",
        state="downloading",
        state_group="downloading",
        size=4_700_372_992,
        progress=0.42,
        ratio=1.5,
        download_rate=1_024,
        upload_rate=512,
        download_limit=500_000,
        upload_limit=0,
        tags=("keep", "stale"),
        downloaded=3_000_000_000,
        uploaded=4_500_000_000,
        seeding_time=86_400,
        added_at=datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC),
        completed_at=datetime(2023, 11, 15, 22, 13, 20, tzinfo=UTC),
        last_activity_at=datetime(2023, 11, 15, 23, 13, 20, tzinfo=UTC),
    )


def test_an_unset_measure_reads_as_absent_never_as_zero_or_the_epoch() -> None:
    """The normalization a policy engine depends on: a marker value must
    not become a real measure a rule could fire on -- and a negative
    marker summed into a byte total would subtract from it."""
    snapshot = build_torrent_snapshot(
        make_torrent(
            downloaded=-1,
            uploaded=-2,
            seeding_time=-1,
            added_on=0,
            completion_on=-1,
            last_activity=0,
        )
    )

    assert snapshot.downloaded is None
    assert snapshot.uploaded is None
    assert snapshot.seeding_time is None
    assert snapshot.added_at is None
    assert snapshot.completed_at is None
    assert snapshot.last_activity_at is None


def test_measures_qbittorrent_never_sent_read_as_absent() -> None:
    snapshot = build_torrent_snapshot(make_torrent())

    assert snapshot.downloaded is None
    assert snapshot.uploaded is None
    assert snapshot.seeding_time is None
    assert snapshot.added_at is None
    assert snapshot.completed_at is None
    assert snapshot.last_activity_at is None


def test_zero_bytes_and_zero_seconds_are_measures_not_unknowns() -> None:
    snapshot = build_torrent_snapshot(
        make_torrent(downloaded=0, uploaded=0, seeding_time=0)
    )

    assert snapshot.downloaded == 0
    assert snapshot.uploaded == 0
    assert snapshot.seeding_time == 0


def test_the_per_torrent_ratio_is_qbittorrents_own_value() -> None:
    """Never derived from the byte counters: a policy and the `--ratio`
    filter must designate exactly the same torrents."""
    snapshot = build_torrent_snapshot(
        make_torrent(ratio=0.25, downloaded=1_000, uploaded=8_000)
    )

    assert snapshot.ratio == 0.25


def test_torrent_snapshot_category_is_raw_not_display_formatted() -> None:
    snapshot = build_torrent_snapshot(make_torrent(category=""))

    assert snapshot.category == ""


# --- Boolean properties, one raw state per case -----------------------------


def _snapshot_with_state(state: str) -> TorrentSnapshot:
    return build_torrent_snapshot(make_torrent(state=state))


def test_actively_downloading_torrent() -> None:
    snapshot = _snapshot_with_state("downloading")

    assert snapshot.is_downloading is True
    assert snapshot.is_uploading is False
    assert snapshot.is_paused is False
    assert snapshot.is_active is True
    assert snapshot.is_checking is False
    assert snapshot.has_error is False


def test_actively_uploading_torrent() -> None:
    snapshot = _snapshot_with_state("uploading")

    assert snapshot.is_uploading is True
    assert snapshot.is_downloading is False
    assert snapshot.is_active is True


def test_paused_download_is_not_reported_as_actively_downloading() -> None:
    """Stays in the "downloading" state group while paused."""
    snapshot = _snapshot_with_state("pausedDL")

    assert snapshot.state_group == "downloading"
    assert snapshot.is_downloading is False
    assert snapshot.is_uploading is False
    assert snapshot.is_paused is True
    assert snapshot.is_active is False


def test_stopped_upload_5x_vocabulary_is_paused_not_uploading() -> None:
    snapshot = _snapshot_with_state("stoppedUP")

    assert snapshot.state_group == "seeding"
    assert snapshot.is_uploading is False
    assert snapshot.is_paused is True
    assert snapshot.is_active is False


def test_checking_torrent() -> None:
    snapshot = _snapshot_with_state("checkingDL")

    assert snapshot.is_checking is True
    assert snapshot.is_downloading is False
    assert snapshot.is_uploading is False
    assert snapshot.is_paused is False
    assert snapshot.is_active is True
    assert snapshot.has_error is False


def test_errored_torrent() -> None:
    snapshot = _snapshot_with_state("error")

    assert snapshot.has_error is True
    assert snapshot.is_paused is False
    assert snapshot.is_active is True
    assert snapshot.is_downloading is False
    assert snapshot.is_uploading is False
    assert snapshot.is_checking is False


def test_stalled_torrent_is_active_but_not_transferring() -> None:
    snapshot = _snapshot_with_state("stalledDL")

    assert snapshot.is_active is True
    assert snapshot.is_paused is False
    assert snapshot.is_downloading is False
    assert snapshot.is_uploading is False
    assert snapshot.is_checking is False
    assert snapshot.has_error is False


def test_unknown_state_reports_every_boolean_false_except_active() -> None:
    snapshot = _snapshot_with_state("forcedMetaDL")

    assert snapshot.state_group == "unknown"
    assert snapshot.is_paused is False
    assert snapshot.is_active is True
    assert snapshot.is_downloading is False
    assert snapshot.is_uploading is False
    assert snapshot.is_checking is False
    assert snapshot.has_error is False


# --- Predicate functions, independent of TorrentSnapshot --------------------


def test_is_actively_downloading_state_excludes_paused_and_stalled() -> None:
    assert is_actively_downloading_state("downloading") is True
    assert is_actively_downloading_state("metaDL") is True
    assert is_actively_downloading_state("forcedDL") is True
    assert is_actively_downloading_state("queuedDL") is True
    assert is_actively_downloading_state("pausedDL") is False
    assert is_actively_downloading_state("stalledDL") is False


def test_is_actively_uploading_state_excludes_paused_and_stalled() -> None:
    assert is_actively_uploading_state("uploading") is True
    assert is_actively_uploading_state("forcedUP") is True
    assert is_actively_uploading_state("queuedUP") is True
    assert is_actively_uploading_state("stoppedUP") is False
    assert is_actively_uploading_state("stalledUP") is False


def test_is_checking_state_covers_every_documented_checking_variant() -> None:
    for state in (
        "checkingDL",
        "checkingUP",
        "checkingResumeData",
        "allocating",
        "moving",
    ):
        assert is_checking_state(state) is True
    assert is_checking_state("downloading") is False


def test_is_errored_state() -> None:
    assert is_errored_state("error") is True
    assert is_errored_state("missingFiles") is True
    assert is_errored_state("downloading") is False


def test_torrent_snapshot_never_exposes_a_raw_qbittorrent_api_object() -> None:
    torrent: dict[str, Any] = make_torrent()
    snapshot = build_torrent_snapshot(torrent)

    for value in (
        snapshot.hash,
        snapshot.name,
        snapshot.category,
        snapshot.state,
        snapshot.state_group,
    ):
        assert isinstance(value, str)
    for value in (snapshot.size, snapshot.download_rate, snapshot.upload_rate):
        assert isinstance(value, int)
    for value in (snapshot.progress, snapshot.ratio):
        assert isinstance(value, float)
    assert not any(value is torrent for value in vars(snapshot).values())
