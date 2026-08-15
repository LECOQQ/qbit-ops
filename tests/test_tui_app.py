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

import asyncio
import re
import threading
from typing import Any, cast

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
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
from qbit_core.shared.selection import TorrentFilter
from qbit_core.shared.torrent_states import TorrentSnapshot
from qbit_ops.tui.app import (
    ActionsScreen,
    ConnectionBanner,
    DetailsPanel,
    DetailsScreen,
    ExplainScreen,
    FiltersPanel,
    FiltersScreen,
    FilterSummary,
    HelpScreen,
    OverviewPanel,
    PreviewScreen,
    QbitOpsTuiApp,
    ResultScreen,
    SortScreen,
    WorkspaceTabs,
    _columns_for_width,
)
from qbit_ops.tui.formatting import (
    _BRAND_ACCENT,
    _INACTIVE_TAB_ACCENT,
    _format_byte_rate,
    _format_local_time,
    _indicator_cell,
    _truncate,
)
from qbit_ops.tui.state import (
    ConnectionState,
    SortDirection,
    SortField,
    SortOrder,
    Workspace,
)
from qbit_ops.tui.widgets.overview import (
    _BRAND_COMPACT_MIN_WIDTH,
    _BRAND_FULL_MIN_WIDTH,
    _LOGO_COMPACT,
    _LOGO_FULL,
    BrandHeader,
    HeaderVariant,
)
from qbit_ops.tui.widgets.status_bar import (
    CommandBar,
    FooterTotal,
    GlobalRateDisplay,
)
from tests.support import FakeQbitClient, make_torrent

pytestmark = pytest.mark.tui

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
    await pilot.press("slash")
    await pilot.pause()
    for char in text:
        await pilot.press(char)
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
            rf"Downloading\s+{status.counts.downloading}\s+"
            rf"Seeding\s+{status.counts.seeding}",
            overview_text,
        )
        assert re.search(
            rf"Completed\s+{status.counts.completed}\s+"
            rf"Incomplete\s+{incomplete}",
            overview_text,
        )
        assert re.search(
            rf"Stopped\s+{app.controller.state.stopped_count}\s+"
            rf"Checking\s+{status.counts.checking}",
            overview_text,
        )
        assert f"{status.counts.errored} errored" in overview_text
        assert f"{status.counts.stalled} stalled" in overview_text


async def test_overview_shows_grounded_warning_reasons_not_just_the_label() -> (
    None
):
    """The compact Health section grounds its label in the counts line
    (e.g. "N stalled") rather than the full alert-message text -- that
    detail belongs to doctor/trackers status/explain instead."""
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
        assert len(status.alerts) > 0
        # Never just the bare health word with no reasons attached.
        assert f"{status.counts.stalled} stalled" in overview_text
        assert f"{len(status.alerts)} finding" in overview_text


async def test_overview_shows_zero_findings_when_healthy() -> None:
    client = FakeQbitClient(torrents=[make_torrent(state="uploading")])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)

        overview_text = _static_text(
            app.query_one("#overview-workspace", OverviewPanel)
        )
        assert "0 findings" in overview_text


async def test_overview_shows_connection_and_nav_hint() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)

        overview_text = _static_text(
            app.query_one("#overview-workspace", OverviewPanel)
        )
        assert "Connected" in overview_text
        assert "Refreshed" in overview_text
        assert "Browse torrents" in overview_text


async def test_overview_never_calls_torrents_trackers() -> None:
    """No tracker-wide scan is ever performed to build the Overview.

    `torrents_trackers_calls` may be at most 1 here -- the torrent
    table's own "cursor starts on row 0" behavior fires one ordinary
    focus-change detail fetch, completely unrelated to (and not used
    by) the Overview's own rendering, which reads only `TuiState.status`
    /`stopped_count`. What this test actually guards against is a scan
    that scales with torrent count.
    """
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


async def test_brand_header_shows_the_installed_version() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        header_text = _brand_header_text(app.query_one(BrandHeader))
        assert f"v{qbit_ops.__version__}" in header_text


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


