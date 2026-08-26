"""Pure(-ish) TUI state and refresh controller, independent of Textual.

Blocking qBittorrent calls are split into `collect_*()` (blocking) /
`apply_*()` (UI-thread) halves so `qbit_ops.tui.app` can run them off
the Textual event loop; a monotonic `_detail_request_id` guards
against a stale background result overwriting a newer one.

Security boundary: same import-level rule as `qbit_ops.tui.app` (see
its module docstring). `TuiController.build_bulk_plan` additionally
makes `"delete"` inexpressible at runtime, since the import scan in
`tests/test_tui_security.py` cannot see a value passed at a call site.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, get_args

from qbit_core.errors import AppError, ErrorCategory
from qbit_core.features.explain import (
    ExplanationReport,
    build_torrent_explanation,
)
from qbit_core.features.status import (
    InstanceStats,
    StatusSnapshot,
    TransferRates,
)
from qbit_core.features.torrents import (
    BulkTorrentActionPlan,
    TorrentBulkAction,
    apply_bulk_torrent_action,
    build_bulk_action_plan_from_snapshot,
    get_peer_discovery_details,
    get_safe_tracker_details,
    known_tags,
    select_torrents_from_items,
)
from qbit_core.features.trackers import (
    sanitize_tracker_text,
    tracker_host_or_none,
)
from qbit_core.qbit.fields import (
    get_field_as_int,
    get_field_as_string,
    get_field_as_tag_list,
    get_transfer_rates,
)
from qbit_core.qbit.protocols import QbitClient
from qbit_core.shared.execution import MutationStatus
from qbit_core.shared.search import search_snapshots
from qbit_core.shared.selection import (
    Selection,
    TorrentFilter,
    format_category_label,
)
from qbit_core.shared.torrent_states import (
    TorrentSnapshot,
    build_torrent_snapshot,
    classify_torrent_state,
    is_stopped_state,
)
from qbit_ops.app_services import (
    TuiRefreshResult,
    classify_recoverable_qbit_failure,
    collect_tui_refresh,
    create_qbit_client,
)
from qbit_ops.config import ConfigError

TuiBulkAction = Literal[
    "pause",
    "resume",
    "start",
    "reannounce",
    "category_set",
    "category_clear",
    "tag_add",
    "tag_remove",
    "throttle",
]
"""`TorrentBulkAction` minus `"delete"` -- everything the TUI may ever
request. Kept as a real `Literal`, not just a runtime set, so a call
site passing a `TorrentBulkAction`-typed value (which *does* include
`"delete"`) is a Pyright error before it is ever a `ValueError`.
"""

# Derived from `TuiBulkAction` itself, not re-listed, so the runtime
# guard in `build_bulk_plan` can never drift from the type it backs.
_TUI_BULK_ACTIONS: frozenset[TorrentBulkAction] = frozenset(
    get_args(TuiBulkAction)
)

DEFAULT_REFRESH_INTERVAL_SECONDS = 5.0

# The rate graph keeps a clock of its own, deliberately not
# `--interval`. Sampling on the refresh tick would make the window
# `slots x interval` seconds wide, so its axis label would become a
# false claim the moment an operator changed the interval.
#
# One second per sample, one column per sample: coarser than that and
# the trace stops reading as a shape. Nothing here is rounded to a
# nicer number, for the same reason `GRAPH_WINDOW_SECONDS` below isn't.
GRAPH_SAMPLE_INTERVAL_SECONDS = 1.0

# The window is sixty seconds, and *that* is what fixes the plot at
# sixty columns -- the dependency runs window -> width, never the other
# way. Letting the width decide gave an exact but ugly label ("-62s");
# the leftover columns now widen the left gutter instead, so `now` still
# ends flush right and the marks read -60s and -30s.
GRAPH_WINDOW_SECONDS = 60
GRAPH_WINDOW_SLOTS = int(GRAPH_WINDOW_SECONDS / GRAPH_SAMPLE_INTERVAL_SECONDS)

# Enough history for a very wide terminal without unbounded growth; a
# sample is two integers, so this costs nothing worth measuring.
GRAPH_MAX_SLOTS = 600

# Below this a trace reports a shape it does not have, so the plot is
# dropped and only its summary lines are kept.
GRAPH_MIN_SLOTS = 20

# The per-tracker sparklines ride the *refresh* tick, not this one: they
# are derived from the torrent list, and re-fetching that every second
# to move twelve cells would be a real cost for no reading.
TRACKER_SPARKLINE_SLOTS = 12

# The bucket every torrent whose `tracker` field names no host falls
# into: a DHT/PeX/LSD marker, an empty value on a torrent that has not
# announced yet, or something unparsable. Kept as one bucket with its
# own glyph rather than folded into a real tracker's row.
NO_TRACKER_KEY = ""
NO_TRACKER_LABEL = "(no tracker · DHT)"


class TrackerActivityKind(StrEnum):
    """What a tracker's torrents are *doing*, derived from their rates.

    Deliberately not tracker health: nothing here reads an announce
    status, and the vocabulary is disjoint from
    `qbit_core.features.trackers.TrackerHealth` so the two can never be
    confused by resemblance. The real per-endpoint status lives on the
    Trackers page, which pays for a scan when it is opened.
    """

    BOTH = "up+down"
    SEEDING = "seeding"
    LEECHING = "leeching"
    ERRORED = "errored"
    IDLE = "idle"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TrackerActivity:
    """One row of the Overview's Trackers window.

    Attributed by `torrents_info()`'s own `tracker` field -- already
    loaded by the periodic refresh, so this costs no API call. That
    field names only the torrent's *currently working* tracker, which is
    why `torrents` here counts torrents attributed to this host and not
    torrents announcing to it.
    """

    key: str
    label: str
    kind: TrackerActivityKind
    download_rate: int
    upload_rate: int
    errored: int
    torrents: int

    @property
    def total_rate(self) -> int:
        return self.download_rate + self.upload_rate


@dataclass(frozen=True)
class TrackerBreakdown:
    """Every tracker row plus the totals the window's footer states.

    `exclusive`/`shared` read each torrent's own `trackers_count`, so
    they never need a per-torrent `torrents_trackers()` call.
    """

    rows: tuple[TrackerActivity, ...]
    torrents: int
    exclusive: int
    shared: int

    def count_by_kind(self, kind: TrackerActivityKind) -> int:
        return sum(1 for row in self.rows if row.kind is kind)


@dataclass(frozen=True)
class LibraryBreakdown:
    """Size, categories and tags of the current library.

    Derived from the same torrent list every other counter reads. A
    torrent carries any number of tags, so the tag counts deliberately
    do not partition the total the way the category counts do.
    """

    total_size_bytes: int
    categories: tuple[tuple[str, int], ...]
    tags: tuple[tuple[str, int], ...]
    untagged: int


def build_tracker_breakdown(raw_torrents: list[Any]) -> TrackerBreakdown:
    """Group torrents by their working tracker's host. Zero API calls."""
    rates: dict[str, list[int]] = {}
    labels: dict[str, str] = {}
    errored: dict[str, int] = {}
    torrents: dict[str, int] = {}
    exclusive = 0
    shared = 0

    for torrent in raw_torrents:
        key, label = _tracker_identity(torrent)
        labels.setdefault(key, label)
        bucket = rates.setdefault(key, [0, 0])
        bucket[0] += get_field_as_int(torrent, "dlspeed")
        bucket[1] += get_field_as_int(torrent, "upspeed")
        torrents[key] = torrents.get(key, 0) + 1
        state = get_field_as_string(torrent, "state")
        if classify_torrent_state(state) == "errored":
            errored[key] = errored.get(key, 0) + 1

        tracker_count = get_field_as_int(torrent, "trackers_count")
        if tracker_count == 1:
            exclusive += 1
        elif tracker_count > 1:
            shared += 1

    rows = tuple(
        sorted(
            (
                TrackerActivity(
                    key=key,
                    label=labels[key],
                    kind=_tracker_activity_kind(
                        key=key,
                        download_rate=rates[key][0],
                        upload_rate=rates[key][1],
                        errored=errored.get(key, 0),
                    ),
                    download_rate=rates[key][0],
                    upload_rate=rates[key][1],
                    errored=errored.get(key, 0),
                    torrents=torrents[key],
                )
                for key in rates
            ),
            key=lambda row: (-row.total_rate, row.label.casefold()),
        )
    )
    return TrackerBreakdown(
        rows=rows,
        torrents=len(raw_torrents),
        exclusive=exclusive,
        shared=shared,
    )


