"""Enrich a selection with per-torrent tracker data -- the INSPECT stage.

SELECT answers "which torrents?" from a single `torrents_info()` call.
INSPECT answers "what trackers do they announce to?", which costs one
`torrents_trackers()` call per torrent -- so it always runs *after* the
cheap filters have narrowed the set, never before.

Deliberately factual: it attaches what qBittorrent returned and nothing
more. Interpreting those trackers -- matching a host, classifying
health, redacting a passkey -- stays in `qbit_core.features.trackers`
and its callers, which is also what keeps `shared/` free of any
dependency on `features/`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from qbit_core.qbit.fields import get_active_tracker_urls
from qbit_core.shared.selection import Selection
from qbit_core.shared.torrent_states import TorrentSnapshot


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
        """Return announce URLs excluding disabled pseudo-trackers."""
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


def inspect_trackers(
    reader: Any,
    selection: Selection,
    *,
    on_progress: Callable[[int, int], None] | None = None,
    tolerate_lookup_errors: bool = False,
) -> Inspection:
    """Call `torrents_trackers()` once per selected torrent, in order.

    `on_progress(completed, total)` fires after every lookup, failed or
    not, so progress reflects calls actually made.

    `tolerate_lookup_errors` is a real policy difference between
    callers, not a convenience: a status report must stay readable while
    surfacing that some torrents could not be read (`lookup_failures`),
    whereas a filtered listing must fail loudly rather than silently
    omit torrents whose trackers could not be checked.
    """
    inspected: list[InspectedTorrent] = []
    total = len(selection.matched)

    for index, snapshot in enumerate(selection.matched, start=1):
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
