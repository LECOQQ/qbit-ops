"""Textual `Pilot`-based interface tests for `qbit-ops tui`.

Headless (`App.run_test()`), no real terminal, no real qBittorrent --
every app under test is built with a `client_factory` returning a
`tests.support.FakeQbitClient`. State/refresh-budget assertions live in
`tests/test_tui_state.py`; this file covers what requires an actual
mounted widget tree (navigation, focus, layout, real key sequences) and
what requires real OS threads (responsiveness, serialization,
stale-result protection).

The app opens on
the Overview workspace, not the torrent table -- every test that needs
the table, search, or filters must first switch to the Torrents
workspace (`await _goto_torrents(app, pilot)`, or a raw `pilot.press
("t")` where the switch itself is under test).

`App.run_test()` defaults to an 80x24 terminal, which is *narrower*
than `NARROW_WIDTH_THRESHOLD` (100) -- i.e. every test that does not
pass an explicit wider `size=` is already exercising the narrow layout,
matching real-world "ordinary terminal size" dogfooding.

Every qBittorrent call runs on a real Textual thread worker, so a
completed action is no longer immediately reflected the instant
`pilot.press()`/an action method returns -- `_settle()` below awaits
every in-flight worker
(`app.workers.wait_for_complete()`) and then pumps one more message
cycle. Blocking-client tests use real `threading.Event`s to control
exactly when a fake network call resolves -- never an arbitrary sleep.
"""

from __future__ import annotations

import ast
import asyncio
import re
import threading
from pathlib import Path
from time import sleep
from typing import Any, cast

import pytest
from rich.cells import cell_len
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.content import Content
from textual.coordinate import Coordinate
from textual.pilot import Pilot
from textual.widgets import (
    Button,
    DataTable,
    Input,
    RadioButton,
    RadioSet,
    Static,
)
from textual.worker import Worker, WorkerState

import qbit_ops
import qbit_ops.tui.app
import qbit_ops.tui.formatting
from qbit_core.features.status import TransferRates
from qbit_core.shared.selection import TorrentFilter
from qbit_core.shared.torrent_states import TorrentSnapshot
from qbit_ops.tui.app import (
    SEARCH_DEBOUNCE_SECONDS,
    ActionsScreen,
    ConnectionBanner,
    DetailsPanel,
    DetailsScreen,
    ExplainScreen,
    FiltersScreen,
    FilterSummary,
    HelpScreen,
    OverviewPanel,
    PreviewScreen,
    QbitOpsTuiApp,
    ResultScreen,
    SetupScreen,
    SortScreen,
    WorkspaceTabs,
    _columns_for_width,
)
from qbit_ops.tui.formatting import (
    _BRAND_ACCENT,
    _GRADIENT_END,
    _GRADIENT_START,
    _INACTIVE_TAB_ACCENT,
    _format_byte_rate,
    _format_local_time,
    _indicator_cell,
    _truncate,
    resolve_key_display,
)
from qbit_ops.tui.modals.base import MODAL_WIDTHS, QbitModal
from qbit_ops.tui.modals.value import (
    CategorySetScreen,
    TagAddScreen,
    TagRemoveScreen,
    ThrottleScreen,
)
from qbit_ops.tui.state import (
    ConnectionState,
    RateHistory,
    SortDirection,
    SortField,
    SortOrder,
    Workspace,
)
from qbit_ops.tui.tab_bar import BORDER_LABEL_MARGIN
from qbit_ops.tui.theme import QBIT_OPS_THEME
from qbit_ops.tui.widgets.filters import FiltersPanel
from qbit_ops.tui.widgets.overview import (
    _BRAND_COMPACT_MIN_WIDTH,
    _BRAND_FULL_MIN_WIDTH,
    _LOGO_COMPACT,
    _LOGO_FULL,
    BrandHeader,
    HeaderVariant,
)
from qbit_ops.tui.widgets.overview_windows import (
    SessionWindow,
    TrackersWindow,
)
from qbit_ops.tui.widgets.rate_graph import RateGraph
from qbit_ops.tui.widgets.status_bar import (
    CommandBar,
    FooterTotal,
    GlobalRateDisplay,
)
from tests.support import FakeQbitClient, make_torrent

pytestmark = pytest.mark.tui

# Every modal the TUI can push. A fixed list, not a scan: a tenth
# modal must be added here deliberately, which is the moment the shared
# frame is either adopted or knowingly skipped.
_ALL_MODALS: tuple[type[QbitModal], ...] = (
    ActionsScreen,
    DetailsScreen,
    ExplainScreen,
    FiltersScreen,
    HelpScreen,
    PreviewScreen,
    ResultScreen,
    SetupScreen,
    SortScreen,
)


def _rule_styles(app: QbitOpsTuiApp, selector: str) -> dict[str, Any]:
    """The declared styles of one *parsed* stylesheet rule.

    Parsed, never grepped: the sheet documents its own rules in CSS
    comments, so a text search would happily pass on a comment
    describing a rule that no longer exists.
    """
    for rule_set in app.stylesheet.rules:
        if rule_set.selector_names == {selector}:
            return dict(rule_set.styles.get_rules())
    raise AssertionError(f"no rule for {selector!r} in the stylesheet")


# Each modal, and the key that opens it from the Torrents workspace.
_MODAL_ENTRY_KEYS: tuple[tuple[str, type], ...] = (
    ("f", FiltersScreen),
    ("s", SortScreen),
    ("question_mark", HelpScreen),
    ("enter", DetailsScreen),
)

LARGE_INTERVAL = 999.0  # effectively disables the periodic timer mid-test
WIDE_SIZE = (140, 40)
MEDIUM_SIZE = (110, 30)
NARROW_SIZE = (80, 24)
RESPONSIVE_SIZES = [(80, 24), (100, 30), (120, 35), (160, 45)]

# `BrandHeader` picks its variant from its own measured width (logo width
# + a small margin, see `overview._BRAND_*_MIN_WIDTH`), not the general
# card-layout breakpoints above -- so it needs its own representative
# terminal sizes, distinct from WIDE_SIZE/MEDIUM_SIZE/NARROW_SIZE. The
# container around `BrandHeader` costs 2 columns of padding, so a
# terminal width of `N` yields a header width of `N - 2`.
BRAND_COMPACT_SIZE = (60, 24)  # header width 58: inside the compact band
BRAND_TEXT_ONLY_SIZE = (45, 20)  # header width 43: below the compact band
WAIT_TIMEOUT = 5.0  # seconds a test will wait on a real threading.Event


def _app(client: FakeQbitClient) -> QbitOpsTuiApp:
    return QbitOpsTuiApp(
        client_factory=lambda: client,
        host="http://localhost:8080",
        refresh_interval=LARGE_INTERVAL,
    )


def _sample_once(app: QbitOpsTuiApp) -> None:
    """One synthetic second of the graph's clock, start to finish.

    The slot is opened on the tick and settled when the reading lands,
    which is the whole point of the split -- so a test that wants a
    measured second has to do both.
    """
    tick = app.controller.open_rate_slot()
    app.controller.settle_rate_sample(
        tick, app.controller.collect_transfer_rates()
    )


def _static_text(widget: Any) -> str:
    """Join every mounted `Static` descendant's content into one string.

    Recursive (`query`, not `children`): `OverviewPanel` nests its cards
    one level deeper, inside `#overview-cards`, below the always-mounted
    `BrandHeader`.
    """
    return "\n".join(str(child.content) for child in widget.query(Static))


def _brand_header_text(header: BrandHeader) -> str:
    """Flatten a `BrandHeader`'s `Group`-of-`Text` renderable to plain
    text for substring assertions, without a full Rich `Console` render."""
    from rich.console import Group
    from rich.text import Text

    def _flatten(renderable: object) -> str:
        if isinstance(renderable, Text):
            return renderable.plain
        if isinstance(renderable, Group):
            return "\n".join(_flatten(item) for item in renderable.renderables)
        return str(renderable)

    return _flatten(header.content)


async def _settle(app: QbitOpsTuiApp, pilot: Pilot) -> None:
    """Wait for every in-flight worker to finish, then pump one message
    cycle so its `on_worker_state_changed` handler has run.

    Use after any action that dispatches a background worker (initial
    mount, focus change, `r`, a periodic tick) instead of `pilot.pause()`
    alone -- the actual qBittorrent call now runs on a real OS thread,
    so a single message-queue pump is not guaranteed to happen after it.
    """
    await app.workers.wait_for_complete()
    await pilot.pause()


async def _settle_one(
    app: QbitOpsTuiApp, pilot: Pilot, worker: Worker[Any] | None
) -> None:
    """Wait for one *specific* worker to finish, then pump one message
    cycle -- unlike `_settle()`, does not wait for any *other* worker
    that may still be deliberately in flight (e.g. an A -> B -> C focus
    sequence, where B and C's workers are still blocked while we only
    want to observe A's completion). A `None` worker (nothing was
    dispatched) is a no-op besides the message pump.
    """
    if worker is not None:
        await app.workers.wait_for_complete(workers=[worker])
    await pilot.pause()


async def _goto_torrents(app: QbitOpsTuiApp, pilot: Pilot) -> None:
    """Switch to the Torrents workspace and pump one message cycle."""
    await pilot.press("t")
    await pilot.pause()


async def _apply_filters_and_close(app: QbitOpsTuiApp, pilot: Pilot) -> None:
    """`enter` commits the draft and stays open (see `FiltersScreen`'s
    class docstring); `escape` then closes without undoing it. The
    net effect most tests actually want -- a filter now in effect,
    the modal gone."""
    await pilot.press("enter")
    await pilot.pause()
    await pilot.press("escape")
    await _settle(app, pilot)


async def _goto_overview(app: QbitOpsTuiApp, pilot: Pilot) -> None:
    await pilot.press("g")
    await pilot.pause()


async def _open_details(app: QbitOpsTuiApp, pilot: Pilot) -> Any:
    """Open the Details modal (`enter`, the sole access path at every
    width now) and return its mounted `DetailsPanel`, settled so any
    dispatched tracker-detail fetch has already completed."""
    await pilot.press("enter")
    await _settle(app, pilot)
    return app.screen.query_one(DetailsPanel)


def _visible_names(app: QbitOpsTuiApp) -> list[str]:
    visible = app.controller.state.visible
    assert visible is not None
    return [t.name for t in visible.matched]


async def _type_into_search(pilot: Pilot[None], text: str) -> None:
    """Type `text`, then settle its debounce deterministically -- like an
    operator who stops typing, without waiting on real time. Tests that
    care about the debounce itself (`SEARCH_DEBOUNCE_SECONDS`) drive it
    directly instead.
    """
    await pilot.press("slash")
    await pilot.pause()
    for char in text:
        await pilot.press(char)
    await pilot.pause()
    cast(QbitOpsTuiApp, pilot.app)._flush_pending_search()
    await pilot.pause()


class BlockingClient(FakeQbitClient):
    """A `FakeQbitClient` whose `torrents_info()` blocks on a real
    `threading.Event` until released.

    `entered` is set the instant the call starts (proving the worker
    thread is genuinely inside the call, not merely scheduled) so tests
    never need an arbitrary sleep to know when to start asserting
    responsiveness; `release` is set by the test to let the call return.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.entry_count = 0
        """Incremented the instant a call *starts*, unlike the base
        class's `torrents_info_calls` (incremented on return) -- the
        only reliable way to assert "no second call started" while the
        first is still blocked."""

    def torrents_info(self) -> list[dict[str, Any]]:
        self.entry_count += 1
        self.entered.set()
        if not self.release.wait(timeout=WAIT_TIMEOUT):
            raise TimeoutError("test forgot to release BlockingClient")
        return super().torrents_info()


class BlockingTrackerClient(FakeQbitClient):
    """A `FakeQbitClient` whose `torrents_trackers()` blocks per-hash.

    Each hash gets its own pair of events, so a test can independently
    control the completion order of concurrent focused-detail fetches
    for different torrents (A -> B -> C).
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._entered: dict[str, threading.Event] = {}
        self._release: dict[str, threading.Event] = {}

    def entered_event(self, torrent_hash: str) -> threading.Event:
        return self._entered.setdefault(torrent_hash, threading.Event())

    def release_event(self, torrent_hash: str) -> threading.Event:
        return self._release.setdefault(torrent_hash, threading.Event())

    def torrents_trackers(self, torrent_hash: str) -> list[dict[str, Any]]:
        self.entered_event(torrent_hash).set()
        if not self.release_event(torrent_hash).wait(timeout=WAIT_TIMEOUT):
            raise TimeoutError("test forgot to release BlockingTrackerClient")
        return super().torrents_trackers(torrent_hash)


class OrderedTrackerClient(FakeQbitClient):
    """A `FakeQbitClient` whose successive `torrents_trackers()` calls
    block on caller-supplied events and return caller-supplied payloads,
    in call order -- used to prove a later-issued (manual `r`) request
    is never overwritten by an earlier one, even if the earlier one
    happens to *complete* after it.
    """

    def __init__(
        self,
        *args: Any,
        responses: list[tuple[threading.Event, list[dict[str, Any]]]],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._responses = responses
        self._call_index = 0
        self.entered: list[threading.Event] = [
            threading.Event() for _ in responses
        ]

    def torrents_trackers(self, torrent_hash: str) -> list[dict[str, Any]]:
        index = self._call_index
        self._call_index += 1
        release_event, payload = self._responses[index]
        self.entered[index].set()
        if not release_event.wait(timeout=WAIT_TIMEOUT):
            raise TimeoutError("test forgot to release OrderedTrackerClient")
        return payload


async def asyncio_wait_for_event(
    event: threading.Event, timeout: float = WAIT_TIMEOUT
) -> None:
    """Await a real `threading.Event` from async test code without
    blocking the event loop.

    Waits on the event itself, off-loop, rather than polling: a poll
    loop built on `asyncio.sleep(0)` reschedules immediately and keeps
    the GIL almost continuously, starving the very worker thread that
    is supposed to set the event. Under CPU contention that turned into
    a spurious timeout -- the event was never late, the thread setting
    it just never got scheduled.
    """
    if not await asyncio.to_thread(event.wait, timeout):
        raise TimeoutError("timed out waiting for a real event")


# --- 1. Overview workspace ---------------------------------------------------


async def test_app_opens_on_overview() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)

        assert app.controller.state.workspace is Workspace.OVERVIEW
        overview = app.query_one("#overview-workspace", OverviewPanel)
        assert overview.display is True
        torrents = app.query_one("#torrents-workspace")
        assert torrents.display is False


async def test_overview_counters_match_the_shared_snapshot() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", state="downloading"),
            make_torrent(hash="b" * 40, name="Beta", state="uploading"),
            make_torrent(hash="c" * 40, name="Gamma", state="pausedUP"),
            make_torrent(hash="d" * 40, name="Delta", state="error"),
            make_torrent(hash="e" * 40, name="Epsilon", state="stalledDL"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)

        status = app.controller.state.status
        assert status is not None
        overview_text = _static_text(
            app.query_one("#overview-workspace", OverviewPanel)
        )
        incomplete = max(status.counts.total - status.counts.completed, 0)
        assert f"{status.counts.total} total" in overview_text
        assert re.search(
            rf"seeding\s+{status.counts.seeding}\s+"
            rf"downloading\s+{status.counts.downloading}",
            overview_text,
        )
        assert re.search(
            rf"complete\s+{status.counts.completed}\s+"
            rf"incomplete\s+{incomplete}",
            overview_text,
        )
        assert re.search(
            rf"stopped\s+{app.controller.state.stopped_count}\s+"
            rf"checking\s+{status.counts.checking}",
            overview_text,
        )
        assert re.search(
            rf"errored\s+{status.counts.errored}\s+"
            rf"stalled\s+{status.counts.stalled}",
            overview_text,
        )


async def test_overview_keeps_errored_and_stalled_as_counters() -> None:
    """The health verdict left the Overview; the two counts that fed it
    did not. "Should I intervene" is answered by `doctor` now, but "how
    many are stuck" still has to be readable here."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", state="stalledDL"),
            make_torrent(hash="b" * 40, name="Beta", state="stalledDL"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)

        overview_text = _static_text(
            app.query_one("#overview-workspace", OverviewPanel)
        )
        status = app.controller.state.status
        assert status is not None
        assert re.search(rf"stalled\s+{status.counts.stalled}", overview_text)
        assert re.search(rf"errored\s+{status.counts.errored}", overview_text)


async def test_overview_status_line_names_the_instance_and_its_freshness() -> (
    None
):
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        overview = app.query_one("#overview-workspace", OverviewPanel)

        assert "Connected" in _static_text(overview)
        # The refresh moment rides the window's border, not the rail:
        # the rail is one fixed line, and a second clause there was the
        # first thing a longer status word truncated away.
        masthead = overview.query_one("#overview-masthead")
        assert "Refreshed" in str(masthead.border_subtitle)


async def test_overview_never_calls_torrents_trackers() -> None:
    """`torrents_trackers_calls` may be at most 1, not 0: the table's own
    row-0 focus fetch is unrelated to the Overview's own rendering. What
    this guards against is a scan that scales with torrent count."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=f"{i:040x}") for i in range(20)]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)

        assert client.torrents_trackers_calls <= 1


# --- 1a. BrandHeader -----------------------------------------------------


async def test_overview_mounts_exactly_one_brand_header() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        assert len(app.query(BrandHeader)) == 1


async def test_the_installed_version_is_named_exactly_once_on_the_page() -> (
    None
):
    """The wordmark no longer restates it: the app frame's border title
    carries the version, and the text-only variant carries it because
    it has no wordmark to identify the application with."""
    import qbit_ops

    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        version = f"v{qbit_ops.__version__}"

        assert version in str(app.screen.border_title)
        assert version not in _brand_header_text(app.query_one(BrandHeader))

    app = _app(client)
    async with app.run_test(size=BRAND_TEXT_ONLY_SIZE) as pilot:
        await _settle(app, pilot)
        header = app.query_one(BrandHeader)

        assert header.variant is HeaderVariant.TEXT_ONLY
        assert version in _brand_header_text(header)


async def test_brand_header_uses_full_logo_at_wide_size() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        assert app.query_one(BrandHeader).variant is HeaderVariant.FULL


async def test_brand_header_uses_compact_logo_at_medium_size() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=BRAND_COMPACT_SIZE) as pilot:
        await _settle(app, pilot)
        assert app.query_one(BrandHeader).variant is HeaderVariant.COMPACT


async def test_brand_header_uses_text_only_at_narrow_size() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=BRAND_TEXT_ONLY_SIZE) as pilot:
        await _settle(app, pilot)
        assert app.query_one(BrandHeader).variant is HeaderVariant.TEXT_ONLY


async def test_resizing_switches_brand_header_variant_without_duplicating() -> (
    None
):
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=BRAND_TEXT_ONLY_SIZE) as pilot:
        await _settle(app, pilot)
        assert app.query_one(BrandHeader).variant is HeaderVariant.TEXT_ONLY

        await pilot.resize_terminal(*BRAND_COMPACT_SIZE)
        await pilot.pause()
        assert app.query_one(BrandHeader).variant is HeaderVariant.COMPACT
        assert len(app.query(BrandHeader)) == 1

        await pilot.resize_terminal(*WIDE_SIZE)
        await pilot.pause()
        assert app.query_one(BrandHeader).variant is HeaderVariant.FULL
        assert len(app.query(BrandHeader)) == 1

        await pilot.resize_terminal(*BRAND_TEXT_ONLY_SIZE)
        await pilot.pause()
        assert app.query_one(BrandHeader).variant is HeaderVariant.TEXT_ONLY
        assert len(app.query(BrandHeader)) == 1


async def test_brand_header_logos_fit_their_breakpoint_without_overflow() -> (
    None
):
    """Each variant's widest static line must fit well inside the
    breakpoint width that selects it -- proven directly against the
    same thresholds the widget picks a variant from, not by measuring
    a rendered terminal cell grid."""
    assert max(len(line) for line in _LOGO_FULL) < _BRAND_FULL_MIN_WIDTH
    assert max(len(line) for line in _LOGO_COMPACT) < _BRAND_COMPACT_MIN_WIDTH


async def test_brand_header_logo_fits_its_container_at_each_variant() -> None:
    """The widest line of the logo actually selected must never exceed
    the `BrandHeader`'s real allocated width -- a stronger guarantee
    than comparing raw logo width to the breakpoint constant."""
    client = FakeQbitClient(torrents=[make_torrent()])
    cases = [
        (WIDE_SIZE, HeaderVariant.FULL, _LOGO_FULL),
        (BRAND_COMPACT_SIZE, HeaderVariant.COMPACT, _LOGO_COMPACT),
    ]
    for size, expected_variant, logo in cases:
        app = _app(client)
        async with app.run_test(size=size) as pilot:
            await _settle(app, pilot)
            header = app.query_one(BrandHeader)
            assert header.variant is expected_variant
            assert max(len(line) for line in logo) <= header.size.width


async def test_brand_header_no_overflow_at_boundary_widths() -> None:
    """Terminal widths right around each breakpoint must never clip
    whichever variant they actually resolve to -- proven against the
    header's real allocated width, not an assumed container offset."""
    client = FakeQbitClient(torrents=[make_torrent()])
    boundaries = [
        _BRAND_COMPACT_MIN_WIDTH,
        _BRAND_COMPACT_MIN_WIDTH + 4,
        _BRAND_FULL_MIN_WIDTH,
        _BRAND_FULL_MIN_WIDTH + 4,
    ]
    for terminal_width in boundaries:
        app = _app(client)
        async with app.run_test(size=(terminal_width, 30)) as pilot:
            await _settle(app, pilot)
            header = app.query_one(BrandHeader)
            widest = max(
                len(line)
                for line in (
                    _LOGO_FULL
                    if header.variant is HeaderVariant.FULL
                    else (
                        _LOGO_COMPACT
                        if header.variant is HeaderVariant.COMPACT
                        else [""]
                    )
                )
            )
            assert widest <= header.size.width


async def test_overview_mounts_its_four_regions_once_each() -> None:
    """The page is a masthead (wordmark + status line), a graph, and two
    windows -- no card grid, and no health verdict."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        overview = app.query_one("#overview-workspace", OverviewPanel)
        assert len(overview.query(".ov-card")) == 0
        assert len(overview.query(".ov-health")) == 0
        assert len(overview.query(BrandHeader)) == 1
        assert len(overview.query(".ov-rail")) == 1
        assert len(overview.query(RateGraph)) == 1
        assert len(overview.query(TrackersWindow)) == 1
        assert len(overview.query(SessionWindow)) == 1


async def test_overview_sections_remain_mounted_at_every_brand_variant() -> (
    None
):
    for size in (WIDE_SIZE, BRAND_COMPACT_SIZE, BRAND_TEXT_ONLY_SIZE):
        client = FakeQbitClient(torrents=[make_torrent()])
        app = _app(client)
        async with app.run_test(size=size) as pilot:
            await _settle(app, pilot)
            overview = app.query_one("#overview-workspace", OverviewPanel)
            assert len(overview.query(RateGraph)) == 1
            assert len(overview.query(TrackersWindow)) == 1
            assert len(overview.query(SessionWindow)) == 1
            assert len(overview.query(BrandHeader)) == 1


async def test_session_window_shows_lifetime_totals_and_peers() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent()],
        all_time_downloaded=1024**3,  # 1 GiB
        all_time_uploaded=2 * 1024**3,  # 2 GiB
        global_ratio="1.75",
        connected_peers=9,
        dht_nodes=387,
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        overview = app.query_one("#overview-workspace", OverviewPanel)
        content = str(overview.query_one(SessionWindow).content)
        assert "1.0 GiB" in content
        assert "2.0 GiB" in content
        assert "1.75" in content
        assert "9 · DHT 387 nodes" in content


async def test_the_session_window_refuses_all_three_sentinels() -> None:
    """A fresh instance reports `'-'` for ratio, `-1` for free space and
    `firewalled` for its connection. None of the three may reach the
    screen as a number or as a success -- see `wireframes/states.txt`."""
    client = FakeQbitClient(
        torrents=[make_torrent()],
        global_ratio="-",
        free_space=-1,
        connection_status="firewalled",
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        overview = app.query_one("#overview-workspace", OverviewPanel)
        session = str(overview.query_one(SessionWindow).content)
        rail = str(overview.query_one("#overview-rail", Static).content)

        # `'-'` on the wire renders as an en dash, never as a number.
        assert "–" in session  # ai-hygiene: allow-em-dash
        assert "-1.00" not in session and "0.00" not in session
        # `-1` bytes renders as a word, never as a size.
        assert "Free space   unavailable" in session
        assert "-1 B" not in session and "0 B/s" not in session
        # `firewalled` gets its own glyph and its own word.
        assert "◐" in rail and "Firewalled" in rail
        assert "Connected" not in rail


async def test_the_two_windows_share_the_row_they_sit_in() -> None:
    """The tracker table gets the wider share -- it has six columns to
    fit against the Session window's label and value."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        overview = app.query_one("#overview-workspace", OverviewPanel)
        trackers = overview.query_one(TrackersWindow)
        session = overview.query_one(SessionWindow)

        assert trackers.region.width > session.region.width
        assert trackers.region.y == session.region.y
        assert trackers.region.right <= session.region.x


async def test_brand_header_survives_a_workspace_round_trip() -> None:
    """Switching away from Overview hides it (`display=False`); this
    must never desync the header's own chosen variant from its actual
    (unchanged) width once Overview is shown again."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        assert app.query_one(BrandHeader).variant is HeaderVariant.FULL

        await _goto_torrents(app, pilot)
        await _goto_overview(app, pilot)

        assert app.query_one(BrandHeader).variant is HeaderVariant.FULL
        assert len(app.query(BrandHeader)) == 1


class _BrandHeaderOnlyApp(App[None]):
    """A minimal App with no `QbitOpsTuiApp` machinery at all, used only
    to prove `BrandHeader` mounts and renders with zero qBittorrent
    client, controller, or worker involved."""

    def compose(self) -> ComposeResult:
        yield BrandHeader(id="brand-header")


def test_brand_header_gradient_uses_more_than_one_colour() -> None:
    """Decorative-only proof the logo isn't rendered in one flat colour --
    not a brittle per-character pin (see AGENTS.md source-prose policy)."""
    from rich.style import Style

    from qbit_ops.tui.widgets.overview import _gradient_row

    text = _gradient_row("qbit-ops", width=8)
    colours = {
        span.style.color
        for span in text.spans
        if isinstance(span.style, Style) and span.style.color
    }
    assert len(colours) > 1


async def test_brand_header_mounts_in_isolation_with_no_client() -> None:
    """A `BrandHeader` needs no `client_factory`, no worker, and no
    qBittorrent call -- it renders purely from static branding data and
    the installed version."""
    app = _BrandHeaderOnlyApp()
    async with app.run_test(size=WIDE_SIZE) as pilot:
        await pilot.pause()
        assert app.query_one(BrandHeader).variant is HeaderVariant.FULL
        assert not app.workers


# --- 2. Workspace navigation --------------------------------------------------


async def test_t_and_2_switch_to_torrents() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)

        await pilot.press("t")
        await pilot.pause()
        assert app.controller.state.workspace is Workspace.TORRENTS

        await pilot.press("g")
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()
        assert app.controller.state.workspace is Workspace.TORRENTS


async def test_g_and_1_switch_to_overview() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("g")
        await pilot.pause()
        assert app.controller.state.workspace is Workspace.OVERVIEW

        await pilot.press("t")
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        assert app.controller.state.workspace is Workspace.OVERVIEW


async def test_workspace_tabs_indicate_the_active_workspace() -> None:
    """The active workspace gets a restrained bold+underline accent
    treatment, not a large reverse-video block (see `_tab_label`)."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        tabs = app.query_one("#workspace-tabs", WorkspaceTabs)
        assert "reverse" not in str(tabs.content)
        overview_content = str(tabs.content)
        assert "Overview" in overview_content
        from qbit_ops.tui.formatting import _BRAND_ACCENT

        assert f"bold underline {_BRAND_ACCENT}" in overview_content

        await _goto_torrents(app, pilot)
        torrents_content = str(tabs.content)
        assert torrents_content != overview_content
        assert f"bold underline {_BRAND_ACCENT}" in torrents_content
        assert "reverse" not in torrents_content