def _tracker_identity(torrent: Any) -> tuple[str, str]:
    """The host a torrent is attributed to, and how to print it.

    Only the host survives: a raw announce URL carries a passkey, and
    nothing on this screen needs one. `tracker_host_or_none` accepts a
    bare host as well as a URL, so a value that is neither is rejected
    here rather than printed verbatim -- an unparsable string must land
    in the unknown bucket, never on screen.
    """
    host = tracker_host_or_none(get_field_as_string(torrent, "tracker"))
    if host is None or not _is_host_shaped(host):
        return NO_TRACKER_KEY, NO_TRACKER_LABEL
    return host, host


def _is_host_shaped(value: str) -> bool:
    """Whether `value` can be a `host[:port]` and nothing else."""
    return bool(value) and not any(
        char.isspace() or char in "/?#@" for char in value
    )


def _tracker_activity_kind(
    *, key: str, download_rate: int, upload_rate: int, errored: int
) -> TrackerActivityKind:
    """Classify one tracker's row. The order of these tests partitions
    the rows, so the window's legend always adds up to its count."""
    if key == NO_TRACKER_KEY:
        return TrackerActivityKind.UNKNOWN
    if errored > 0:
        return TrackerActivityKind.ERRORED
    if upload_rate > 0 and download_rate > 0:
        return TrackerActivityKind.BOTH
    if upload_rate > 0:
        return TrackerActivityKind.SEEDING
    if download_rate > 0:
        return TrackerActivityKind.LEECHING
    return TrackerActivityKind.IDLE


def build_library_breakdown(raw_torrents: list[Any]) -> LibraryBreakdown:
    """Total size, per-category and per-tag counts. Zero API calls."""
    total_size = 0
    categories: dict[str, int] = {}
    tags: dict[str, int] = {}
    untagged = 0

    for torrent in raw_torrents:
        total_size += get_field_as_int(torrent, "size")
        label = format_category_label(get_field_as_string(torrent, "category"))
        categories[label] = categories.get(label, 0) + 1
        torrent_tags = get_field_as_tag_list(torrent)
        if not torrent_tags:
            untagged += 1
        for tag in torrent_tags:
            tags[tag] = tags.get(tag, 0) + 1

    return LibraryBreakdown(
        total_size_bytes=total_size,
        categories=_ranked(categories),
        tags=_ranked(tags),
        untagged=untagged,
    )


def _ranked(counts: dict[str, int]) -> tuple[tuple[str, int], ...]:
    """Busiest first, then alphabetical -- a stable order across refreshes."""
    return tuple(
        sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))
    )


class RateHistory:
    """A rolling second-by-second window of transfer samples.

    Samples are `int | None`, and the difference is load-bearing:
    `None` means *not measured*, which is never the same claim as a
    measured zero. Three things produce it -- the first minute after
    launch, the stretch while the operator was on another page and the
    sampler was deliberately not running, and a second whose reading
    never came back.

    **A slot belongs to the second that asked for it, not to the second
    its answer arrived in.** `open_slot()` advances the window on the
    clock and `settle()` fills the slot afterwards, so a reply that took
    900 ms and one that took 50 ms still land one column apart. Writing
    the sample on arrival instead put network jitter straight into the
    time axis: measured spacing between recorded samples ran from 0.05 s
    to 1.94 s while the timer itself never left 1.00 s.

    Nothing is persisted; the window dies with the process.
    """

    def __init__(self, slots: int = GRAPH_MAX_SLOTS) -> None:
        self._slots = slots
        self._download: deque[int | None] = deque(maxlen=slots)
        self._upload: deque[int | None] = deque(maxlen=slots)
        self._by_tracker: dict[str, deque[int]] = {}
        # Seconds elapsed since the window opened. Only ever increases,
        # so a slot's identity survives the deque discarding it.
        self._ticks = 0

    @property
    def slots(self) -> int:
        return self._slots

    @property
    def measured(self) -> int:
        """How many of the held slots carry a real measurement."""
        return sum(1 for value in self._download if value is not None)

    @property
    def downloads(self) -> tuple[int | None, ...]:
        return tuple(self._download)

    @property
    def uploads(self) -> tuple[int | None, ...]:
        return tuple(self._upload)

    def window(self, slots: int) -> tuple[list[int | None], list[int | None]]:
        """The newest `slots` samples of each series, oldest first.

        Left-padded with `None` when the history is shorter: the plot is
        always as wide as the panel, so a short history shows as unmeasured
        ground rather than as a narrower chart.
        """
        return (
            _padded(self._download, slots),
            _padded(self._upload, slots),
        )

    @property
    def tracker_keys(self) -> tuple[str, ...]:
        return tuple(self._by_tracker)

    def tracker(self, key: str) -> tuple[int, ...]:
        return tuple(self._by_tracker.get(key, ()))

    @property
    def tracker_peak(self) -> int:
        """The busiest sample across every tracker in the window.

        One shared scale for every sparkline: rows drawn against their
        own maxima would each peak at full ink and stop being
        comparable, which is the only thing a column of them is for.
        """
        return max(
            (max(samples, default=0) for samples in self._by_tracker.values()),
            default=0,
        )

    def open_slot(self) -> int:
        """Advance the window by one second and return that slot's tick.

        Called the moment the timer fires, before anything is asked of
        qBittorrent: the column exists because the second passed, not
        because a reading came back for it.
        """
        self._download.append(None)
        self._upload.append(None)
        self._ticks += 1
        return self._ticks - 1

    def settle(self, tick: int, *, download: int, upload: int) -> bool:
        """Fill the slot `tick` owns. `False` once it has scrolled away."""
        oldest = self._ticks - len(self._download)
        index = tick - oldest
        if not 0 <= index < len(self._download):
            return False
        self._download[index] = max(download, 0)
        self._upload[index] = max(upload, 0)
        return True

    def record_transfer(self, *, download: int, upload: int) -> None:
        """Open a slot and settle it at once -- for tests and fixtures."""
        self.settle(self.open_slot(), download=download, upload=upload)

    def skip(self, samples: int) -> None:
        """Advance the window by `samples` unmeasured seconds.

        Called when the sampler was not running -- the operator was on
        another page. Drawing those seconds as zero would report a still
        library where the truth is that nobody was looking.
        """
        for _ in range(min(max(samples, 0), self._slots)):
            self.open_slot()

    def record_trackers(self, by_tracker: Mapping[str, int]) -> None:
        """Append one refresh tick to every per-tracker series."""
        seen = dict(by_tracker)
        for key in list(self._by_tracker):
            samples = self._by_tracker[key]
            samples.append(max(seen.pop(key, 0), 0))
            # A tracker that has left the library *and* has gone quiet
            # for a whole window stops costing a series.
            if not any(samples):
                del self._by_tracker[key]
        for key, value in seen.items():
            samples = deque(maxlen=TRACKER_SPARKLINE_SLOTS)
            samples.append(max(value, 0))
            self._by_tracker[key] = samples


