"""Enrich a selection with per-torrent tracker data -- the INSPECT stage.

SELECT answers "which torrents?" from a single `torrents_info()` call.
INSPECT answers "what trackers do they announce to?". qBittorrent's Web
API exposes two ways to get there: `torrents_trackers()`, one call per
torrent, or `torrents_info(include_trackers=True)`, which embeds each
torrent's tracker list in the same response `torrents_info()` already
returns -- but only on Web API >= 2.11.4 (qbittorrent-api's own
`torrents_info` docstring; confirmed against qBittorrent's own
`TorrentsController::infoAction`, which inserts the `"trackers"` key
only when the `includeTrackers` request parameter is honored). Two of
the four matrix entries in `qbittorrent-matrix.toml` predate that floor
(`qbit-4.6.7` at Web API 2.9.3, `qbit-5.0.0` at 2.11.2).

Whether a given server actually honored the request is never inferred
from its reported version here -- it is read off the response itself,
per torrent: a server that does not support `include_trackers` simply
omits the `"trackers"` key, which `get_optional_field` distinguishes
from a present-but-empty list. A hash missing from the bulk result
falls back to `torrents_trackers()` for that one torrent, so the
observed output is identical either way -- only the call count differs.

**Trap**: a qbittorrent-api `TorrentDictionary` is a `dict` subclass,
but it also defines `trackers` as a live *property* that calls
`torrents_trackers()` again. `dict.get("trackers")`/`get_optional_field`
read the raw embedded data qbittorrent-api already fetched;
`getattr(item, "trackers")` would silently issue a fresh per-torrent
API call (or raise, with no client attached) instead of reading it --
reintroducing exactly the cost this module exists to avoid. Never read
`"trackers"` from a torrent item any other way.

Deliberately factual: it attaches what qBittorrent returned and nothing
more. Interpreting those trackers -- matching a host, classifying
health, redacting a passkey -- stays in `qbit_core.features.trackers`
and its callers, which is also what keeps `shared/` free of any
dependency on `features/`.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from qbit_core.qbit.fields import (
    get_active_tracker_urls,
    get_field_as_string,
    get_optional_field,
)
from qbit_core.shared.selection import (
    Selection,
    SelectionRequest,
    select_from_items,
)
from qbit_core.shared.torrent_states import TorrentSnapshot

__all__ = [
    "select_and_inspect",
]


@dataclass(frozen=True)
class InspectedTorrent:
    """One torrent plus the tracker entries qBittorrent reported for it.

    `raw_trackers` holds the untouched API entries, because consumers
    need different views of them: active URLs for host matching, raw
    status and message for health classification. `lookup_failed` marks
    a torrent whose `torrents_trackers()` call raised and was tolerated
    -- its `raw_trackers` is empty, which must never be read as "this
    torrent has no trackers".
    """

    snapshot: TorrentSnapshot
    raw_trackers: tuple[Any, ...]
    lookup_failed: bool = False

    @property
    def active_tracker_urls(self) -> tuple[str, ...]:
        """Return real announce URLs: no disabled tracker, no DHT/PeX/LSD."""
        return tuple(get_active_tracker_urls(self.raw_trackers))

    @property
    def tracker_count(self) -> int:
        """Count active trackers. Always an integer -- an inspected
        torrent with no tracker is `0`, never "unknown"."""
        return len(self.active_tracker_urls)


@dataclass(frozen=True)
class Inspection:
    """A `Selection` and the tracker data collected for each of its torrents.

    Holding an `Inspection` rather than a bare `Selection` is what
    distinguishes "tracker data was collected" from "it was not" -- the
    distinction consumers must preserve when rendering a tracker count.
    """

    selection: Selection
    torrents: tuple[InspectedTorrent, ...]

    @property
    def lookup_failures(self) -> int:
        """Count torrents whose tracker lookup failed and was tolerated."""
        return sum(1 for torrent in self.torrents if torrent.lookup_failed)


def _extract_bulk_trackers(items: Iterable[Any]) -> dict[str, tuple[Any, ...]]:
    """Pull each item's already-embedded `"trackers"` field, when present.

    A hash absent from the result means its item did not carry the
    field -- `include_trackers` unsupported or silently ignored by the
    server -- never something inferred from a reported version.
    """
    by_hash: dict[str, tuple[Any, ...]] = {}
    for item in items:
        raw_trackers = get_optional_field(item, "trackers")
        if raw_trackers is None:
            continue
        item_hash = get_field_as_string(item, "hash")
        if item_hash == "":
            continue
        by_hash[item_hash] = tuple(raw_trackers)
    return by_hash


def _fetch_trackers_in_bulk(
    reader: Any, hashes: Iterable[str]
) -> dict[str, tuple[Any, ...]]:
    """Attempt one `torrents_info(include_trackers=True)` call for `hashes`.

    Bounded, one-shot overhead: if the server does not honor
    `include_trackers` (or the call fails outright), this costs exactly
    one wasted request against the per-torrent fallback that follows --
    never a per-torrent one. `torrent_hashes` keeps the request scoped
    to what was actually selected, rather than the whole library.
    """
    hash_list = list(hashes)
    if not hash_list:
        return {}
    try:
        items = list(
            reader.torrents_info(
                include_trackers=True, torrent_hashes=hash_list
            )
        )
    except Exception:
        return {}
    return _extract_bulk_trackers(items)


def _build_inspection(
    reader: Any,
    selection: Selection,
    bulk_trackers: dict[str, tuple[Any, ...]],
    *,
    on_progress: Callable[[int, int], None] | None,
    tolerate_lookup_errors: bool,
) -> Inspection:
    """Assemble an `Inspection`, using `bulk_trackers` where it has a hash
    and falling back to `torrents_trackers()` per torrent otherwise."""
    inspected: list[InspectedTorrent] = []
    total = len(selection.matched)

    for index, snapshot in enumerate(selection.matched, start=1):
        raw_trackers = bulk_trackers.get(snapshot.hash)
        if raw_trackers is None:
            try:
                raw_trackers = tuple(reader.torrents_trackers(snapshot.hash))
            except Exception:
                if not tolerate_lookup_errors:
                    raise
                inspected.append(
                    InspectedTorrent(
                        snapshot=snapshot, raw_trackers=(), lookup_failed=True
                    )
                )
                if on_progress is not None:
                    on_progress(index, total)
                continue

        inspected.append(
            InspectedTorrent(snapshot=snapshot, raw_trackers=raw_trackers)
        )
        if on_progress is not None:
            on_progress(index, total)

    return Inspection(selection=selection, torrents=tuple(inspected))


def inspect_trackers(
    reader: Any,
    selection: Selection,
    *,
    on_progress: Callable[[int, int], None] | None = None,
    tolerate_lookup_errors: bool = False,
) -> Inspection:
    """Collect tracker data for every torrent in `selection`.

    Tries one `torrents_info(include_trackers=True)` call covering the
    whole selection first; a torrent whose item did not carry the
    `"trackers"` field (server does not support or honor the parameter)
    falls back to its own `torrents_trackers()` call, exactly the prior
    behavior. `on_progress(completed, total)` fires once per torrent, in
    order, regardless of which path served it.

    `tolerate_lookup_errors` is a real policy difference between
    callers, not a convenience: a status report must stay readable while
    surfacing that some torrents could not be read (`lookup_failures`),
    whereas a filtered listing must fail loudly rather than silently
    omit torrents whose trackers could not be checked.
    """
    bulk_trackers = _fetch_trackers_in_bulk(
        reader, (snapshot.hash for snapshot in selection.matched)
    )
    return _build_inspection(
        reader,
        selection,
        bulk_trackers,
        on_progress=on_progress,
        tolerate_lookup_errors=tolerate_lookup_errors,
    )


def select_and_inspect(
    reader: Any,
    request: SelectionRequest,
    *,
    on_progress: Callable[[int, int], None] | None = None,
    tolerate_lookup_errors: bool = False,
) -> Inspection:
    """Run SELECT then INSPECT from a single `torrents_info()` call.

    The composition every tracker operation plans from. Every caller of
    this function needs tracker data unconditionally, so the one SELECT
    call it must make anyway asks for `include_trackers=True` directly
    -- no separate INSPECT-stage call, no wasted request, on a server
    that honors it. On one that does not, the same response (still a
    valid, if trackers-less, `torrents_info()` result) feeds SELECT as
    usual, and INSPECT falls back to `torrents_trackers()` per torrent,
    exactly the prior behavior.

    A request whose filters need tracker data is rejected: narrowing on
    a tracker host is an interpretation of the inspection result, so it
    belongs to the caller, after this returns.
    """
    if request.requires_inspection:
        raise ValueError(
            "select_and_inspect cannot resolve a tracker filter itself; "
            "narrow the returned Inspection instead."
        )

    items = list(reader.torrents_info(include_trackers=True))
    selection = select_from_items(items, request)
    bulk_trackers = _extract_bulk_trackers(items)
    return _build_inspection(
        reader,
        selection,
        bulk_trackers,
        on_progress=on_progress,
        tolerate_lookup_errors=tolerate_lookup_errors,
    )