async def test_workspace_tabs_underline_hugs_the_page_name_only() -> None:
    """The underline span
    on the active tab is tightened to cover just the page name, never
    the surrounding padding or the `(key)` hint -- measured from the
    widget's actual rendered `Content` spans (`tabs.visual`), not the
    raw markup string (Rich-markup-aware measurement is required here,
    per `_tab_label`'s own docstring)."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    def _underline_span(visual: Content) -> tuple[int, int]:
        spans = [
            span
            for span in visual.spans
            if "underline" in str(span.style)
            and _BRAND_ACCENT in str(span.style)
        ]
        assert len(spans) == 1, spans
        return spans[0].start, spans[0].end

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        tabs = app.query_one("#workspace-tabs", WorkspaceTabs)

        visual = cast(Content, tabs.visual)
        plain = visual.plain
        start, end = _underline_span(visual)
        assert plain[start:end] == "Overview"
        # Neither the leading/trailing padding nor "(1/g)" sits inside
        # the one underline span found above (`_underline_span` already
        # asserts there is exactly one).
        assert plain[start - 1 : start] == " "
        assert plain[end : end + 1] == " "

        await _goto_torrents(app, pilot)
        visual = cast(Content, tabs.visual)
        plain = visual.plain
        start, end = _underline_span(visual)
        assert plain[start:end] == "Torrents"


async def test_leaving_the_overview_stops_the_per_second_sampler() -> None:
    """The graph costs one `transfer_info()` a second, so it runs only
    while the page that shows it is on screen. Switching *away* must
    cost nothing at all, and must not leave the timer running."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)

        await _goto_torrents(app, pilot)
        await _settle(app, pilot)
        assert app._sample_timer is None

        calls_on_torrents = len(client.calls)
        await pilot.pause()
        await _settle(app, pilot)
        assert len(client.calls) == calls_on_torrents

        await _goto_overview(app, pilot)
        await _settle(app, pilot)
        assert app._sample_timer is not None


async def test_switching_workspaces_fetches_nothing_but_the_graph() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        before = [name for name, _, _ in client.calls]

        await _goto_torrents(app, pilot)
        await _goto_overview(app, pilot)
        await _goto_torrents(app, pilot)
        await _settle(app, pilot)

        added = [name for name, _, _ in client.calls[len(before) :]]
        assert set(added) <= {"transfer_info"}, added


async def test_switching_workspaces_preserves_search_and_filter_state() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha ISO", category="films"),
            make_torrent(hash="b" * 40, name="Beta ISO", category="tv"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _type_into_search(pilot, "alpha")
        assert _visible_names(app) == ["Alpha ISO"]
        await pilot.press("escape")  # leave the search input before nav
        await pilot.pause()

        await _goto_overview(app, pilot)
        await _goto_torrents(app, pilot)

        assert app.controller.state.search == "alpha"
        assert _visible_names(app) == ["Alpha ISO"]


async def test_switching_workspaces_preserves_last_focused_torrent() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha"),
            make_torrent(hash="b" * 40, name="Beta"),
        ],
        trackers_by_hash={"a" * 40: [], "b" * 40: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        table = app.query_one("#torrents", DataTable)
        table.move_cursor(row=1)
        await _settle(app, pilot)
        assert app.controller.state.focused_hash == "b" * 40

        await _goto_overview(app, pilot)
        await _goto_torrents(app, pilot)

        assert app.controller.state.focused_hash == "b" * 40
        table = app.query_one("#torrents", DataTable)
        assert table.cursor_row == 1


async def test_switching_to_overview_never_leaves_a_hidden_widget_focused() -> (
    None
):
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        assert app.query_one("#torrents", DataTable).has_focus

        await _goto_overview(app, pilot)

        focused = app.focused
        if focused is not None:
            assert focused.display is not False
            with_torrents_ancestor = True
            try:
                focused.query_ancestor("#torrents-workspace")
            except Exception:
                with_torrents_ancestor = False
            assert not with_torrents_ancestor


async def test_workspace_switch_is_a_noop_while_a_modal_is_open() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()
        assert len(app.screen_stack) > 1

        await pilot.press("g")
        await pilot.pause()

        assert app.controller.state.workspace is Workspace.TORRENTS
        assert len(app.screen_stack) > 1


async def test_workspace_navigation_works_at_narrow_and_wide_sizes() -> None:
    for size in RESPONSIVE_SIZES:
        client = FakeQbitClient(torrents=[make_torrent()])
        app = _app(client)
        async with app.run_test(size=size) as pilot:
            await _settle(app, pilot)
            await _goto_torrents(app, pilot)
            assert app.controller.state.workspace is Workspace.TORRENTS
            await _goto_overview(app, pilot)
            assert app.controller.state.workspace is Workspace.OVERVIEW


async def test_command_palette_is_disabled_and_absent() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    assert QbitOpsTuiApp.ENABLE_COMMAND_PALETTE is False

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        assert app.use_command_palette is False

        await pilot.press("ctrl+p")
        await pilot.pause()

        assert len(app.screen_stack) == 1


async def test_help_screen_opens_and_closes_from_either_workspace() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1

        await _goto_torrents(app, pilot)
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()


async def test_q_exits_from_overview_and_from_torrents() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await pilot.press("q")
        await pilot.pause()
        assert app._exit is True

    client2 = FakeQbitClient(torrents=[make_torrent()])
    app2 = _app(client2)
    async with app2.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app2, pilot)
        await _goto_torrents(app2, pilot)
        await pilot.press("q")
        await pilot.pause()
        assert app2._exit is True


# --- 3. Torrents workspace: table, context line ------------------------------


