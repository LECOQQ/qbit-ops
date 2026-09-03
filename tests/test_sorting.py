"""Test `qbit_core.shared.sorting`: the torrent local-sort engine shared
by `torrents list --sort` and the TUI's Torrents table."""

from __future__ import annotations

import pytest

from qbit_core.shared.sorting import (
    SortDirection,
    SortField,
    TorrentSortField,
    parse_torrent_sort_field,
    sort_torrent_snapshots,
)
from qbit_core.shared.torrent_states import (
    TorrentSnapshot,
    build_torrent_snapshot,
)
from tests.support import make_torrent


def _snap(**overrides: object) -> TorrentSnapshot:
    return build_torrent_snapshot(make_torrent(**overrides))


# --- One field at a time -----------------------------------------------


def test_sorts_by_name_case_insensitively() -> None:
    torrents = (
        _snap(hash="a" * 40, name="banana"),
        _snap(hash="b" * 40, name="Apple"),
    )
    result = sort_torrent_snapshots(
        torrents, SortField.NAME, SortDirection.ASCENDING
    )
    assert [t.name for t in result] == ["Apple", "banana"]


def test_sorts_by_state_grouped_not_alphabetically_by_raw_value() -> None:
    # "queuedDL" raw-sorts before "uploading", but the *label* Downloading
    # sorts before Seeding for a different reason -- this proves the
    # grouped label drives the order, not the raw qBittorrent string.
    downloading = _snap(hash="a" * 40, name="A", state="queuedDL")
    seeding = _snap(hash="b" * 40, name="B", state="uploading")
    result = sort_torrent_snapshots(
        (seeding, downloading), SortField.STATE, SortDirection.ASCENDING
    )
    assert [t.hash for t in result] == [downloading.hash, seeding.hash]


def test_sorts_stopped_state_as_its_own_group() -> None:
    """A stopped download and a stopped seed both land in "Stopped",
    distinct from their direction -- unlike `state_group`, which folds
    a stopped torrent back into downloading/seeding."""
    stopped_download = _snap(hash="a" * 40, name="A", state="stoppedDL")
    downloading = _snap(hash="b" * 40, name="B", state="downloading")
    result = sort_torrent_snapshots(
        (downloading, stopped_download),
        SortField.STATE,
        SortDirection.DESCENDING,
    )
    # Descending: "Stopped" > "Downloading" alphabetically.
    assert [t.hash for t in result] == [
        stopped_download.hash,
        downloading.hash,
    ]


def test_sorts_by_progress() -> None:
    low = _snap(hash="a" * 40, name="A", progress=0.1)
    high = _snap(hash="b" * 40, name="B", progress=0.9)
    result = sort_torrent_snapshots(
        (high, low), SortField.PROGRESS, SortDirection.ASCENDING
    )
    assert [t.hash for t in result] == [low.hash, high.hash]


def test_sorts_by_download_rate() -> None:
    slow = _snap(hash="a" * 40, name="A", dlspeed=1)
    fast = _snap(hash="b" * 40, name="B", dlspeed=100)
    result = sort_torrent_snapshots(
        (slow, fast), SortField.DOWN, SortDirection.DESCENDING
    )
    assert [t.hash for t in result] == [fast.hash, slow.hash]


def test_sorts_by_upload_rate() -> None:
    slow = _snap(hash="a" * 40, name="A", upspeed=1)
    fast = _snap(hash="b" * 40, name="B", upspeed=100)
    result = sort_torrent_snapshots(
        (slow, fast), SortField.UP, SortDirection.DESCENDING
    )
    assert [t.hash for t in result] == [fast.hash, slow.hash]


def test_sorts_by_ratio() -> None:
    low = _snap(hash="a" * 40, name="A", ratio=0.5)
    high = _snap(hash="b" * 40, name="B", ratio=3.0)
    result = sort_torrent_snapshots(
        (low, high), SortField.RATIO, SortDirection.DESCENDING
    )
    assert [t.hash for t in result] == [high.hash, low.hash]


def test_sorts_by_category_case_insensitively() -> None:
    zulu = _snap(hash="a" * 40, name="A", category="zulu")
    alpha = _snap(hash="b" * 40, name="B", category="Alpha")
    result = sort_torrent_snapshots(
        (zulu, alpha), SortField.CATEGORY, SortDirection.ASCENDING
    )
    assert [t.hash for t in result] == [alpha.hash, zulu.hash]


