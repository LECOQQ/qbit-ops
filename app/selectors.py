"""Resolve a torrent hash or unique hash prefix to a concrete torrent.

Kept free of Typer and Rich so it stays independently unit-testable and
reusable by any future interface (CLI, TUI) without pulling in
presentation concerns. See docs/PHILOSOPHY.md §3: the infohash is the
primary identifier, and an operation must never silently affect several
torrents because a selector was ambiguous.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.qbit_fields import get_field_as_string


@dataclass(frozen=True)
class ResolvedTorrent:
    """Identify one torrent resolved from a hash selector."""

    hash: str
    name: str


class TorrentSelectorError(Exception):
    """Base class for torrent hash selector errors."""


class InvalidTorrentSelectorError(TorrentSelectorError, ValueError):
    """Report an empty or otherwise malformed hash selector."""


class TorrentNotFoundError(TorrentSelectorError):
    """Report that no torrent matches the given hash selector."""

    def __init__(self, value: str) -> None:
        """Store the selector value that matched no torrent."""
        self.value = value
        super().__init__(f"No torrent matches hash '{value}'.")


class AmbiguousTorrentHashError(TorrentSelectorError):
    """Report that a hash prefix matches more than one torrent."""

    def __init__(
        self,
        value: str,
        candidates: tuple[ResolvedTorrent, ...],
    ) -> None:
        """Store the ambiguous selector and its sorted candidates."""
        self.value = value
        self.candidates = candidates
        super().__init__(
            f"Hash prefix '{value}' matches {len(candidates)} torrents."
        )


def resolve_torrent_hash(
    torrents: Iterable[Any],
    value: str,
) -> ResolvedTorrent:
    """Resolve a complete hash or unique hash prefix to one torrent.

    Matching is case-insensitive against each torrent's `hash` field,
    read through the same field-access helpers as the rest of
    `app.torrents` so unusual torrent objects (mappings or
    attribute-based) behave identically. A complete hash is simply an
    unambiguous prefix equal to the full hash, so both cases share one
    code path.

    Raises `InvalidTorrentSelectorError` for an empty selector,
    `TorrentNotFoundError` when nothing matches, and
    `AmbiguousTorrentHashError` when several torrents share the prefix.
    Candidates in the latter are sorted deterministically by hash, then
    name.
    """
    normalized_value = value.strip().lower()
    if normalized_value == "":
        raise InvalidTorrentSelectorError(
            "Provide a non-empty torrent hash or hash prefix."
        )

    candidates: list[ResolvedTorrent] = []
    for torrent in torrents:
        torrent_hash = get_field_as_string(torrent, "hash")
        if torrent_hash == "":
            continue
        if torrent_hash.lower().startswith(normalized_value):
            candidates.append(
                ResolvedTorrent(
                    hash=torrent_hash,
                    name=get_field_as_string(torrent, "name"),
                )
            )

    if not candidates:
        raise TorrentNotFoundError(value)

    if len(candidates) > 1:
        candidates.sort(
            key=lambda candidate: (
                candidate.hash.lower(),
                candidate.name.casefold(),
            )
        )
        raise AmbiguousTorrentHashError(value, tuple(candidates))

    return candidates[0]