async def test_torrents_table_and_context_line() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(
                hash="a" * 40,
                name="Debian ISO",
                category="films",
                state="stalledUP",
            ),
            make_torrent(hash="b" * 40, name="Ubuntu ISO", category="tv"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        summary = app.query_one("#filter-summary", FilterSummary)
        assert "2 shown / 2" in str(summary.content)

        table = app.query_one("#torrents", DataTable)
        assert table.row_count == 2


async def test_filter_summary_reflects_active_filter_and_search() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(
                hash="a" * 40,
                name="Debian ISO",
                category="films",
                state="stalledUP",
            ),
            make_torrent(hash="b" * 40, name="Ubuntu ISO", category="tv"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        summary = app.query_one("#filter-summary", FilterSummary)
        assert "2 shown / 2" in str(summary.content)

        await pilot.press("f")
        await pilot.pause()
        category_input = app.screen.query_one("#f-categories", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()
        await pilot.press("enter")
        await _settle(app, pilot)

        summary_text = str(summary.content)
        assert "1 shown / 2" in summary_text
        assert "films" in summary_text


async def test_keyboard_navigation_moves_focus_and_updates_details() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha"),
            make_torrent(hash="b" * 40, name="Beta"),
        ],
        trackers_by_hash={"a" * 40: [], "b" * 40: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        assert app.controller.state.focused_hash == "a" * 40

        await pilot.press("j")
        await _settle(app, pilot)

        assert app.controller.state.focused_hash == "b" * 40
        details = await _open_details(app, pilot)
        assert "Beta" in _static_text(details)


async def test_row_highlighted_with_none_row_key_does_not_crash() -> None:
    client = FakeQbitClient(torrents=[])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        table = app.query_one("#torrents", DataTable)
        event = DataTable.RowHighlighted(table, -1, None)  # type: ignore[arg-type]
        table.post_message(event)
        await pilot.pause()

        assert app.controller.state.focused_hash is None


async def test_filtering_to_zero_rows_clears_focus_and_details() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha", category="films")],
        trackers_by_hash={"a" * 40: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        assert app.controller.state.focused_hash == "a" * 40

        await pilot.press("f")
        await pilot.pause()
        category_input = app.screen.query_one("#f-categories", Input)
        category_input.focus()
        await pilot.press(*"radarr")
        await pilot.pause()
        await _apply_filters_and_close(app, pilot)

        assert app.controller.state.focused_hash is None
        details = await _open_details(app, pilot)
        assert "No torrent focused" in _static_text(details)


async def test_clearing_filters_repopulates_table_safely() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", category="films"),
            make_torrent(hash="b" * 40, name="Beta", category="tv"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("f")
        await pilot.pause()
        category_input = app.screen.query_one("#f-categories", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()
        await pilot.press("ctrl+r")
        await pilot.pause()
        await pilot.press("enter")
        await _settle(app, pilot)

        table = app.query_one("#torrents", DataTable)
        assert table.row_count == 2


async def test_no_tracker_secrets_appear_in_details() -> None:
    torrent_hash = "a" * 40
    secret_url = "https://tracker.example/announce/TOPSECRETPASSKEY?passkey=abc"
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={
            torrent_hash: [{"url": secret_url, "status": 2}],
        },
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        details = await _open_details(app, pilot)
        rendered = _static_text(details)
        assert "TOPSECRETPASSKEY" not in rendered
        assert "passkey" not in rendered
        assert "https://" not in rendered


# --- 4. Search -----------------------------------------------------------
#
# The App's own `enter` binding (`action_activate`) is `priority=True`,
# so it wins key resolution before the focused `Input`'s own declarative
# `enter` -> `submit` binding is ever considered: `/` filters live on
# every keystroke (`on_input_changed`) instead, and `action_activate`
# special-cases the search input's `enter` to return focus to the table.


async def test_slash_focuses_a_visible_editable_input_from_the_table() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        table = app.query_one("#torrents", DataTable)
        assert table.has_focus

        await pilot.press("slash")
        await pilot.pause()

        search = app.query_one("#search-input", Input)
        assert search.has_focus


async def test_typed_characters_appear_in_the_search_input() -> None:
    client = FakeQbitClient(torrents=[make_torrent(name="Ubuntu ISO")])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _type_into_search(pilot, "ubu")

        search = app.query_one("#search-input", Input)
        assert search.value == "ubu"


async def test_search_recompute_is_debounced_while_typing_a_burst() -> None:
    """A burst of scheduled searches restarts the same timer -- only the
    last one is ever applied, and none of them until flushed. Drives
    `_schedule_search` directly rather than real keystrokes: this is a
    coalescing-logic assertion, not a timing race a slow machine should
    ever be able to fail (see `SEARCH_DEBOUNCE_SECONDS`)."""
    client = FakeQbitClient(torrents=[make_torrent(name="Ubuntu ISO")])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("slash")
        await pilot.pause()

        for partial in ("u", "ub", "ubu", "ubun", "ubunt", "ubuntu"):
            app._schedule_search(partial)

        assert app.controller.state.search == ""
        assert _visible_names(app) == ["Ubuntu ISO"]  # unfiltered still

        app._flush_pending_search()

        assert app.controller.state.search == "ubuntu"
        assert _visible_names(app) == ["Ubuntu ISO"]


async def test_search_fires_naturally_after_the_debounce_settles() -> None:
    """End-to-end through the real `Input`: typing, then a pause well
    past `SEARCH_DEBOUNCE_SECONDS` with no further keystroke, applies
    the search with no explicit flush -- proving `on_input_changed` is
    actually wired to the timer, not just `_flush_pending_search`
    itself."""
    client = FakeQbitClient(torrents=[make_torrent(name="Ubuntu ISO")])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("slash")
        await pilot.pause()
        await pilot.press(*"ubuntu")
        await pilot.pause(SEARCH_DEBOUNCE_SECONDS * 5)

        assert app.controller.state.search == "ubuntu"


async def test_leaving_the_workspace_flushes_a_pending_search() -> None:
    """Tearing down `#search-input` mid-debounce must not strand the
    last keystrokes: re-entering search later reads `state.search`, so
    a lost flush here would silently roll the text back. Schedules and
    switches workspace back to back, with no `await` between them, so
    no real time -- and therefore no natural firing -- can pass in
    between: only the explicit flush in `_switch_workspace` can be
    responsible for the result."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Debian ISO"),
            make_torrent(hash="b" * 40, name="Ubuntu ISO"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("slash")
        await pilot.pause()

        app._schedule_search("ubuntu")
        assert app.controller.state.search == ""  # still pending
        app._switch_workspace(Workspace.OVERVIEW)

        assert app.controller.state.workspace is Workspace.OVERVIEW
        assert app.controller.state.search == "ubuntu"
        # Switching into Overview resumes its per-second sampler worker;
        # wait for it like every other worker-dispatching action, or its
        # completion can race the app's own teardown below.
        await _settle(app, pilot)


async def test_escape_flushes_a_pending_search() -> None:
    """Same guarantee as the workspace switch above, for the other path
    that tears down `#search-input` -- `action_dismiss_overlay`."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Debian ISO"),
            make_torrent(hash="b" * 40, name="Ubuntu ISO"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("slash")
        await pilot.pause()

        app._schedule_search("ubuntu")
        assert app.controller.state.search == ""  # still pending
        app.action_dismiss_overlay()

        assert app.controller.state.search == "ubuntu"
        assert _visible_names(app) == ["Ubuntu ISO"]
        await pilot.pause()


async def test_search_matches_name_substring_case_insensitively() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Debian ISO"),
            make_torrent(hash="b" * 40, name="Ubuntu ISO"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _type_into_search(pilot, "UBUNTU")

        assert _visible_names(app) == ["Ubuntu ISO"]


async def test_search_matches_full_infohash() -> None:
    full_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=full_hash, name="Debian ISO"),
            make_torrent(hash="b" * 40, name="Ubuntu ISO"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _type_into_search(pilot, full_hash.upper())

        assert _visible_names(app) == ["Debian ISO"]


async def test_search_matches_leading_infohash_prefix() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="deadbeef" + "0" * 32, name="Debian ISO"),
            make_torrent(hash="b" * 40, name="Ubuntu ISO"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _type_into_search(pilot, "DEADBEEF")

        assert _visible_names(app) == ["Debian ISO"]


async def test_search_and_filters_combine_with_and_semantics() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Debian ISO", category="films"),
            make_torrent(hash="b" * 40, name="Debian Extra", category="tv"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("f")
        await pilot.pause()
        category_input = app.screen.query_one("#f-categories", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        await _type_into_search(pilot, "debian")

        assert _visible_names(app) == ["Debian ISO"]


async def test_zero_result_search_does_not_crash() -> None:
    client = FakeQbitClient(torrents=[make_torrent(name="Debian ISO")])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _type_into_search(pilot, "nonexistent-torrent-name")

        assert _visible_names(app) == []
        table = app.query_one("#torrents", DataTable)
        assert table.row_count == 0

        await pilot.press("j")
        await pilot.pause()


async def test_clearing_search_repopulates_rows() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Debian ISO"),
            make_torrent(hash="b" * 40, name="Ubuntu ISO"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _type_into_search(pilot, "debian")
        assert _visible_names(app) == ["Debian ISO"]

        search = app.query_one("#search-input", Input)
        search.focus()
        await pilot.press("ctrl+u")
        await pilot.pause()
        app._flush_pending_search()

        assert search.value == ""
        assert sorted(_visible_names(app)) == ["Debian ISO", "Ubuntu ISO"]


async def test_search_performs_zero_qbittorrent_api_calls() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Debian ISO"),
            make_torrent(hash="b" * 40, name="Ubuntu ISO"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        scans_before = (
            client.torrents_info_calls,
            client.transfer_info_calls,
            client.app_version_calls,
            client.app_web_api_version_calls,
        )

        await _type_into_search(pilot, "ubuntu")
        await pilot.press("enter")
        await pilot.pause()
        search = app.query_one("#search-input", Input)
        search.focus()
        await pilot.press("ctrl+u")
        await pilot.pause()
        app._flush_pending_search()

        scans_after = (
            client.torrents_info_calls,
            client.transfer_info_calls,
            client.app_version_calls,
            client.app_web_api_version_calls,
        )
        assert scans_after == scans_before


async def test_global_bindings_ignore_keystrokes_while_search_is_focused() -> (
    None
):
    client = FakeQbitClient(torrents=[make_torrent(name="Ubuntu ISO")])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("slash")
        await pilot.pause()
        search = app.query_one("#search-input", Input)

        await pilot.press("q", "f", "r", "g", "1", "question_mark")
        await pilot.pause()

        assert search.value == "qfrg1?"
        assert app._exit is False
        assert len(app.screen_stack) == 1
        assert app.controller.state.workspace is Workspace.TORRENTS
        assert search.has_focus


async def test_search_hiding_focused_torrent_clears_focus_and_details() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Debian ISO")],
        trackers_by_hash={"a" * 40: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        assert app.controller.state.focused_hash == "a" * 40

        await _type_into_search(pilot, "nonexistent-torrent-name")

        assert app.controller.state.focused_hash is None
        assert app.controller.state.focused_tracker_details is None

        # Leave the search input (its own `enter` only returns focus to
        # the table) before a second `enter` opens the Details modal.
        await pilot.press("enter")
        await pilot.pause()
        details = await _open_details(app, pilot)
        assert "No torrent focused" in _static_text(details)


async def test_enter_in_search_keeps_text_and_returns_focus_to_table() -> None:
    client = FakeQbitClient(torrents=[make_torrent(name="Ubuntu ISO")])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _type_into_search(pilot, "ubuntu")

        await pilot.press("enter")
        await pilot.pause()

        table = app.query_one("#torrents", DataTable)
        assert table.has_focus
        search = app.query_one("#search-input", Input)
        assert search.value == "ubuntu"
        assert app.controller.state.search == "ubuntu"
        assert len(app.screen_stack) == 1


async def test_escape_leaves_search_editing_without_crashing() -> None:
    client = FakeQbitClient(torrents=[make_torrent(name="Ubuntu ISO")])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _type_into_search(pilot, "ubuntu")

        await pilot.press("escape")
        await pilot.pause()

        assert len(app.query("#search-input")) == 0
        table = app.query_one("#torrents", DataTable)
        assert table.has_focus
        await pilot.press("j")
        await pilot.pause()


async def test_search_works_at_every_tested_width() -> None:
    for size in RESPONSIVE_SIZES:
        client = FakeQbitClient(torrents=[make_torrent(name="Ubuntu ISO")])
        app = _app(client)
        async with app.run_test(size=size) as pilot:
            await _settle(app, pilot)
            await _goto_torrents(app, pilot)

            await pilot.press("slash")
            await pilot.pause()
            search = app.query_one("#search-input", Input)
            assert search.has_focus
            await pilot.press("u", "b", "u")
            await pilot.pause()

            assert search.value == "ubu"
            assert _visible_names(app) == ["Ubuntu ISO"]
            await pilot.press("escape")
            await pilot.pause()


# --- 5. Filters modal ---------------------------------------------------


async def test_filter_modal_opens_at_every_tested_width() -> None:
    for size in RESPONSIVE_SIZES:
        client = FakeQbitClient(torrents=[make_torrent()])
        app = _app(client)
        async with app.run_test(size=size) as pilot:
            await _settle(app, pilot)
            await _goto_torrents(app, pilot)

            await pilot.press("f")
            await pilot.pause()

            assert isinstance(app.screen, FiltersScreen)
            assert app.screen.query_one(FiltersPanel) is not None
            await pilot.press("escape")
            await pilot.pause()
            assert len(app.screen_stack) == 1


async def test_filter_modal_exposes_current_values() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha", category="films")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()
        category_input = app.screen.query_one("#f-categories", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()
        await _apply_filters_and_close(app, pilot)

        await pilot.press("f")
        await pilot.pause()
        reopened_input = app.screen.query_one("#f-categories", Input)
        assert reopened_input.value == "films"
        await pilot.press("escape")
        await pilot.pause()


async def test_filter_apply_with_enter_applies_and_stays_open() -> None:
    """`enter` applies the draft and leaves the modal
    open -- the one gesture that commits, unlike every other modal's
    `Apply`."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", category="films"),
            make_torrent(hash="b" * 40, name="Beta", category="tv"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()
        category_input = app.screen.query_one("#f-categories", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert len(app.screen_stack) > 1
        assert isinstance(app.screen, FiltersScreen)
        assert app.controller.state.filters.categories == ("films",)
        assert _visible_names(app) == ["Alpha"]


async def test_filter_cancel_with_escape_never_applies_the_draft() -> None:
    """`esc` closes without undoing anything already
    applied -- and a draft that was never `Apply`-ed was never in
    effect to begin with, so editing it and pressing `esc` changes
    nothing about `state.filters`."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", category="films"),
            make_torrent(hash="b" * 40, name="Beta", category="tv"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        # Apply "films" first.
        await pilot.press("f")
        await pilot.pause()
        category_input = app.screen.query_one("#f-categories", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.controller.state.filters.categories == ("films",)

        # Reopen, edit the draft, never apply it -- state stays "films"
        # throughout, since typing no longer filters live.
        await pilot.press("f")
        await pilot.pause()
        category_input2 = app.screen.query_one("#f-categories", Input)
        category_input2.focus()
        await pilot.press("ctrl+u")
        await pilot.press(*"tv")
        await pilot.pause()
        assert app.controller.state.filters.categories == ("films",)

        await pilot.press("escape")
        await pilot.pause()

        assert len(app.screen_stack) == 1
        assert app.controller.state.filters.categories == ("films",)
        assert _visible_names(app) == ["Alpha"]


async def test_filter_clear_empties_draft_keeps_table_filtered() -> None:
    """`^r` never applies anything (see `test_filter_clear_key_empties_
    the_draft_without_applying`): the table stays exactly as filtered
    by the last real `Apply`, not reset to the unfiltered list."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", category="films"),
            make_torrent(hash="b" * 40, name="Beta", category="tv"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()
        category_input = app.screen.query_one("#f-categories", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert _visible_names(app) == ["Alpha"]

        await pilot.press(*"tv")
        await pilot.pause()
        await pilot.press("ctrl+r")
        await pilot.pause()

        assert len(app.screen_stack) > 1  # clear keeps the modal open
        assert app.controller.state.filters.categories == ("films",)
        assert _visible_names(app) == ["Alpha"]
        cleared_input = app.screen.query_one("#f-categories", Input)
        assert cleared_input.value == ""

        await pilot.press("escape")
        await pilot.pause()


async def test_filter_apply_performs_zero_api_calls() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", category="films"),
            make_torrent(hash="b" * 40, name="Beta", category="tv"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        scans_before = (
            client.torrents_info_calls,
            client.transfer_info_calls,
            client.app_version_calls,
            client.app_web_api_version_calls,
        )

        await pilot.press("f")
        await pilot.pause()
        category_input = app.screen.query_one("#f-categories", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        scans_after = (
            client.torrents_info_calls,
            client.transfer_info_calls,
            client.app_version_calls,
            client.app_web_api_version_calls,
        )
        assert scans_after == scans_before


async def test_no_keystroke_ever_calls_set_filters_before_apply() -> None:
    """Filtering is commit-on-`Apply`, not live -- typing
    across every field in the draft calls `TuiController.set_filters`
    zero times, and `enter` calls it exactly once."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha", category="films")]
    )
    app = _app(client)
    calls = 0
    real_set_filters = app.controller.set_filters

    def _counting_set_filters(filters: TorrentFilter) -> None:
        nonlocal calls
        calls += 1
        real_set_filters(filters)

    app.controller.set_filters = _counting_set_filters  # type: ignore[method-assign]

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()
        category_input = app.screen.query_one("#f-categories", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()
        assert calls == 0

        await pilot.press("enter")
        await pilot.pause()
        assert calls == 1

        await pilot.press("escape")
        await pilot.pause()


async def test_no_dot_glyph_appears_in_the_filters_modal() -> None:
    """`*` is the only "pending"/"in attention" marker
    in this modal -- `●` is already a state lamp elsewhere in the
    product (tracker health, connection status), where colour carries
    the meaning; reusing it here would be a second, unrelated subject
    for the same glyph. Checked on the rendered screen, not the
    source, with a state that actually has something to mark (a
    pending edit and an applied filter) so the absence is not merely
    because nothing was ever drawn."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", category="films"),
            make_torrent(hash="b" * 40, name="Beta", category="tv"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()
        category_input = app.screen.query_one("#f-categories", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()
        await pilot.press("enter")  # one filter applied
        await pilot.pause()
        await pilot.press(*"tv")  # a pending, un-applied edit
        await pilot.pause()

        dialog = app.screen.query_one("#filters-dialog")
        strips = app.screen._compositor.render_strips()
        region = dialog.region
        rendered = "\n".join(
            "".join(segment.text for segment in strips[y])[
                region.x : region.x + region.width
            ]
            for y in range(region.y, region.y + region.height)
        )
        assert "*" in rendered, "fixture produced no pending marker to check"
        assert "●" not in rendered

        await pilot.press("escape")
        await pilot.pause()


# --- 6. Details --------------------------------------------------------------


async def test_details_open_at_every_tested_width() -> None:
    """`enter` opens the Details modal at every width now -- there is
    no more width-conditional side-panel-vs-modal branching."""
    for size in RESPONSIVE_SIZES:
        torrent_hash = "a" * 40
        client = FakeQbitClient(
            torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
            trackers_by_hash={torrent_hash: []},
        )
        app = _app(client)
        async with app.run_test(size=size) as pilot:
            await _settle(app, pilot)
            await _goto_torrents(app, pilot)

            await pilot.press("enter")
            await _settle(app, pilot)
            assert isinstance(app.screen, DetailsScreen)
            rendered = _static_text(app.screen.query_one(DetailsPanel))
            assert "Alpha" in rendered
            await pilot.press("escape")
            await pilot.pause()


async def test_details_screen_retries_a_stuck_loading_fetch() -> None:
    """If the initial on-open fetch's result never lands (e.g. discarded
    as stale by a request-id bump in between), the modal must not stay
    on "Loading..." forever with no user action -- the same recovery
    `r` already provides fires automatically once, unprompted."""
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={torrent_hash: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("enter")
        await _settle(app, pilot)
        assert isinstance(app.screen, DetailsScreen)

        # Simulate the lost-result scenario directly: still loading,
        # not failed -- exactly what a discarded/never-arriving result
        # leaves behind.
        app.controller.state.focused_tracker_details = None
        app.controller.state.focused_tracker_fetch_failed = False
        calls = []
        app.action_refresh_details = lambda: calls.append(1)  # type: ignore[method-assign]

        app.screen._retry_if_still_loading()

        assert calls == [1]
        await pilot.press("escape")
        await pilot.pause()


async def test_details_screen_does_not_retry_an_already_resolved_fetch() -> (
    None
):
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={torrent_hash: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("enter")
        await _settle(app, pilot)
        assert isinstance(app.screen, DetailsScreen)
        assert app.controller.state.focused_tracker_details is not None

        calls = []
        app.action_refresh_details = lambda: calls.append(1)  # type: ignore[method-assign]

        app.screen._retry_if_still_loading()

        assert calls == []
        await pilot.press("escape")
        await pilot.pause()


async def test_details_show_required_safe_fields() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[
            make_torrent(
                hash=torrent_hash,
                name="Alpha",
                category="films",
                state="uploading",
            )
        ],
        trackers_by_hash={
            torrent_hash: [
                {"url": "https://tracker.example/announce", "status": 2}
            ]
        },
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        details = await _open_details(app, pilot)
        rendered = _static_text(details)
        assert "Alpha" in rendered
        # The hash is shortened for display -- the full hash is reachable
        # via Copy (see test_copy_hash_from_details), not printed here.
        assert torrent_hash[:8] in rendered
        assert torrent_hash not in rendered
        assert "uploading" in rendered  # subdued secondary raw state
        assert "films" in rendered
        assert "Download" in rendered and "Upload" in rendered
        assert "Last fetched" in rendered  # tracker-detail fetched timestamp
        assert "https://" not in rendered


async def test_r_with_no_focus_is_safe() -> None:
    client = FakeQbitClient(torrents=[])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("r")
        await pilot.pause()


async def test_r_refreshes_focused_details() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={torrent_hash: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        # Focus alone never fetches trackers -- only opening the modal
        # (or an explicit refresh inside it) does.
        assert client.torrents_trackers_calls == 0

        await _open_details(app, pilot)
        assert client.torrents_trackers_calls == 1

        await pilot.press("r")
        await _settle(app, pilot)

        assert client.torrents_trackers_calls == 2


# --- 7. Responsive layouts -----------------------------------------------


async def test_overview_remains_readable_at_80x24() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha", state="stalledDL")]
    )
    app = _app(client)

    async with app.run_test(size=(80, 24)) as pilot:
        await _settle(app, pilot)
        overview = app.query_one("#overview-workspace", OverviewPanel)
        overview_text = _static_text(overview)
        assert "Torrents" in overview_text
        assert "stalled" in overview_text
        # Narrow stacks without horizontal overflow.
        assert overview.region.width <= 80


async def test_the_graph_fills_the_band_beside_the_wordmark() -> None:
    """The graph costs the page no line: it occupies columns the
    wordmark reserved and left blank, on the same rows."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        overview = app.query_one("#overview-workspace", OverviewPanel)
        header = overview.query_one(BrandHeader)
        graph = overview.query_one(RateGraph)

        assert graph.region.x >= header.region.right
        assert graph.region.y == header.region.y
        assert graph.region.width > 0


async def test_overview_medium_layout_stays_readable() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=MEDIUM_SIZE) as pilot:
        await _settle(app, pilot)
        overview = app.query_one("#overview-workspace", OverviewPanel)
        assert overview.query_one(TrackersWindow).region.width > 0
        assert overview.query_one(SessionWindow).region.width > 0
        assert overview.region.width <= MEDIUM_SIZE[0]


async def test_overview_narrow_layout_stacks_windows_without_overflow() -> None:
    """Narrow stacks the two windows instead of squeezing the tracker
    table below the width its six columns need."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=NARROW_SIZE) as pilot:
        await _settle(app, pilot)
        overview = app.query_one("#overview-workspace", OverviewPanel)
        trackers = overview.query_one(TrackersWindow)
        session = overview.query_one(SessionWindow)
        assert trackers.region.y < session.region.y
        assert overview.region.width <= NARROW_SIZE[0]


async def test_overview_rail_shows_connection_identity_not_transfer_rates() -> (
    None
):
    """Connection and version are one status rail; transfer rates live
    in the top-right `GlobalRateDisplay` and in the graph, never
    duplicated here."""
    client = FakeQbitClient(
        torrents=[make_torrent()], download_speed=0, upload_speed=2_500_000
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        status = app.controller.state.status
        assert status is not None
        rail_text = str(app.query_one("#overview-rail", Static).content)
        assert "Connected" in rail_text
        up = _format_byte_rate(status.rates.upload_bytes_per_second)
        assert up not in rail_text


@pytest.mark.parametrize(
    "connection",
    [
        ConnectionState.CONNECTED,
        ConnectionState.RECONNECTING,
        ConnectionState.AUTH_FAILED,
        ConnectionState.CONFIG_FAILED,
    ],
    ids=["connected", "reconnecting", "auth_failed", "config_failed"],
)
async def test_overview_rail_stays_readable_across_connection_states(
    connection: ConnectionState,
) -> None:
    """The rail must remain readable in every connection state, and the
    staleness banner must never be part of it: folding it in made the
    rail two lines tall and pushed every window below it down a row the
    moment a refresh went stale."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        app.controller.state.connection = connection
        app.controller.state.stale = True
        app._render_overview()
        await pilot.pause()

        rail = app.query_one("#overview-rail", Static)
        rail_text = str(rail.content)
        assert rail_text
        assert "\n" not in rail_text
        assert rail.size.height == 1

        stale = app.query_one("#overview-stale", Static)
        assert stale.display is True
        assert "STALE" in str(stale.content)


async def test_torrents_workspace_is_full_width_table_at_every_size() -> None:
    for size in RESPONSIVE_SIZES:
        client = FakeQbitClient(torrents=[make_torrent(name="Alpha")])
        app = _app(client)
        async with app.run_test(size=size) as pilot:
            await _settle(app, pilot)
            await _goto_torrents(app, pilot)
            table = app.query_one("#torrents", DataTable)
            assert table.row_count == 1


async def test_resize_narrow_to_wide_to_narrow_is_safe() -> None:
    client = FakeQbitClient(torrents=[make_torrent(name="Alpha")])
    app = _app(client)

    async with app.run_test(size=NARROW_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.resize_terminal(*WIDE_SIZE)
        await pilot.pause()
        assert "narrow" not in app.screen.classes

        await pilot.resize_terminal(*NARROW_SIZE)
        await pilot.pause()
        assert "narrow" in app.screen.classes

        assert client.torrents_info_calls == 1


async def test_resize_does_not_trigger_extra_api_calls() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        # The graph's per-second sampler runs on real wall-clock time,
        # independent of `refresh_interval`; under load the resizes below
        # can take over a second and its tick adds an extra `transfer_info`
        # call unrelated to what this test budgets.
        app._pause_sampling()
        calls_before = len(client.calls)

        await pilot.resize_terminal(*NARROW_SIZE)
        await pilot.pause()
        await pilot.resize_terminal(*WIDE_SIZE)
        await pilot.pause()

        assert len(client.calls) == calls_before


async def test_resize_narrow_to_wide_preserves_focus_and_details_content() -> (
    None
):
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={torrent_hash: []},
    )
    app = _app(client)

    async with app.run_test(size=NARROW_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        narrow_focused_hash = app.controller.state.focused_hash

        await pilot.resize_terminal(*WIDE_SIZE)
        await pilot.pause()

        assert app.controller.state.focused_hash == narrow_focused_hash
        details = await _open_details(app, pilot)
        assert "Alpha" in _static_text(details)


async def test_no_function_disappears_at_narrow_width() -> None:
    """Narrow mode retains filters, search, and details -- all through
    an alternate path (modal), never simply unavailable."""
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=torrent_hash, name="Alpha", category="films")
        ],
        trackers_by_hash={torrent_hash: []},
    )
    app = _app(client)

    async with app.run_test(size=NARROW_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("f")
        await pilot.pause()
        assert isinstance(app.screen, FiltersScreen)
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("slash")
        await pilot.pause()
        assert app.query_one("#search-input", Input).has_focus
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("enter")
        await _settle(app, pilot)
        assert isinstance(app.screen, DetailsScreen)
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, SortScreen)
        await pilot.press("escape")
        await pilot.pause()


# --- 8. Worker hardening: responsiveness ------------------------------------


async def test_slow_periodic_refresh_does_not_block_key_handling() -> None:
    """With the periodic refresh's `torrents_info()` genuinely blocked on
    a real thread, keyboard navigation must still be processed
    immediately -- proving the UI thread is never inside the blocking
    call itself."""
    client = BlockingClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha"),
            make_torrent(hash="b" * 40, name="Beta"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        # The very first refresh (from on_mount) is already in flight.
        await asyncio_wait_for_event(client.entered)

        # Local, non-network interactions must all still work.
        await pilot.press("t")
        await pilot.pause()
        assert app.controller.state.workspace is Workspace.TORRENTS

        await pilot.press("slash")
        await pilot.pause()
        assert app.query_one("#search-input", Input).has_focus
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("f")
        await pilot.pause()
        assert isinstance(app.screen, FiltersScreen)
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()

        client.release.set()
        await _settle(app, pilot)
        assert app.controller.state.status is not None


async def test_q_works_during_an_in_flight_refresh() -> None:
    client = BlockingClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await asyncio_wait_for_event(client.entered)

        await pilot.press("q")
        await pilot.pause()

        assert app._exit is True

        # Let the still-blocked worker finish so the test process does
        # not leave a lingering thread behind.
        client.release.set()


async def test_search_and_filter_remain_local_during_slow_refresh() -> None:
    client = BlockingClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Debian ISO", category="films"),
            make_torrent(hash="b" * 40, name="Ubuntu ISO", category="tv"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        # Let the very first refresh complete so a torrent snapshot
        # exists to filter/search against.
        await asyncio_wait_for_event(client.entered)
        client.release.set()
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        # Start a second, slow refresh and filter/search while it blocks.
        client.release.clear()
        client.entered.clear()
        app._start_periodic_refresh()
        await asyncio_wait_for_event(client.entered)

        await pilot.press("f")
        await pilot.pause()
        category_input = app.screen.query_one("#f-categories", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert app.controller.state.visible is not None
        assert [t.name for t in app.controller.state.visible.matched] == [
            "Debian ISO"
        ]

        client.release.set()
        await _settle(app, pilot)


# --- 9. Worker hardening: serialization -------------------------------------


async def test_periodic_refreshes_never_overlap() -> None:
    client = BlockingClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await asyncio_wait_for_event(client.entered)
        assert client.entry_count == 1

        # A tick firing while the first refresh is still in flight must
        # not start a second, concurrent call.
        app._start_periodic_refresh()
        await pilot.pause()
        assert client.entry_count == 1

        client.release.set()
        await _settle(app, pilot)


async def test_skipped_tick_is_deterministic_and_cadence_continues() -> None:
    client = BlockingClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await asyncio_wait_for_event(client.entered)

        app._start_periodic_refresh()
        app._start_periodic_refresh()
        await pilot.pause()
        assert client.entry_count == 1

        client.release.set()
        await _settle(app, pilot)
        assert client.torrents_info_calls == 1

        client.entered.clear()
        client.release.clear()
        app._start_periodic_refresh()
        await asyncio_wait_for_event(client.entered)
        assert client.entry_count == 2
        client.release.set()
        await _settle(app, pilot)


# --- 10. Worker hardening: stale-result protection ---------------------------


class _FakeCompletedWorker:
    """A minimal stand-in for a `Worker`, just enough to satisfy
    `Worker.StateChanged`'s `worker.group`/`worker.result` reads --
    avoids actually dispatching a worker after the app has already
    exited, which the real `run_worker()` may itself reject."""

    def __init__(self, group: str, result: Any) -> None:
        self.group = group
        self.result = result


async def test_late_periodic_result_is_ignored_after_shutdown() -> None:
    from qbit_ops.tui.app import REFRESH_WORKER_GROUP

    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        calls_before = client.torrents_info_calls

        await app.action_quit()
        await pilot.pause()
        assert app.is_running is False

        fake_worker = _FakeCompletedWorker(REFRESH_WORKER_GROUP, (None, None))
        event = Worker.StateChanged(fake_worker, WorkerState.SUCCESS)  # type: ignore[arg-type]
        app.on_worker_state_changed(event)

        assert client.torrents_info_calls == calls_before


async def test_stale_detail_fetch_never_overwrites_a_newer_focus() -> None:
    """A slow tracker-detail fetch (dispatched by an earlier modal-open
    or refresh) for a torrent the user has since focused away from must
    never overwrite a newer torrent's displayed details -- regardless
    of which fetch actually completes first -- see
    `TuiController.apply_tracker_details_success`'s `request_id` guard.
    """
    hash_a, hash_b = "a" * 40, "b" * 40
    client = BlockingTrackerClient(
        torrents=[
            make_torrent(hash=hash_a, name="Alpha"),
            make_torrent(hash=hash_b, name="Beta"),
        ],
        trackers_by_hash={
            hash_a: [{"url": "https://a.example/announce", "status": 2}],
            hash_b: [{"url": "https://b.example/announce", "status": 2}],
        },
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        # Focus alone never fetches; a fetch is only dispatched when
        # explicitly requested (as the Details modal does on open).
        app._focus_torrent(hash_a)
        worker_a = app.action_refresh_details()

        # The user navigates away before A's fetch resolves.
        app._focus_torrent(hash_b)
        worker_b = app.action_refresh_details()

        # B's fetch resolves first...
        client.release_event(hash_b).set()
        await _settle_one(app, pilot, worker_b)
        details = app.controller.state.focused_tracker_details
        assert details is not None and "b.example" in details[0]["tracker"]

        # ...then A's late, now-stale fetch resolves -- it must not
        # overwrite B's already-displayed details.
        client.release_event(hash_a).set()
        await _settle_one(app, pilot, worker_a)
        details = app.controller.state.focused_tracker_details
        assert details is not None and "b.example" in details[0]["tracker"]
        assert app.controller.state.focused_hash == hash_b


async def test_clearing_focus_ignores_pending_detail_result() -> None:
    torrent_hash = "a" * 40
    client = BlockingTrackerClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={torrent_hash: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        app._focus_torrent(torrent_hash)
        worker = app.action_refresh_details()

        app._clear_focus_and_render()
        client.release_event(torrent_hash).set()
        await _settle_one(app, pilot, worker)

        assert app.controller.state.focused_tracker_details is None


async def test_manual_refresh_wins_over_an_earlier_slower_request() -> None:
    torrent_hash = "a" * 40
    slow_release = threading.Event()
    fast_release = threading.Event()
    client = OrderedTrackerClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        responses=[
            (
                slow_release,
                [{"url": "https://slow.example/announce", "status": 2}],
            ),
            (
                fast_release,
                [{"url": "https://fast.example/announce", "status": 2}],
            ),
        ],
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        app._focus_torrent(torrent_hash)
        first_worker = app.action_refresh_details()
        await asyncio_wait_for_event(client.entered[0])

        manual_worker = app.action_refresh_details()
        await asyncio_wait_for_event(client.entered[1])

        fast_release.set()
        await _settle_one(app, pilot, manual_worker)
        slow_release.set()
        await _settle_one(app, pilot, first_worker)

        details = app.controller.state.focused_tracker_details
        assert details is not None
        assert "fast.example" in details[0]["tracker"]


# --- 11. Worker hardening: failure states ------------------------------------


async def test_connection_failure_produces_stale_state() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)

        def _boom() -> Any:
            raise OSError("connection refused")

        client.torrents_info = _boom  # type: ignore[method-assign]
        app._start_periodic_refresh()
        await _settle(app, pilot)

        assert app.controller.state.stale is True
        assert app.controller.state.connection is ConnectionState.RECONNECTING
        banner = app.query_one("#banner", ConnectionBanner)
        assert "visible" in banner.classes


async def test_recovery_clears_stale() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)

        def _boom() -> Any:
            raise OSError("connection refused")

        client.torrents_info = _boom  # type: ignore[method-assign]
        app._start_periodic_refresh()
        await _settle(app, pilot)
        assert app.controller.state.stale is True

        client.torrents_info = FakeQbitClient.torrents_info.__get__(client)
        app._start_periodic_refresh()
        await _settle(app, pilot)

        assert app.controller.state.stale is False
        assert app.controller.state.connection is ConnectionState.CONNECTED
        banner = app.query_one("#banner", ConnectionBanner)
        assert "visible" not in banner.classes


async def test_internal_error_shows_distinct_fatal_state() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)

        def _boom() -> Any:
            raise TypeError("a real programming defect")

        client.torrents_info = _boom  # type: ignore[method-assign]
        app._start_periodic_refresh()
        await _settle(app, pilot)

        banner = app.query_one("#banner", ConnectionBanner)
        assert "visible" in banner.classes
        assert "internal error" in str(banner.content).lower()
        assert "unavailable" not in str(banner.content).lower()
        assert app.controller.state.connection != ConnectionState.RECONNECTING


# --- 12. Visual-polish + Explain phase ---------------------------------------


async def test_torrents_columns_disclose_progressively() -> None:
    # "Sel" (the focus/selection indicator column) is always present at
    # every width -- focus and multi-selection must never lose their
    # only visual indicator just because the terminal is narrow.
    assert _columns_for_width(80) == ("Sel", "Name", "State", "Progress")
    assert _columns_for_width(99) == ("Sel", "Name", "State", "Progress")
    assert _columns_for_width(100) == (
        "Sel",
        "Name",
        "State",
        "Progress",
        "Rate",
        "Ratio",
    )
    assert _columns_for_width(129) == (
        "Sel",
        "Name",
        "State",
        "Progress",
        "Rate",
        "Ratio",
    )
    assert _columns_for_width(130) == (
        "Sel",
        "Name",
        "Category",
        "State",
        "Progress",
        "Rate",
        "Ratio",
    )


async def test_torrents_table_columns_match_width_at_each_tested_size() -> None:
    for size in RESPONSIVE_SIZES:
        client = FakeQbitClient(
            torrents=[make_torrent(name="Alpha", category="films")]
        )
        app = _app(client)
        async with app.run_test(size=size) as pilot:
            await _settle(app, pilot)
            await _goto_torrents(app, pilot)

            table = app.query_one("#torrents", DataTable)
            # Strip a possible trailing active-sort arrow (" ↑"/" ↓",
            # see `_column_header`) before comparing -- no column name
            # itself contains a space.
            labels = tuple(
                str(c.label).split(" ")[0] for c in table.columns.values()
            )
            assert labels == _columns_for_width(size[0])
            # Row identity always survives regardless of visible columns:
            # the Name column itself is never dropped at any tested width.
            assert "Name" in labels
            assert table.row_count == 1


async def test_table_row_identity_is_always_the_full_hash() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=NARROW_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        assert app._hash_by_row[0] == torrent_hash


async def test_overview_conceptual_groups_are_distinct() -> None:
    """Activity and Completion must not be presented as one partition:
    a completed, seeding, stopped torrent should show up correctly in
    all three dimensions at once, not contradict any of them."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(
                hash="a" * 40,
                name="Alpha",
                state="pausedUP",
                progress=1.0,
            )
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)

        overview_text = _static_text(
            app.query_one("#overview-workspace", OverviewPanel)
        )
        # Seeding-by-direction (paused* + UP suffix folds into "seeding").
        assert re.search(r"seeding\s+1", overview_text)
        # Also 1 stopped (Activity's own, separate line).
        assert re.search(r"stopped\s+1", overview_text)
        # Also 1 complete (Completion's own, separate dimension).
        assert re.search(r"complete\s+1", overview_text)


async def test_no_tracker_endpoint_shows_duplicated_disabled_word() -> None:
    """A disabled pseudo-tracker (DHT/PeX/LSD) must render
    its disabled status exactly once, not 'disabled disabled'."""
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={torrent_hash: [{"url": "", "status": 0, "msg": ""}]},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        details = await _open_details(app, pilot)
        rendered = _static_text(details)
        assert "disabled disabled" not in rendered


# --- Copy hash -----------------------------------------------------------


async def test_copy_hash_performs_zero_api_calls() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={torrent_hash: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        calls_before = len(client.calls)

        await pilot.press("c")
        await pilot.pause()

        assert len(client.calls) == calls_before


async def test_copy_hash_uses_the_full_canonical_hash_from_table() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={torrent_hash: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("c")
        await pilot.pause()

        assert app._clipboard == torrent_hash


async def test_copy_hash_from_details_screen_at_narrow_width() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={torrent_hash: []},
    )
    app = _app(client)

    async with app.run_test(size=NARROW_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("enter")
        await _settle(app, pilot)
        assert isinstance(app.screen, DetailsScreen)

        await pilot.press("c")
        await pilot.pause()

        assert app._clipboard == torrent_hash


async def test_copy_hash_with_no_focus_is_a_safe_notification() -> None:
    client = FakeQbitClient(torrents=[])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("c")
        await pilot.pause()

        assert not app._clipboard


# --- Filters modal: exclusive radios + visible actions ------------------


async def test_filter_modal_completion_radio_maps_exclusively() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", progress=1.0),
            make_torrent(hash="b" * 40, name="Beta", progress=0.5),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()

        completion = app.screen.query_one("#f-completed", RadioSet)
        buttons = list(completion.query(RadioButton))
        buttons[1].value = True  # "Completed"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert app.controller.state.filters.completed is True
        assert _visible_names(app) == ["Alpha"]

        buttons[2].value = True  # "Incomplete"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert app.controller.state.filters.completed is False
        assert _visible_names(app) == ["Beta"]

        buttons[0].value = True  # "Any"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert app.controller.state.filters.completed is None
        assert sorted(_visible_names(app)) == ["Alpha", "Beta"]


async def test_filter_modal_activity_radio_maps_exclusively() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", state="downloading"),
            make_torrent(hash="b" * 40, name="Beta", state="pausedDL"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()

        activity = app.screen.query_one("#f-active", RadioSet)
        buttons = list(activity.query(RadioButton))
        buttons[1].value = True  # "Active"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert app.controller.state.filters.active is True
        assert _visible_names(app) == ["Alpha"]

        buttons[2].value = True  # "Inactive"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert app.controller.state.filters.active is False
        assert _visible_names(app) == ["Beta"]


async def test_filter_apply_key_applies_and_stays_open() -> None:
    """`enter` commits the draft -- and, unlike every other modal's
    `Apply`, does not close (see `FiltersScreen`'s class docstring)."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", category="films"),
            make_torrent(hash="b" * 40, name="Beta", category="tv"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()
        category_input = app.screen.query_one("#f-categories", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert len(app.screen_stack) > 1
        assert app.controller.state.filters.categories == ("films",)


async def test_the_footer_count_describes_the_list_not_the_draft() -> None:
    """The footer's count line reads off the *applied*
    filter and the table's own visible count, never the draft -- an
    un-applied edit changes what the `*` line says, never this one."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", category="films"),
            make_torrent(hash="b" * 40, name="Beta", category="tv"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()

        count = app.screen.query_one(".f-count", Static)
        assert str(count.content) == "0 filters applied · showing 2 of 2"

        category_input = app.screen.query_one("#f-categories", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()
        # Typed, not applied -- the list line does not move yet.
        assert str(count.content) == "0 filters applied · showing 2 of 2"

        await pilot.press("enter")
        await pilot.pause()
        assert str(count.content) == "1 filters applied · showing 1 of 2"

        await pilot.press("escape")
        await pilot.pause()


async def test_an_impossible_range_disarms_apply_and_shows_the_error() -> None:
    """`min > max` never reaches `state.filters` -- `enter`
    is answered (it re-renders the error), but it does not apply, and
    the modal stays open with `✕ ...` explaining why."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha", size=1_000_000)]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()
        await pilot.press("alt+right", "alt+right")  # Organisation -> Measures
        await pilot.pause()

        size_min = app.screen.query_one("#f-size_min", Input)
        size_max = app.screen.query_one("#f-size_max", Input)
        size_min.focus()
        await pilot.press(*"50GiB")
        await pilot.pause()
        size_max.focus()
        await pilot.press(*"1GiB")
        await pilot.pause()

        error = app.screen.query_one(".f-error", Static)
        assert str(error.content).startswith("✕")

        await pilot.press("enter")
        await pilot.pause()

        assert len(app.screen_stack) > 1  # never closed either
        assert app.controller.state.filters.size.is_unset
        assert str(error.content).startswith("✕")

        # Fixing the range arms Apply for real.
        size_max.focus()
        await pilot.press("ctrl+u")
        await pilot.press(*"2TiB")
        await pilot.pause()
        assert str(app.screen.query_one(".f-error", Static).content) == ""

        await pilot.press("enter")
        await pilot.pause()
        assert app.controller.state.filters.size.min == 50 * 1024**3

        await pilot.press("escape")
        await pilot.pause()


async def test_measures_placeholders_never_apply_and_never_pend() -> None:
    """Every Measures field shows a
    grayed-out example of the syntax it expects. `Input.placeholder` is
    never `.value` in Textual, but this checks it end to end rather
    than assuming: an untouched placeholder must not count as a pending
    edit (no `*` on the tab, see criterion 4/5bis) and must not reach
    `state.filters` on Apply (criterion 6/7)."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()
        await pilot.press("alt+right", "alt+right")  # Organisation -> Measures
        await pilot.pause()

        placeholders = {
            "f-ratio_min": "2.0",
            "f-ratio_max": "2.0",
            "f-size_min": "50GiB",
            "f-size_max": "50GiB",
            "f-progress_min": "90%",
            "f-progress_max": "90%",
            "f-uploaded_min": "50GiB",
            "f-uploaded_max": "50GiB",
            "f-added_min": "7d",
            "f-added_max": "7d",
            "f-completed_at_min": "7d",
            "f-completed_at_max": "7d",
            "f-last_activity_min": "7d",
            "f-last_activity_max": "7d",
            "f-seeded_for": "30d",
        }
        for field_id, expected in placeholders.items():
            field = app.screen.query_one(f"#{field_id}", Input)
            assert field.placeholder == expected, field_id
            # A placeholder is never a value -- untouched, `.value` is
            # still empty, not the example text.
            assert field.value == "", field_id

        dialog = app.screen.query_one("#filters-dialog")
        title = Text.from_markup(str(dialog.border_title)).plain
        assert "*" not in title, title

        await pilot.press("enter")
        await pilot.pause()

        assert app.controller.state.filters == TorrentFilter()
        assert len(app.screen_stack) > 1  # `enter` never closes


def test_filters_draft_is_open_no_longer_exists() -> None:
    """`_filters_draft_is_open` existed
    only to suppress reconciliation while a live-applying draft was
    open. There is no such draft state to protect any more (see
    `TuiController.apply_refresh_success`), and a helper nobody calls
    is exactly the kind of stale escape hatch that gets rediscovered
    and reused for the wrong reason."""
    assert not hasattr(QbitOpsTuiApp, "_filters_draft_is_open")


async def test_filter_cancel_key_closes_without_applying_the_draft() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", category="films"),
            make_torrent(hash="b" * 40, name="Beta", category="tv"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()
        category_input = app.screen.query_one("#f-categories", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()

        assert len(app.screen_stack) == 1
        assert app.controller.state.filters.categories == ()


async def test_filter_clear_key_empties_the_draft_without_applying() -> None:
    """`^r` clears the *draft*, never the applied filter: a category
    already `Apply`-ed stays in effect through a `Clear` that only
    touches what has not been committed yet."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", category="films"),
            make_torrent(hash="b" * 40, name="Beta", category="tv"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()
        category_input = app.screen.query_one("#f-categories", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.controller.state.filters.categories == ("films",)

        await pilot.press(*"tv")
        await pilot.pause()

        await pilot.press("ctrl+r")
        await pilot.pause()

        assert len(app.screen_stack) > 1
        assert category_input.value == ""
        assert app.controller.state.filters.categories == ("films",)
        await pilot.press("escape")
        await pilot.pause()


async def test_filter_modal_visible_and_actionable_at_every_width() -> None:
    for size in RESPONSIVE_SIZES:
        client = FakeQbitClient(torrents=[make_torrent()])
        app = _app(client)
        async with app.run_test(size=size) as pilot:
            await _settle(app, pilot)
            await _goto_torrents(app, pilot)
            await pilot.press("f")
            await pilot.pause()

            assert app.screen.query_one("#f-categories", Input) is not None
            assert app.screen.query_one("#f-completed", RadioSet) is not None
            assert app.screen.query_one("#f-active", RadioSet) is not None

            await pilot.press("escape")
            await pilot.pause()
            assert len(app.screen_stack) == 1


async def test_the_dialog_keeps_two_columns_of_floor_at_every_width() -> None:
    """Asserts the geometry itself (`x=2, w=92` at 96 columns), not
    merely the absence of overflow: `max-width: 100%` alone already
    does not overflow, so only checking for overflow would miss a
    dialog flush against both edges instead of margined. Exercised on
    `DetailsScreen`, which stays wide enough to still overflow (and
    clamp) at every terminal width below -- `FiltersScreen` no longer
    does."""
    cases = [
        (90, 2, 86),
        (96, 2, 92),
        (140, 20, 100),
    ]
    for width, expected_x, expected_w in cases:
        client = FakeQbitClient(torrents=[make_torrent()])
        app = _app(client)
        async with app.run_test(size=(width, 40)) as pilot:
            await _settle(app, pilot)
            await _goto_torrents(app, pilot)
            await pilot.press("enter")
            await pilot.pause()

            region = app.screen.query_one("#details-dialog").region
            assert region.x == expected_x, (width, region)
            assert region.width == expected_w, (width, region)
            assert region.x + region.width <= width, (width, region)
            # Two columns of floor on the right too, not just the left.
            assert width - (region.x + region.width) == expected_x, (
                width,
                region,
            )

            await pilot.press("escape")
            await pilot.pause()


# --- Explain ---------------------------------------------------------------


async def test_e_opens_explain_from_torrents_with_details_already_loaded() -> (
    None
):
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=torrent_hash, name="Alpha", state="uploading")
        ],
        trackers_by_hash={torrent_hash: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("e")
        await pilot.pause()

        assert isinstance(app.screen, ExplainScreen)
        content = str(app.screen.query_one("#explain-content", Static).content)
        assert "Alpha" in content
        assert "Fetching tracker data" not in content


async def test_e_performs_zero_api_calls_when_details_already_loaded() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={torrent_hash: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        # Load tracker details first (opening the modal, then closing
        # it) -- focus alone never fetches them.
        await _open_details(app, pilot)
        await pilot.press("escape")
        await pilot.pause()
        calls_before = len(client.calls)

        await pilot.press("e")
        await pilot.pause()

        assert len(client.calls) == calls_before


async def test_e_never_calls_torrents_info() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={torrent_hash: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        calls_before = client.torrents_info_calls

        await pilot.press("e")
        await pilot.pause()

        assert client.torrents_info_calls == calls_before


async def test_e_fetches_at_most_one_tracker_call_when_not_loaded() -> None:
    torrent_hash = "a" * 40
    client = BlockingTrackerClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={torrent_hash: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        # Focus alone never fetches -- `e` (Explain) dispatches the
        # only fetch here, and it blocks immediately.
        await pilot.press("e")
        await pilot.pause()
        await asyncio_wait_for_event(client.entered_event(torrent_hash))

        assert isinstance(app.screen, ExplainScreen)
        content = str(app.screen.query_one("#explain-content", Static).content)
        assert "Fetching tracker data" in content

        client.release_event(torrent_hash).set()
        await _settle(app, pilot)

        assert client.torrents_trackers_calls == 1
        content_after = str(
            app.screen.query_one("#explain-content", Static).content
        )
        assert "Fetching tracker data" not in content_after


async def test_e_from_overview_is_a_safe_notification() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)

        await pilot.press("e")
        await pilot.pause()

        assert len(app.screen_stack) == 1
        assert app.controller.state.workspace is Workspace.OVERVIEW


async def test_e_with_no_focus_is_a_safe_notification() -> None:
    client = FakeQbitClient(torrents=[])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("e")
        await pilot.pause()

        assert len(app.screen_stack) == 1


async def test_explain_modal_closes_with_escape() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")],
        trackers_by_hash={"a" * 40: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, ExplainScreen)

        await pilot.press("escape")
        await pilot.pause()

        assert len(app.screen_stack) == 1


async def test_workspace_navigation_blocked_behind_explain_modal() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")],
        trackers_by_hash={"a" * 40: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("e")
        await pilot.pause()

        await pilot.press("g")
        await pilot.pause()

        assert isinstance(app.screen, ExplainScreen)
        assert app.controller.state.workspace is Workspace.TORRENTS


async def test_explain_preserves_search_filter_and_cursor_state() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha ISO", category="films"),
            make_torrent(hash="b" * 40, name="Beta ISO", category="films"),
        ],
        trackers_by_hash={"a" * 40: [], "b" * 40: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _type_into_search(pilot, "iso")
        await pilot.press("escape")  # leave the search input, keep the text
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app.controller.state.search == "iso"
        assert sorted(_visible_names(app)) == ["Alpha ISO", "Beta ISO"]
        assert app.controller.state.workspace is Workspace.TORRENTS


async def test_explain_race_discards_stale_result_after_focus_change() -> None:
    hash_a, hash_b = "a" * 40, "b" * 40
    client = BlockingTrackerClient(
        torrents=[
            make_torrent(hash=hash_a, name="Alpha"),
            make_torrent(hash=hash_b, name="Beta"),
        ],
        trackers_by_hash={hash_a: [], hash_b: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        # `e` (Explain) dispatches A's fetch -- focus alone never does.
        await pilot.press("e")
        await pilot.pause()
        await asyncio_wait_for_event(client.entered_event(hash_a))
        assert isinstance(app.screen, ExplainScreen)
        explain_screen = app.screen

        # Focus moves to B before A's fetch resolves -- this bumps the
        # detail-request generation even though it dispatches no fetch
        # of its own now (see `TuiController.begin_focus_change`).
        table = app.query_one("#torrents", DataTable)
        table.move_cursor(row=1)
        await pilot.pause()

        # A's fetch now completes -- its result must never populate the
        # still-open Explain modal (which was opened for A), since the
        # focus change already superseded its request id.
        client.release_event(hash_a).set()
        await asyncio.sleep(0)
        await pilot.pause()

        content = str(
            explain_screen.query_one("#explain-content", Static).content
        )
        assert "Fetching tracker data" in content
        assert explain_screen.report is None
        await _settle(app, pilot)


async def test_explain_modal_closed_before_result_arrives_never_reopens() -> (
    None
):
    torrent_hash = "a" * 40
    client = BlockingTrackerClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={torrent_hash: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("e")
        await pilot.pause()
        await asyncio_wait_for_event(client.entered_event(torrent_hash))
        assert isinstance(app.screen, ExplainScreen)

        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1

        client.release_event(torrent_hash).set()
        await _settle(app, pilot)

        assert len(app.screen_stack) == 1


async def test_explain_survives_a_result_before_it_mounts() -> None:
    """`push_screen` adds a screen to `screen_stack` synchronously but
    mounts it -- and composes `#explain-content` -- on a later tick.
    `_maybe_resolve_pending_explain` runs from a worker's completion
    message, which can be processed before that tick and must not raise
    `NoMatches` on the still-unmounted screen. This installs that exact
    ordering -- push, then resolve, no `await` between -- instead of
    relying on real thread/loop timing to hit it.
    """
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={torrent_hash: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        request_id = app.controller.begin_manual_detail_refresh()
        assert request_id is not None
        app.controller.apply_tracker_details_success(
            request_id, torrent_hash, []
        )
        screen = ExplainScreen("Alpha", None)
        app._explain_screen = screen
        app._pending_explain_request_id = request_id
        app.push_screen(screen)
        assert screen in app.screen_stack
        assert not screen.is_mounted

        app._maybe_resolve_pending_explain(request_id, torrent_hash)

        await pilot.pause()
        content = str(screen.query_one("#explain-content", Static).content)
        assert "Alpha" in content
        assert "Fetching tracker data" not in content


async def test_explain_modal_scrollable_at_every_tested_width() -> None:
    for size in RESPONSIVE_SIZES:
        torrent_hash = "a" * 40
        client = FakeQbitClient(
            torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
            trackers_by_hash={torrent_hash: []},
        )
        app = _app(client)
        async with app.run_test(size=size) as pilot:
            await _settle(app, pilot)
            await _goto_torrents(app, pilot)
            await pilot.press("e")
            await pilot.pause()

            assert isinstance(app.screen, ExplainScreen)
            from textual.containers import VerticalScroll

            assert app.screen.query_one("#explain-dialog", VerticalScroll)
            await pilot.press("escape")
            await pilot.pause()


async def test_explain_never_leaks_a_raw_tracker_url() -> None:
    torrent_hash = "a" * 40
    secret_url = "https://tracker.example/announce/TOPSECRETPASSKEY?passkey=abc"
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=torrent_hash, name="Alpha", state="stalledDL")
        ],
        trackers_by_hash={torrent_hash: [{"url": secret_url, "status": 4}]},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("e")
        await pilot.pause()

        content = str(app.screen.query_one("#explain-content", Static).content)
        assert "TOPSECRETPASSKEY" not in content
        assert "passkey" not in content
        assert "https://" not in content


# --- 13. Final UX-polish pass -------------------------------------------


def _footer_actions(app: QbitOpsTuiApp) -> set[str]:
    active = app.screen.active_bindings
    return {
        binding.action
        for (_, binding, enabled, _) in active.values()
        if binding.show and enabled
    }


async def test_overview_footer_has_no_torrent_only_actions() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)

        actions = _footer_actions(app)

        assert actions == {"toggle_help", "quit", "reconfigure"}
        for forbidden in (
            "copy_hash",
            "explain",
            "refresh_details",
            "focus_search",
            "open_filters",
            "open_sort",
            "show_overview",
            "show_torrents",
        ):
            assert forbidden not in actions


async def test_focused_torrent_actions_stay_out_of_footer_but_reachable() -> (
    None
):
    """Copy/Explain/Refresh belong in the details panel, not the global
    footer (kept to primary workspace actions) -- but the bindings stay
    reachable by key whenever a torrent is focused, and `check_action`
    still refuses them with nothing focused."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        focused_actions = _footer_actions(app)
        for action in ("copy_hash", "explain", "refresh_details"):
            assert action not in focused_actions
        assert app.check_action("copy_hash", ()) is True
        assert app.check_action("explain", ()) is True
        assert app.check_action("refresh_details", ()) is True

        await _type_into_search(pilot, "nonexistent-name")
        await pilot.press("escape")  # leave the search Input, back to the
        await pilot.pause()  # table -- clears focus (no rows match).

        unfocused_actions = _footer_actions(app)
        assert "copy_hash" not in unfocused_actions
        assert "explain" not in unfocused_actions
        assert "refresh_details" not in unfocused_actions
        assert app.check_action("copy_hash", ()) is False
        assert app.check_action("explain", ()) is False
        assert app.check_action("refresh_details", ()) is False
        # Search/Filters/Sort/Help/Quit remain regardless of focus --
        # Overview/Torrents navigation is never advertised in the
        # footer at all now (the top workspace-tabs strip is the sole
        # visible way to advertise switching pages).
        assert "focus_search" in unfocused_actions
        assert "open_filters" in unfocused_actions
        assert "open_sort" in unfocused_actions
        assert "show_overview" not in unfocused_actions
        assert "quit" in unfocused_actions
        assert "toggle_help" in unfocused_actions


async def test_footer_never_shows_workspace_nav_hints_in_either_workspace() -> (
    None
):
    """Neither `show_overview` nor `show_torrents` is ever advertised in
    the footer, in either workspace -- the top workspace-tabs strip is
    the sole visible way to advertise switching pages. The `g`/`t`/`1`/
    `2` keys themselves keep working regardless (see
    `test_workspace_nav_keys_still_work_without_a_footer_hint`)."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        overview_actions = _footer_actions(app)
        assert "show_torrents" not in overview_actions
        assert "show_overview" not in overview_actions

        await _goto_torrents(app, pilot)
        torrents_actions = _footer_actions(app)
        assert "show_overview" not in torrents_actions
        assert "show_torrents" not in torrents_actions


async def test_workspace_nav_keys_still_work_without_a_footer_hint() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        assert app.controller.state.workspace is Workspace.OVERVIEW

        await pilot.press("t")
        await pilot.pause()
        assert app.controller.state.workspace is Workspace.TORRENTS

        await pilot.press("g")
        await pilot.pause()
        assert app.controller.state.workspace is Workspace.OVERVIEW

        await pilot.press("2")
        await pilot.pause()
        assert app.controller.state.workspace is Workspace.TORRENTS

        await pilot.press("1")
        await pilot.pause()
        assert app.controller.state.workspace is Workspace.OVERVIEW


async def test_help_is_readable_at_80x24() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=(80, 24)) as pilot:
        await _settle(app, pilot)

        await pilot.press("question_mark")
        await pilot.pause()

        assert isinstance(app.screen, HelpScreen)
        from textual.containers import VerticalScroll

        dialog = app.screen.query_one("#help-dialog", VerticalScroll)
        assert dialog is not None
        text = _static_text(dialog)
        assert "Global" in text
        assert "Torrents workspace" in text
        # No line should be so long it cannot fit an 80-column terminal
        # with room for the dialog's own border/padding.
        for line in text.splitlines():
            assert len(line) < 80

        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1


async def test_help_down_scrolls_instead_of_leaving_the_dialog_unfocused() -> (
    None
):
    """`HelpScreen` has no focusable control besides its own dialog --
    `QbitModal`'s shared `up`/`down` focus-navigation binding must not
    steal the key from `ScrollableContainer`'s native scroll, or a
    content-only modal would lose focus outright (see `QbitModal`'s
    `BINDINGS` comment)."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=(80, 20)) as pilot:
        await _settle(app, pilot)
        await pilot.press("question_mark")
        await pilot.pause()

        from textual.containers import VerticalScroll

        dialog = app.screen.query_one("#help-dialog", VerticalScroll)
        assert dialog.has_focus

        await pilot.press("down")
        await pilot.pause()

        assert dialog.has_focus
        assert dialog.scroll_y > 0


# --- Local timestamps: injected timezone, not the CI machine's ----------


def test_format_local_time_uses_injected_timezone_not_system() -> None:
    from datetime import UTC, datetime, timedelta, timezone

    fixed_ist = timezone(timedelta(hours=5, minutes=30), "IST")
    moment = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    rendered = _format_local_time(moment, tz=fixed_ist)

    assert rendered == "17:30:00 IST"


def test_format_local_time_preserves_timezone_aware_input() -> None:
    from datetime import UTC, datetime, timedelta, timezone

    fixed = timezone(timedelta(hours=-3), "ART")
    moment = datetime(2026, 6, 15, 0, 30, 0, tzinfo=UTC)

    rendered = _format_local_time(moment, tz=fixed)

    assert rendered == "21:30:00 ART"


async def test_overview_connection_uses_injected_timezone() -> None:
    from datetime import timedelta, timezone
    from unittest.mock import patch

    fixed_tz = timezone(timedelta(hours=9), "JST")
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)

        # Patched at `qbit_ops.tui.widgets.overview`: the refresh moment
        # is formatted by its own local `_format_rail_time`, not the
        # shared `qbit_ops.tui.formatting._format_local_time`.
        with patch("qbit_ops.tui.widgets.overview._format_rail_time") as mocked:
            mocked.side_effect = lambda moment, tz=None: (
                f"stub-time {fixed_tz.tzname(moment)}"
            )
            app._render_overview()
            masthead = app.query_one("#overview-masthead")
            assert "stub-time JST" in str(masthead.border_subtitle)


# --- Explain rendering polish ---------------------------------------------


async def test_explain_summary_is_not_duplicated() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=torrent_hash, name="Alpha", state="stalledUP")
        ],
        trackers_by_hash={torrent_hash: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("e")
        await pilot.pause()

        content = str(app.screen.query_one("#explain-content", Static).content)
        report = app.controller.build_explanation()
        assert report is not None
        occurrences = content.count(report.summary)
        assert occurrences == 1


async def test_explain_evidence_is_human_formatted() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[
            make_torrent(
                hash=torrent_hash,
                name="Alpha",
                state="downloading",
                progress=0.4567,
                dlspeed=2_000_000,
                upspeed=0,
            )
        ],
        trackers_by_hash={torrent_hash: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("e")
        await pilot.pause()

        content = str(app.screen.query_one("#explain-content", Static).content)
        assert "45.7%" in content
        assert "MiB/s" in content or "KiB/s" in content
        # Never a raw float/int dump for progress.
        assert "0.4567" not in content


async def test_explain_long_torrent_title_is_truncated_safely() -> None:
    long_name = "A" * 200
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name=long_name)],
        trackers_by_hash={torrent_hash: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("e")
        await pilot.pause()

        content = str(app.screen.query_one("#explain-content", Static).content)
        header_line = content.splitlines()[0]
        # The header title itself is truncated safely -- the full name
        # may still legitimately appear later as an evidence value
        # (that's real data, not a layout risk the same way a runaway
        # header line is).
        assert len(header_line) < 90
        assert long_name not in header_line


def test_truncate_helper_is_safe_and_stable() -> None:
    assert _truncate("short", 60) == "short"
    truncated = _truncate("x" * 100, 60)
    assert len(truncated) <= 60
    assert truncated.endswith("…")


# --- Filters: radio focus/selection distinguishability ---------------------


async def test_radio_selected_and_focused_states_are_distinguishable() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()

        completion = app.screen.query_one("#f-completed", RadioSet)
        buttons = list(completion.query(RadioButton))

        # Selection state (on/off) is glyph-based, not color-only --
        # verify exactly one is selected at a time (Any, by default).
        assert sum(1 for b in buttons if b.value) == 1

        # Focus state is independently visible: focusing the RadioSet
        # itself is possible and does not change which button is
        # selected.
        completion.focus()
        await pilot.pause()
        assert completion.has_focus
        assert sum(1 for b in buttons if b.value) == 1

        await pilot.press("escape")
        await pilot.pause()


async def test_completion_and_activity_each_have_one_semantic_value() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()

        completion = app.screen.query_one("#f-completed", RadioSet)
        activity = app.screen.query_one("#f-active", RadioSet)

        assert completion.pressed_index is not None
        assert activity.pressed_index is not None

        completion_buttons = list(completion.query(RadioButton))
        completion_buttons[1].value = True
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert app.controller.state.filters.completed is True
        assert app.controller.state.filters.active is None

        await pilot.press("escape")
        await pilot.pause()


# --- Details: no duplicated tracker status ---------------------------------


async def test_details_never_show_duplicated_tracker_status_text() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={
            torrent_hash: [
                {"url": "", "status": 0, "msg": ""},
                {"url": "https://tracker.example/announce", "status": 4},
            ]
        },
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        details = await _open_details(app, pilot)
        rendered = _static_text(details)
        assert "disabled disabled" not in rendered
        assert "critical critical" not in rendered
        for line in rendered.splitlines():
            words = line.split()
            # No status word should repeat back-to-back on the same
            # line (e.g. "disabled disabled") -- a symptom of double-
            # reporting the same fact via two different fields.
            for first, second in zip(words, words[1:], strict=False):
                assert not (first == second and first.isalpha())


# --- Modal bindings still work through App-level dispatch -------------------


async def test_all_modal_bindings_still_dispatch_correctly() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=torrent_hash, name="Alpha", category="films")
        ],
        trackers_by_hash={torrent_hash: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        # FiltersScreen: Enter applies, stays open; Escape then closes.
        await pilot.press("f")
        await pilot.pause()
        category_input = app.screen.query_one("#f-categories", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert len(app.screen_stack) > 1
        assert app.controller.state.filters.categories == ("films",)
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1

        # FiltersScreen: an un-applied edit never reaches state.filters,
        # so Escape leaves it exactly as it was.
        await pilot.press("f")
        await pilot.pause()
        cat2 = app.screen.query_one("#f-categories", Input)
        cat2.focus()
        await pilot.press("ctrl+u")
        await pilot.press(*"tv")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert app.controller.state.filters.categories == ("films",)

        # DetailsScreen: enter opens (at every width now), escape closes.
        # Re-focus the torrents table first: the prior FiltersScreen
        # cancel left focus on its now-unmounted Input widget.
        app.query_one("#torrents", DataTable).focus()
        await pilot.pause()
        await pilot.press("enter")
        await _settle(app, pilot)
        assert isinstance(app.screen, DetailsScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1

        # HelpScreen: question_mark opens, escape closes.
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1

        # ExplainScreen: e opens, escape closes.
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, ExplainScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1


async def test_filters_modal_is_keyboard_interactive_immediately_on_open() -> (
    None
):
    """Textual's default `AUTO_FOCUS = "*"` auto-focuses the *first*
    focusable widget in DOM order on a newly pushed screen --
    `#filters-dialog` itself (a `VerticalScroll`, therefore focusable)
    qualifies before the category `Input` nested inside it. Without an
    explicit override, every keystroke right after pressing `f` would go
    to the scroll container (up/down/page keys only) instead of any
    actual field."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", category="films"),
            make_torrent(hash="b" * 40, name="Beta", category="tv"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("f")
        await pilot.pause()

        category_input = app.screen.query_one("#f-categories", Input)
        assert category_input.has_focus

        await pilot.press(*"films")
        await pilot.pause()

        assert category_input.value == "films"
        await pilot.press("enter")
        await pilot.pause()
        assert app.controller.state.filters.categories == ("films",)
        assert _visible_names(app) == ["Alpha"]

        await pilot.press("escape")
        await pilot.pause()


async def test_tab_navigates_between_filter_fields() -> None:
    """`check_action` must not block `app.focus_next`/`app.focus_previous`
    while a modal is open -- Textual's own `Screen`-level `tab`/
    `shift+tab` bindings depend on them. Tab only ever reaches the
    *active* pane's fields -- the other three panes' rows are mounted
    but `display: none` (see `FiltersPanel`), so `alt+right` is what
    reaches a field in a different pane, not Tab."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()

        category_input = app.screen.query_one("#f-categories", Input)
        assert category_input.has_focus

        await pilot.press("tab")
        await pilot.pause()
        excluded_input = app.screen.query_one("#f-categories_excluded", Input)
        assert excluded_input.has_focus

        await pilot.press("shift+tab")
        await pilot.pause()
        assert category_input.has_focus

        await pilot.press("alt+right")
        await pilot.pause()
        state_input = app.screen.query_one("#f-states", Input)
        assert state_input.has_focus

        await pilot.press("escape")
        await pilot.pause()


async def test_section_keys_actually_change_the_active_pane() -> None:
    """The announcement guard only checks a key is *bound*
    (`test_every_announced_key_is_a_binding_that_is_actually_active`),
    never that pressing it changes anything -- so `alt+left`/`alt+right`,
    if silently eaten by the window manager before reaching the
    terminal, would go uncaught. `pageup`/`pagedown` are the announced
    gesture; `alt+left`/`alt+right` stay bound for terminals that do
    deliver them. This presses all four, each direction, and checks the
    pane actually switched (via which field picks up focus), not just
    that the key resolves to a binding."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()

        category_input = app.screen.query_one("#f-categories", Input)
        assert category_input.has_focus

        # Organisation(0) -> Trackers(3): "prev_pane" wraps backward.
        await pilot.press("pageup")
        await pilot.pause()
        assert app.screen.query_one("#f-no_trackers").has_focus

        # Trackers(3) -> Organisation(0): "next_pane" wraps forward.
        await pilot.press("pagedown")
        await pilot.pause()
        assert category_input.has_focus

        # Same two directions again, via the kept-but-unannounced keys.
        await pilot.press("alt+left")
        await pilot.pause()
        assert app.screen.query_one("#f-no_trackers").has_focus

        await pilot.press("alt+right")
        await pilot.pause()
        assert category_input.has_focus

        await pilot.press("escape")
        await pilot.pause()


async def test_pageup_pagedown_switch_panes_even_when_the_dialog_scrolls() -> (
    None
):
    """`priority=True` is what makes this true regardless of window
    height: shrink the terminal enough that the dialog actually clips
    and becomes scrollable, and a non-priority `pageup` would be
    consumed by that real scroll instead of switching sections."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=(140, 20)) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()

        dialog = app.screen.query_one("#filters-dialog")
        assert dialog.allow_vertical_scroll, (
            "test assumption broken: the dialog no longer scrolls at "
            "this terminal height, so this test would pass vacuously"
        )

        await pilot.press("pageup")
        await pilot.pause()
        assert app.screen.query_one("#f-no_trackers").has_focus

        await pilot.press("escape")
        await pilot.pause()


# --- 14. TUI 2: multi-selection + LOW-risk bulk actions ---------------------


class BlockingMutationClient(FakeQbitClient):
    """A `FakeQbitClient` whose `torrents_pause()` blocks on a real
    `threading.Event` until released -- used to prove Apply cannot be
    double-dispatched and that a periodic refresh never races it."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.entry_count = 0

    def torrents_pause(self, torrent_hashes: Any) -> None:
        self.entry_count += 1
        self.entered.set()
        if not self.release.wait(timeout=WAIT_TIMEOUT):
            raise TimeoutError("test forgot to release BlockingMutationClient")
        super().torrents_pause(torrent_hashes)


async def test_space_selects_and_shows_a_visible_marker() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        assert app.controller.state.selected_hashes == set()
        await pilot.press("space")
        await pilot.pause()

        assert app.controller.state.selected_hashes == {"a" * 40}
        table = app.query_one("#torrents", DataTable)
        cell = table.get_row_at(0)[0]
        # A heavier, bold+colored glyph -- easier to spot at a glance
        # than a plain unstyled "✓" (requested after dogfooding). Focus
        # and selection are two independent glyphs in this cell (row 0
        # is both focused, from the initial cursor, and now selected),
        # so their styles are checked per-span, not on the cell as a
        # whole.
        assert "✔" in str(cell)
        from qbit_ops.tui.formatting import _BRAND_ACCENT

        # Focus and selection now share the same restrained brand
        # accent (both glyphs are "warm orange" per design), so they're
        # told apart by glyph shape, not colour -- find the span that
        # actually covers the "✔" character and check its style there.
        select_span = next(
            span
            for span in cell.spans
            if cell.plain[span.start : span.end] == "✔"
        )
        assert select_span.style == f"bold {_BRAND_ACCENT}"


async def test_filter_summary_shows_selected_count() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha"),
            make_torrent(hash="b" * 40, name="Beta"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.press("j")
        await pilot.press("space")
        await pilot.pause()

        summary = app.query_one("#filter-summary", FilterSummary)
        assert "2 selected" in str(summary.content)


async def test_ctrl_a_selects_only_visible_rows_with_a_filter_active() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", category="films"),
            make_torrent(hash="b" * 40, name="Beta", category="tv"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _type_into_search(pilot, "alpha")
        await pilot.press("escape")  # leave the search box, keep the term
        await pilot.pause()

        await pilot.press("ctrl+a")
        await pilot.pause()

        assert app.controller.state.selected_hashes == {"a" * 40}


async def test_changing_filters_clears_hidden_selections() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", category="films"),
            make_torrent(hash="b" * 40, name="Beta", category="tv"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("ctrl+a")
        await pilot.pause()
        assert len(app.controller.state.selected_hashes) == 2

        await pilot.press("f")
        await pilot.pause()
        category_input = app.screen.query_one("#f-categories", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert app.controller.state.selected_hashes == {"a" * 40}


async def test_actions_modal_inaccessible_with_no_selection() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("a")
        await pilot.pause()

        assert len(app.screen_stack) == 1


async def test_actions_modal_opens_with_selection() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.pause()

        await pilot.press("a")
        await pilot.pause()

        assert isinstance(app.screen, ActionsScreen)
        content = _static_text(app.screen.query_one("#actions-dialog"))
        assert "1 selected" in content
        await pilot.press("escape")
        await pilot.pause()


async def test_each_low_risk_action_opens_a_preview() -> None:
    for button_id in ("actions-pause", "actions-resume", "actions-reannounce"):
        client = FakeQbitClient(
            torrents=[make_torrent(hash="a" * 40, name="Alpha")]
        )
        app = _app(client)
        async with app.run_test(size=WIDE_SIZE) as pilot:
            await _settle(app, pilot)
            await _goto_torrents(app, pilot)
            await pilot.press("space")
            await pilot.press("a")
            await pilot.pause()

            button = app.screen.query_one(f"#{button_id}", Button)
            await pilot.click(button)
            await pilot.pause()

            assert isinstance(app.screen, PreviewScreen)
            await pilot.press("escape")
            await pilot.pause()


async def test_each_value_action_button_opens_its_own_modal() -> None:
    """The four new `ActionsScreen` buttons each open the modal that
    collects their argument, never straight to `PreviewScreen` (see
    `qbit_ops.tui.modals.value`)."""
    cases = (
        ("actions-category-set", CategorySetScreen),
        ("actions-tag-add", TagAddScreen),
        ("actions-tag-remove", TagRemoveScreen),
        ("actions-throttle", ThrottleScreen),
    )
    for button_id, screen_class in cases:
        client = FakeQbitClient(
            torrents=[make_torrent(hash="a" * 40, name="Alpha")]
        )
        app = _app(client)
        async with app.run_test(size=WIDE_SIZE) as pilot:
            await _settle(app, pilot)
            await _goto_torrents(app, pilot)
            await pilot.press("space")
            await pilot.press("a")
            await pilot.pause()

            button = app.screen.query_one(f"#{button_id}", Button)
            await pilot.click(button)
            await pilot.pause()

            assert isinstance(app.screen, screen_class), (
                button_id,
                app.screen,
            )
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, ActionsScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert len(app.screen_stack) == 1


async def test_escape_returns_from_a_value_action_to_actions() -> None:
    """`escape` pops one conceptual level: a value modal was opened from
    `ActionsScreen`, so leaving it lands back there with every other
    action still reachable and the same frozen selection. A second
    `escape`, now on Actions, closes as it always did.

    The window manager takes `alt` plus an arrow for its own workspace
    switching, so an `alt+left` binding here would never reach the app
    -- which is why the assertion is that the key *acts*, never that it
    is bound."""
    cases = (
        ("actions-category-set", CategorySetScreen),
        ("actions-tag-add", TagAddScreen),
        ("actions-tag-remove", TagRemoveScreen),
        ("actions-throttle", ThrottleScreen),
    )
    for button_id, screen_class in cases:
        client = FakeQbitClient(
            torrents=[make_torrent(hash="a" * 40, name="Alpha")]
        )
        app = _app(client)
        async with app.run_test(size=WIDE_SIZE) as pilot:
            await _settle(app, pilot)
            await _goto_torrents(app, pilot)
            await pilot.press("space")
            await pilot.press("a")
            await pilot.pause()

            button = app.screen.query_one(f"#{button_id}", Button)
            await pilot.click(button)
            await pilot.pause()
            assert isinstance(app.screen, screen_class), (button_id, app.screen)

            assert any(
                "escape" in hint.keys and hint.label == "Back"
                for hint in screen_class.MODAL_KEYS
            ), screen_class

            await pilot.press("escape")
            await pilot.pause()

            assert isinstance(app.screen, ActionsScreen)
            assert len(app.screen_stack) == 2
            content = _static_text(app.screen.query_one("#actions-dialog"))
            assert "1 selected" in content
            assert "Alpha" in content

            # Every other action is still reachable from there.
            resume = app.screen.query_one("#actions-resume", Button)
            await pilot.click(resume)
            await pilot.pause()
            assert isinstance(app.screen, PreviewScreen)


async def test_category_set_flow_reaches_preview_with_the_typed_category() -> (
    None
):
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha", category="tv")],
        categories={"tv": {"name": "tv"}, "films": {"name": "films"}},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#actions-category-set", Button).press()
        await pilot.pause()

        input_ = app.screen.query_one("#v-category", Input)
        input_.focus()
        await pilot.press(*"films")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, PreviewScreen)
        assert app.screen.plan.category == "films"
        assert app.screen.plan.category_needs_creation is False
        await pilot.press("escape")
        await pilot.pause()


async def test_throttle_plan_can_carry_either_direction_alone() -> None:
    """`throttle` exposes both directions independently --
    a plan built from only one typed field carries only that limit,
    never a forced value on the other."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#actions-throttle", Button).press()
        await pilot.pause()

        app.screen.query_one("#v-upload", Input).focus()
        await pilot.press(*"2MiB/s")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, PreviewScreen)
        assert app.screen.plan.upload_limit == 2 * 1024 * 1024
        assert app.screen.plan.download_limit is None
        await pilot.press("escape")
        await pilot.pause()


async def test_category_set_from_snapshot_skips_already_set_torrents() -> None:
    """Uses categories that genuinely differ, so a defect that always
    reports "already_set" regardless of the real values cannot hide
    behind both sides sharing the same ""/`None` default: a torrent in
    "tv" targeted at "films" must report as a real change."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=f"{i:040x}", name=f"T{i}", category="tv")
            for i in range(3)
        ],
        categories={"tv": {"name": "tv"}, "films": {"name": "films"}},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("ctrl+a")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#actions-category-set", Button).press()
        await pilot.pause()

        app.screen.query_one("#v-category", Input).focus()
        await pilot.press(*"films")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, PreviewScreen)
        plan = app.screen.plan
        assert len(plan.changes) == 3, plan.skipped
        assert len(plan.skipped) == 0
        await pilot.press("escape")
        await pilot.pause()


async def test_category_set_from_snapshot_also_skips_when_already_set() -> None:
    """Complement to the test above: a torrent genuinely already in
    the target category is skipped, not reported as a change."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=f"{i:040x}", name=f"T{i}", category="films")
            for i in range(3)
        ],
        categories={"films": {"name": "films"}},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("ctrl+a")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#actions-category-set", Button).press()
        await pilot.pause()

        app.screen.query_one("#v-category", Input).focus()
        await pilot.press(*"films")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, PreviewScreen)
        plan = app.screen.plan
        assert len(plan.changes) == 0
        assert len(plan.skipped) == 3
        assert all(skip.reason == "already_set" for skip in plan.skipped)
        await pilot.press("escape")
        await pilot.pause()


async def test_opening_modals_costs_zero_calls_after_startup() -> None:
    """Filters and every value-action modal are built
    entirely from the already-fetched snapshot and the cached instance
    lists -- opening any of them issues no further qBittorrent call."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha", category="tv")],
        categories={"tv": {"name": "tv"}},
        tags=["stale"],
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        calls_before = len(client.calls)

        await pilot.press("f")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("space")
        await pilot.press("a")
        await pilot.pause()
        for button_id in (
            "actions-category-set",
            "actions-tag-add",
            "actions-tag-remove",
            "actions-throttle",
        ):
            await pilot.press("a")
            await pilot.pause()
            app.screen.query_one(f"#{button_id}", Button).press()
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

        assert len(client.calls) == calls_before, client.calls[calls_before:]


async def test_throttle_with_neither_direction_disarms_preview() -> None:
    """Pressing `enter`
    with both fields blank shows the "at least one direction" error
    and never opens `PreviewScreen`."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#actions-throttle", Button).press()
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, ThrottleScreen)
        verdict = str(app.screen.query_one(".v-verdict", Static).content)
        assert verdict.startswith("✕")
        assert "at least one direction" in verdict
        await pilot.press("escape")
        await pilot.pause()


async def test_escape_leaves_a_value_action_modal_without_opening_preview() -> (
    None
):
    """Leaving lands on Actions, one level up, and never on Preview: the
    modal collects an argument, it does not dispatch one."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#actions-tag-add", Button).press()
        await pilot.pause()
        assert isinstance(app.screen, TagAddScreen)

        await pilot.press("escape")
        await pilot.pause()

        assert isinstance(app.screen, ActionsScreen)
        await pilot.press("escape")
        await pilot.pause()

        assert len(app.screen_stack) == 1


async def test_preview_list_matches_selected_rows() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha ISO", state="downloading"),
            make_torrent(hash="b" * 40, name="Beta ISO", state="downloading"),
            make_torrent(hash="c" * 40, name="Gamma ISO", state="downloading"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.press("j")
        await pilot.press("space")
        await pilot.press("a")
        await pilot.pause()

        pause_button = app.screen.query_one("#actions-pause", Button)
        await pilot.click(pause_button)
        await pilot.pause()

        content = str(app.screen.query_one("#preview-content", Static).content)
        assert "Alpha ISO" in content
        assert "Beta ISO" in content
        assert "Gamma ISO" not in content
        assert "Selected             2" in content


async def test_escape_cancels_actions_and_preview_without_mutation() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", state="downloading")
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.press("a")
        await pilot.pause()
        pause_button = app.screen.query_one("#actions-pause", Button)
        await pilot.click(pause_button)
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()

        assert len(app.screen_stack) == 1
        assert client.paused_hashes == []
        assert app.controller.state.selected_hashes == {"a" * 40}


async def test_apply_runs_exactly_once_and_reports_applied() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", state="downloading")
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.press("a")
        await pilot.pause()
        await pilot.click(app.screen.query_one("#actions-pause", Button))
        await pilot.pause()

        apply_button = app.screen.query_one("#preview-apply", Button)
        await pilot.click(apply_button)
        await _settle(app, pilot)

        assert client.paused_hashes == [["a" * 40]]
        assert isinstance(app.screen, ResultScreen)
        content = str(app.screen.query_one("#result-content", Static).content)
        # "Submitted", not "Applied": qBittorrent's bulk endpoints
        # confirm request acceptance, not a per-hash state transition
        # (documented accepted limitation).
        assert "Submitted" in content
        assert "submitted for 1 torrent(s)" in content


async def test_result_modal_reflects_no_changes_truthfully() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha", state="pausedDL")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.press("a")
        await pilot.pause()
        await pilot.click(app.screen.query_one("#actions-pause", Button))
        await pilot.pause()
        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await _settle(app, pilot)

        assert isinstance(app.screen, ResultScreen)
        content = str(app.screen.query_one("#result-content", Static).content)
        assert "No changes" in content
        assert client.paused_hashes == []


async def test_double_apply_invokes_the_mutation_only_once() -> None:
    client = BlockingMutationClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", state="downloading")
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.press("a")
        await pilot.pause()
        await pilot.click(app.screen.query_one("#actions-pause", Button))
        await pilot.pause()

        apply_button = app.screen.query_one("#preview-apply", Button)
        await pilot.click(apply_button)
        await pilot.pause()
        await asyncio_wait_for_event(client.entered)
        # A second press while Apply is already in flight must not
        # dispatch a second mutation -- the button is disabled.
        assert apply_button.disabled is True
        await pilot.click(apply_button)
        await pilot.pause()
        assert client.entry_count == 1

        client.release.set()
        await _settle(app, pilot)
        assert client.entry_count == 1
        assert client.paused_hashes == [["a" * 40]]


async def test_refresh_never_overlaps_an_in_flight_mutation() -> None:
    client = BlockingMutationClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", state="downloading")
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.press("a")
        await pilot.pause()
        await pilot.click(app.screen.query_one("#actions-pause", Button))
        await pilot.pause()
        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await pilot.pause()
        await asyncio_wait_for_event(client.entered)

        calls_before = client.torrents_info_calls
        app._start_periodic_refresh()
        await pilot.pause()

        assert client.torrents_info_calls == calls_before

        client.release.set()
        await _settle(app, pilot)


async def test_late_mutation_result_ignored_after_preview_closed() -> None:
    client = BlockingMutationClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", state="downloading")
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.press("a")
        await pilot.pause()
        await pilot.click(app.screen.query_one("#actions-pause", Button))
        await pilot.pause()
        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await pilot.pause()
        await asyncio_wait_for_event(client.entered)

        # The Apply button is disabled while applying -- Escape (which
        # would refuse anyway) is not needed to simulate "already
        # gone"; instead we directly pop the screen to model an already
        # -closed modal by the time the worker resolves.
        app.pop_screen()
        client.release.set()
        await _settle(app, pilot)

        assert len(app.screen_stack) == 1
        assert not isinstance(app.screen, ResultScreen)


async def test_workspace_switch_blocked_behind_preview_modal() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", state="downloading")
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.press("a")
        await pilot.pause()
        await pilot.click(app.screen.query_one("#actions-pause", Button))
        await pilot.pause()

        await pilot.press("g")
        await pilot.pause()

        assert isinstance(app.screen, PreviewScreen)
        assert app.controller.state.workspace is Workspace.TORRENTS
        await pilot.press("escape")
        await pilot.pause()


async def test_copy_and_explain_operate_on_focus_not_selection() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha"),
            make_torrent(hash="b" * 40, name="Beta"),
        ],
        trackers_by_hash={"a" * 40: [], "b" * 40: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        # Select Alpha, then move focus to Beta without selecting it.
        await pilot.press("space")
        await pilot.press("j")
        await pilot.pause()

        assert app.controller.state.selected_hashes == {"a" * 40}
        assert app.controller.state.focused_hash == "b" * 40

        await pilot.press("c")
        await pilot.pause()

        assert app._clipboard == "b" * 40


async def test_no_actions_binding_reachable_from_overview() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.pause()
        await _goto_overview(app, pilot)

        await pilot.press("a")
        await pilot.pause()

        assert len(app.screen_stack) == 1
        assert app.controller.state.workspace is Workspace.OVERVIEW


async def test_text_inputs_retain_normal_space_and_a_characters() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("slash")
        await pilot.pause()
        search = app.query_one("#search-input", Input)

        await pilot.press("a", "space", "a")
        await pilot.pause()

        assert search.value == "a a"
        assert app.controller.state.selected_hashes == set()


async def test_selection_and_actions_at_every_tested_width() -> None:
    for size in RESPONSIVE_SIZES:
        client = FakeQbitClient(
            torrents=[
                make_torrent(hash="a" * 40, name="Alpha", state="downloading")
            ]
        )
        app = _app(client)
        async with app.run_test(size=size) as pilot:
            await _settle(app, pilot)
            await _goto_torrents(app, pilot)
            await pilot.press("space")
            await pilot.pause()
            assert app.controller.state.selected_hashes == {"a" * 40}

            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, ActionsScreen)
            await pilot.click(app.screen.query_one("#actions-pause", Button))
            await pilot.pause()
            assert isinstance(app.screen, PreviewScreen)

            await pilot.click(app.screen.query_one("#preview-apply", Button))
            await _settle(app, pilot)
            assert isinstance(app.screen, ResultScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert len(app.screen_stack) == 1


async def test_no_tracker_secrets_in_preview_or_result() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=torrent_hash, name="Alpha", state="downloading")
        ],
        trackers_by_hash={
            torrent_hash: [
                {
                    "url": "https://tracker.example/announce/TOPSECRET",
                    "status": 2,
                }
            ]
        },
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.press("a")
        await pilot.pause()
        await pilot.click(app.screen.query_one("#actions-reannounce", Button))
        await pilot.pause()

        preview_content = str(
            app.screen.query_one("#preview-content", Static).content
        )
        assert "TOPSECRET" not in preview_content
        assert "https://" not in preview_content

        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await _settle(app, pilot)
        result_content = str(
            app.screen.query_one("#result-content", Static).content
        )
        assert "TOPSECRET" not in result_content
        assert "https://" not in result_content


# --- 15. Dogfooding follow-up fixes -----------------------------------------


async def test_enter_presses_the_focused_button_in_actions_and_preview() -> (
    None
):
    """`action_activate` (bound to `enter` with `priority=True`)
    intercepts `enter` before a focused `Button`'s own native
    enter-activates-click behavior ever runs -- it must therefore
    explicitly press the focused button on `ActionsScreen`/
    `PreviewScreen`/`ResultScreen` too, not only `FiltersScreen`."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", state="downloading")
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, ActionsScreen)
        assert app.focused is not None and app.focused.id == "actions-pause"

        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, PreviewScreen)
        assert app.focused is not None and app.focused.id == "preview-apply"

        await pilot.press("enter")
        await _settle(app, pilot)

        assert isinstance(app.screen, ResultScreen)
        assert client.paused_hashes == [["a" * 40]]

        await pilot.press("enter")
        await pilot.pause()
        assert len(app.screen_stack) == 1


async def test_up_down_navigate_actions_menu() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.press("a")
        await pilot.pause()

        assert app.focused is not None and app.focused.id == "actions-pause"
        await pilot.press("down")
        await pilot.pause()
        assert app.focused is not None and app.focused.id == "actions-resume"
        await pilot.press("down")
        await pilot.pause()
        assert (
            app.focused is not None and app.focused.id == "actions-reannounce"
        )
        await pilot.press("up")
        await pilot.pause()
        assert app.focused is not None and app.focused.id == "actions-resume"

        await pilot.press("escape")
        await pilot.pause()


async def test_up_down_navigate_filters_modal() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()

        category_input = app.screen.query_one("#f-categories", Input)
        assert category_input.has_focus

        await pilot.press("down")
        await pilot.pause()
        excluded_input = app.screen.query_one("#f-categories_excluded", Input)
        assert excluded_input.has_focus

        await pilot.press("up")
        await pilot.pause()
        assert category_input.has_focus

        await pilot.press("escape")
        await pilot.pause()


def _focused_id(app: QbitOpsTuiApp) -> str | None:
    return app.focused.id if app.focused is not None else None


async def test_down_from_the_last_actions_button_wraps_to_the_first() -> None:
    """A `down` at the last button used to land on `#actions-dialog`
    itself -- the scrollable container is still in the DOM and still
    focusable (short terminals need it to scroll), but nothing
    highlights there, which reads as a dead key rather than the
    wraparound it is meant to be."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.press("a")
        await pilot.pause()

        seen: list[str | None] = []
        for _ in range(8):
            await pilot.press("down")
            await pilot.pause()
            seen.append(_focused_id(app))

        assert "actions-dialog" not in seen, seen
        assert seen[-1] == "actions-pause", seen

        await pilot.press("up")
        await pilot.pause()
        assert _focused_id(app) == "actions-cancel"


async def test_down_from_the_last_filters_field_wraps_to_the_first() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()

        seen: list[str | None] = []
        for _ in range(10):
            await pilot.press("down")
            await pilot.pause()
            seen.append(_focused_id(app))

        assert "filters-dialog" not in seen, seen
        assert seen[-1] == "f-categories", seen

        await pilot.press("escape")
        await pilot.pause()


async def test_down_from_the_last_preview_button_wraps_to_the_first() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#actions-pause", Button).press()
        await pilot.pause()
        assert isinstance(app.screen, PreviewScreen)

        seen: list[str | None] = []
        for _ in range(4):
            await pilot.press("down")
            await pilot.pause()
            seen.append(_focused_id(app))

        assert "preview-dialog" not in seen, seen


async def test_down_from_the_last_value_modal_control_wraps_to_the_first() -> (
    None
):
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha", category="tv")],
        categories={"tv": {"name": "tv"}},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#actions-category-set", Button).press()
        await pilot.pause()
        assert isinstance(app.screen, CategorySetScreen)

        seen: list[str | None] = []
        for _ in range(4):
            await pilot.press("down")
            await pilot.pause()
            seen.append(_focused_id(app))

        assert "value-dialog" not in seen, seen


async def test_down_from_the_last_setup_control_wraps_to_the_first() -> None:
    client = FakeQbitClient(torrents=[])
    app = QbitOpsTuiApp(
        client_factory=lambda: client,
        host="http://localhost:8080",
        refresh_interval=LARGE_INTERVAL,
        needs_setup=True,
    )

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await pilot.pause()

        seen: list[str | None] = []
        for _ in range(5):
            await pilot.press("down")
            await pilot.pause()
            seen.append(_focused_id(app))

        assert "setup-dialog" not in seen, seen
        assert seen[-1] == "setup-host", seen


async def test_ctrl_d_deselects_all_visible_torrents() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha"),
            make_torrent(hash="b" * 40, name="Beta"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("ctrl+a")
        await pilot.pause()
        assert len(app.controller.state.selected_hashes) == 2

        await pilot.press("ctrl+d")
        await pilot.pause()

        assert app.controller.state.selected_hashes == set()


async def test_ctrl_d_clears_a_checkmark_a_direct_write_left_stale() -> None:
    """Same defect as
    `test_ctrl_r_clears_a_checkmark_a_direct_write_left_stale`, on
    `deselect_all` -- same code shape (`clear_selection()` then
    `_render_table()`), so it inherits the same stale-diff-cache risk.
    """
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await _type_into_search(pilot, "a")
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("space")
        await pilot.pause()
        assert app.controller.state.selected_hashes == {"a" * 40}

        await pilot.press("ctrl+d")
        await pilot.pause()

        assert app.controller.state.selected_hashes == set()
        table = app.query_one("#torrents", DataTable)
        cell = table.get_cell_at(Coordinate(0, 0))
        assert "✔" not in cell.plain, cell.plain


async def test_ctrl_d_with_no_selection_is_a_safe_noop() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("ctrl+d")
        await pilot.pause()

        assert app.controller.state.selected_hashes == set()


# --- Torrent workspace redesign: summary, indicator, state, progress -------


async def test_summary_puts_criteria_left_and_the_count_hard_right() -> None:
    """One row, not two, and a right edge that lines up with the rest of
    the page."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha"),
            make_torrent(hash="b" * 40, name="Beta"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.pause()

        summary = app.query_one("#filter-summary", FilterSummary)
        line = str(summary.content)

        assert "\n" not in line
        assert summary.size.height == 1
        assert line.startswith("No filters · Sorted by")
        assert line.rstrip().endswith("2 shown / 2 · 1 selected")
        assert cell_len(line) == summary.size.width


async def test_focus_and_selection_render_as_independent_glyphs() -> None:
    """Moving focus must never select a torrent, and a selected row
    must stay visibly checked once focus moves elsewhere -- the two
    concepts render as two distinct glyph slots in the same cell."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha"),
            make_torrent(hash="b" * 40, name="Beta"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("space")  # select row 0 (Alpha), still focused
        await pilot.pause()
        await pilot.press("j")  # move focus to row 1 (Beta); never selects
        await _settle(app, pilot)

        assert app.controller.state.selected_hashes == {"a" * 40}
        assert app.controller.state.focused_hash == "b" * 40

        table = app.query_one("#torrents", DataTable)
        alpha_cell = table.get_row_at(0)[0]
        beta_cell = table.get_row_at(1)[0]

        # Alpha: selected but no longer focused -- checked, no chevron.
        assert "✔" in str(alpha_cell)
        assert "›" not in str(alpha_cell)
        # Beta: focused but never selected -- chevron, no check.
        assert "›" in str(beta_cell)
        assert "✔" not in str(beta_cell)


async def test_raw_qbittorrent_state_is_not_the_primary_table_label() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha", state="stalledUP")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        table = app.query_one("#torrents", DataTable)
        state_cell = table.get_row_at(0)[table.get_column_index("State")]
        assert str(state_cell) == "Stalled"
        assert "stalledUP" not in str(state_cell)


# --- Category display contract ----------------------------------------------
#
# Black-box counterpart to `tests/test_selection_output_contract.py`:
# only rendered cells and panel text, never a domain object's field, so
# replacing `SelectedTorrent` with `TorrentSnapshot` (raw `""` category)
# cannot require editing these.


async def test_table_renders_the_uncategorized_label_not_an_empty_cell() -> (
    None
):
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha", category="")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        table = app.query_one("#torrents", DataTable)
        cell = table.get_row_at(0)[table.get_column_index("Category")]
        assert str(cell) == "(uncategorized)"


async def test_details_modal_renders_the_uncategorized_label() -> None:
    """The Details grid must not fall back to a second
    spelling (`(none)`) for a torrent qBittorrent reports with an empty
    category -- one vocabulary across table, details and `--category`.
    """
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha", category="")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        panel = await _open_details(app, pilot)

        text = _static_text(panel)
        assert "(uncategorized)" in text
        assert "(none)" not in text


async def test_sorting_by_category_orders_uncategorized_deterministically() -> (
    None
):
    """Locks the observed order, not a rationale: the label's leading
    `(` sorts before any letter, so the uncategorized torrent comes
    first. Ordering alone cannot distinguish sorting on the label from
    sorting on a raw `""` (both sort first) -- the rendered labels
    asserted alongside it are what makes this a contract.
    """
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", category="alpha"),
            make_torrent(hash="b" * 40, name="Blank", category=""),
            make_torrent(hash="c" * 40, name="Zulu", category="zeta"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("s")
        await pilot.pause()
        app.screen.query_one("#sort-category-asc", RadioButton).value = True
        await pilot.pause()
        await _settle(app, pilot)

        table = app.query_one("#torrents", DataTable)
        column = table.get_column_index("Category")
        assert [str(table.get_row_at(row)[column]) for row in range(3)] == [
            "(uncategorized)",
            "alpha",
            "zeta",
        ]


def test_progress_cells_render_representative_bar_and_percent_values() -> None:
    from qbit_ops.tui.formatting import _progress_cell

    assert _progress_cell(0.0, bar=False) == "0%"
    assert _progress_cell(1.0, bar=False) == "100%"
    # Clamped, never a domain-data change: out-of-range inputs still
    # produce an honest 0%/100% cell rather than a negative bar or an
    # overfull one.
    assert _progress_cell(-0.5, bar=False) == "0%"
    assert _progress_cell(1.5, bar=False) == "100%"

    zero_bar = _progress_cell(0.0, bar=True)
    full_bar = _progress_cell(1.0, bar=True)
    half_bar = _progress_cell(0.5, bar=True)
    assert zero_bar.endswith("0%")
    assert full_bar.endswith("100%")
    assert half_bar.endswith("50%")
    assert "#" not in zero_bar
    assert "-" not in full_bar
    # Half progress is a genuine mid-fill, not all-empty/all-full.
    assert "#" in half_bar and "-" in half_bar


def test_rate_cell_covers_download_upload_both_and_idle() -> None:
    from qbit_ops.tui.formatting import _format_rate_cell

    idle = _format_rate_cell(0, 0)
    down_only = _format_rate_cell(18 * 1024 * 1024, 0)
    up_only = _format_rate_cell(0, 2 * 1024 * 1024)
    both = _format_rate_cell(18 * 1024 * 1024, 2 * 1024 * 1024)

    assert idle == "—"  # ai-hygiene: allow-em-dash
    assert down_only == "↓ 18.0 MiB/s"
    assert "↑" not in down_only
    assert up_only == "↑ 2.0 MiB/s"
    assert "↓" not in up_only
    assert "↓ 18.0 MiB/s" in both and "↑ 2.0 MiB/s" in both


async def test_rate_column_renders_representative_torrents() -> None:
    """Rendered proof (not just the pure formatter) for a downloading-
    only, uploading-only, both-active, and idle torrent side by side."""
    hash_down, hash_up, hash_both, hash_idle = (
        "a" * 40,
        "b" * 40,
        "c" * 40,
        "d" * 40,
    )
    client = FakeQbitClient(
        torrents=[
            make_torrent(
                hash=hash_down,
                name="Downloading",
                dlspeed=18 * 1024 * 1024,
                upspeed=0,
            ),
            make_torrent(
                hash=hash_up,
                name="Uploading",
                dlspeed=0,
                upspeed=2 * 1024 * 1024,
            ),
            make_torrent(
                hash=hash_both,
                name="Both",
                dlspeed=18 * 1024 * 1024,
                upspeed=2 * 1024 * 1024,
            ),
            make_torrent(hash=hash_idle, name="Idle", dlspeed=0, upspeed=0),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        table = app.query_one("#torrents", DataTable)
        rate_col = table.get_column_index("Rate")
        down_cell = str(table.get_row(hash_down)[rate_col])
        up_cell = str(table.get_row(hash_up)[rate_col])
        both_cell = str(table.get_row(hash_both)[rate_col])
        idle_cell = str(table.get_row(hash_idle)[rate_col])

        assert "↓" in down_cell and "↑" not in down_cell
        assert "↑" in up_cell and "↓" not in up_cell
        assert "↓" in both_cell and "↑" in both_cell
        assert idle_cell == "—"  # ai-hygiene: allow-em-dash


async def test_dht_pex_lsd_show_disabled_exactly_once_each() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={
            torrent_hash: [
                {"url": "** [DHT] **", "status": 0, "msg": ""},
                {"url": "** [PeX] **", "status": 0, "msg": ""},
                {"url": "** [LSD] **", "status": 0, "msg": ""},
            ]
        },
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        from rich.text import Text as RichText

        details = await _open_details(app, pilot)
        # Strip Rich markup before matching -- `_format_details_tracker_line`
        # now wraps the health label in a colour tag, so the raw markup
        # string has tag characters between the padding and the word.
        rendered = RichText.from_markup(_static_text(details)).plain
        # No duplicated status text (e.g. "Disabled disabled") --
        # `_format_details_tracker_line` shows the health label exactly
        # once per mechanism, never a redundant `endpoint["enabled"]`
        # suffix.
        assert not re.search(r"Disabled\W+[Dd]isabled", rendered)
        for name in ("DHT", "PeX", "LSD"):
            assert re.search(rf"{name}\s+Disabled", rendered)


async def test_resize_preserves_selection_markers() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha"),
            make_torrent(hash="b" * 40, name="Beta"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.pause()
        assert app.controller.state.selected_hashes == {"a" * 40}

        await pilot.resize_terminal(*NARROW_SIZE)
        await pilot.pause()
        await pilot.resize_terminal(*MEDIUM_SIZE)
        await pilot.pause()

        assert app.controller.state.selected_hashes == {"a" * 40}
        table = app.query_one("#torrents", DataTable)
        assert "✔" in str(table.get_row_at(0)[0])


# --- Torrent explorer refinement: palette, header, local sorting -----------


async def test_datatable_cursor_never_uses_the_default_block_cursor() -> None:
    """Textual's own `DataTable` cursor fills with
    `$block-cursor-background`, which this theme resolves to the brand
    orange -- a full-width bar that would outshout the `›`/`✔` glyphs
    carrying the real focus/selection signal. Asserted on the *resolved*
    component style, not on the stylesheet's text."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        table = app.query_one("#torrents", DataTable)
        cursor = table.get_component_styles("datatable--cursor").background
        variables = app.get_css_variables()
        block_cursor = Color.parse(variables["block-cursor-background"])

        assert cursor.rgb != block_cursor.rgb
        assert cursor.rgb == Color.parse(variables["panel-lighten-3"]).rgb


def test_focus_and_selection_marks_use_brand_accent_not_default() -> None:
    """The focus chevron and selection check both use the same warm
    brand accent (derived from `BrandHeader`'s own gradient start),
    never Textual's default (blue) `$accent`."""
    from qbit_ops.tui.formatting import _BRAND_ACCENT, _GRADIENT_START

    assert _BRAND_ACCENT == "#{:02x}{:02x}{:02x}".format(*_GRADIENT_START)
    cell_focused_only = _indicator_cell(focused=True, selected=False)
    cell_selected_only = _indicator_cell(focused=False, selected=True)
    focus_style = next(iter(cell_focused_only.spans)).style
    select_style = next(iter(cell_selected_only.spans)).style
    assert _BRAND_ACCENT in str(focus_style)
    assert _BRAND_ACCENT in str(select_style)
    assert "$accent" not in str(focus_style)


async def test_active_sort_column_gets_a_header_arrow() -> None:
    client = FakeQbitClient(torrents=[make_torrent(name="Alpha")])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        table = app.query_one("#torrents", DataTable)
        # Default sort is Name ascending -- its header carries the arrow;
        # every other column's header stays plain.
        labels = {key: str(col.label) for key, col in table.columns.items()}
        name_header = next(v for k, v in labels.items() if k == "Name")
        assert "↑" in name_header
        state_header = next(v for k, v in labels.items() if k == "State")
        assert "↑" not in state_header and "↓" not in state_header


async def test_sort_screen_exposes_every_declared_sort_option() -> None:
    client = FakeQbitClient(torrents=[make_torrent(name="Alpha")])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, SortScreen)

        buttons = app.screen.query(RadioButton)
        assert len(buttons) == len(SortField) * 2
        ids = {button.id for button in buttons}
        for field in SortField:
            assert f"sort-{field.value}-asc" in ids
            assert f"sort-{field.value}-desc" in ids

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, SortScreen)


async def test_sorting_makes_zero_qbittorrent_api_calls() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Bravo", dlspeed=10),
            make_torrent(hash="b" * 40, name="Alpha", dlspeed=5),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        before = client.torrents_info_calls
        assert client.torrents_trackers_calls >= 0  # baseline, unused count

        await pilot.press("s")
        await pilot.pause()
        button = app.screen.query_one("#sort-state-desc", RadioButton)
        button.value = True
        await pilot.pause()
        await _settle(app, pilot)

        assert client.torrents_info_calls == before
        assert app.controller.state.sort.field == SortField.STATE
        assert app.controller.state.sort.direction == SortDirection.DESCENDING


def test_sort_torrents_tie_breaks_by_name_then_hash_deterministically() -> None:
    from qbit_core.shared.torrent_states import build_torrent_snapshot
    from qbit_ops.tui.state import _sort_torrents

    def _t(hash_: str, name: str, ratio: float) -> TorrentSnapshot:
        return build_torrent_snapshot(
            make_torrent(hash=hash_, name=name, ratio=ratio)
        )

    torrents = (
        _t("b" * 40, "Same", 2.0),
        _t("a" * 40, "Same", 2.0),
        _t("c" * 40, "Zeta", 1.0),
    )

    ascending = _sort_torrents(
        torrents,
        SortOrder(field=SortField.RATIO, direction=SortDirection.ASCENDING),
    )
    assert [t.hash for t in ascending] == ["c" * 40, "a" * 40, "b" * 40]

    descending = _sort_torrents(
        torrents,
        SortOrder(field=SortField.RATIO, direction=SortDirection.DESCENDING),
    )
    # Tie-break (name, hash) stays ascending regardless of the primary
    # direction -- only the Ratio grouping itself reverses.
    assert [t.hash for t in descending] == ["a" * 40, "b" * 40, "c" * 40]


async def test_active_sort_survives_refresh_filter_search_and_resize() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", category="films"),
            make_torrent(hash="b" * 40, name="Beta", category="tv"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        app.apply_sort(
            SortOrder(field=SortField.RATIO, direction=SortDirection.DESCENDING)
        )
        await pilot.pause()
        assert app.controller.state.sort.field == SortField.RATIO

        # Periodic refresh.
        app._start_periodic_refresh()
        await _settle(app, pilot)
        assert app.controller.state.sort.field == SortField.RATIO

        # Search.
        await _type_into_search(pilot, "al")
        await pilot.press("escape")
        await pilot.pause()
        assert app.controller.state.sort.field == SortField.RATIO

        # Filter.
        await pilot.press("f")
        await pilot.pause()
        category_input = app.screen.query_one("#f-categories", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.press("enter")
        await pilot.pause()
        assert app.controller.state.sort.field == SortField.RATIO

        # Resize.
        await pilot.resize_terminal(*NARROW_SIZE)
        await pilot.pause()
        await pilot.resize_terminal(*WIDE_SIZE)
        await pilot.pause()
        assert app.controller.state.sort.field == SortField.RATIO

        # Workspace switch.
        await pilot.press("g")
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        assert app.controller.state.sort.field == SortField.RATIO


async def test_focus_and_selection_survive_sorting_when_still_visible() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Bravo", ratio=1.0),
            make_torrent(hash="b" * 40, name="Alpha", ratio=2.0),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        # Default sort is Name ascending -- "Alpha" (hash b) sorts
        # first, so it's already focused by the initial cursor.
        await pilot.press("space")  # select it
        await pilot.pause()
        focused_hash = app.controller.state.focused_hash
        assert focused_hash == "b" * 40
        assert app.controller.state.selected_hashes == {"b" * 40}

        app.apply_sort(
            SortOrder(field=SortField.RATIO, direction=SortDirection.DESCENDING)
        )
        await pilot.pause()

        assert app.controller.state.focused_hash == focused_hash
        assert app.controller.state.selected_hashes == {"b" * 40}


def test_wrap_name_at_separators_never_truncates() -> None:
    from qbit_ops.tui.formatting import _wrap_name_at_separators

    short = _wrap_name_at_separators("Alpha", 40)
    assert short == "Alpha"

    long_name = (
        "Some.Extremely.Long.Release.Name.With.No.Spaces.At.All.2024."
        "MULTi.1080p.BluRay.x265-EXAMPLE"
    )
    wrapped = _wrap_name_at_separators(long_name, 24)
    # Never truncated: every character survives, just reflowed.
    assert wrapped.replace("\n", "") == long_name
    assert "…" not in wrapped
    # Every wrapped line breaks right after a separator, never mid-run.
    for line in wrapped.split("\n")[:-1]:
        assert line[-1] in " .-_[]"


async def test_details_modal_shows_full_name_wrapped_never_truncated() -> None:
    long_name = (
        "Some.Extremely.Long.Release.Name.With.No.Spaces.At.All.2024."
        "MULTi.1080p.BluRay.x265-EXAMPLE"
    )
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name=long_name)]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        details = await _open_details(app, pilot)
        # The identity section alone -- unlike the panel as a whole,
        # never contains "…": the Hash line further down legitimately
        # shortens the 40-char hash with one (`_shorten_hash`).
        identity = str(details.query_one("#details-identity", Static).content)
        assert "…" not in identity
        assert long_name in identity.replace("\n", "")


async def test_details_dialog_is_wide_relative_to_the_app() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=(200, 40)) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _open_details(app, pilot)

        dialog = app.screen.query_one("#details-dialog")
        # Wide (per the brief), capped, never wider than the app itself.
        assert dialog.size.width >= 60
        assert dialog.size.width <= 100
        assert dialog.size.width < app.size.width


async def test_tracker_health_renders_semantic_glyphs() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={
            torrent_hash: [
                {"url": "https://tracker.example/announce", "status": 2},
            ]
        },
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        details = await _open_details(app, pilot)
        rendered = _static_text(details)
        assert "Healthy" in rendered
        assert "●" in rendered


async def test_long_name_truncates_and_never_wraps_a_second_table_row() -> None:
    """A release name far wider than any realistic column must still
    render as exactly one table row -- truncated with an ellipsis, not
    wrapped onto a second physical row, and never wide enough to force
    horizontal overflow of the table itself."""
    long_name = "A." * 120  # 240 chars, no whitespace at all
    for size in RESPONSIVE_SIZES:
        client = FakeQbitClient(
            torrents=[make_torrent(hash="a" * 40, name=long_name)]
        )
        app = _app(client)
        async with app.run_test(size=size) as pilot:
            await _settle(app, pilot)
            await _goto_torrents(app, pilot)

            table = app.query_one("#torrents", DataTable)
            row_key = next(iter(table.rows))
            assert table.get_row_height(row_key) == 1

            name_cell = str(table.get_row_at(0)[table.get_column_index("Name")])
            assert name_cell.endswith("…")
            assert long_name.startswith(name_cell[:-1])

            total_render_width = sum(
                column.get_render_width(table)
                for column in table.columns.values()
            )
            assert total_render_width <= table.size.width


def test_name_column_width_never_overflows_at_the_wide_tiers_own_edge() -> None:
    """A synthetic worst case (every optional column visible, plus a
    single unbreakable 200+ character name) at the Wide tier's own
    narrowest edge must still fit: `_name_column_width` prefers a
    narrower-than-target `Name` column over any horizontal overflow --
    see `test_long_name_truncates_and_never_wraps_a_second_table_row`
    for the real-render, end-to-end proof this unit-level check
    mirrors."""
    from qbit_ops.tui.formatting import (
        _COLUMN_WIDTHS,
        _TORRENTS_BORDER_COLS,
        NARROW_WIDTH_THRESHOLD,
        _columns_for_width,
        _content_width,
        _name_column_width,
        _progress_column_width,
    )

    for total_width in (80, 99, 100, 101, 129, 130, 131, 140, 150, 200, 400):
        bar = total_width >= NARROW_WIDTH_THRESHOLD
        # The real column set for this width -- not an unconditional
        # "every column" set, which `_columns_for_width` itself would
        # never actually select at a narrower width.
        other = tuple(
            c
            for c in _columns_for_width(total_width)
            if c not in ("Sel", "Name")
        )
        name_width = _name_column_width(total_width, other, bar=bar)
        reserved = sum(
            (
                _progress_column_width(bar=bar)
                if name == "Progress"
                else _COLUMN_WIDTHS.get(name, 0)
            )
            + 2
            for name in other
        )
        table_width = _content_width(total_width) - _TORRENTS_BORDER_COLS
        total_render = reserved + name_width + 2
        assert total_render <= table_width, (
            total_width,
            name_width,
            total_render,
            table_width,
        )
        assert name_width >= 1


# --- Unified application chrome ---------------------------------------------


async def test_app_frame_shows_the_runtime_version_as_a_floating_title() -> (
    None
):
    """The outer AppFrame's border title reads `qbit-ops v{version}`,
    sourced from `qbit_ops.__version__` -- never a second resolver."""
    import qbit_ops

    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        expected = f"qbit-ops v{qbit_ops.__version__}"
        assert str(app.screen.border_title) == expected


def test_global_rate_hue_names_the_direction_not_the_activity() -> None:
    """The hue says *which way* traffic is moving; having a hue at all
    says it is moving. Download and upload must therefore never share a
    colour while both are active -- which is what would happen if the
    hue still encoded activity."""
    from qbit_ops.tui.formatting import (
        DOWN_RATE_ACCENT,
        IDLE_RATE_STYLE,
        UP_RATE_ACCENT,
        _format_global_rate,
    )

    assert DOWN_RATE_ACCENT != UP_RATE_ACCENT

    idle = _format_global_rate(0, 0)
    assert idle.spans[0].style == IDLE_RATE_STYLE
    assert idle.spans[-1].style == IDLE_RATE_STYLE

    down_only = _format_global_rate(2_500_000, 0)
    assert down_only.spans[0].style == DOWN_RATE_ACCENT
    assert down_only.spans[-1].style == IDLE_RATE_STYLE

    up_only = _format_global_rate(0, 2_500_000)
    assert up_only.spans[0].style == IDLE_RATE_STYLE
    assert up_only.spans[-1].style == UP_RATE_ACCENT

    both = _format_global_rate(2_500_000, 2_500_000)
    assert both.spans[0].style == DOWN_RATE_ACCENT
    assert both.spans[-1].style == UP_RATE_ACCENT


def test_details_trackers_section_distinguishes_loading_from_failed() -> None:
    """A failed fetch must never render identically to "still loading" --
    otherwise the Details modal reads as permanently stuck with no hint
    that a retry (`r`) would help."""
    from qbit_ops.tui.formatting import _format_details_trackers

    loading = _format_details_trackers(None, None, fetch_failed=False)
    assert "Loading" in loading

    failed = _format_details_trackers(None, None, fetch_failed=True)
    assert "Loading" not in failed
    assert "retry" in failed.lower()
    assert failed != loading


def test_details_identity_keeps_a_bracketed_name_literal() -> None:
    """A torrent name is third-party text (whoever built the `.torrent`
    controls it) interpolated into a markup string the Details modal
    later hands to `Static.update()`. An unescaped `[/]` is a
    `textual.markup.MarkupError` there, not just a Rich CLI concern."""
    from qbit_core.shared.torrent_states import build_torrent_snapshot
    from qbit_ops.tui.formatting import _format_details_identity

    torrent = build_torrent_snapshot(make_torrent(name="Movie [/] x264"))

    rendered = _format_details_identity(torrent, name_width=40)

    assert "Movie [/] x264" in Content.from_markup(rendered).plain


def test_explain_header_keeps_a_bracketed_torrent_name_literal() -> None:
    from qbit_ops.tui.formatting import _format_explain_text
    from qbit_ops.tui.state import TuiState

    rendered = _format_explain_text("Movie [/] x264", None, TuiState())

    assert "Movie [/] x264" in Content.from_markup(rendered).plain


def test_details_tracker_message_is_escaped_before_rendering() -> None:
    """`message` is qBittorrent's own tracker `msg` field, echoed
    verbatim from a tracker's announce response --
    `sanitize_tracker_text` strips URLs/userinfo from it, not markup, so
    a malicious tracker can otherwise inject Textual markup into the
    Details modal."""
    from qbit_ops.tui.formatting import _format_details_trackers

    rendered = _format_details_trackers(
        [
            {
                "tracker": "tracker.example",
                "health": "critical",
                "enabled": True,
                "message": "not registered [/] torrent",
            }
        ],
        None,
    )

    assert "not registered [/] torrent" in Content.from_markup(rendered).plain


def test_explain_evidence_keeps_a_bracketed_torrent_name_literal() -> None:
    """The `"name"` evidence code carries the torrent's own name (see
    `qbit_core.features.explain._build_torrent_finding`), the same
    third-party text as everywhere else it is rendered."""
    from qbit_core.features.explain import (
        Evidence,
        ExplanationFinding,
        ExplanationSeverity,
    )
    from qbit_ops.tui.formatting import _format_finding

    finding = ExplanationFinding(
        code="TEST",
        severity=ExplanationSeverity.INFO,
        title="Test finding",
        explanation="Irrelevant to this test.",
        evidence=(Evidence("name", "Name", "Movie [/] x264", "torrents_info"),),
    )

    rendered = _format_finding(finding)

    assert "Movie [/] x264" in Content.from_markup(rendered).plain


async def test_top_right_global_rate_display_shows_live_status_rates() -> None:
    from qbit_ops.tui.formatting import IDLE_RATE_STYLE, UP_RATE_ACCENT

    client = FakeQbitClient(
        torrents=[make_torrent()], download_speed=0, upload_speed=2_500_000
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)

        rate = app.query_one("#global-rate", GlobalRateDisplay)
        content = rate.content
        assert "↓" in str(content) and "↑" in str(content)
        assert "2.4 MiB/s" in str(content)
        styles = {span.style for span in cast(Text, content).spans}
        # Idle download (no hue), seeding upload (brand orange) -- both
        # present, never one colour for the whole indicator.
        assert IDLE_RATE_STYLE in styles
        assert UP_RATE_ACCENT in styles


async def test_app_frame_is_removed_at_narrow_width() -> None:
    """ "Simplify or remove decorative outer framing" at narrow widths
    -- every column is worth more than a floating title there."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=NARROW_SIZE) as pilot:
        await _settle(app, pilot)
        assert "narrow" in app.screen.classes


async def test_titled_regions_expose_the_expected_border_titles() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")],
        trackers_by_hash={"a" * 40: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        table = app.query_one("#torrents", DataTable)
        assert str(table.border_title).startswith("Torrents ·")

        footer_row = app.query_one("#footer-row")
        height_before_search = footer_row.size.height

        await pilot.press("slash")
        await pilot.pause()
        # No bordered/titled box, no separate row above the footer --
        # search mounts into the existing `#footer-row` alongside
        # `CommandBar` (whose own `border-top` already accounts for the
        # row's full outer height), so opening search must never grow
        # it (see `test_search_footer_replaces_search_token_in_same_row`).
        assert footer_row.size.height == height_before_search
        await pilot.press("escape")
        await pilot.pause()

        # Precise per-dialog title values are checked by
        # `test_modal_dialogs_expose_expected_border_titles` below --
        # this just proves each modal still opens/closes correctly.
        for key, screen_type in (
            ("f", FiltersScreen),
            ("s", SortScreen),
            ("question_mark", HelpScreen),
        ):
            await pilot.press(key)
            await pilot.pause()
            assert isinstance(app.screen, screen_type)
            await pilot.press("escape")
            await pilot.pause()


async def test_modal_dialogs_expose_expected_border_titles() -> None:
    # `App.query_one` only ever searches `App.default_screen`, never the
    # currently active top-of-stack screen -- `app.screen.query_one`
    # is required once a modal has been pushed.
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=torrent_hash, name="Alpha", category="films")
        ],
        trackers_by_hash={torrent_hash: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("f")
        await pilot.pause()
        # Not a plain title: `border_title` is the tab strip itself
        # (see `qbit_ops.tui.tab_bar`), which always starts with the
        # dialog name at the widest ladder rung.
        assert str(
            app.screen.query_one("#filters-dialog").border_title
        ).startswith("Filters ")
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("s")
        await pilot.pause()
        assert str(app.screen.query_one("#sort-dialog").border_title) == "Sort"
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("space")
        await pilot.press("a")
        await pilot.pause()
        assert (
            str(app.screen.query_one("#actions-dialog").border_title)
            == "Actions"
        )
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("question_mark")
        await pilot.pause()
        assert str(app.screen.query_one("#help-dialog").border_title) == "Help"
        await pilot.press("escape")
        await pilot.pause()

        details = await _open_details(app, pilot)
        assert (
            str(app.screen.query_one("#details-dialog").border_title)
            == "Torrent details"
        )
        assert details is not None
        await pilot.press("escape")
        await pilot.pause()


def test_every_modal_is_built_from_the_shared_frame() -> None:
    """A modal that skipped `QbitModal` would be free to re-decide its
    width, border and title -- exactly the divergence the base class
    exists to make impossible."""
    for screen_class in _ALL_MODALS:
        assert issubclass(screen_class, QbitModal), screen_class
        assert screen_class.MODAL_WIDTH in MODAL_WIDTHS, screen_class
        assert screen_class.MODAL_TITLE, screen_class


def test_a_modal_cannot_declare_a_width_outside_the_scale() -> None:
    """The scale is the guarantee: a tenth modal picks a word, and a
    word outside the scale must not reach a running app."""
    with pytest.raises(ValueError, match="MODAL_WIDTH"):

        class _Rogue(QbitModal):
            MODAL_TITLE = "Rogue"
            DIALOG_ID = "rogue-dialog"
            MODAL_WIDTH = "enormous"


async def test_the_shared_dialog_border_carries_the_brand_accent() -> None:
    """One frame, one accent: the dialog's border and its border title
    both resolve to the theme's `$primary`, never Textual's own."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()

        dialog = app.screen.query_one("#filters-dialog")
        accent = Color.parse(_BRAND_ACCENT)

        assert dialog.styles.border_top == ("round", accent)
        assert dialog.styles.border_title_color == accent


async def test_command_bar_keys_use_the_brand_accent() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        bar = app.query_one("#command-bar", CommandBar)
        content = str(bar.content)
        assert "[q→Quit]" in Text.from_markup(content).plain
        assert _BRAND_ACCENT in content


async def test_command_bar_reflects_active_bindings_only() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        overview_plain = Text.from_markup(
            str(app.query_one("#command-bar", CommandBar).content)
        ).plain
        assert "Explain" not in overview_plain
        assert "Actions" not in overview_plain

        await _goto_torrents(app, pilot)
        torrents_plain = Text.from_markup(
            str(app.query_one("#command-bar", CommandBar).content)
        ).plain
        assert "f→Filters" in torrents_plain
        # "Explain"/"Copy"/"Refresh" are `show=False` bindings (see
        # `QbitOpsTuiApp.BINDINGS`) -- reachable by key/Help/Details,
        # deliberately never in the global command bar. "Actions" only
        # shows once something is actually selected.
        assert "e→Explain" not in torrents_plain
        assert "a→Actions" not in torrents_plain

        await pilot.press("space")
        await pilot.pause()
        selected_plain = Text.from_markup(
            str(app.query_one("#command-bar", CommandBar).content)
        ).plain
        assert "a→Actions" in selected_plain


async def test_search_focus_and_typing_performs_zero_qbittorrent_calls() -> (
    None
):
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha ISO"),
            make_torrent(hash="b" * 40, name="Beta ISO"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        before = client.torrents_info_calls
        await _type_into_search(pilot, "alpha")
        assert _visible_names(app) == ["Alpha ISO"]
        assert client.torrents_info_calls == before

        await pilot.press("escape")
        await pilot.pause()
        assert not app.query("#search-input")


async def test_torrent_row_no_longer_uses_default_blue_cursor() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha"),
            make_torrent(hash="b" * 40, name="Beta"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("j")
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()

        table = app.query_one("#torrents", DataTable)
        alpha_cell = str(table.get_row_at(0)[0])
        beta_cell = str(table.get_row_at(1)[0])
        # "j" moves the cursor from row 0 (Alpha) to row 1 (Beta) before
        # "space" toggles selection there -- Beta ends up both focused
        # and selected, Alpha neither.
        assert "✔" not in alpha_cell and "›" not in alpha_cell
        assert "›" in beta_cell and "✔" in beta_cell


# --- 9. Visual-polish pass: uniform background, tabs, search, toast ------


async def test_workspace_tabs_show_both_pages_in_distinct_colours() -> None:
    """The active page renders in the brand orange, the inactive one in
    the distinct blue reserved for it -- both always visible, never a
    dim/muted inactive label (see `WorkspaceTabs._tab_label`)."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        tabs = app.query_one("#workspace-tabs", WorkspaceTabs)
        content = str(tabs.content)
        assert "Overview" in content and "Torrents" in content
        assert _BRAND_ACCENT in content
        assert _INACTIVE_TAB_ACCENT in content
        # Overview is active on mount.
        overview_index = content.index("Overview")
        torrents_index = content.index("Torrents")
        accent_index = content.index(_BRAND_ACCENT)
        inactive_index = content.index(_INACTIVE_TAB_ACCENT)
        assert accent_index < overview_index
        assert inactive_index < torrents_index

        await _goto_torrents(app, pilot)
        content = str(app.query_one("#workspace-tabs", WorkspaceTabs).content)
        # Torrents is now active: the brand accent markup now precedes
        # "Torrents" rather than "Overview".
        assert content.index(_BRAND_ACCENT) < content.index("Torrents")
        assert content.index(_INACTIVE_TAB_ACCENT) < content.index("Overview")


async def test_the_top_and_bottom_strips_carry_no_panel_background() -> None:
    """`#workspace-tabs` and `#command-bar` must not fill with `$panel`
    -- that was the empty grey seam above the black background. A fully
    transparent alpha means they show whatever is behind them rather
    than a distinct fill."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        for selector in ("#workspace-tabs", "#command-bar"):
            assert app.query_one(selector).styles.background.a == 0, selector


async def test_search_footer_replaces_search_token_in_same_row() -> None:
    """No separate `search:`/Total row above the footer: the
    `[/→Search]` token inside `CommandBar` itself is replaced in place
    by the pipe-delimited `|search: xxx|` token, restored once search
    closes -- the right-aligned `|Total: y|` token lives in the
    separate `FooterTotal` sibling instead."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Ubuntu ISO"),
            make_torrent(hash="b" * 40, name="Debian"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        bar = app.query_one("#command-bar", CommandBar)
        footer_total = app.query_one("#footer-total", FooterTotal)
        inactive_content = str(bar.content)
        assert "→Search]" in inactive_content
        assert "search:" not in inactive_content
        assert str(footer_total.content) == ""

        await _type_into_search(pilot, "ubu")

        search_input = app.query_one("#search-input", Input)
        assert search_input.value == "ubu"
        # No separate row was mounted -- the footer stays one line.
        assert not app.query("#search-region")
        assert not app.query("#search-label")
        assert not app.query("#search-total")

        active_content = str(bar.content)
        assert "→Search]" not in active_content
        assert "|search: ubu|" in Text.from_markup(active_content).plain
        assert "Total: 2" not in Text.from_markup(active_content).plain
        total_content = str(footer_total.content)
        assert "|Total: 2|" in Text.from_markup(total_content).plain

        await pilot.press("escape")
        await pilot.pause()
        restored_content = str(bar.content)
        assert "→Search]" in restored_content
        assert "search:" not in restored_content
        assert str(footer_total.content) == ""
        # Escape only closes the input -- the search term itself, and
        # the filtering it drives, are untouched (see
        # `action_dismiss_overlay`'s docstring).
        assert app.controller.state.search == "ubu"
        assert _visible_names(app) == ["Ubuntu ISO"]


async def test_search_matching_is_unaffected_by_the_footer_rework() -> None:
    """Presentation-only change -- the actual
    match/filter behaviour driving `_apply_search` is untouched."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha ISO"),
            make_torrent(hash="b" * 40, name="Beta ISO"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        before = client.torrents_info_calls
        await _type_into_search(pilot, "alpha")
        assert _visible_names(app) == ["Alpha ISO"]
        assert client.torrents_info_calls == before


async def test_search_input_torn_down_when_leaving_torrents_workspace() -> None:
    """A live search `Input` lives in the always-mounted
    `#footer-row`, not inside `#torrents-workspace`, so it must be torn
    down explicitly on workspace switch or it would keep eating
    keystrokes meant for Overview navigation."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _type_into_search(pilot, "al")

        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("g")
        await pilot.pause()

        assert app.controller.state.workspace is Workspace.OVERVIEW
        assert not app.query("#search-input")
        bar = app.query_one("#command-bar", CommandBar)
        assert "search:" not in str(bar.content)


async def test_matching_characters_are_highlighted_orange_while_searching() -> (
    None
):
    client = FakeQbitClient(torrents=[make_torrent(name="Ubuntu 24.04 ISO")])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _type_into_search(pilot, "ubu")

        table = app.query_one("#torrents", DataTable)
        name_cell = table.get_cell_at(Coordinate(0, 1))
        assert name_cell.plain.lower().startswith("ubu")
        spans = [s for s in name_cell.spans if _BRAND_ACCENT in s.style]
        assert spans, name_cell.spans
        assert spans[0].start == 0 and spans[0].end == 3


async def test_search_result_notification_toast_uses_brand_styling() -> None:
    """The default `-information` toast reads as brand orange, not
    Textual's default green. Asserted on the *parsed* stylesheet: the
    sheet documents its own rules in comments, and a text search would
    pass on one of them."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        accent = Color.parse(_BRAND_ACCENT)

        assert _rule_styles(app, ".-information")["border_left"] == (
            "outer",
            accent,
        )
        assert _rule_styles(app, ".toast--title")["color"] == accent


async def test_filters_and_sort_modals_still_expose_all_options() -> None:
    """Restyling never drops a filter field or a sort option. Covers
    every control in all three modals (requirement 7 of the follow-up
    visual-polish brief), not just one field/one modal."""
    from textual.widgets import Checkbox, RadioSet

    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha", category="films")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("f")
        await pilot.pause()
        assert app.screen.query_one("#f-categories", Input) is not None
        assert app.screen.query_one("#f-states", Input) is not None
        assert app.screen.query_one("#f-stalled", Checkbox) is not None
        assert app.screen.query_one("#f-errored", Checkbox) is not None
        assert app.screen.query_one("#f-completed", RadioSet) is not None
        assert app.screen.query_one("#f-active", RadioSet) is not None
        assert app.screen.query_one("#f-private", RadioSet) is not None
        # Buttons are gone: Apply/Clear/Cancel are the bottom-border
        # gestures now (see `FiltersScreen`'s class docstring).
        assert not app.screen.query(Button)
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("s")
        await pilot.pause()
        radios = app.screen.query(RadioButton)
        assert len(radios) == 14
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("space")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        for button_id in (
            "actions-pause",
            "actions-resume",
            "actions-reannounce",
            "actions-cancel",
        ):
            assert app.screen.query_one(f"#{button_id}", Button) is not None
        await pilot.press("escape")
        await pilot.pause()


# --- 10. Follow-up quick visual-polish pass ------------------------------


async def test_footer_never_shows_the_workspace_nav_hint_at_all() -> None:
    """Neither `g→Overview` nor `t→Torrents` is ever rendered into the
    footer, in either workspace -- the top workspace-tabs strip is now
    the sole visible way to advertise switching pages (see
    `test_footer_never_shows_workspace_nav_hints_in_either_workspace`
    for the same fact asserted against `Screen.active_bindings`)."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        bar = app.query_one("#command-bar", CommandBar)

        overview_plain = Text.from_markup(str(bar.content)).plain
        assert "t→Torrents" not in overview_plain
        assert "g→Overview" not in overview_plain

        await _goto_torrents(app, pilot)
        torrents_plain = Text.from_markup(str(bar.content)).plain
        assert "g→Overview" not in torrents_plain
        assert "t→Torrents" not in torrents_plain


async def test_torrents_table_background_matches_app_uniform_background() -> (
    None
):
    """`#torrents`' computed background must equal the
    Screen's, in every focus state -- not merely unset in CSS source.
    `DataTable`'s own `DEFAULT_CSS` applies a `background-tint` while
    focused (the Torrents workspace's default focus target), which
    alone would still composite a visibly different background even
    after `background: transparent`, so both are asserted."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        table = app.query_one("#torrents", DataTable)
        table.focus()
        await pilot.pause()

        assert table.styles.background.a == 0
        assert table.background_colors == app.screen.background_colors


async def test_modal_dialog_background_matches_the_app_uniform_background() -> (
    None
):
    """`$surface` (a measurably lighter grey, e.g. #1e1e1e) as a dialog
    fill against the app's own uniform `$background` (e.g. #121212)
    would break the round border's floating-outline look -- checked on
    the actual computed/composited background, not CSS source text.
    Also covers each dialog's *children* (`RadioSet`, `Input`,
    default-variant `Button`): those widgets' own `DEFAULT_CSS`
    independently fills with `$surface`, so fixing only the outer
    dialog would leave the same grey-box clash one level in."""
    from textual.widgets import RadioSet

    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha", category="films")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        screen_bg = app.screen.background_colors

        await pilot.press("f")
        await pilot.pause()
        dialog = app.screen.query_one("#filters-dialog")
        assert dialog.background_colors == screen_bg
        assert (
            app.screen.query_one("#f-categories", Input).background_colors
            == screen_bg
        )
        assert (
            app.screen.query_one("#f-completed", RadioSet).background_colors
            == screen_bg
        )
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("s")
        await pilot.pause()
        dialog = app.screen.query_one("#sort-dialog")
        assert dialog.background_colors == screen_bg
        assert app.screen.query_one(RadioSet).background_colors == screen_bg
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("space")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        dialog = app.screen.query_one("#actions-dialog")
        assert dialog.background_colors == screen_bg
        assert (
            app.screen.query_one("#actions-cancel", Button).background_colors
            == screen_bg
        )


async def test_modal_focus_indicators_use_brand_accent_not_default_blue() -> (
    None
):
    """Checks the computed style, not the CSS source: Textual's own
    default focus/selection chrome draws from a saturated blue, so this
    proves `.qbit-dialog` actually overrides it to the brand orange at
    render time. Also checks the foreground on that fill -- the app's
    near-white `$text` would only reach ~2:1 contrast against the
    orange, barely better than the blue it replaces; the app's dark
    `$background` tone reused as foreground gives ~9:1."""
    from textual.widgets import Checkbox, RadioSet

    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha", category="films")]
    )
    app = _app(client)
    orange = _GRADIENT_START
    dark = (18, 18, 18)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("f")
        await pilot.pause()
        # No buttons in this modal any more (see `FiltersScreen`'s
        # class docstring) -- Input/Checkbox/RadioSet focus below cover
        # the same claim for the widgets it still has.
        category_input = app.screen.query_one("#f-categories", Input)
        category_input.focus()
        await pilot.pause()
        # `border-left`, not `border` (all four edges): a full top+bottom
        # border on a `height: 1` field leaves no row for its own
        # content -- see `qbit_ops.tcss`'s `Input:focus` comment.
        assert category_input.styles.border.left[1].rgb == orange

        # Stalled/Errored live on the State pane -- switch to it first,
        # or the checkbox is focused while `display: none`.
        await pilot.press("alt+right")
        await pilot.pause()
        checkbox = app.screen.query_one("#f-stalled", Checkbox)
        checkbox.focus()
        await pilot.pause()
        assert checkbox.styles.border.left[1].rgb == orange
        # A Checkbox's border-left is its *only* focus signal (see
        # `qbit_ops.tcss`): its label is deliberately left unpainted, so
        # a background fill there would compete with it.
        checkbox_label = checkbox.get_component_styles("toggle--label")
        assert checkbox_label.background.rgb != orange

        completion = app.screen.query_one("#f-completed", RadioSet)
        completion.focus()
        await pilot.pause()
        selected = completion.query(RadioButton).first()
        selected_label = selected.get_component_styles("toggle--label")
        assert selected_label.background.rgb == orange
        assert selected_label.color.rgb == dark
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("s")
        await pilot.pause()
        sort_set = app.screen.query_one(RadioSet)
        sort_set.focus()
        await pilot.pause()
        sort_selected = sort_set.query(RadioButton).first()
        sort_label = sort_selected.get_component_styles("toggle--label")
        assert sort_label.background.rgb == orange
        assert sort_label.color.rgb == dark
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("space")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        pause_button = app.screen.query_one("#actions-pause", Button)
        pause_button.focus()
        await pilot.pause()
        assert pause_button.styles.color.rgb == orange


async def test_a_focused_height_one_field_keeps_its_own_row_and_its_text() -> (
    None
):
    """`border: tall` on a `height: 1` `Input`/`Checkbox` has no row
    to spare for the border it draws, so Textual grows the widget's own
    region past its declared height while focused and the one row that
    remains renders the border's fill glyph, never the typed text. A
    colour-only assertion cannot catch this (`border.left` alone stays
    green), so this checks the region's height and the text surviving
    into the exported render instead."""
    from textual.widgets import Checkbox

    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha", category="films")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("f")
        await pilot.pause()
        category_input = app.screen.query_one("#f-categories", Input)
        category_input.focus()
        category_input.value = "films"
        await pilot.pause()
        assert category_input.region.height == 1
        assert "films" in app.export_screenshot()

        await pilot.press("alt+right")
        await pilot.pause()
        checkbox = app.screen.query_one("#f-stalled", Checkbox)
        checkbox.focus()
        await pilot.pause()
        assert checkbox.region.height == 1
        assert "Stalled" in app.export_screenshot()


# --- The style system, seen from a running app ----------------------------


def test_the_stylesheet_is_loaded_from_a_file_next_to_the_app() -> None:
    """`CSS_PATH` is what makes one sheet possible; a class-level `CSS`
    block on the App would quietly reopen the nine-blocks door."""
    sheet = Path(qbit_ops.tui.app.__file__).parent / str(QbitOpsTuiApp.CSS_PATH)

    assert sheet.is_file()
    assert not QbitOpsTuiApp.__dict__.get("CSS")


def test_formatting_names_no_brand_colour_of_its_own() -> None:
    """`formatting.py` reads the theme, it does not define
    it. Scanned as *code* (string constants only), so a hex mentioned
    in a comment neither passes nor fails this."""
    source = Path(qbit_ops.tui.formatting.__file__).read_text(encoding="utf-8")
    literals = [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    hexes = [
        value for value in literals if re.fullmatch(r"#[0-9a-fA-F]{6}", value)
    ]

    assert not hexes, hexes
    # Non-vacuous: the module *does* carry string constants to scan.
    assert len(literals) > 50


def test_the_theme_makes_primary_the_brand_orange() -> None:
    """The reversal this system is built on: Textual's `$primary` was
    rejected as too saturated for this palette, so the palette now sets
    what `$primary` is instead of arguing with it."""
    variables = QBIT_OPS_THEME.to_color_system().generate()

    assert Color.parse(variables["primary"]) == Color.parse(_BRAND_ACCENT)
    assert Color.parse(variables["primary"]) != Color.parse("#0178d4")


def test_both_gradient_ends_reach_the_stylesheet() -> None:
    """A `Theme` has one `primary` slot and the brand gradient has two
    ends, so the second would otherwise have nowhere to live but a
    Python literal."""
    client = FakeQbitClient(torrents=[make_torrent()])
    variables = _app(client).get_css_variables()

    assert Color.parse(variables["brand-gradient-start"]).rgb == _GRADIENT_START
    assert Color.parse(variables["brand-gradient-end"]).rgb == _GRADIENT_END


async def test_every_modal_titles_itself_in_its_border() -> None:
    """The title belongs in the interrupted border, coloured by the
    theme -- not in a `Static` the modal composes for itself."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)
    accent = Color.parse(_BRAND_ACCENT)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        for key, screen_class in (
            ("f", FiltersScreen),
            ("s", SortScreen),
            ("question_mark", HelpScreen),
            ("enter", DetailsScreen),
        ):
            await pilot.press(key)
            await _settle(app, pilot)
            screen = app.screen
            assert isinstance(screen, screen_class)
            dialog = screen.query_one(f"#{screen_class.DIALOG_ID}")

            if screen_class is FiltersScreen:
                # Its border_title is the tab strip, not a plain
                # title -- see `qbit_ops.tui.tab_bar`.
                assert str(dialog.border_title).startswith(
                    screen_class.MODAL_TITLE
                )
            else:
                assert str(dialog.border_title) == screen_class.MODAL_TITLE
            assert dialog.styles.border_title_color == accent
            await pilot.press("escape")
            await _settle(app, pilot)


async def test_every_modal_advertises_its_keys_in_its_border_subtitle() -> None:
    """No modal ships a bare border, and none of them writes a
    sentence: every hint is a `[key->Label]` token, the same grammar
    the command bar uses. Which keys appear is measured per modal, not
    assumed -- `filters` opens on a text field, where `up`/`down`
    scroll the dialog rather than move focus, so it advertises `tab`.
    """
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        for key, _screen_class in _MODAL_ENTRY_KEYS:
            await pilot.press(key)
            await _settle(app, pilot)
            screen = cast(QbitModal, app.screen)
            plain = Text.from_markup(
                str(screen.query_one(f"#{screen.DIALOG_ID}").border_subtitle)
            ).plain

            assert plain.startswith("["), (screen, plain)
            assert plain.count("[") == len(screen.MODAL_KEYS), (screen, plain)
            # The old hand-written grammar, gone for good.
            assert "\u00b7" not in plain, (screen, plain)
            # Every modal reachable by `escape` says so.
            assert "esc" in plain, (screen, plain)
            await pilot.press("escape")
            await _settle(app, pilot)


async def test_the_details_modal_scrolls() -> None:
    """The only divergence that was a defect rather than a choice: a
    `Vertical` dialog clipped the app's densest surface instead of
    scrolling it."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    # Short enough that the details content genuinely overflows; at a
    # tall terminal a non-scrolling dialog and a scrolling one are
    # indistinguishable, which is how the defect survived.
    async with app.run_test(size=(140, 20)) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _open_details(app, pilot)

        dialog = app.screen.query_one(f"#{DetailsScreen.DIALOG_ID}")
        assert dialog.max_scroll_y > 0
        assert dialog.allow_vertical_scroll


async def test_the_table_advertises_page_navigation_that_works() -> None:
    """`←`/`→` in the table's own border, and the keys behind it: an
    indication of navigation that did nothing would be a lie."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        table = app.query_one("#torrents", DataTable)
        assert "←" in str(table.border_subtitle)
        assert "→" in str(table.border_subtitle)

        # `DataTable` binds left/right itself; without the App's
        # priority binding it would swallow both.
        await pilot.press("left")
        await pilot.pause()
        assert app.controller.state.workspace is Workspace.OVERVIEW

        await pilot.press("right")
        await pilot.pause()
        assert app.controller.state.workspace is Workspace.TORRENTS


async def test_the_overview_masthead_advertises_page_navigation() -> None:
    """The torrents table has `test_the_table_advertises_page_navigation_
    that_works`; the Overview side of the same gesture had nothing --
    same hint, same border-subtitle mechanism, the masthead's own."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)

        masthead = app.query_one("#overview-masthead")
        assert "←" in str(masthead.border_subtitle)
        assert "→" in str(masthead.border_subtitle)

        await pilot.press("right")
        await pilot.pause()
        assert app.controller.state.workspace is Workspace.TORRENTS

        await pilot.press("left")
        await pilot.pause()
        assert app.controller.state.workspace is Workspace.OVERVIEW


async def test_page_navigation_never_steals_the_search_caret() -> None:
    """A focused text field owns `left`/`right`. The priority binding
    that beats `DataTable` would otherwise beat `Input` too."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Ubuntu ISO")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _type_into_search(pilot, "ubu")

        search = app.query_one("#search-input", Input)
        await pilot.press("left")
        await pilot.pause()

        assert app.controller.state.workspace is Workspace.TORRENTS
        assert search.cursor_position == 2


def _keys_the_screen_answers(app: QbitOpsTuiApp) -> set[str]:
    """Every key Textual would dispatch to a binding on this screen.

    Mirrors Textual's own two passes, because one alone is a wrong
    answer. `Screen.active_bindings` walks the *modal* chain, which
    stops at the screen -- so it never lists `escape`, an App binding
    that reaches a modal only because it is `priority=True`. Checking
    that source alone would call a working key a lie.
    """
    answered = set(app.screen.active_bindings)
    answered |= {
        binding.key
        for binding in app.BINDINGS
        if isinstance(binding, Binding) and binding.priority
    }
    return answered


# Labels that promise the operator leaves this modal. `Save` is not
# one: setup validates first and stays open to report what it found.
# `Apply` is not one either, as of `tui-filters`: `FiltersScreen`'s
# `Apply` commits the draft and *stays open* (see its class docstring)
# -- the label still means "this key does something real", just not
# "this key closes the modal" any more, which is the narrower promise
# this set checks.
_LABELS_THAT_LEAVE = frozenset({"Cancel", "Close", "Select", "Run"})


async def test_every_announced_key_is_a_binding_that_is_actually_active() -> (
    None
):
    """A border that advertises a key the screen does not answer is
    worse than a bare border: it teaches a gesture that does nothing.

    Deliberately not *derived* from the live bindings: `sort` alone
    exposes 17, including `Page Left` and `Copy selected text`, so the
    list stays a per-modal editorial choice. This makes it a verifiable
    one.
    """
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)
    checked = 0

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        for key, screen_class in _MODAL_ENTRY_KEYS:
            await pilot.press(key)
            await _settle(app, pilot)
            screen = cast(QbitModal, app.screen)
            assert isinstance(screen, screen_class)
            answered = _keys_the_screen_answers(app)

            for hint in screen.MODAL_KEYS:
                for announced in hint.keys:
                    assert announced in answered, (
                        f"{screen_class.__name__} announces {announced!r} "
                        f"as {hint.label!r}, but no active binding "
                        "answers that key on this screen"
                    )
                    checked += 1
            await pilot.press("escape")
            await _settle(app, pilot)

    # Non-vacuous: a modal that declared no key at all would otherwise
    # satisfy every assertion above by having none to run.
    assert checked >= 10, checked


async def test_a_key_announced_as_leaving_the_modal_actually_leaves_it() -> (
    None
):
    """The sharper half of the same question: a border can announce a key
    that is bound *somewhere* (`sort`'s `enter select` is answered by the
    App's priority `activate` binding) without that key doing what the
    border promises on this specific screen -- here `space`, not
    `enter`, is what actually selects. "Is it bound" and "does it do
    what the border says" are different questions, and only the second
    one is the promise the operator reads.
    """
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)
    checked = 0

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        for entry_key, screen_class in _MODAL_ENTRY_KEYS:
            for hint in screen_class.MODAL_KEYS:
                if hint.label not in _LABELS_THAT_LEAVE:
                    continue
                for announced in hint.keys:
                    await pilot.press(entry_key)
                    await _settle(app, pilot)
                    assert isinstance(app.screen, screen_class)

                    # `sort` only fires on a *change* of option, so
                    # move first: pressing Select on the option that is
                    # already chosen is legitimately a no-op.
                    if screen_class is SortScreen:
                        await pilot.press("down")
                        await _settle(app, pilot)

                    await pilot.press(announced)
                    await _settle(app, pilot)
                    assert not isinstance(app.screen, screen_class), (
                        f"{screen_class.__name__} announces {announced!r} "
                        f"as {hint.label!r}, but pressing it leaves the "
                        "modal open"
                    )
                    checked += 1
                    if app.screen is not app.get_default_screen():
                        await pilot.press("escape")
                        await _settle(app, pilot)

    assert checked >= 5, checked


async def test_border_hints_and_the_command_bar_share_one_grammar() -> None:
    """Two grammars for the same thing is one to learn twice. Both the
    footer and every border render `[key->Description]` through
    `_format_command_entry`."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("s")
        await _settle(app, pilot)

        subtitle = str(
            app.screen.query_one(f"#{SortScreen.DIALOG_ID}").border_subtitle
        )
        plain = Text.from_markup(subtitle).plain

        # The real key displays, resolved by Textual -- never spelled
        # out in the modal, so they cannot drift from what it answers.
        assert "[space\u2192Select]" in plain, plain
        assert "[esc\u2192Cancel]" in plain, plain
        assert "\u00b7" not in plain, plain


async def test_a_modal_border_never_outgrows_its_own_width() -> None:
    """The narrowest surface is the binding constraint: a subtitle
    wider than the border is silently truncated, and a truncated hint
    reads as a different key."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        for key, screen_class in (
            ("f", FiltersScreen),
            ("s", SortScreen),
            ("question_mark", HelpScreen),
            ("enter", DetailsScreen),
        ):
            await pilot.press(key)
            await _settle(app, pilot)
            dialog = app.screen.query_one(f"#{screen_class.DIALOG_ID}")
            width = Text.from_markup(str(dialog.border_subtitle)).cell_len
            # `BORDER_LABEL_MARGIN`: what Textual itself reserves
            # around a border label once both corners are drawn
            # (`qbit_ops.tui.tab_bar`, measured empirically).
            budget = (
                MODAL_WIDTHS[screen_class.MODAL_WIDTH] - BORDER_LABEL_MARGIN
            )

            assert width <= budget, (
                f"{screen_class.__name__}: subtitle is {width} cells, "
                f"border allows {budget}"
            )
            await pilot.press("escape")
            await _settle(app, pilot)


async def test_filters_modal_width_fits_its_own_footer_not_oversized() -> None:
    """On `large` (100), this
    dialog's content never exceeded ~59 cells, but the dialog was sized
    for headroom nothing used -- the real floor is the border's own
    footer (68 cells on Linux, `Ctrl+R`), not the fields. `wide` (76)
    clears that floor by a small, bounded margin instead of the ~26
    cells `large` would have left unused."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = QbitOpsTuiApp(
        client_factory=lambda: client,
        host="http://localhost:8080",
        refresh_interval=LARGE_INTERVAL,
        platform="linux",
    )

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()

        assert FiltersScreen.MODAL_WIDTH == "wide"
        dialog = app.screen.query_one("#filters-dialog")
        assert dialog.outer_size.width == MODAL_WIDTHS["wide"] == 76

        footer = Text.from_markup(str(dialog.border_subtitle)).cell_len
        budget = MODAL_WIDTHS["wide"] - BORDER_LABEL_MARGIN
        slack = budget - footer
        # Never negative (the footer must still fit -- see
        # `test_a_modal_border_never_outgrows_its_own_width`), and
        # nowhere near the ~26-cell slack `large` (100) would leave.
        assert 0 <= slack <= 8, (footer, budget, slack)


async def test_navigation_is_advertised_where_there_is_something_to_move() -> (
    None
):
    """One visible token, announcing the arrows alone, and only on the
    page that has rows: `action_cursor_*` already no-ops on Overview,
    so advertising it there would teach a move that does nothing.

    The bar teaches the gesture a first-time reader reaches for; it is
    not the key inventory. `j`/`k` stay bound and stay working, and the
    help modal lists the whole set.
    """
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        bar = app.query_one("#command-bar", CommandBar)

        overview = Text.from_markup(str(bar.content)).plain
        assert "Navigate" not in overview, overview

        await _goto_torrents(app, pilot)
        torrents = Text.from_markup(str(bar.content)).plain

        assert "[↑/↓→Navigate]" in torrents, torrents
        # The vim aliases work but are not advertised here.
        assert "j/k" not in torrents, torrents
        # Exactly one token for the four keys, not four saying the same.
        assert torrents.count("Navigate") == 1, torrents
        # The keys behind it still work, all four of them -- including
        # the two the bar deliberately does not name.
        for key in ("j", "down", "k", "up"):
            await pilot.press(key)
            await pilot.pause()
        assert app.controller.state.focused_hash == "a" * 40


async def test_the_command_bar_still_fits_the_page_it_describes() -> None:
    """Adding a token can push another off the end. The bar has no
    ellipsis: what overflows is simply not there to be read."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        bar = app.query_one("#command-bar", CommandBar)
        rendered = Text.from_markup(str(bar.content))

        # `#command-bar` is `width: 1fr` with `padding: 0 1` inside the
        # app frame's own one-column border on each side.
        budget = WIDE_SIZE[0] - 4
        assert (
            rendered.cell_len <= budget
        ), f"{rendered.cell_len} cells of {budget}: {rendered.plain}"
        # Non-vacuous: the bar is genuinely populated.
        assert rendered.plain.count("[") >= 6, rendered.plain


# `.qbit-dialog` names its width in absolute columns, so the widest
# word (`-large`, 100) outgrows any terminal narrower than that. These
# two sizes bracket the break: 100 is the last width that fits exactly,
# 96 and 90 are ordinary terminals that did not.
DIALOG_OVERFLOW_SIZES = ((96, 30), (90, 30))


@pytest.mark.parametrize("size", DIALOG_OVERFLOW_SIZES)
async def test_no_modal_dialog_runs_off_a_narrow_terminal(
    size: tuple[int, int],
) -> None:
    """A dialog wider than the screen puts its right border off-screen.
    Geometry is measured, not judged, so this is checked against each
    dialog's computed region rather than its declared width."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=size) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        checked = 0
        for key, screen_class in (*_MODAL_ENTRY_KEYS, ("e", ExplainScreen)):
            await pilot.press(key)
            await _settle(app, pilot)
            assert isinstance(app.screen, screen_class)

            dialog = app.screen.query_one(f"#{screen_class.DIALOG_ID}")
            region = dialog.region
            assert region.x >= 0, (screen_class.__name__, size, region)
            assert region.x + region.width <= size[0], (
                f"{screen_class.__name__} at width {size[0]}: dialog spans "
                f"x={region.x} w={region.width}, past the screen edge"
            )
            checked += 1
            await pilot.press("escape")
            await _settle(app, pilot)

        assert checked == len(_MODAL_ENTRY_KEYS) + 1


# --- The graph's own clock, and the window titles --------------------------


async def test_the_graph_samples_on_its_own_clock_not_the_refresh_one() -> None:
    """`--interval` moves the refresh, never the graph's window. One
    second per sample and one column per sample, so the axis label is
    read back off the width rather than written beside it."""
    from qbit_ops.tui.state import GRAPH_SAMPLE_INTERVAL_SECONDS

    client = FakeQbitClient(torrents=[make_torrent()], download_speed=1_000)
    app = QbitOpsTuiApp(
        client_factory=lambda: client,
        host="http://localhost:8080",
        refresh_interval=LARGE_INTERVAL,
    )

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        history = app.controller.state.rate_history

        assert GRAPH_SAMPLE_INTERVAL_SECONDS == 1.0
        assert LARGE_INTERVAL != GRAPH_SAMPLE_INTERVAL_SECONDS

        before = history.measured
        for _ in range(3):
            _sample_once(app)
        assert history.measured == before + 3

        app._render_overview()
        graph = app.query_one(RateGraph)
        axis = next(
            line for line in str(graph.content).splitlines() if "|now|" in line
        )
        # The window is exactly as many seconds as the plot is columns.
        plot_columns = len(axis) - (len(axis) - len(axis.lstrip(" ")))
        window = re.search(r"\|-(\d+)s\|", axis)
        assert window is not None, axis
        seconds = int(window.group(1))
        assert seconds > 0
        assert abs(seconds - plot_columns) <= 6, (seconds, plot_columns, axis)


async def test_the_graph_draws_its_whole_axis_on_the_very_first_frame() -> None:
    """An axis that built itself up as samples arrived made a freshly
    opened page look broken for a whole minute. Only the trace fills
    in; the rule, its ticks and its labels are there from frame one."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        # The sampler fires the moment the page appears, so an honest
        # "first frame" has to be asked for: a fresh window, rendered.
        app.controller.state.rate_history = RateHistory()
        app._render_overview()
        await pilot.pause()

        graph = app.query_one(RateGraph)
        lines = str(graph.content).splitlines()
        axis = next(line for line in lines if "|now|" in line)

        # Nothing has been plotted, and the axis is already complete.
        assert app.controller.state.rate_history.measured == 0
        assert "no samples yet" in str(graph.content)
        assert len(axis) == graph.size.width
        assert axis.count("─") > graph.size.width // 2
        assert "|now|" in axis
        assert re.search(r"\|-\d+s\|", axis)


async def test_window_titles_ask_nothing_of_the_terminal_font() -> None:
    """The default is ordinary capitals. Unicode small capitals cannot
    be made font-independent -- their 25 letters sit in three unrelated
    blocks and no terminal font covers them evenly, so a title mixes
    sizes on the reader's machine however it is composed here."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        titles = [
            str(app.query_one("#overview-masthead").border_title),
            str(app.query_one(TrackersWindow).border_title),
            str(app.query_one(SessionWindow).border_title),
        ]

        assert titles == ["TRANSFER", "TRACKERS", "SESSION"]
        for title in titles:
            assert title.isascii(), title


async def test_the_small_caps_setting_is_opt_in_and_reaches_every_title() -> (
    None
):
    """Available for whoever has a font that covers all three blocks --
    behind a switch, never as the default."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = QbitOpsTuiApp(
        client_factory=lambda: client,
        host="http://localhost:8080",
        refresh_interval=LARGE_INTERVAL,
        small_caps_titles=True,
    )

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        titles = [
            str(app.query_one("#overview-masthead").border_title),
            str(app.query_one(TrackersWindow).border_title),
            str(app.query_one(SessionWindow).border_title),
        ]

        assert titles == ["ᴛʀᴀɴꜱꜰᴇʀ", "ᴛʀᴀᴄᴋᴇʀꜱ", "ꜱᴇꜱꜱɪᴏɴ"]


@pytest.mark.parametrize("small_caps", [False, True])
async def test_no_window_title_ever_outgrows_its_own_border(
    small_caps: bool,
) -> None:
    """A title wider than the border it sits in is silently clipped,
    so both modes are measured against the box that carries them."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = QbitOpsTuiApp(
        client_factory=lambda: client,
        host="http://localhost:8080",
        refresh_interval=LARGE_INTERVAL,
        small_caps_titles=small_caps,
    )

    async with app.run_test(size=NARROW_SIZE) as pilot:
        await _settle(app, pilot)
        checked = 0
        for selector in (
            "#overview-masthead",
            "#trackers-window",
            "#session-window",
        ):
            window = app.query_one(selector)
            title = str(window.border_title)
            # Two corners plus one dash of border on each side.
            budget = window.outer_size.width - 4

            assert title, selector
            assert cell_len(title) <= budget, (
                f"{selector}: title is {cell_len(title)} cells, "
                f"border allows {budget}"
            )
            checked += 1
        assert checked == 3


async def test_the_overview_still_never_scans_trackers() -> None:
    """The Trackers window attributes torrents by the `tracker` field
    the refresh already carries. A per-torrent announce scan here would
    grow with the library, which is exactly what the Trackers page is
    for."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=f"{i:040x}", tracker=f"http://t{i}.tld/a")
            for i in range(20)
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        _sample_once(app)

        assert client.torrents_trackers_calls <= 1
        breakdown = app.controller.state.tracker_breakdown
        assert breakdown is not None
        assert len(breakdown.rows) == 20


# A gesture announced on screen has to exist, and has to do what the
# announcement says. Two earlier passes found this defect by hand
# (`enter select` on a screen `enter` did not close, `↑/↓ move` on a
# text field); this is the mechanical version.
_KEYED_PAGE_TOKEN = re.compile(r"\(([0-9])/([a-z])\)")


async def test_the_overview_announces_no_page_it_cannot_open() -> None:
    """Every `(n/k)` token on the page must name a workspace that
    exists, reachable by both keys it advertises."""
    from qbit_ops.tui.state import Workspace

    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=f"{i:040x}", tracker=f"http://t{i}.tld/a")
            for i in range(30)
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        page = _static_text(app.query_one("#overview-workspace", OverviewPanel))
        page += str(app.query_one("#workspace-tabs", WorkspaceTabs).content)

        tokens = _KEYED_PAGE_TOKEN.findall(page)
        assert tokens, "no page-switch token found -- the scan proves nothing"
        assert len(tokens) == len(Workspace)

        bound = {binding.key for _, binding in app._bindings}
        for digit, letter in tokens:
            assert digit in bound, digit
            assert letter in bound, letter


async def test_the_overview_never_offers_a_key_that_does_something_else() -> (
    None
):
    """`k` moves a table cursor. A window that told an operator to press
    it "for the full list" was not merely pointing at a missing page --
    it was handing out an unrelated gesture."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=f"{i:040x}", tracker=f"http://t{i}.tld/a")
            for i in range(30)
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        trackers = str(app.query_one(TrackersWindow).content)

        # Non-vacuous: the window really is showing fewer rows than it has.
        assert "more" in trackers
        assert "k for the full list" not in trackers
        assert "Trackers (3/k)" not in trackers
        # `k` is bound, and it is bound to moving a cursor.
        actions = {binding.key: binding.action for _, binding in app._bindings}
        assert actions["k"] == "cursor_up"


@pytest.mark.parametrize("size", RESPONSIVE_SIZES)
async def test_the_graph_ink_reaches_the_edge_of_its_panel(
    size: tuple[int, int],
) -> None:
    """A floor division left the block short and hard against the left,
    so the page's right edge stepped between the masthead band and the
    windows band, and `now` fell short of the real right edge."""
    client = FakeQbitClient(
        torrents=[make_torrent()], download_speed=4_000_000, upload_speed=1
    )
    app = _app(client)

    async with app.run_test(size=size) as pilot:
        await _settle(app, pilot)
        for _ in range(80):
            _sample_once(app)
        app._render_overview()
        await pilot.pause()

        graph = app.query_one(RateGraph)
        panel = graph.size.width
        lines = str(graph.content).splitlines()
        axis = next(line for line in lines if "|now|" in line)

        assert panel > 0
        assert (
            len(axis) == panel
        ), f"axis spans {len(axis)} of {panel} columns at {size}"
        assert axis.rstrip().endswith("|now|")
        # Non-vacuous: the trace was actually drawn, and ends flush too.
        drawn = [line for line in lines if "⣿" in line]
        assert drawn, lines
        assert all(len(line) == panel for line in drawn)


async def test_each_window_wears_a_one_word_title() -> None:
    """A descriptive suffix on the border (e.g. "derived from torrent
    activity") would restate -- less precisely -- the caveat already
    carried by the window's own last line ("announce status not read
    here")."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        trackers = str(app.query_one(TrackersWindow).border_title)
        session = str(app.query_one(SessionWindow).border_title)

        assert trackers == "TRACKERS"
        assert session == "SESSION"
        assert "derived" not in trackers
        # The caveat still exists -- once, against the data it qualifies.
        assert "announce status not read here" in str(
            app.query_one(TrackersWindow).content
        )


# --- Three windows, and a page that does not shuffle -----------------------


async def test_the_overview_is_three_bordered_windows() -> None:
    """One grammar: the wordmark and the graph get a frame of their own,
    the same way the two windows below already had one."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        overview = app.query_one("#overview-workspace", OverviewPanel)
        masthead = overview.query_one("#overview-masthead")
        trackers = overview.query_one(TrackersWindow)
        session = overview.query_one(SessionWindow)

        for window in (masthead, trackers, session):
            assert window.styles.border != ((None, None),) * 4
            assert str(window.border_title)

        assert str(masthead.border_title) == "TRANSFER"
        # The wordmark and the graph live inside that frame.
        assert overview.query_one(BrandHeader).region in masthead.region
        assert overview.query_one(RateGraph).region in masthead.region


@pytest.mark.parametrize("size", RESPONSIVE_SIZES)
async def test_the_page_never_moves_when_the_trackers_go_quiet(
    size: tuple[int, int],
) -> None:
    """A window that shrank when its trackers stopped emitting moved the
    whole page. Page movement that carries no information is noise."""

    def regions(app: QbitOpsTuiApp) -> dict[str, Any]:
        overview = app.query_one("#overview-workspace", OverviewPanel)
        return {
            "masthead": overview.query_one("#overview-masthead").region,
            "graph": overview.query_one(RateGraph).region,
            "trackers": overview.query_one(TrackersWindow).region,
            "session": overview.query_one(SessionWindow).region,
        }

    torrents = [
        make_torrent(
            hash=f"{i:040x}",
            tracker=f"http://t{i}.tld/a",
            dlspeed=900_000,
            upspeed=400_000,
        )
        for i in range(6)
    ]
    client = FakeQbitClient(torrents=torrents, upload_speed=2_000_000)
    app = _app(client)

    async with app.run_test(size=size) as pilot:
        await _settle(app, pilot)
        for _ in range(40):
            _sample_once(app)
        app._render_overview()
        await pilot.pause()
        busy = regions(app)

        for torrent in torrents:
            torrent["dlspeed"] = 0
            torrent["upspeed"] = 0
        client.upload_speed = 0
        app.controller.apply_refresh_success(app.controller.collect_refresh())
        app._render_all()
        await pilot.pause()

        assert regions(app) == busy


async def test_going_stale_never_pushes_a_window_down_a_row() -> None:
    """The staleness banner has a widget of its own, outside the
    fixed-height masthead, for exactly this reason."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        overview = app.query_one("#overview-workspace", OverviewPanel)
        before = overview.query_one("#overview-masthead").region

        app.controller.state.stale = True
        app._render_overview()
        await pilot.pause()

        assert overview.query_one("#overview-masthead").region == before
        assert "STALE" in str(
            overview.query_one("#overview-stale", Static).content
        )


async def test_the_seconds_nobody_watched_are_not_drawn_as_zero() -> None:
    """Leaving the page stops the sampler. Coming back records the gap
    as unmeasured rather than back-filling a still library."""
    client = FakeQbitClient(torrents=[make_torrent()], download_speed=5_000)
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        for _ in range(5):
            _sample_once(app)
        measured_before = app.controller.state.rate_history.measured

        app.controller.skip_rate_samples(30)
        history = app.controller.state.rate_history

        assert history.measured == measured_before
        downloads, _ = history.window(40)
        assert downloads[-1] is None
        assert any(value is not None for value in downloads)


# --- The window commands the width, and the clock commands the column ------


@pytest.mark.parametrize("size", RESPONSIVE_SIZES)
async def test_the_window_commands_the_plot_and_the_label_says_which(
    size: tuple[int, int],
) -> None:
    """The dependency runs window -> width, never the other way. Letting
    the panel decide gave an exact but ugly label ("-62s"); the leftover
    columns widen the left gutter instead, so `now` still ends flush and
    the marks read -60s and -30s wherever the whole window fits."""
    from qbit_ops.tui.state import GRAPH_WINDOW_SLOTS
    from qbit_ops.tui.widgets.rate_graph import plot_slots

    client = FakeQbitClient(
        torrents=[make_torrent()], download_speed=4_000_000, upload_speed=1
    )
    app = _app(client)

    async with app.run_test(size=size) as pilot:
        await _settle(app, pilot)
        for _ in range(80):
            _sample_once(app)
        app._render_overview()
        await pilot.pause()

        graph = app.query_one(RateGraph)
        panel = graph.size.width
        lines = str(graph.content).splitlines()
        axis = next(line for line in lines if "|now|" in line)
        slots = plot_slots(panel)

        # Never more than the window, and never a label that outruns the
        # trace under it.
        assert 0 < slots <= GRAPH_WINDOW_SLOTS
        assert f"|-{slots}s|" in axis
        assert f"|-{slots // 2}s|" in axis
        # Flush right, and every plotted row exactly as wide as the panel.
        assert len(axis) == panel
        assert axis.rstrip().endswith("|now|")
        drawn = [line for line in lines if "⣿" in line]
        assert drawn, lines
        assert all(len(line) == panel for line in drawn)


async def test_a_panel_with_room_shows_the_whole_sixty_second_window() -> None:
    """Non-vacuous companion: the degraded case above must not be the
    only one this suite ever exercises."""
    from qbit_ops.tui.state import GRAPH_WINDOW_SECONDS, GRAPH_WINDOW_SLOTS
    from qbit_ops.tui.widgets.rate_graph import plot_slots

    client = FakeQbitClient(
        torrents=[make_torrent()], download_speed=4_000_000, upload_speed=1
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        for _ in range(80):
            _sample_once(app)
        app._render_overview()
        await pilot.pause()

        graph = app.query_one(RateGraph)
        axis = next(
            line for line in str(graph.content).splitlines() if "|now|" in line
        )

        assert plot_slots(graph.size.width) == GRAPH_WINDOW_SLOTS
        assert f"|-{GRAPH_WINDOW_SECONDS}s|" in axis
        assert f"|-{GRAPH_WINDOW_SECONDS // 2}s|" in axis
        assert len(axis) == graph.size.width


async def test_a_panel_too_narrow_for_the_window_still_tells_the_truth() -> (
    None
):
    """Sixty columns do not always fit. What shrinks is the window, and
    the label says the number it actually shows -- it never keeps
    claiming sixty seconds over a shorter trace."""
    from qbit_ops.tui.state import GRAPH_WINDOW_SLOTS, RateHistory
    from qbit_ops.tui.widgets.rate_graph import build_rate_graph, plot_slots

    history = RateHistory()
    for _ in range(GRAPH_WINDOW_SLOTS + 20):
        history.record_transfer(download=4_000_000, upload=1_000_000)

    narrow = 40
    slots = plot_slots(narrow)
    axis = next(
        line
        for line in build_rate_graph(history, width=narrow).plain.splitlines()
        if "|now|" in line
    )

    assert 0 < slots < GRAPH_WINDOW_SLOTS
    assert f"|-{slots}s|" in axis
    assert "|-60s|" not in axis
    assert len(axis) == narrow


async def test_a_column_belongs_to_the_second_that_asked_for_it() -> None:
    """Measured before it was fixed: writing the sample on arrival put
    network jitter straight into the time axis -- recorded spacing ran
    0.05s to 1.94s while the timer never left 1.00s. The slot is opened
    on the tick and settled afterwards, so a slow reply and a fast one
    still land one column apart."""
    client = FakeQbitClient(torrents=[make_torrent()], download_speed=7)
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        history = app.controller.state.rate_history

        first = app.controller.open_rate_slot()
        second = app.controller.open_rate_slot()
        third = app.controller.open_rate_slot()

        # Three seconds passed, so three columns exist -- before a single
        # answer has come back.
        downloads, _ = history.window(3)
        assert downloads == [None, None, None]

        # The answers land out of order, as a jittery link delivers them.
        app.controller.settle_rate_sample(third, TransferRates(30, 3))
        app.controller.settle_rate_sample(first, TransferRates(10, 1))

        downloads, uploads = history.window(3)
        assert downloads == [10, None, 30]
        assert uploads == [1, None, 3]
        # The unanswered second stays unmeasured rather than borrowing a
        # neighbour's reading. Counted inside the three slots under
        # test: the app's own sampler has already filled earlier ones.
        assert sum(1 for value in downloads if value is not None) == 2
        assert second not in (first, third)


async def test_a_tick_that_asks_nothing_still_advances_the_trace() -> None:
    """An instance slower than a second: the tick is coalesced away, but
    the second still passed and the column has to account for it."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        history = app.controller.state.rate_history
        before = len(history.downloads)

        # A worker is still in flight, so this tick dispatches nothing.
        app._sample_worker = app.run_worker(
            lambda: sleep(0.4) or TransferRates(0, 0),
            group="qbit-sample",
            thread=True,
            exit_on_error=False,
        )
        app._start_rate_sample()

        assert len(history.downloads) == before + 1
        assert history.downloads[-1] is None
        await _settle(app, pilot)


async def test_the_status_line_ends_level_with_the_graph_legend() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        rail = app.query_one("#overview-rail", Static).region
        graph = app.query_one(RateGraph).region

        assert rail.height == 1
        assert rail.y == graph.y + graph.height - 1


@pytest.mark.parametrize(
    "counts",
    [(1147, 1061, 86, 0, 3, 0, 2, 3), (12, 5, 4, 3, 2, 0, 2, 3)],
    ids=["four-digit", "single-digit"],
)
async def test_the_counter_grid_holds_when_a_number_gains_a_digit(
    counts: tuple[int, ...],
) -> None:
    """On a 1147-torrent library `incomplete` sat a column off from the
    three labels under it: the value field was sized per row, so a
    four-digit count overflowed it and shoved its neighbour sideways."""
    (
        total,
        completed,
        seeding,
        downloading,
        stopped,
        checking,
        errored,
        stalled,
    ) = counts
    torrents = (
        [
            make_torrent(hash=f"{i:040x}", state="uploading", progress=1.0)
            for i in range(completed)
        ]
        + [
            make_torrent(
                hash=f"{i + completed:040x}", state="downloading", progress=0.5
            )
            for i in range(downloading)
        ]
        + [
            make_torrent(
                hash=f"{i + completed + downloading:040x}",
                state="error",
                progress=0.5,
            )
            for i in range(errored)
        ]
    )
    client = FakeQbitClient(torrents=torrents)
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        body = str(app.query_one(SessionWindow).content)
        rows = [line for line in body.splitlines() if line.startswith("   ")]

        assert len(rows) == 4, rows
        labels = {
            tuple(m.start() for m in re.finditer(r"[a-z]+", row))
            for row in rows
        }
        ends = {
            tuple(m.end() for m in re.finditer(r"\d+", row)) for row in rows
        }
        assert len(labels) == 1, rows
        assert len(ends) == 1, rows


# --- Second pass, dogfooding after human review: real-desktop fixes --------


@pytest.mark.parametrize(
    "is_macos, expected",
    [(True, "^r"), (False, "Ctrl+R")],
    ids=["macos", "not-macos"],
)
def test_resolve_key_display_branches_on_the_injected_platform_only(
    is_macos: bool, expected: str
) -> None:
    """`^r` reads as the macOS convention; `Ctrl+R` is what's actually
    read everywhere else. Parametrized over the injected `is_macos`
    flag itself, never a conditional inside the test -- `sys.platform`
    is read once, at `QbitOpsTuiApp.__init__`, never inside this
    function (see `resolve_key_display`'s docstring)."""
    binding = Binding("ctrl+r", "clear", "Clear")
    assert resolve_key_display(binding, is_macos=is_macos) == expected


@pytest.mark.parametrize(
    "key, is_macos, expected",
    [
        ("pageup", False, "PgUp"),
        ("pageup", True, "fn+↑"),
        ("pagedown", False, "PgDn"),
        ("pagedown", True, "fn+↓"),
    ],
    ids=[
        "pageup-not-macos",
        "pageup-macos",
        "pagedown-not-macos",
        "pagedown-macos",
    ],
)
def test_resolve_key_display_names_the_mac_page_gesture(
    key: str, is_macos: bool, expected: str
) -> None:
    """A MacBook has no physical PgUp/PgDn key -- `fn`+arrow reaches
    them instead -- so this pair, like `ctrl`, is resolved OS-aware
    rather than through a modal's own fixed `key_display`."""
    binding = Binding(key, "noop", "Section")
    assert resolve_key_display(binding, is_macos=is_macos) == expected


@pytest.mark.parametrize(
    "platform, expected",
    [("darwin", "^r"), ("linux", "Ctrl+R"), ("win32", "Ctrl+R")],
    ids=["darwin", "linux", "win32"],
)
async def test_filters_clear_hint_is_os_aware_end_to_end(
    platform: str, expected: str
) -> None:
    """The border subtitle -- one of the two real render call sites,
    alongside `CommandBar` -- reflects the injected platform through
    `QbitOpsTuiApp.get_key_display`, with no other conditional along
    the way."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = QbitOpsTuiApp(
        client_factory=lambda: client,
        host="http://localhost:8080",
        refresh_interval=LARGE_INTERVAL,
        platform=platform,
    )

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()

        dialog = app.screen.query_one("#filters-dialog")
        subtitle = Text.from_markup(str(dialog.border_subtitle)).plain
        assert f"[{expected}→Clear]" in subtitle


@pytest.mark.parametrize(
    "platform, expected",
    [("darwin", "fn+↑/fn+↓"), ("linux", "PgUp/PgDn"), ("win32", "PgUp/PgDn")],
    ids=["darwin", "linux", "win32"],
)
async def test_filters_section_hint_is_os_aware_end_to_end(
    platform: str, expected: str
) -> None:
    """The "Section" hint names a key a MacBook keyboard actually has
    (`fn`+arrow), not the `PgUp`/`PgDn` printed on a full-size one."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = QbitOpsTuiApp(
        client_factory=lambda: client,
        host="http://localhost:8080",
        refresh_interval=LARGE_INTERVAL,
        platform=platform,
    )

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()

        dialog = app.screen.query_one("#filters-dialog")
        subtitle = Text.from_markup(str(dialog.border_subtitle)).plain
        assert f"[{expected}→Section]" in subtitle


@pytest.mark.parametrize(
    "platform, select_all_key, deselect_all_key, reset_view_key",
    [
        ("darwin", "^a", "^d", "^r"),
        ("linux", "Ctrl+A", "Ctrl+D", "Ctrl+R"),
    ],
    ids=["darwin", "linux"],
)
async def test_help_screen_ctrl_a_is_os_aware_and_stays_aligned(
    platform: str,
    select_all_key: str,
    deselect_all_key: str,
    reset_view_key: str,
) -> None:
    """The help modal's `ctrl+a`/`ctrl+d`/`ctrl+r` lines are a *third*
    render site -- a hand-written block, not a `KeyHint` -- unified
    through the same `get_key_display` call rather than a second,
    hardcoded grammar. The description column must not drift: `^a` and
    `Ctrl+A` alike still start the description at column 11, like every
    other entry in the block."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = QbitOpsTuiApp(
        client_factory=lambda: client,
        host="http://localhost:8080",
        refresh_interval=LARGE_INTERVAL,
        platform=platform,
    )

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("question_mark")
        await pilot.pause()

        lines = cast(HelpScreen, app.screen)._help_text().splitlines()
        select_all_line = next(
            line for line in lines if "Select all visible" in line
        )
        deselect_all_line = next(
            line for line in lines if "Deselect all" in line
        )
        reset_view_line = next(
            line for line in lines if "Reset filters" in line
        )
        assert select_all_line.startswith(select_all_key.ljust(11))
        assert deselect_all_line.startswith(deselect_all_key.ljust(11))
        assert reset_view_line.startswith(reset_view_key.ljust(11))


async def test_ctrl_r_resets_filters_sort_and_selection() -> None:
    """One gesture, three fields back to their initial state -- all
    local to `TuiController`, zero qBittorrent calls either way."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", category="films"),
            make_torrent(hash="b" * 40, name="Beta", category="films"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        app.controller.set_filters(TorrentFilter(categories=("films",)))
        app.controller.set_sort(
            SortOrder(field=SortField.RATIO, direction=SortDirection.DESCENDING)
        )
        await pilot.press("ctrl+a")
        await pilot.pause()

        assert app.controller.state.filters != TorrentFilter()
        assert app.controller.state.sort != SortOrder()
        assert app.controller.state.selected_hashes == {"a" * 40, "b" * 40}

        calls_before = len(client.calls)
        await pilot.press("ctrl+r")
        await pilot.pause()

        assert app.controller.state.filters == TorrentFilter()
        assert app.controller.state.sort == SortOrder()
        assert app.controller.state.selected_hashes == set()
        assert len(client.calls) == calls_before, client.calls[calls_before:]


async def test_ctrl_r_clears_a_checkmark_a_direct_write_left_stale() -> None:
    """`action_toggle_selection` writes the `Sel` cell directly,
    bypassing `_render_table()`'s diff cache -- if a later render then
    diffs against a cache that predates that write and the state cycles
    back to what the stale cache already says, it can wrongly leave the
    glyph exactly as that direct write left it. Asserts on the cell
    actually painted, not on `selected_hashes`."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        # A real `_render_table()` with the cursor already on this row
        # -- as a periodic refresh would produce in real usage -- so
        # the diff cache already holds `focused=True, selected=False`
        # before the direct writes below.
        await _type_into_search(pilot, "a")
        await pilot.press("escape")  # leave the input, keep focus tracking
        await pilot.pause()

        await pilot.press("space")
        await pilot.pause()
        assert app.controller.state.selected_hashes == {"a" * 40}

        await pilot.press("ctrl+r")
        await pilot.pause()

        assert app.controller.state.selected_hashes == set()
        table = app.query_one("#torrents", DataTable)
        cell = table.get_cell_at(Coordinate(0, 0))
        assert "✔" not in cell.plain, cell.plain


async def test_ctrl_r_with_nothing_to_reset_is_a_safe_noop() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        calls_before = len(client.calls)
        await pilot.press("ctrl+r")
        await pilot.pause()

        assert app.controller.state.filters == TorrentFilter()
        assert app.controller.state.sort == SortOrder()
        assert app.controller.state.selected_hashes == set()
        assert len(client.calls) == calls_before


async def test_reset_view_is_announced_only_once_something_differs() -> None:
    """`check_action` both hides and disables the gesture until there
    is something to reset -- same pattern as `deselect_all`."""
    client = FakeQbitClient(torrents=[make_torrent(hash="a" * 40)])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        assert app.check_action("reset_view", ()) is False
        assert "reset_view" not in _footer_actions(app)

        await pilot.press("ctrl+a")
        await pilot.pause()

        assert app.check_action("reset_view", ()) is True
        assert "reset_view" in _footer_actions(app)

        await pilot.press("ctrl+r")
        await pilot.pause()

        assert app.check_action("reset_view", ()) is False
        assert "reset_view" not in _footer_actions(app)


@pytest.mark.parametrize("glyph", ["✓", "✗"], ids=["check", "cross"])
def test_checkbox_glyphs_are_a_single_safe_width_cell(glyph: str) -> None:
    """Measured against `▌` (`Checkbox.BUTTON_RIGHT`'s Textual default),
    which is East Asian Width *Ambiguous* and can render two cells wide
    under a CJK-leaning locale: both replacement glyphs are *Neutral*
    and `cell_len` 1 -- strictly safer, never wider."""
    import unicodedata

    assert cell_len(glyph) == 1
    assert unicodedata.east_asian_width(glyph) == "N"


def test_textuals_default_checkbox_right_glyph_is_ambiguous_width() -> None:
    """The measurement `QbitCheckbox` responds to: proves the default
    this replaces is the wider width class, not an assumption."""
    import unicodedata

    from textual.widgets import Checkbox

    assert unicodedata.east_asian_width(Checkbox.BUTTON_RIGHT) == "A"


async def test_qbit_checkbox_renders_one_state_coloured_glyph() -> None:
    """`▐X▌` becomes a single `✓`/`✗`, state-coloured -- applied to
    every checkbox in the TUI, not only `v-create` (the one that
    prompted it): checked here on the Filters modal's `f-stalled` (a
    plain in-place check) and on `v-create`, mounted through
    `CategorySetScreen` exactly as the app opens it."""
    from qbit_ops.tui.widgets.checkbox import QbitCheckbox

    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha", category="films")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("f")
        await pilot.pause()
        await pilot.press("alt+right")  # State pane: f-stalled/f-errored
        await pilot.pause()

        stalled = app.screen.query_one("#f-stalled", QbitCheckbox)
        assert stalled.BUTTON_LEFT == ""
        assert stalled.BUTTON_RIGHT == ""
        assert str(stalled._button.plain) == "✗"
        stalled.value = True
        await pilot.pause()
        assert str(stalled._button.plain) == "✓"

        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("space")
        await pilot.press("a")
        await pilot.pause()
        button = app.screen.query_one("#actions-category-set", Button)
        await pilot.click(button)
        await pilot.pause()

        create = app.screen.query_one("#v-create", QbitCheckbox)
        assert create.BUTTON_LEFT == ""
        assert create.BUTTON_RIGHT == ""
        assert str(create._button.plain) == "✗"
        create.value = True
        await pilot.pause()
        assert str(create._button.plain) == "✓"


def _rendered_widget(app: QbitOpsTuiApp, widget: Any) -> str:
    """One widget's own rendered rows, flattened to plain text -- the
    same technique `test_no_dot_glyph_appears_in_the_filters_modal`
    uses on a whole dialog, narrowed to a single widget's `.region` so
    a `Checkbox` row can be checked without also picking up a sibling
    `RadioSet`'s own (out of scope here) button chrome."""
    strips = app.screen._compositor.render_strips()
    region = widget.region
    return "\n".join(
        "".join(segment.text for segment in strips[y])[
            region.x : region.x + region.width
        ]
        for y in range(region.y, region.y + region.height)
    )


async def test_no_checkbox_chrome_glyph_reaches_the_rendered_screen() -> None:
    """`▐`/`▌` never reach either checkbox's own rendered row, with the
    box actually checked so `✓` has something to draw, not merely the
    unchecked default. Scoped to the checkbox's own region, not the
    whole dialog: `RadioSet`'s tri-states shared the same
    `▐`/`▌`-around-`●` chrome once -- `QbitRadioButton` now covers that
    case too, see `test_no_radio_chrome_glyph_reaches_the_rendered_screen`."""
    from qbit_ops.tui.widgets.checkbox import QbitCheckbox

    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha", category="films")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("f")
        await pilot.pause()
        await pilot.press("alt+right")
        await pilot.pause()
        stalled = app.screen.query_one("#f-stalled", QbitCheckbox)
        stalled.value = True
        await pilot.pause()

        rendered = _rendered_widget(app, stalled)
        assert "▐" not in rendered
        assert "▌" not in rendered
        assert "✓" in rendered, "fixture produced no checked box to check"

        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("space")
        await pilot.press("a")
        await pilot.pause()
        button = app.screen.query_one("#actions-category-set", Button)
        await pilot.click(button)
        await pilot.pause()
        create = app.screen.query_one("#v-create", QbitCheckbox)
        create.value = True
        await pilot.pause()

        rendered = _rendered_widget(app, create)
        assert "▐" not in rendered
        assert "▌" not in rendered
        assert "✓" in rendered, "fixture produced no checked box to check"


async def test_no_radio_chrome_glyph_reaches_the_rendered_screen() -> None:
    """`RadioButton` always renders `▐●▌`, on or off, a second control
    grammar beside `QbitCheckbox`'s `✓`/`✗` -- and Textual's own default
    `.toggle--button` background (`$panel`, this theme's blue-tinted
    grey) leaks into the `▐`/`▌` glyphs' own colour. `QbitRadioButton`
    unifies both: checked here on the Filters modal's State pane
    (`f-completed`, a plain tri-state) and on `sort`'s `RadioSet`,
    mounted exactly as the app opens each."""
    from textual.widgets import RadioButton, RadioSet

    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha", category="films")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("f")
        await pilot.pause()
        await pilot.press("alt+right")  # State pane: f-completed
        await pilot.pause()

        completed = app.screen.query_one("#f-completed", RadioSet)
        rendered = _rendered_widget(app, completed)
        assert "▐" not in rendered
        assert "▌" not in rendered
        assert "●" not in rendered
        assert "✓" in rendered, "fixture produced no selected option to check"

        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("s")
        await pilot.pause()
        sort_set = app.screen.query_one("#sort-options", RadioSet)
        rendered = _rendered_widget(app, sort_set)
        assert "▐" not in rendered
        assert "▌" not in rendered
        assert "●" not in rendered
        assert "✓" in rendered, "fixture produced no selected option to check"

        for button in sort_set.query(RadioButton):
            assert button.BUTTON_LEFT == ""
            assert button.BUTTON_RIGHT == ""