def _padded(samples: deque[int | None], slots: int) -> list[int | None]:
    held = list(samples)[-slots:]
    return [None] * (slots - len(held)) + held


# Downloading/seeding are the two "active" groups a torrent spends most
# of its life in; everything else (including a still-unclassified raw
# state) stays neutral or, for a genuine error, red -- see
# `qbit_ops.tui.formatting._STATE_STYLES` for the colour side of this,
# kept separate since colour is presentation-only.
_STATE_LABELS: dict[str, str] = {
    "downloading": "Downloading",
    "seeding": "Seeding",
    "stalled": "Stalled",
    "checking": "Checking",
    "errored": "Error",
    "unknown": "Unknown",
}


def _state_label(raw_state: str) -> str:
    """Classify a raw qBittorrent state into one human-readable label.

    Reuses `qbit_core.shared.torrent_states` entirely -- `is_stopped_state`
    for the qBittorrent 4/5 pause-vs-stop split (checked first, since
    `classify_torrent_state` folds a stopped torrent into its seeding/
    downloading *direction* rather than reporting it as stopped), then
    `classify_torrent_state` for every other group. Never a second,
    parallel classifier. Lives here (not `tui.formatting`) so local
    sorting -- which must stay in this Textual-independent module --
    can order by the same human label the table displays.
    """
    if is_stopped_state(raw_state):
        return "Stopped"
    return _STATE_LABELS[classify_torrent_state(raw_state)]


class SortField(StrEnum):
    """Torrent-table columns local sorting can order by."""

    NAME = "name"
    STATE = "state"
    PROGRESS = "progress"
    DOWN = "down"
    UP = "up"
    RATIO = "ratio"
    CATEGORY = "category"


class SortDirection(StrEnum):
    ASCENDING = "asc"
    DESCENDING = "desc"


_SORT_FIELD_LABELS: dict[SortField, str] = {
    SortField.NAME: "Name",
    SortField.STATE: "State",
    SortField.PROGRESS: "Progress",
    SortField.DOWN: "Down speed",
    SortField.UP: "Up speed",
    SortField.RATIO: "Ratio",
    SortField.CATEGORY: "Category",
}


@dataclass(frozen=True)
class SortOrder:
    """The Torrents table's current local sort -- purely presentational,
    computed from the already-collected snapshot, never a qBittorrent
    call. Defaults to Name ascending."""

    field: SortField = SortField.NAME
    direction: SortDirection = SortDirection.ASCENDING

    @property
    def label(self) -> str:
        arrow = "↑" if self.direction is SortDirection.ASCENDING else "↓"
        return f"{_SORT_FIELD_LABELS[self.field]} {arrow}"


_SORT_KEY_FUNCS: dict[SortField, Callable[[TorrentSnapshot], Any]] = {
    SortField.NAME: lambda t: t.name.casefold(),
    SortField.STATE: lambda t: _state_label(t.state),
    SortField.PROGRESS: lambda t: t.progress,
    SortField.DOWN: lambda t: t.download_rate,
    SortField.UP: lambda t: t.upload_rate,
    SortField.RATIO: lambda t: t.ratio,
    SortField.CATEGORY: lambda t: format_category_label(t.category).casefold(),
}


def _sort_torrents(
    matched: tuple[TorrentSnapshot, ...], order: SortOrder
) -> tuple[TorrentSnapshot, ...]:
    """Sort `matched` by `order`, purely in-memory -- zero API calls.

    Deterministic tie-break: canonical (casefolded) name, then full
    hash, applied via a two-pass stable sort so ties always resolve the
    same way regardless of `order.direction` -- Python's `sorted` is
    stable, so sorting by the tie-break first and the primary key
    second leaves equal-primary-key groups in tie-break order no matter
    which direction the primary key itself is sorted in.
    """
    tie_broken = sorted(matched, key=lambda t: (t.name.casefold(), t.hash))
    primary = _SORT_KEY_FUNCS[order.field]
    return tuple(
        sorted(
            tie_broken,
            key=primary,
            reverse=order.direction is SortDirection.DESCENDING,
        )
    )


class ConnectionState(StrEnum):
    """The TUI's high-level connection/session state.

    Distinct from `qbit_core.features.status.Health`: this tracks
    whether the TUI *can talk to qBittorrent at all*, not the
    instance's own operational health (which is carried inside
    `TuiState.status` once connected).
    """

    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    AUTH_FAILED = "auth_failed"
    CONFIG_FAILED = "config_failed"


class Workspace(StrEnum):
    """The TUI's two top-level operator workspaces.

    Purely a UI-navigation concern -- switching never triggers a
    qBittorrent call and never touches `filters`/`search`/`focused_hash`,
    all of which are preserved across a switch by construction (nothing
    in `set_workspace` resets them).
    """

    OVERVIEW = "overview"
    TORRENTS = "torrents"


