"""Test the hash-centric torrent selector."""

from dataclasses import dataclass

import pytest

from qbit_ops.shared.selectors import (
    AmbiguousTorrentHashError,
    InvalidTorrentSelectorError,
    ResolvedTorrent,
    TorrentNotFoundError,
    resolve_torrent_hash,
)

TORRENTS = [
    {"hash": "abc123def456", "name": "Debian ISO"},
    {"hash": "abc987fed654", "name": "Debian live image"},
    {"hash": "zz00112233ff", "name": "Unrelated torrent"},
]


def test_resolve_torrent_hash_matches_full_lowercase_hash() -> None:
    """Ensure a complete lowercase hash resolves directly."""
    resolved = resolve_torrent_hash(TORRENTS, "zz00112233ff")

    assert resolved == ResolvedTorrent(
        hash="zz00112233ff",
        name="Unrelated torrent",
    )


def test_resolve_torrent_hash_matches_full_hash_case_insensitively() -> None:
    """Ensure an uppercase selector resolves the same lowercase hash."""
    resolved = resolve_torrent_hash(TORRENTS, "ZZ00112233FF")

    assert resolved.hash == "zz00112233ff"
    assert resolved.name == "Unrelated torrent"


def test_resolve_torrent_hash_matches_unique_prefix() -> None:
    """Ensure an unambiguous prefix resolves to its single match."""
    resolved = resolve_torrent_hash(TORRENTS, "zz0011")

    assert resolved.hash == "zz00112233ff"


def test_resolve_torrent_hash_rejects_empty_selector() -> None:
    """Ensure an empty selector fails validation before any matching."""
    with pytest.raises(InvalidTorrentSelectorError):
        resolve_torrent_hash(TORRENTS, "   ")


def test_resolve_torrent_hash_rejects_empty_selector_as_value_error() -> None:
    """Ensure the empty-selector error is also catchable as ValueError."""
    with pytest.raises(ValueError):
        resolve_torrent_hash(TORRENTS, "")


def test_resolve_torrent_hash_raises_not_found_for_no_match() -> None:
    """Ensure a selector matching nothing raises TorrentNotFoundError."""
    with pytest.raises(TorrentNotFoundError) as excinfo:
        resolve_torrent_hash(TORRENTS, "doesnotexist")

    assert excinfo.value.value == "doesnotexist"


def test_resolve_torrent_hash_raises_ambiguous_for_shared_prefix() -> None:
    """Ensure a shared prefix raises with every matching candidate."""
    with pytest.raises(AmbiguousTorrentHashError) as excinfo:
        resolve_torrent_hash(TORRENTS, "abc")

    error = excinfo.value
    assert error.value == "abc"
    assert len(error.candidates) == 2
    assert {candidate.hash for candidate in error.candidates} == {
        "abc123def456",
        "abc987fed654",
    }


def test_resolve_torrent_hash_ambiguous_candidates_are_deterministic() -> None:
    """Ensure ambiguous candidates are sorted the same way every time."""
    reversed_torrents = list(reversed(TORRENTS))

    with pytest.raises(AmbiguousTorrentHashError) as first:
        resolve_torrent_hash(TORRENTS, "abc")
    with pytest.raises(AmbiguousTorrentHashError) as second:
        resolve_torrent_hash(reversed_torrents, "abc")

    assert first.value.candidates == second.value.candidates
    assert first.value.candidates == (
        ResolvedTorrent(hash="abc123def456", name="Debian ISO"),
        ResolvedTorrent(hash="abc987fed654", name="Debian live image"),
    )


@dataclass
class _AttributeTorrent:
    """A non-mapping torrent object, like qbittorrent-api's own objects."""

    hash: str
    name: str


def test_resolve_torrent_hash_supports_attribute_based_objects() -> None:
    """Ensure the resolver reuses the shared safe field-access helpers."""
    attribute_torrents = [
        _AttributeTorrent(hash="feedface0001", name="Attribute torrent"),
    ]

    resolved = resolve_torrent_hash(attribute_torrents, "feedface")

    assert resolved.hash == "feedface0001"
    assert resolved.name == "Attribute torrent"