def test_sorts_by_size() -> None:
    small = _snap(hash="a" * 40, name="A", size=100)
    large = _snap(hash="b" * 40, name="B", size=900)
    result = sort_torrent_snapshots(
        (small, large), TorrentSortField.SIZE, SortDirection.DESCENDING
    )
    assert [t.hash for t in result] == [large.hash, small.hash]


def test_sorts_by_added_on() -> None:
    old = _snap(hash="a" * 40, name="A", added_on=1_000)
    new = _snap(hash="b" * 40, name="B", added_on=2_000)
    result = sort_torrent_snapshots(
        (new, old), TorrentSortField.ADDED_ON, SortDirection.ASCENDING
    )
    assert [t.hash for t in result] == [old.hash, new.hash]


def test_sorts_by_seeding_time() -> None:
    short = _snap(hash="a" * 40, name="A", seeding_time=10)
    long_ = _snap(hash="b" * 40, name="B", seeding_time=1_000)
    result = sort_torrent_snapshots(
        (short, long_),
        TorrentSortField.SEEDING_TIME,
        SortDirection.DESCENDING,
    )
    assert [t.hash for t in result] == [long_.hash, short.hash]


# --- Determinism (tie-break + repeatability) ----------------------------


def test_ties_break_by_name_then_hash_regardless_of_direction() -> None:
    torrents = (
        _snap(hash="b" * 40, name="Same", ratio=2.0),
        _snap(hash="a" * 40, name="Same", ratio=2.0),
        _snap(hash="c" * 40, name="Zeta", ratio=1.0),
    )
    ascending = sort_torrent_snapshots(
        torrents, SortField.RATIO, SortDirection.ASCENDING
    )
    assert [t.hash for t in ascending] == ["c" * 40, "a" * 40, "b" * 40]

    descending = sort_torrent_snapshots(
        torrents, SortField.RATIO, SortDirection.DESCENDING
    )
    assert [t.hash for t in descending] == ["a" * 40, "b" * 40, "c" * 40]


def test_two_runs_over_an_unchanged_corpus_render_the_same_order() -> None:
    """Acceptance criterion: a stable library sorts identically every
    time, ties included."""
    torrents = (
        _snap(hash="b" * 40, name="Same", size=10),
        _snap(hash="a" * 40, name="Same", size=10),
        _snap(hash="c" * 40, name="Other", size=10),
    )
    first = sort_torrent_snapshots(
        torrents, TorrentSortField.SIZE, SortDirection.DESCENDING
    )
    second = sort_torrent_snapshots(
        torrents, TorrentSortField.SIZE, SortDirection.DESCENDING
    )
    assert [t.hash for t in first] == [t.hash for t in second]


# --- Missing values: grouped at a fixed end, never folded into 0 -------


def test_missing_added_on_sorts_last_ascending() -> None:
    unknown = _snap(hash="a" * 40, name="A")  # no added_on -> None
    known = _snap(hash="b" * 40, name="B", added_on=500)
    result = sort_torrent_snapshots(
        (unknown, known), TorrentSortField.ADDED_ON, SortDirection.ASCENDING
    )
    assert [t.hash for t in result] == [known.hash, unknown.hash]


def test_missing_added_on_sorts_last_descending_too() -> None:
    """The unknown-added_on torrent stays last even when the direction
    reverses -- a fixed, predictable spot rather than one that flips
    with `--desc`."""
    unknown = _snap(hash="a" * 40, name="A")
    known = _snap(hash="b" * 40, name="B", added_on=500)
    result = sort_torrent_snapshots(
        (unknown, known), TorrentSortField.ADDED_ON, SortDirection.DESCENDING
    )
    assert [t.hash for t in result] == [known.hash, unknown.hash]


def test_missing_seeding_time_sorts_last_both_directions() -> None:
    unknown = _snap(hash="a" * 40, name="A")  # no seeding_time -> None
    known = _snap(hash="b" * 40, name="B", seeding_time=42)
    for direction in (SortDirection.ASCENDING, SortDirection.DESCENDING):
        result = sort_torrent_snapshots(
            (unknown, known), TorrentSortField.SEEDING_TIME, direction
        )
        assert [t.hash for t in result] == [known.hash, unknown.hash]


# --- `--sort` field validation -------------------------------------------


def test_parse_torrent_sort_field_accepts_every_declared_value() -> None:
    for field in TorrentSortField:
        assert parse_torrent_sort_field(field.value) is field


def test_parse_torrent_sort_field_rejects_unknown_and_lists_all_ten() -> None:
    with pytest.raises(ValueError) as excinfo:
        parse_torrent_sort_field("taille")

    message = str(excinfo.value)
    assert "Unknown --sort value 'taille'" in message
    for field in TorrentSortField:
        assert field.value in message