@dataclass
class TuiState:
    """The TUI's complete state, split into three kinds of field.

    Authoritative, replaced wholesale by a successful refresh:
    `status`, `instance_stats`, `total_torrents`, `stopped_count`.
    Derived, recomputed locally, never fetched: `visible`. Everything
    else is ephemeral UI state, never persisted.
    """

    connection: ConnectionState = ConnectionState.CONNECTING
    status: StatusSnapshot | None = None
    instance_stats: InstanceStats | None = None
    total_torrents: int | None = None
    """Instance-wide torrent count from the last successful refresh;
    `None` until one has landed."""
    stopped_count: int = 0
    tracker_breakdown: TrackerBreakdown | None = None
    library_breakdown: LibraryBreakdown | None = None
    rate_history: RateHistory = field(default_factory=RateHistory)
    """The graph's rolling 60 s window, filled by its own timer rather
    than by the refresh -- see `GRAPH_SAMPLE_INTERVAL_SECONDS`."""
    filters: TorrentFilter = field(default_factory=TorrentFilter)
    search: str = ""
    visible: Selection | None = None
    focused_hash: str | None = None
    focused_tracker_details: list[dict[str, Any]] | None = None
    focused_peer_discovery: list[dict[str, Any]] | None = None
    """DHT/PeX/LSD for the focused torrent. Reported apart from
    `focused_tracker_details`: they are peer-discovery mechanisms,
    never trackers, so they are neither listed nor counted as one."""
    focused_details_fetched_at: datetime | None = None
    focused_tracker_fetch_failed: bool = False
    refreshing: bool = False
    stale: bool = False
    last_successful_refresh: datetime | None = None
    last_error: AppError | None = None
    workspace: Workspace = Workspace.OVERVIEW
    sort: SortOrder = field(default_factory=SortOrder)
    """The Torrents table's active local sort. Presentation-only:
    persists across refresh, filter/search changes, workspace
    switches, and resizing -- reset only by process restart."""
    selected_hashes: set[str] = field(default_factory=set)
    """Explicit multi-selection for bulk actions -- full canonical
    hashes only. Deliberately distinct from `focused_hash` (never
    implied by focus alone), `visible` (the current filter/search
    result), and any CLI `--all` concept: `set()` always means
    "nothing selected", never "every torrent" -- see
    `TuiController.select_all_visible`/`reconcile_selection`.
    """
    categories_available: tuple[str, ...] = ()
    tags_available: tuple[str, ...] = ()
    """Every category/tag declared on the instance, including one no
    torrent currently carries -- read once (`TuiController.
    collect_instance_lists`) and reinvalidated only by our own
    category-creating/tag-adding mutations, never by a TTL (see
    `.agents/features/tui-filters/SPEC.md`, "un cache a durée de vie est la
    mauvaise forme")."""

    def focused_torrent(self) -> TorrentSnapshot | None:
        """Look up the focused torrent's live fields from `visible`.

        Deliberately a lookup, not a stored copy: name/state/progress/
        category/rates always come from the latest periodic snapshot --
        only tracker details are focus-scoped and cached
        (`focused_tracker_details`).
        """
        if self.focused_hash is None or self.visible is None:
            return None
        for torrent in self.visible.matched:
            if torrent.hash == self.focused_hash:
                return torrent
        return None