async def test_overview_no_longer_uses_six_equal_bordered_cards() -> None:
    """Item 11.1: the old six-card equal grid is no longer the rendered
    hierarchy -- there is no `.ov-card` class left, and exactly one
    primary (Torrents) and one secondary (Health) content section."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        overview = app.query_one("#overview-workspace", OverviewPanel)
        assert len(overview.query(".ov-card")) == 0
        assert len(overview.query(".ov-torrents")) == 1
        assert len(overview.query(".ov-health")) == 1
        assert len(overview.query(BrandHeader)) == 1


async def test_overview_sections_remain_mounted_at_every_brand_variant() -> (
    None
):
    for size in (WIDE_SIZE, BRAND_COMPACT_SIZE, BRAND_TEXT_ONLY_SIZE):
        client = FakeQbitClient(torrents=[make_torrent()])
        app = _app(client)
        async with app.run_test(size=size) as pilot:
            await _settle(app, pilot)
            overview = app.query_one("#overview-workspace", OverviewPanel)
            assert len(overview.query(".ov-torrents")) == 1
            assert len(overview.query(".ov-health")) == 1
            assert len(overview.query(".ov-instance")) == 1
            assert len(overview.query(BrandHeader)) == 1


async def test_overview_instance_card_shows_lifetime_totals_and_peers() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent()],
        all_time_downloaded=1024**3,  # 1 GiB
        all_time_uploaded=2 * 1024**3,  # 2 GiB
        global_ratio="1.75",
        connected_peers=9,
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        overview = app.query_one("#overview-workspace", OverviewPanel)
        instance_section = overview.query_one(".ov-instance", Static)
        content = str(instance_section.content)
        assert "1.0 GiB" in content
        assert "2.0 GiB" in content
        assert "1.75" in content
        assert "9" in content


async def test_overview_instance_card_shows_dash_when_no_ratio_yet() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent()],
        global_ratio="-1",
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        overview = app.query_one("#overview-workspace", OverviewPanel)
        instance_section = overview.query_one(".ov-instance", Static)
        assert "–" in str(instance_section.content)


async def test_overview_torrents_section_does_not_pad_beyond_its_content() -> (
    None
):
    """The Torrents section must stay content-driven height, not
    stretched by an oversized fixed row/`min-height` the old card grid
    used."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        overview = app.query_one("#overview-workspace", OverviewPanel)
        torrents_section = overview.query_one(".ov-torrents", Static)
        expected_lines = str(torrents_section.content).count("\n") + 1
        assert torrents_section.outer_size.height == expected_lines


async def test_overview_nav_hint_stays_reachable_below_header() -> None:
    """Nesting the cards one level deeper (under `BrandHeader`, inside
    `#overview-cards`) must not strand the last card/nav hint outside
    the scrollable region -- the header adds height, but the panel is
    still a `VerticalScroll` and must still reach its own bottom."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=NARROW_SIZE) as pilot:
        await _settle(app, pilot)
        overview = app.query_one("#overview-workspace", OverviewPanel)
        overview.scroll_end(animate=False)
        await pilot.pause()

        nav_hint = next(
            widget
            for widget in overview.query(Static)
            if "ov-nav" in widget.classes
        )
        visible = overview.region
        nav_region = nav_hint.region
        assert nav_region.y < visible.y + visible.height
        assert nav_region.y + nav_region.height > visible.y


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
    """Point 2 of the follow-up visual-polish brief: the underline span
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


