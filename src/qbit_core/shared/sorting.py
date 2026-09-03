"""Deterministic, in-memory sort engine for `TorrentSnapshot` rows.

Sort is a rendering concern, never a selection -- the same boundary
`--limit` already holds: it reorders `matched`, it never changes what
is in it. Every function here is pure (`TorrentSnapshot`-in, reordered
tuple-out), so `qbit_ops.tui` and `qbit_ops.cli` share exactly one
grouping and tie-break logic rather than risking two silently
diverging definitions of "sort by state".

`SortField` (the seven columns the TUI's local sort exposes) and
`TorrentSortField` (all ten `torrents list --sort` accepts) are kept
as two distinct enums rather than one grown to ten members: the TUI's
sort-picker modal renders one button pair per `SortField` member, and
`tests/test_tui_app.py::test_sort_screen_exposes_every_declared_sort_option`
asserts that correspondence -- widening the enum would silently create
three field values with no picker entry. Both enums share the same
string values for their seven common fields, and `_SORT_KEY_FUNCS` is
keyed by that plain string, so one table of key functions serves both.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from qbit_core.shared.selection import format_category_label
from qbit_core.shared.torrent_states import (
    TorrentSnapshot,
    describe_torrent_state_label,
)

__all__ = [
    "SortDirection",
    "SortField",
    "SortOrder",
    "TorrentSortField",
    "parse_torrent_sort_field",
    "sort_torrent_snapshots",
]


class SortDirection(StrEnum):
    ASCENDING = "asc"
    DESCENDING = "desc"


class SortField(StrEnum):
    """Torrent-table columns the TUI's local sort can order by."""

    NAME = "name"
    STATE = "state"
    PROGRESS = "progress"
    DOWN = "down"
    UP = "up"
    RATIO = "ratio"
    CATEGORY = "category"


class TorrentSortField(StrEnum):
    """`torrents list --sort`'s full vocabulary.

    The same seven values as `SortField`, plus three the TUI's table
    never shows: `size`, `added_on`, `seeding_time`.
    """

    NAME = "name"
    STATE = "state"
    PROGRESS = "progress"
    DOWN = "down"
    UP = "up"
    RATIO = "ratio"
    CATEGORY = "category"
    SIZE = "size"
    ADDED_ON = "added_on"
    SEEDING_TIME = "seeding_time"


@dataclass(frozen=True)
class SortOrder:
    """The Torrents table's current local sort -- purely presentational,
    computed from the already-collected snapshot, never a qBittorrent
    call. Defaults to Name ascending."""

    field: SortField = SortField.NAME
    direction: SortDirection = SortDirection.ASCENDING


# Keyed by the plain field-value string (not by enum identity) so both
# `SortField` and `TorrentSortField` members -- distinct classes, same
# values for their seven shared fields -- resolve to the same function.
_SORT_KEY_FUNCS: dict[str, Callable[[TorrentSnapshot], Any]] = {
    "name": lambda t: t.name.casefold(),
    "state": lambda t: describe_torrent_state_label(t.state),
    "progress": lambda t: t.progress,
    "down": lambda t: t.download_rate,
    "up": lambda t: t.upload_rate,
    "ratio": lambda t: t.ratio,
    "category": lambda t: format_category_label(t.category).casefold(),
    "size": lambda t: t.size,
    "added_on": lambda t: t.added_at,
    "seeding_time": lambda t: t.seeding_time,
}


def sort_torrent_snapshots(
    matched: Sequence[TorrentSnapshot],
    field: SortField | TorrentSortField,
    direction: SortDirection,
) -> tuple[TorrentSnapshot, ...]:
    """Sort `matched` by `field`/`direction`, purely in-memory -- zero
    API calls.

    Deterministic tie-break: canonical (casefolded) name, then full
    hash, applied via a stable sort so ties always resolve the same
    way regardless of `direction` -- Python's `sorted` is stable, so
    sorting by the tie-break first and the primary key second leaves
    equal-primary-key groups in tie-break order no matter which
    direction the primary key itself is sorted in.

    A torrent whose primary value is `None` (an `added_on`/
    `seeding_time` qBittorrent never reported) is never folded into the
    comparison -- `None` is not a low or a high value, it is an
    unknown. Every such torrent is grouped at the end, after every
    torrent with a real value, regardless of `direction`: a fixed,
    predictable spot rather than one that flips with the sort order.
    """
    key_func = _SORT_KEY_FUNCS[field.value]
    tie_broken = sorted(matched, key=lambda t: (t.name.casefold(), t.hash))
    present = [t for t in tie_broken if key_func(t) is not None]
    missing = [t for t in tie_broken if key_func(t) is None]
    present.sort(key=key_func, reverse=direction is SortDirection.DESCENDING)
    return tuple(present) + tuple(missing)


def parse_torrent_sort_field(value: str) -> TorrentSortField:
    """Validate a raw `--sort` value against `torrents list`'s vocabulary.

    Raises `ValueError` naming every supported field -- mirrors
    `qbit_core.features.torrents._normalize_states`'s "Unknown ...
    Supported values: ..." shape, so an unfamiliar field never sends
    the operator to `--help` instead.
    """
    candidate = value.strip().lower()
    try:
        return TorrentSortField(candidate)
    except ValueError:
        supported = ", ".join(sorted(f.value for f in TorrentSortField))
        raise ValueError(
            f"Unknown --sort value '{value}'. Supported values: {supported}."
        ) from None