class TuiController:
    """Own `TuiState` and every state-mutating operation the TUI needs.

    Thread-safety: `_remote_lock` serializes the three blocking entry
    points (`collect_refresh`, `collect_tracker_details`,
    `apply_bulk_plan`) since `qbittorrentapi`'s `requests.Session` is
    not thread-safe -- provable via `max_concurrent_remote_operations`.
    `_client_lock` is a separate, narrower lock guarding only lazy
    client construction; acquisition order is always `_remote_lock`
    then `_client_lock`.

    Every `apply_*` method mutates `self.state` and must only run on
    the UI thread; every `collect_*` method is blocking I/O only and
    safe on a worker thread.
    """

    def __init__(
        self,
        *,
        client_factory: Callable[[], Any] = create_qbit_client,
        host: str | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._host = host
        self._client: Any | None = None
        self._client_lock = threading.Lock()
        self._remote_lock = threading.Lock()
        self._remote_in_flight = 0
        self._max_concurrent_remote = 0
        self._raw_torrents: list[Any] = []
        self._detail_request_id = 0
        self.state = TuiState()

    def reset_connection(self, host: str | None) -> None:
        """Point the controller at a freshly (re)configured instance.

        Called both from the first-run setup form and from a
        reconfigure of an already-running TUI. `_host` feeds the status
        snapshot's redacted host label, and a stale one would mislabel
        every later refresh. `_client` is dropped so the next remote
        call reconnects instead of replaying the old instance's
        session -- the same "force a fresh login" `apply_refresh_
        failure` already does on a recoverable failure; harmless at
        first run, where `_client` is still `None`. The rate-history
        window is replaced rather than kept: its samples carry no
        per-source tag, so appending a new instance's throughput after
        a reconfigure would splice two instances into what reads as one
        continuous trace -- unlike every other authoritative `TuiState`
        field, which the next successful refresh already replaces
        wholesale.
        """
        self._host = host
        self._client = None
        self.state.rate_history = RateHistory()

    @property
    def max_concurrent_remote_operations(self) -> int:
        """High-water mark of simultaneous remote client operations.

        Regression-test instrumentation: must never exceed `1`.
        Incremented/decremented inside `_remote_operation()`, so it
        measures actual overlap rather than merely asserting the lock
        exists.
        """
        return self._max_concurrent_remote

    @contextmanager
    def _remote_operation(self) -> Iterator[QbitClient]:
        """Own the qBittorrent client exclusively for one blocking call.

        Yields the (lazily created) client. Worker threads only. The
        in-flight accounting lives inside the lock, so
        `max_concurrent_remote_operations` is an honest measurement of
        overlap, not a restatement of the lock's existence.
        """
        with self._remote_lock:
            self._remote_in_flight += 1
            self._max_concurrent_remote = max(
                self._max_concurrent_remote, self._remote_in_flight
            )
            try:
                yield self._ensure_client()
            finally:
                self._remote_in_flight -= 1

    def _ensure_client(self) -> QbitClient:
        """Lazily create (or reuse) the qBittorrent client.

        Guarded by `_client_lock` so two callers racing to reconnect
        can never both construct a client at once. A blocking caller
        must go through `_remote_operation()` instead of calling this
        directly -- this method only makes construction safe.
        `_client_factory` itself stays `Any`-typed (the third-party
        `qbittorrentapi.Client` carries no usable stubs); `QbitClient`
        is the honest boundary where that external `Any` becomes a
        typed structural contract for every caller inside the TUI.
        """
        with self._client_lock:
            client = self._client
            if client is None:
                client = self._client_factory()
                self._client = client
            return client

    # -- periodic refresh -------------------------------------------------

    def collect_refresh(self) -> TuiRefreshResult:
        """Perform one periodic refresh's blocking network work only.

        No state mutation -- safe on a background thread. Raises
        unchanged; the caller applies the outcome via
        `apply_refresh_success`/`apply_refresh_failure` on the UI
        thread. Has no opinion on when it's appropriate to call
        (mirrors `refresh()`'s terminal-state guard, but doesn't
        enforce it itself).
        """
        with self._remote_operation() as client:
            return collect_tui_refresh(client, host=self._host)

    def apply_refresh_success(self, result: TuiRefreshResult) -> None:
        """Apply a successful periodic refresh. UI-thread only.

        Reconciles the selection unconditionally: filters now commit
        only on `FiltersScreen`'s `Apply` (see
        `.agents/features/tui-filters/SPEC.md`, "Le filtrage n'est plus en
        direct"), so a draft being edited never touches
        `self.state.filters` in the first place -- there is nothing
        for a periodic tick landing mid-edit to disturb.
        """
        self._raw_torrents = result.raw_torrents
        self.state.status = result.status
        self.state.instance_stats = result.instance_stats
        self.state.total_torrents = result.total_torrents
        self.state.stopped_count = _count_stopped_torrents(result.raw_torrents)
        self.state.tracker_breakdown = build_tracker_breakdown(
            result.raw_torrents
        )
        self.state.library_breakdown = build_library_breakdown(
            result.raw_torrents
        )
        # The sparklines ride this tick, not the graph's: they are
        # derived from the torrent list, which only changes here.
        breakdown = self.state.tracker_breakdown
        self.state.rate_history.record_trackers(
            {row.key: row.total_rate for row in breakdown.rows}
        )
        self.state.connection = ConnectionState.CONNECTED
        self.state.stale = False
        self.state.last_error = None
        self.state.last_successful_refresh = result.status.generated_at
        self.state.refreshing = False
        self._recompute_visible()
        self._reconcile_focus()
        self.reconcile_selection()

    def collect_instance_lists(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Read the instance-wide category and tag sets. Worker threads
        only, two calls (`torrents_categories()`, `torrents_tags()`).

        Called once at startup and once more after a mutation that can
        widen either set (`category_set` with creation, `tag_add`) --
        never on a timer. `torrents_categories()` is called directly,
        with no wrapper, matching every other call site in `qbit_core`;
        `known_tags()` exists because unlike categories, there was no
        read primitive for tags at all before this needed one.
        """
        with self._remote_operation() as client:
            categories = tuple(sorted(client.torrents_categories()))
            tags = known_tags(client)
        return categories, tags

    def apply_instance_lists_success(
        self, categories: tuple[str, ...], tags: tuple[str, ...]
    ) -> None:
        """Apply a successful `collect_instance_lists()`. UI thread only."""
        self.state.categories_available = categories
        self.state.tags_available = tags

    def collect_transfer_rates(self) -> TransferRates:
        """One `transfer_info()` call, and nothing else. Worker threads only.

        The graph's own second-by-second sample. Deliberately *not* the
        five-call refresh: a torrent list per second to move a trace
        would be an absurd price, and the two numbers this needs are the
        cheapest call qBittorrent offers.
        """
        with self._remote_operation() as client:
            download, upload = get_transfer_rates(client.transfer_info())
        return TransferRates(
            download_bytes_per_second=download,
            upload_bytes_per_second=upload,
        )

    def open_rate_slot(self) -> int:
        """Advance the graph one second. UI-thread only, zero API calls.

        Called on the timer tick itself, so the column belongs to the
        second that asked for it however long the answer takes.
        """
        return self.state.rate_history.open_slot()

    def settle_rate_sample(self, tick: int, rates: TransferRates) -> bool:
        """Fill the slot `tick` owns. UI-thread only, zero API calls."""
        return self.state.rate_history.settle(
            tick,
            download=rates.download_bytes_per_second,
            upload=rates.upload_bytes_per_second,
        )

    def skip_rate_samples(self, seconds: int) -> None:
        """Record `seconds` the sampler was not running. UI-thread only."""
        self.state.rate_history.skip(seconds)

    def apply_refresh_failure(self, error: Exception) -> None:
        """Classify and apply a failed periodic refresh. UI-thread only.

        Re-raises unrecognized exceptions for the caller to treat as an
        unexpected internal error -- never silently degraded to
        "unavailable" forever, per
        `qbit_ops.app_services.classify_recoverable_qbit_failure`'s contract.
        """
        self.state.refreshing = False
        if isinstance(error, ConfigError):
            self.mark_config_failed(
                AppError(
                    category=ErrorCategory.CONFIGURATION,
                    code="configuration_invalid",
                    message=str(error),
                )
            )
            return

        failure = classify_recoverable_qbit_failure(error)
        if failure is None:
            raise error
        # Force reconnect (a fresh login) on the next attempt rather than
        # reusing a client that may be in a bad session state.
        self._client = None
        self.state.connection = (
            ConnectionState.AUTH_FAILED
            if failure.code == "authentication_failed"
            else ConnectionState.RECONNECTING
        )
        self.state.stale = self.state.status is not None
        self.state.last_error = AppError(
            category=(
                ErrorCategory.AUTHENTICATION
                if failure.code == "authentication_failed"
                else ErrorCategory.UNAVAILABLE
            ),
            code=failure.code,
            message=failure.message,
        )

    def refresh(self) -> None:
        """Perform one periodic refresh tick synchronously.

        A no-op once `connection` has reached a terminal blocking state
        (`AUTH_FAILED`/`CONFIG_FAILED`), since neither can self-heal
        without a restart. A synchronous convenience composing
        `collect_refresh()` and `apply_refresh_success()`/`_failure()`;
        the real TUI never calls this directly, instead running
        `collect_refresh()` on a thread worker and applying the result
        on the UI thread so I/O never blocks the event loop.
        """
        if self.state.connection in (
            ConnectionState.AUTH_FAILED,
            ConnectionState.CONFIG_FAILED,
        ):
            return

        self.state.refreshing = True
        try:
            result = self.collect_refresh()
        except Exception as error:
            self.apply_refresh_failure(error)
            return

        self.apply_refresh_success(result)

    def mark_config_failed(self, error: AppError) -> None:
        """Enter the blocking configuration-failure state.

        Called once, before the refresh loop ever starts (mirrors
        `status`/`doctor`'s pre-loop configuration check) -- never
        retried, since invalid local configuration cannot self-heal.
        """
        self.state.connection = ConnectionState.CONFIG_FAILED
        self.state.last_error = error

    # -- workspace navigation (always local, no I/O) ------------------------

    def set_sort(self, order: SortOrder) -> None:
        """Apply a new local sort order. Zero qBittorrent API calls.

        Purely a reordering of `visible.matched` -- membership,
        `focused_hash`, and `selected_hashes` are all untouched, so
        focus and selection survive a sort change unchanged.
        """
        self.state.sort = order
        self._recompute_visible()

    def set_workspace(self, workspace: Workspace) -> None:
        """Switch the active workspace. Zero qBittorrent API calls.

        Deliberately touches nothing else: `filters`, `search`,
        `focused_hash`, and `focused_tracker_details` all survive a
        workspace switch untouched.
        """
        self.state.workspace = workspace

    # -- filters / search (always local, no I/O) ---------------------------

    def set_filters(self, filters: TorrentFilter) -> None:
        """Apply a new `TorrentFilter`. Zero qBittorrent API calls.

        Deliberately does *not* reconcile the selection itself: the
        Filters modal only ever calls this once per commit
        (`FiltersScreen.apply_draft`, on `Apply`), but a category
        filter is exact-match, and reconciling before the caller has
        re-rendered would report a hidden selection against a table
        that still shows the old rows. The caller reconciles once,
        explicitly, right after (`reconcile_selection()`).
        """
        self.state.filters = filters
        self._recompute_visible()
        self._reconcile_focus()

    def set_search(self, text: str) -> int:
        """Apply the shared search engine's `tokens` ladder (tiers 0-5).

        `hash_min_length=1`, `limit=0` -- see `.agents/features/search/SPEC.md`.
        Zero API calls; a no-longer-matching focus is cleared. Returns
        the number of selected hashes hidden and dropped. Unlike
        `set_filters()`, reconciles the selection live on every
        keystroke: the invariant holds because *every* active tier is
        monotonic under a forward-typed prefix -- tier 0 (hash) included,
        at threshold 1 -- so `set_filters()`'s transient-wipe risk
        doesn't apply. An empty search skips the engine entirely (see
        `_recompute_visible`): it must show the whole corpus, not the
        empty result the engine itself would return for a blank query.

        **`fuzzy` stays out for that reason, not by oversight.** Typing
        `xbuntu` matches nothing until the last character, where fuzzy
        makes a torrent appear -- so a selection dropped at `x` never
        comes back. Measured, and pinned by
        `test_adding_fuzzy_breaks_forward_typing_monotonicity`, which
        also fails the day fuzzy becomes monotone and the ban stops
        being warranted.
        """
        self.state.search = text
        self._recompute_visible()
        self._reconcile_focus()
        return self.reconcile_selection()

    # -- multi-selection (always local, no I/O) -----------------------------

    def visible_hashes(self) -> frozenset[str]:
        """The set of full hashes currently visible (post filter/search)."""
        if self.state.visible is None:
            return frozenset()
        return frozenset(torrent.hash for torrent in self.state.visible.matched)

    def toggle_selection(self, torrent_hash: str) -> None:
        """Toggle one torrent's membership in the selection.

        Never implied by focus -- this is the only way a torrent enters
        `selected_hashes`. Doesn't check visibility; the caller must
        pass an already-known, currently visible hash.
        """
        if torrent_hash in self.state.selected_hashes:
            self.state.selected_hashes.discard(torrent_hash)
        else:
            self.state.selected_hashes.add(torrent_hash)

    def select_all_visible(self) -> None:
        """Select every currently visible torrent -- never a hidden one
        and never "the whole instance". Zero qBittorrent API calls."""
        self.state.selected_hashes = set(self.visible_hashes())

    def clear_selection(self) -> None:
        """Clear the selection entirely. Zero qBittorrent API calls."""
        self.state.selected_hashes = set()

    def reconcile_selection(self) -> int:
        """Drop any selected hash no longer visible. UI-thread only.

        Enforces "selection ⊆ visible". Called after every filter/
        search change and every successful refresh. Returns the number
        of hashes removed, so the caller can notify; `0` (the common
        case) is never itself a reason to.
        """
        visible = self.visible_hashes()
        hidden = self.state.selected_hashes - visible
        if not hidden:
            return 0
        self.state.selected_hashes -= hidden
        return len(hidden)

    def clear_selection_for(self, hashes: Iterable[str]) -> None:
        """Remove exactly `hashes` from the selection.

        Used after a result modal closes to clear only the torrents a
        plan actually acted on -- a skipped one is left selected so the
        operator can reconsider it.
        """
        self.state.selected_hashes -= set(hashes)

    # -- bulk actions ---------------------------------------------------

    def build_bulk_plan(
        self,
        action: TuiBulkAction,
        torrent_hashes: tuple[str, ...],
        *,
        category: str | None = None,
        tags: Sequence[str] = (),
        category_needs_creation: bool = False,
        download_limit: int | None = None,
        upload_limit: int | None = None,
    ) -> BulkTorrentActionPlan:
        """Build a frozen bulk-action plan -- pure, zero API calls.

        `torrent_hashes` must already be an immutable snapshot; this
        method never reads `self.state.selected_hashes` itself, so a
        later selection change can't affect an already-built plan.
        Delegates all skip-reason logic to
        `build_bulk_action_plan_from_snapshot` (the same rule the CLI
        uses) against the current `_raw_torrents` -- no rescan. The
        keyword-only arguments are the value-action's collected input
        (`category_set`/`tag_add`/`tag_remove`/`throttle`); every other
        action ignores them.

        `action` is typed `TuiBulkAction`, not the wider
        `TorrentBulkAction` `build_bulk_action_plan_from_snapshot`
        itself accepts (the CLI's engine, which does reach `"delete"`)
        -- so passing `"delete"` here is a Pyright error at every real
        call site. The `_TUI_BULK_ACTIONS` check below is not
        redundant with that: nothing in Python enforces a parameter's
        static type at runtime, so a caller that defeats the checker
        (a `cast`, a future refactor that widens this parameter back)
        would otherwise reach `apply_bulk_torrent_action` ->
        `client.torrents_delete` with no further gate in between --
        the import-only scan in `tests/test_tui_security.py` cannot
        see it, since no import changes when the *value* passed here
        does.
        """
        if action not in _TUI_BULK_ACTIONS:
            raise ValueError(
                f"{action!r} is not a TUI-reachable bulk action -- the "
                "TUI never requests 'delete'."
            )
        return build_bulk_action_plan_from_snapshot(
            self._raw_torrents,
            action,
            torrent_hashes,
            category=category,
            tags=tags,
            category_needs_creation=category_needs_creation,
            download_limit=download_limit,
            upload_limit=upload_limit,
        )

    def classify_plan_status(
        self, plan: BulkTorrentActionPlan
    ) -> MutationStatus:
        """Classify a plan before Apply -- pure.

        Refines the CLI's mapping for a TUI-only case: a selection that
        entirely *disappeared* before the plan was built (`matched > 0`,
        only `not_found` skips) is `NO_MATCH`, not the misleading
        "already satisfied" (`NO_CHANGES`).
        """
        if plan.matched == 0:
            return MutationStatus.NO_MATCH
        if plan.changes:
            return MutationStatus.PREVIEW
        if all(skip.reason == "not_found" for skip in plan.skipped):
            return MutationStatus.NO_MATCH
        return MutationStatus.NO_CHANGES

    def apply_bulk_plan(
        self,
        plan: BulkTorrentActionPlan,
        *,
        should_proceed: Callable[[], bool] | None = None,
    ) -> bool:
        """Apply an already-frozen plan. Blocking I/O -- safe on a
        background worker thread, never the UI thread.

        Mutates exactly `plan.changes`; never rescans or re-reads
        `selected_hashes`. Returns whether the mutation was actually
        dispatched. `should_proceed` is checked *inside*
        `_remote_operation()`, right before the qBittorrent call, since
        a mutation can sit queued behind a blocked read for an
        unbounded time -- an earlier check would prove nothing. When
        false: nothing is called, and `False` signals a
        cancelled-before-dispatch outcome.
        """
        with self._remote_operation() as client:
            if should_proceed is not None and not should_proceed():
                return False
            apply_bulk_torrent_action(client, plan)
            return True

    # -- focused torrent / tracker details ----------------------------------

    def begin_focus_change(self, torrent_hash: str | None) -> int | None:
        """Update focus bookkeeping; return a request id for a background
        fetch, or `None` if no fetch is needed.

        A full no-op if `torrent_hash` already matches the current
        focus -- avoids a redundant re-fetch. Otherwise always bumps
        `_detail_request_id`, even when clearing focus, invalidating
        any already-in-flight fetch for the previously focused torrent.
        """
        if torrent_hash == self.state.focused_hash:
            return None
        self.state.focused_hash = torrent_hash
        self.state.focused_tracker_details = None
        self.state.focused_details_fetched_at = None
        self.state.focused_tracker_fetch_failed = False
        self._detail_request_id += 1
        if torrent_hash is None:
            return None
        return self._detail_request_id

    def begin_manual_detail_refresh(self) -> int | None:
        """Return a fresh request id for a manual (`r`) details refresh,
        or `None` if nothing is focused.

        Always bumps `_detail_request_id`, even though the focused hash
        is unchanged, so a slower already-in-flight automatic fetch can
        never overwrite this newer, explicit request.
        """
        if self.state.focused_hash is None:
            return None
        self.state.focused_tracker_fetch_failed = False
        self._detail_request_id += 1
        return self._detail_request_id

    def clear_focus(self) -> None:
        """Clear focus and any cached focused-torrent details.

        Bumps `_detail_request_id` even though nothing is refetched, so
        any already-in-flight background fetch for the torrent that was
        focused is discarded when it eventually completes -- never
        calls `torrents_trackers()` itself.
        """
        self.state.focused_hash = None
        self.state.focused_tracker_details = None
        self.state.focused_details_fetched_at = None
        self.state.focused_tracker_fetch_failed = False
        self._detail_request_id += 1

    @property
    def detail_request_id(self) -> int:
        """The current monotonic detail-fetch generation.

        Read-only outside this module -- `qbit_ops.tui.app` uses it to
        confirm a deferred Explain result hasn't been superseded by a
        newer focus change or manual refresh before applying it.
        """
        return self._detail_request_id

    def raw_torrent_by_hash(self, torrent_hash: str) -> Any | None:
        """Look up one already-fetched raw `torrents_info()` item by hash.

        Pure, no I/O -- reads the same cached `_raw_torrents` list
        `_recompute_visible()` re-filters from. Returns `None` if the
        torrent is no longer present in the latest snapshot (e.g. it was
        removed from qBittorrent since it was focused).
        """
        for item in self._raw_torrents:
            if (
                get_field_as_string(item, "hash").lower()
                == torrent_hash.lower()
            ):
                return item
        return None

    def snapshots_for(
        self, hashes: Sequence[str]
    ) -> tuple[TorrentSnapshot, ...]:
        """Build `TorrentSnapshot`s for exactly `hashes`, from the
        already-fetched raw listing -- zero API calls, and independent
        of `state.visible` (a selected torrent the current filter now
        hides must still be found here). A hash no longer present is
        silently dropped, not fabricated."""
        wanted = {h.lower() for h in hashes}
        return tuple(
            build_torrent_snapshot(item)
            for item in self._raw_torrents
            if get_field_as_string(item, "hash").lower() in wanted
        )

    def build_explanation(self) -> ExplanationReport | None:
        """Build an `ExplanationReport` for the focused torrent -- zero
        API calls, deterministic.

        Delegates to the same `build_torrent_explanation` the CLI's
        `explain_torrent` uses -- no second, TUI-only catalogue.
        Returns `None` when nothing is focused or the focused torrent
        has disappeared from the latest raw snapshot; the caller must
        treat that as "not found", never open a modal.
        """
        if self.state.focused_hash is None:
            return None
        raw_torrent = self.raw_torrent_by_hash(self.state.focused_hash)
        if raw_torrent is None:
            return None
        return build_torrent_explanation(
            raw_torrent,
            self.state.focused_tracker_details,
            generated_at=datetime.now(UTC),
        )

    def collect_tracker_details(self, torrent_hash: str) -> Any:
        """Perform the blocking `torrents_trackers()` call only.

        No state mutation -- safe to call from a background thread.
        Returns the raw qBittorrent tracker payload; the caller applies
        it via `apply_tracker_details_success`/`_failure` on the UI
        thread, which also enforces the stale-result guard.
        """
        with self._remote_operation() as client:
            return client.torrents_trackers(torrent_hash)

    def apply_tracker_details_success(
        self,
        request_id: int,
        torrent_hash: str,
        raw_trackers: Any,
    ) -> None:
        """Apply a successful tracker-details fetch. UI-thread only.

        Discarded if `request_id` no longer matches
        `_detail_request_id` -- the sole mechanism protecting against
        out-of-order completion for rapid focus movement (A -> B -> C:
        only C's result ever applies), since a blocking fetch can't be
        reliably cancelled once started.
        """
        if request_id != self._detail_request_id:
            return
        # Safe, structural fields only -- never a raw announce URL, path,
        # query value, or unsanitized message (see qbit_core.features.torrents).
        self.state.focused_tracker_details = get_safe_tracker_details(
            raw_trackers
        )
        self.state.focused_peer_discovery = get_peer_discovery_details(
            raw_trackers
        )
        self.state.focused_details_fetched_at = datetime.now(UTC)
        self.state.focused_tracker_fetch_failed = False

    def apply_tracker_details_failure(
        self,
        request_id: int,
        error: Exception,
    ) -> None:
        """Classify and apply a failed tracker-details fetch. UI-thread only.

        Also discarded if stale, per `apply_tracker_details_success`.
        Re-raises unrecognized exceptions for the caller to treat as an
        internal error.
        """
        if request_id != self._detail_request_id:
            return
        failure = classify_recoverable_qbit_failure(error)
        if failure is None:
            raise error
        self.state.last_error = AppError(
            category=(
                ErrorCategory.AUTHENTICATION
                if failure.code == "authentication_failed"
                else ErrorCategory.UNAVAILABLE
            ),
            code=failure.code,
            message=failure.message,
        )
        # Never leave the Details modal reading "Loading..." forever: a
        # failed fetch is a terminal outcome for this request, distinct
        # from "still in flight", and must render as such.
        self.state.focused_tracker_fetch_failed = True

    def set_focus(self, torrent_hash: str | None) -> None:
        """Focus a torrent by its complete hash, or clear focus with `None`.

        Synchronous convenience composing `begin_focus_change()`,
        `collect_tracker_details()`, and `apply_tracker_details_success()`/
        `_failure()`. The real TUI never calls this directly: it runs
        the fetch on a background worker instead.
        """
        request_id = self.begin_focus_change(torrent_hash)
        if request_id is None:
            return

        assert torrent_hash is not None  # begin_focus_change's own contract
        try:
            raw_trackers = self.collect_tracker_details(torrent_hash)
        except Exception as error:
            self.apply_tracker_details_failure(request_id, error)
            return

        self.apply_tracker_details_success(
            request_id, torrent_hash, raw_trackers
        )

    def refresh_focused_details(self) -> None:
        """Manually re-fetch tracker details for the currently focused torrent.

        No-op when nothing is focused. Never triggered automatically by
        the periodic refresh tick. Synchronous convenience, same
        caveat as `set_focus()`.
        """
        request_id = self.begin_manual_detail_refresh()
        if request_id is None:
            return

        torrent_hash = self.state.focused_hash
        assert (
            torrent_hash is not None
        )  # begin_manual_detail_refresh's contract
        try:
            raw_trackers = self.collect_tracker_details(torrent_hash)
        except Exception as error:
            self.apply_tracker_details_failure(request_id, error)
            return

        self.apply_tracker_details_success(
            request_id, torrent_hash, raw_trackers
        )

    # -- internal -----------------------------------------------------------

    def _recompute_visible(self) -> None:
        if self.state.total_torrents is None:
            self.state.visible = None
            return

        filtered = select_torrents_from_items(
            self._raw_torrents, self.state.filters
        )
        if self.state.search:
            # The moment a query normalizes to blank, the engine itself
            # returns zero hits (correct in the CLI, where that case is
            # refused before any API call) -- so this branch is gated on
            # the raw text, not called unconditionally and trusted to
            # degrade gracefully. See `set_search`'s docstring.
            results = search_snapshots(
                filtered.matched,
                self.state.search,
                mode="tokens",
                hash_min_length=1,
                limit=0,
            )
            matched = tuple(hit.torrent for hit in results.hits)
        else:
            matched = filtered.matched

        # The search engine's own ranking never reaches the table: the
        # operator's chosen sort always wins, applied here regardless of
        # where `matched` came from.
        matched = _sort_torrents(matched, self.state.sort)
        self.state.visible = replace(filtered, matched=matched)

    def _reconcile_focus(self) -> None:
        if self.state.focused_hash is None:
            return
        if self.state.focused_torrent() is None:
            self.clear_focus()


def _count_stopped_torrents(raw_torrents: list[Any]) -> int:
    """Count paused/stopped torrents using the existing state helper.

    An additional, overlapping breakdown for the Overview (not folded
    into `TransferCounts`, which classifies these under
    `downloading`/`seeding` by direction) -- reuses
    `is_stopped_state`, the single source of truth for the
    qBittorrent 4 `paused*` vs 5 `stopped*` naming split.
    """
    return sum(
        1
        for torrent in raw_torrents
        if is_stopped_state(get_field_as_string(torrent, "state"))
    )


def _split_skips(
    plan: BulkTorrentActionPlan,
) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    """Split a plan's skips into (already-satisfied, not-found).

    "Disappeared from the snapshot" and "already in the requested
    state" are different facts and must never collapse into one
    message.
    """
    not_found = tuple(
        skip for skip in plan.skipped if skip.reason == "not_found"
    )
    satisfied = tuple(
        skip for skip in plan.skipped if skip.reason != "not_found"
    )
    return satisfied, not_found


_PAST_TENSE_ACTION: dict[TorrentBulkAction, str] = {
    "pause": "paused",
    "resume": "resumed",
    "start": "started",
    "reannounce": "reannounced",
}


@dataclass(frozen=True)
class MutationUiResult:
    """One completed Apply's truthful, safe, structured outcome.

    Small and immutable so a submitted mutation can still be reported
    once its `PreviewScreen` is no longer active. Carries only safe
    data -- counts, hashes, a semantic error category, a sanitized
    message -- never an exception, traceback, or tracker data.
    """

    operation_id: int
    action: TorrentBulkAction
    status: MutationStatus
    planned_hashes: tuple[str, ...]
    submitted_hashes: tuple[str, ...]
    satisfied_hashes: tuple[str, ...]
    not_found_hashes: tuple[str, ...]
    completed_at: datetime
    error_category: ErrorCategory | None = None
    error_message: str | None = None
    cancelled_before_dispatch: bool = False
    """The Apply lost authority while queued behind the shared remote
    lock and was abandoned *before* any qBittorrent call. Deliberately
    a distinct fact rather than a reuse of `error_category`: nothing
    failed remotely, nothing was submitted, and the operator did not
    cancel the Preview either. Kept TUI-local (not added to the shared
    `MutationStatus`) because the CLI has no equivalent
    queue-then-shutdown window."""

    @classmethod
    def from_plan(
        cls,
        plan: BulkTorrentActionPlan,
        status: MutationStatus,
        *,
        operation_id: int,
        error_category: ErrorCategory | None = None,
        error_message: str | None = None,
        cancelled_before_dispatch: bool = False,
    ) -> MutationUiResult:
        satisfied, not_found = _split_skips(plan)
        submitted = tuple(change.hash for change in plan.changes)
        nothing_sent = error_category is not None or cancelled_before_dispatch
        return cls(
            operation_id=operation_id,
            action=plan.action,
            status=status,
            planned_hashes=submitted
            + tuple(skip.hash for skip in plan.skipped),
            submitted_hashes=(() if nothing_sent else submitted),
            satisfied_hashes=tuple(skip.hash for skip in satisfied),
            not_found_hashes=tuple(skip.hash for skip in not_found),
            completed_at=datetime.now(UTC),
            error_category=error_category,
            error_message=error_message,
            cancelled_before_dispatch=cancelled_before_dispatch,
        )


def _classify_mutation_error(
    error: Exception,
) -> tuple[ErrorCategory, str]:
    """Classify an Apply failure into one semantic category + message.

    Outer exception first, then `__cause__` as fallback. All returned
    text is already safe to render: these are qbit-ops' own error
    messages, the same ones the refresh path shows in its banner.
    """
    classified = _classify_one_mutation_error(error)
    if classified is not None:
        return classified

    cause = error.__cause__
    if isinstance(cause, Exception):
        classified = _classify_one_mutation_error(cause)
        if classified is not None:
            return classified

    return (
        ErrorCategory.INTERNAL,
        f"{type(error).__name__}: {sanitize_tracker_text(str(error))}",
    )


def _classify_one_mutation_error(
    error: Exception,
) -> tuple[ErrorCategory, str] | None:
    """One rung of the ladder: classify `error` itself, or `None`."""
    if isinstance(error, ConfigError):
        return (ErrorCategory.CONFIGURATION, str(error))

    failure = classify_recoverable_qbit_failure(error)
    if failure is None:
        return None
    if failure.code == "authentication_failed":
        return (ErrorCategory.AUTHENTICATION, failure.message)
    return (ErrorCategory.UNAVAILABLE, failure.message)