async def test_switching_workspaces_performs_zero_api_calls() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        calls_before = len(client.calls)

        await _goto_torrents(app, pilot)
        await _goto_overview(app, pilot)
        await _goto_torrents(app, pilot)

        assert len(client.calls) == calls_before


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
        category_input = app.screen.query_one(".f-category", Input)
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
        category_input = app.screen.query_one(".f-category", Input)
        category_input.focus()
        await pilot.press(*"radarr")
        await pilot.pause()
        await pilot.press("enter")
        await _settle(app, pilot)

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
        category_input = app.screen.query_one(".f-category", Input)
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
# `/` used to mount a real `Input` but Enter never worked: the App's own
# `enter` binding (`action_activate`) is `priority=True`, which wins key
# resolution *before* the focused `Input`'s own declarative `enter` ->
# `submit` binding is ever considered. Fixed by filtering live on every
# keystroke (`on_input_changed`) and having `action_activate` special-case
# the search input's `enter` to simply return focus to the table.


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
        category_input = app.screen.query_one(".f-category", Input)
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
        category_input = app.screen.query_one(".f-category", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        await pilot.press("f")
        await pilot.pause()
        reopened_input = app.screen.query_one(".f-category", Input)
        assert reopened_input.value == "films"
        await pilot.press("escape")
        await pilot.pause()


async def test_filter_apply_with_enter_closes_and_keeps_filter() -> None:
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
        category_input = app.screen.query_one(".f-category", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert len(app.screen_stack) == 1
        assert app.controller.state.filters.categories == ("films",)
        assert _visible_names(app) == ["Alpha"]


async def test_filter_cancel_with_escape_retains_previous_filter() -> None:
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
        category_input = app.screen.query_one(".f-category", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.controller.state.filters.categories == ("films",)

        # Reopen, edit, then cancel -- must revert to "films".
        await pilot.press("f")
        await pilot.pause()
        category_input2 = app.screen.query_one(".f-category", Input)
        category_input2.focus()
        await pilot.press("ctrl+u")
        await pilot.press(*"tv")
        await pilot.pause()
        assert app.controller.state.filters.categories == ("tv",)

        await pilot.press("escape")
        await pilot.pause()

        assert len(app.screen_stack) == 1
        assert app.controller.state.filters.categories == ("films",)
        assert _visible_names(app) == ["Alpha"]


async def test_filter_clear_restores_the_unfiltered_list_and_stays_open() -> (
    None
):
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
        category_input = app.screen.query_one(".f-category", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert _visible_names(app) == ["Alpha"]

        await pilot.press("f")
        await pilot.pause()
        await pilot.press("ctrl+r")
        await pilot.pause()

        assert len(app.screen_stack) > 1  # clear keeps the modal open
        assert (
            app.controller.state.filters
            == app.controller.state.filters.__class__()
        )
        assert sorted(_visible_names(app)) == ["Alpha", "Beta"]
        cleared_input = app.screen.query_one(".f-category", Input)
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
        category_input = app.screen.query_one(".f-category", Input)
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
        assert "Health" in overview_text
        assert "finding" in overview_text
        # Item 11.9: narrow stacks without horizontal overflow.
        assert overview.region.width <= 80


async def test_overview_wide_layout_is_asymmetric_torrents_dominant() -> None:
    """Item 11.7/§4: at wide size Torrents (primary) must be visually
    larger than Health (secondary), not two equal-width cards."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        overview = app.query_one("#overview-workspace", OverviewPanel)
        torrents_section = overview.query_one(".ov-torrents", Static)
        health_section = overview.query_one(".ov-health", Static)
        assert torrents_section.region.width > health_section.region.width


async def test_overview_medium_layout_stays_readable() -> None:
    """Item 11.8: the medium layout keeps both sections comfortably
    two-column, with no horizontal overflow."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=MEDIUM_SIZE) as pilot:
        await _settle(app, pilot)
        overview = app.query_one("#overview-workspace", OverviewPanel)
        torrents_section = overview.query_one(".ov-torrents", Static)
        health_section = overview.query_one(".ov-health", Static)
        assert torrents_section.region.width > 0
        assert health_section.region.width > 0
        assert overview.region.width <= MEDIUM_SIZE[0]


async def test_overview_narrow_layout_stacks_sections_without_overflow() -> (
    None
):
    """Item 11.9: narrow stacks status rail / Torrents / Health / Browse
    action -- Torrents sits above Health, both full-width, no scroll."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=NARROW_SIZE) as pilot:
        await _settle(app, pilot)
        overview = app.query_one("#overview-workspace", OverviewPanel)
        torrents_section = overview.query_one(".ov-torrents", Static)
        health_section = overview.query_one(".ov-health", Static)
        assert torrents_section.region.y < health_section.region.y
        assert overview.region.width <= NARROW_SIZE[0]


async def test_overview_health_color_reflects_state_not_brand_gradient() -> (
    None
):
    """Regression guard for the prior phase's dropped-border lesson:
    a warning-health snapshot must still carry a distinguishable
    semantic style, and it must never be the brand orange/coral."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", state="stalledDL"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        overview = app.query_one("#overview-workspace", OverviewPanel)
        health_section = overview.query_one(".ov-health", Static)
        content = str(health_section.content)
        assert "bold yellow" in content
        assert "ov-health-warning" in health_section.classes
        assert "#ff9933" not in content
        assert "#d62839" not in content


async def test_overview_health_color_is_green_when_healthy() -> None:
    client = FakeQbitClient(torrents=[make_torrent(state="uploading")])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        overview = app.query_one("#overview-workspace", OverviewPanel)
        health_section = overview.query_one(".ov-health", Static)
        assert "bold green" in str(health_section.content)
        assert "ov-health-healthy" in health_section.classes


async def test_overview_rail_shows_connection_identity_not_transfer_rates() -> (
    None
):
    """Connection/version/refresh are one status rail; transfer rates
    live only in the top-right `GlobalRateDisplay` now, never
    duplicated here."""
    client = FakeQbitClient(
        torrents=[make_torrent()], download_speed=0, upload_speed=2_500_000
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        status = app.controller.state.status
        assert status is not None
        rail = app.query_one("#overview-rail", Static)
        rail_text = str(rail.content)
        assert "Connected" in rail_text
        assert "Refreshed" in rail_text
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
)
async def test_overview_rail_stays_readable_across_connection_states(
    connection: ConnectionState,
) -> None:
    """Item §1: the rail must remain readable in every connection
    state, including a stale reconnect."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        app.controller.state.connection = connection
        app.controller.state.stale = True
        app._render_overview()

        rail = app.query_one("#overview-rail", Static)
        rail_text = str(rail.content)
        assert rail_text
        if connection is not ConnectionState.CONNECTED:
            assert "STALE" in rail_text


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
        category_input = app.screen.query_one(".f-category", Input)
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
        assert re.search(r"Seeding\s+1", overview_text)
        # Also 1 stopped (Activity's own, separate line).
        assert re.search(r"Stopped\s+1", overview_text)
        # Also 1 completed (Completion's own, separate dimension).
        assert re.search(r"Completed\s+1", overview_text)


async def test_no_tracker_endpoint_shows_duplicated_disabled_word() -> None:
    """Regression: a disabled pseudo-tracker (DHT/PeX/LSD) must render
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

        completion = app.screen.query_one(".f-completion", RadioSet)
        buttons = list(completion.query(RadioButton))
        buttons[1].value = True  # "Completed"
        await pilot.pause()

        assert app.controller.state.filters.completed is True
        assert _visible_names(app) == ["Alpha"]

        buttons[2].value = True  # "Incomplete"
        await pilot.pause()

        assert app.controller.state.filters.completed is False
        assert _visible_names(app) == ["Beta"]

        buttons[0].value = True  # "Any"
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

        activity = app.screen.query_one(".f-activity", RadioSet)
        buttons = list(activity.query(RadioButton))
        buttons[1].value = True  # "Active"
        await pilot.pause()

        assert app.controller.state.filters.active is True
        assert _visible_names(app) == ["Alpha"]

        buttons[2].value = True  # "Inactive"
        await pilot.pause()

        assert app.controller.state.filters.active is False
        assert _visible_names(app) == ["Beta"]


async def test_filter_modal_apply_button_applies_and_closes() -> None:
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
        category_input = app.screen.query_one(".f-category", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()

        apply_button = app.screen.query_one("#filters-apply", Button)
        await pilot.click(apply_button)
        await pilot.pause()

        assert len(app.screen_stack) == 1
        assert app.controller.state.filters.categories == ("films",)


async def test_filter_modal_cancel_button_reverts_and_closes() -> None:
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
        category_input = app.screen.query_one(".f-category", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()

        cancel_button = app.screen.query_one("#filters-cancel", Button)
        await pilot.click(cancel_button)
        await pilot.pause()

        assert len(app.screen_stack) == 1
        assert app.controller.state.filters.categories == ()


async def test_filter_modal_clear_button_resets_and_stays_open() -> None:
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
        category_input = app.screen.query_one(".f-category", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()

        clear_button = app.screen.query_one("#filters-clear", Button)
        await pilot.click(clear_button)
        await pilot.pause()

        assert len(app.screen_stack) > 1
        assert app.controller.state.filters == TorrentFilter()
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

            assert app.screen.query_one("#filters-apply", Button) is not None
            assert app.screen.query_one("#filters-clear", Button) is not None
            assert app.screen.query_one("#filters-cancel", Button) is not None
            assert app.screen.query_one(".f-completion", RadioSet) is not None
            assert app.screen.query_one(".f-activity", RadioSet) is not None

            await pilot.press("escape")
            await pilot.pause()
            assert len(app.screen_stack) == 1


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

        assert actions == {"toggle_help", "quit"}
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
    """`g`/`t`/`1`/`2` keep switching workspaces even though none of
    them are advertised in the footer any more."""
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

        # Patched at `qbit_ops.tui.widgets.overview`: the rail formats
        # its refresh time via its own local `_format_rail_time`, not
        # the shared `qbit_ops.tui.formatting._format_local_time`.
        with patch("qbit_ops.tui.widgets.overview._format_rail_time") as mocked:
            mocked.side_effect = lambda moment, tz=None: (
                f"stub-time {fixed_tz.tzname(moment)}"
            )
            app._render_overview()
            overview_text = _static_text(
                app.query_one("#overview-workspace", OverviewPanel)
            )
            assert "stub-time JST" in overview_text


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

        completion = app.screen.query_one(".f-completion", RadioSet)
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

        completion = app.screen.query_one(".f-completion", RadioSet)
        activity = app.screen.query_one(".f-activity", RadioSet)

        assert completion.pressed_index is not None
        assert activity.pressed_index is not None

        completion_buttons = list(completion.query(RadioButton))
        completion_buttons[1].value = True
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

        # FiltersScreen: Enter applies and closes.
        await pilot.press("f")
        await pilot.pause()
        category_input = app.screen.query_one(".f-category", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert app.controller.state.filters.categories == ("films",)

        # FiltersScreen: Escape cancels.
        await pilot.press("f")
        await pilot.pause()
        cat2 = app.screen.query_one(".f-category", Input)
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
    """Regression: Textual's default `AUTO_FOCUS = "*"` auto-focuses the
    *first* focusable widget in DOM order on a newly pushed screen --
    which was `#filters-dialog` itself (a `VerticalScroll`, and
    therefore focusable), not the category `Input` nested inside it.
    Every keystroke right after pressing `f` went to the scroll
    container (which only understands up/down/page keys) instead of
    any actual field, making Filters look entirely unresponsive to the
    keyboard without an explicit click or Tab press first."""
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

        category_input = app.screen.query_one(".f-category", Input)
        assert category_input.has_focus

        await pilot.press(*"films")
        await pilot.pause()

        assert category_input.value == "films"
        assert app.controller.state.filters.categories == ("films",)
        assert _visible_names(app) == ["Alpha"]

        await pilot.press("escape")
        await pilot.pause()


async def test_tab_navigates_between_filter_fields() -> None:
    """Regression: `check_action` blocked *every* action while a modal
    was open, including `app.focus_next`/`app.focus_previous` -- the
    actions behind Textual's own `Screen`-level `tab`/`shift+tab`
    bindings. That silently broke Tab navigation between fields inside
    any modal (category -> state -> checkboxes -> radio sets -> Apply/
    Clear/Cancel buttons in Filters), even though typing into the
    already-focused first field worked fine."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("f")
        await pilot.pause()

        category_input = app.screen.query_one(".f-category", Input)
        assert category_input.has_focus

        await pilot.press("tab")
        await pilot.pause()
        state_input = app.screen.query_one(".f-state", Input)
        assert state_input.has_focus

        await pilot.press("shift+tab")
        await pilot.pause()
        assert category_input.has_focus

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
        category_input = app.screen.query_one(".f-category", Input)
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
    """Regression: `action_activate` (bound to `enter` with
    `priority=True`) only special-cased `FiltersScreen`, so `enter`
    silently did nothing in `ActionsScreen`/`PreviewScreen`/
    `ResultScreen` even with a `Button` focused -- the priority binding
    intercepted the key before the `Button`'s own native
    enter-activates-click behavior ever ran."""
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

        category_input = app.screen.query_one(".f-category", Input)
        assert category_input.has_focus

        await pilot.press("down")
        await pilot.pause()
        state_input = app.screen.query_one(".f-state", Input)
        assert state_input.has_focus

        await pilot.press("up")
        await pilot.pause()
        assert category_input.has_focus

        await pilot.press("escape")
        await pilot.pause()


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


async def test_summary_shows_selected_count_and_criteria_on_a_second_line() -> (
    None
):
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Documentary", category="sonarr"),
            make_torrent(hash="b" * 40, name="Ubuntu ISO", category="films"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")
        await pilot.pause()

        summary = app.query_one("#filter-summary", FilterSummary)
        text = str(summary.content)
        lines = text.splitlines()
        assert lines[0] == "2 shown / 2 · 1 selected"
        # No criteria are active, so the second line reads "No filters"
        # -- combined with the always-present local sort state, never
        # an empty trailing line.
        assert len(lines) == 2
        assert lines[1] == "No filters · Sorted by Name ↑"

        await pilot.press("slash")
        await pilot.pause()
        for char in "documentary":
            await pilot.press(char)
        await pilot.pause()

        text = str(summary.content)
        lines = text.splitlines()
        assert lines[0] == "1 shown / 2 · 1 selected"
        assert lines[1] == "search: documentary · Sorted by Name ↑"


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
    """Regression guard: the Details grid must not fall back to a second
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


def test_datatable_cursor_never_uses_the_default_blue_block() -> None:
    """`QbitOpsTuiApp` must override `#torrents`' cursor styling --
    Textual's own `DataTable` default (`$block-cursor-background`)
    resolves to the active theme's `primary` colour, `#0178D4` (a
    strong blue) for the built-in `textual-dark` theme this app uses,
    unless overridden."""
    assert "#torrents > .datatable--cursor" in QbitOpsTuiApp.CSS
    assert "block-cursor-background" not in QbitOpsTuiApp.CSS


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
        category_input = app.screen.query_one(".f-category", Input)
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
    narrowest edge must still fit -- `_name_column_width` prefers a
    narrower-than-target `Name` column over any horizontal overflow.

    Mirrors `_name_column_width`'s own budget exactly (outer AppFrame
    border and the `#torrents` table's own titled-region border both
    subtract from the raw App width -- there is no more permanent side
    panel to account for) -- see
    `test_long_name_truncates_and_never_wraps_a_second_table_row` for
    the real-render, end-to-end proof this unit-level check mirrors.
    """
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


def test_format_global_rate_colours_each_direction_independently() -> None:
    from qbit_ops.tui.formatting import (
        _BRAND_ACCENT,
        _INACTIVE_TAB_ACCENT,
        _format_global_rate,
    )

    idle = _format_global_rate(0, 0)
    assert idle.spans[0].style == _INACTIVE_TAB_ACCENT
    assert idle.spans[-1].style == _INACTIVE_TAB_ACCENT

    down_only = _format_global_rate(2_500_000, 0)
    assert down_only.spans[0].style == _BRAND_ACCENT
    assert down_only.spans[-1].style == _INACTIVE_TAB_ACCENT

    up_only = _format_global_rate(0, 2_500_000)
    assert up_only.spans[0].style == _INACTIVE_TAB_ACCENT
    assert up_only.spans[-1].style == _BRAND_ACCENT

    both = _format_global_rate(2_500_000, 2_500_000)
    assert both.spans[0].style == _BRAND_ACCENT
    assert both.spans[-1].style == _BRAND_ACCENT


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


async def test_top_right_global_rate_display_shows_live_status_rates() -> None:
    from qbit_ops.tui.formatting import _BRAND_ACCENT, _INACTIVE_TAB_ACCENT

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
        # Inactive download (blue), active upload (orange) -- both
        # present, never one colour for the whole indicator.
        assert _INACTIVE_TAB_ACCENT in styles
        assert _BRAND_ACCENT in styles


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
        assert (
            str(app.screen.query_one("#filters-dialog").border_title)
            == "Filters"
        )
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


def test_modal_borders_use_the_brand_accent_not_default_accent() -> None:
    """Every modal dialog's border uses the same warm brand orange
    (`#ff9933`, matching `formatting._BRAND_ACCENT`), never Textual's
    default (blue) `$accent`."""
    for screen_class in (
        FiltersScreen,
        ActionsScreen,
        SortScreen,
        PreviewScreen,
        ResultScreen,
        ExplainScreen,
        HelpScreen,
    ):
        css = screen_class.CSS
        assert "#ff9933" in css, screen_class
        assert "$accent" not in css, screen_class


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


def test_workspace_tabs_region_carries_no_panel_background() -> None:
    """`#workspace-tabs` and `#command-bar` no longer fill with `$panel`
    -- the empty grey seam the user reported above the black
    background. Scoped to each rule's own `{ ... }` block, not a bare
    substring check: `#torrents`'s cursor-tint background
    (`$panel-lighten-2 60%`, a deliberately kept functional signal)
    also contains the text "background: $panel"."""
    css = QbitOpsTuiApp.CSS
    for selector in ("#workspace-tabs", "#command-bar"):
        match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
        assert match, selector
        assert "background" not in match.group(1), selector


async def test_workspace_tabs_computed_background_is_unset() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        tabs = app.query_one("#workspace-tabs", WorkspaceTabs)
        # No explicit background rule -> fully transparent alpha, i.e.
        # it shows whatever is behind it rather than a distinct fill.
        assert tabs.styles.background.a == 0


async def test_search_footer_replaces_search_token_in_same_row() -> None:
    """No separate `search:`/Total row above the footer -- the
    `[/→Search]` token inside `CommandBar` itself is replaced in place
    by the pipe-delimited `|search: xxx|` token (distinct from the
    bracketed `[key→Description]` key hints beside it), restored to
    `[/→Search]` once search closes. The right-aligned `|Total: y|`
    token lives in the separate `FooterTotal` sibling, not appended
    into `CommandBar`'s own string (see `FooterTotal`'s docstring). The
    underlying `Input` (still the real keystroke sink -- see
    `CommandBar`'s docstring) keeps the same id/value contract
    `_type_into_search`/other tests already rely on.
    """
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
    """Requirement 2: presentation-only change -- the actual
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
    """A live search `Input` now lives in the always-mounted
    `#footer-row`, not inside `#torrents-workspace` -- it must be torn
    down explicitly on workspace switch, or it would keep eating
    keystrokes meant for Overview navigation.

    `g`/`t` can't be pressed to switch workspaces while `#search-input`
    itself is focused (a single-char binding like `g` is consumed by
    the focused `Input` as typed text -- Textual's own
    `check_consume_key`), so this presses Tab first to move focus to
    the table, exactly as an operator would."""
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
    """The `Toast` App-level CSS override applies the brand orange to
    the default `-information` state, not Textual's default green."""
    assert "Toast.-information" in QbitOpsTuiApp.CSS
    assert "#ff9933" in QbitOpsTuiApp.CSS


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
        assert app.screen.query_one(".f-category", Input) is not None
        assert app.screen.query_one(".f-state", Input) is not None
        assert app.screen.query_one(".f-stalled", Checkbox) is not None
        assert app.screen.query_one(".f-errored", Checkbox) is not None
        assert app.screen.query_one(".f-completion", RadioSet) is not None
        assert app.screen.query_one(".f-activity", RadioSet) is not None
        for button_id in ("filters-apply", "filters-clear", "filters-cancel"):
            assert app.screen.query_one(f"#{button_id}", Button) is not None
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
    """Point 4: `#torrents`' computed background must equal the
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
    """Point 5: the reported "clash" was `$surface` (a measurably
    lighter grey, e.g. #1e1e1e) used as the dialog fill against the
    app's own uniform `$background` (e.g. #121212) -- a visibly
    distinct box breaking the round border's floating-outline look.
    Fixed by using `$background` for the three dialogs; regression-
    tested on the actual computed/composited background, not CSS
    source text. Also covers each dialog's *children* (`RadioSet`,
    `Input`, default-variant `Button`): those widgets' own
    `DEFAULT_CSS` independently fills with `$surface`, so fixing only
    the outer dialog would just move the same grey-box clash one level
    in -- reproduced and fixed for real, not merely assumed."""
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
            app.screen.query_one(".f-category", Input).background_colors
            == screen_bg
        )
        assert (
            app.screen.query_one(".f-completion", RadioSet).background_colors
            == screen_bg
        )
        assert (
            app.screen.query_one("#filters-clear", Button).background_colors
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
    """Point 6: Textual's own default focus/selection chrome for
    `Input`, `Checkbox`, `Button` (`-primary` variant), and a
    `RadioSet`'s highlighted option all draw from `$primary`/
    `$block-cursor-background` -- a saturated blue (`#0178d4`) -- by
    default. Every one of those is overridden to the brand orange in
    `FiltersScreen`/`SortScreen`/`ActionsScreen`'s own CSS; this checks
    the actual computed styles a user would see, not the CSS source.

    Also asserts the *foreground* colour on each orange fill: an
    earlier draft used the dialog's near-white `$text` there, which is
    only ~2:1 contrast against `#ff9933` -- barely more legible than
    the default Textual blue it replaced. The app's own dark
    `$background` tone reused as foreground gives ~9:1."""
    from textual.widgets import Checkbox, RadioSet

    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha", category="films")]
    )
    app = _app(client)
    orange = (255, 153, 51)
    dark = (18, 18, 18)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        await pilot.press("f")
        await pilot.pause()
        # Flat buttons: unfocused `-primary` shows orange *text* on a
        # transparent fill; focus flips to a solid orange fill with
        # dark text -- either way, never Textual's default blue.
        apply_button = app.screen.query_one("#filters-apply", Button)
        assert apply_button.styles.color.rgb == orange
        apply_button.focus()
        await pilot.pause()
        assert apply_button.styles.background.rgb == orange
        assert apply_button.styles.color.rgb == dark

        category_input = app.screen.query_one(".f-category", Input)
        category_input.focus()
        await pilot.pause()
        assert category_input.styles.border.top[1].rgb == orange

        checkbox = app.screen.query_one(".f-stalled", Checkbox)
        checkbox.focus()
        await pilot.pause()
        assert checkbox.styles.border.top[1].rgb == orange
        checkbox_label = checkbox.get_component_styles("toggle--label")
        assert checkbox_label.background.rgb == orange
        assert checkbox_label.color.rgb == dark

        completion = app.screen.query_one(".f-completion", RadioSet)
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
