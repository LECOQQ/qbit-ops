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
import threading
import time
from typing import Any

import pytest
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

from qbit_ops.features.torrents import TorrentFilter
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
    WorkspaceTabs,
    _columns_for_width,
)
from qbit_ops.tui.formatting import (
    _format_byte_rate,
    _format_local_time,
    _truncate,
)
from qbit_ops.tui.state import ConnectionState, Workspace
from tests.support import FakeQbitClient, make_torrent

pytestmark = pytest.mark.tui

LARGE_INTERVAL = 999.0  # effectively disables the periodic timer mid-test
WIDE_SIZE = (140, 40)
NARROW_SIZE = (80, 24)
RESPONSIVE_SIZES = [(80, 24), (100, 30), (120, 35), (160, 45)]
WAIT_TIMEOUT = 5.0  # seconds a test will wait on a real threading.Event


def _app(client: FakeQbitClient) -> QbitOpsTuiApp:
    return QbitOpsTuiApp(
        client_factory=lambda: client,
        host="http://localhost:8080",
        refresh_interval=LARGE_INTERVAL,
    )


def _static_text(widget: Any) -> str:
    """Join every mounted `Static` child's content into one string."""
    return "\n".join(
        str(child.content)
        for child in widget.children
        if isinstance(child, Static)
    )


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
    blocking the event loop or sleeping arbitrarily -- polls a tight
    loop yielding control each time, bounded by `timeout`."""
    deadline = time.monotonic() + timeout
    while not event.is_set():
        if time.monotonic() > deadline:
            raise TimeoutError("timed out waiting for a real event")
        await _yield()


async def _yield() -> None:
    await asyncio.sleep(0)


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
        assert f"{status.counts.total} total" in overview_text
        assert f"{status.counts.downloading} downloading" in overview_text
        assert f"{status.counts.seeding} seeding" in overview_text
        assert f"{status.counts.errored} errored" in overview_text
        assert f"{status.counts.stalled} stalled" in overview_text
        assert f"{app.controller.state.stopped_count} stopped" in overview_text
        assert f"{status.counts.completed} completed" in overview_text
        down = _format_byte_rate(status.rates.download_bytes_per_second)
        up = _format_byte_rate(status.rates.upload_bytes_per_second)
        assert down in overview_text
        assert up in overview_text


async def test_overview_shows_grounded_warning_reasons_not_just_the_label() -> (
    None
):
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
        for alert in status.alerts:
            assert alert.message in overview_text
        # Never just the bare health word with no reasons attached.
        assert "finding(s)" in overview_text


async def test_overview_shows_zero_findings_when_healthy() -> None:
    client = FakeQbitClient(torrents=[make_torrent(state="uploading")])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)

        overview_text = _static_text(
            app.query_one("#overview-workspace", OverviewPanel)
        )
        assert "0 finding(s)" in overview_text


async def test_overview_shows_connection_and_nav_hint() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)

        overview_text = _static_text(
            app.query_one("#overview-workspace", OverviewPanel)
        )
        assert "connected" in overview_text
        assert "last successful refresh" in overview_text
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
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        tabs = app.query_one("#workspace-tabs", WorkspaceTabs)
        assert "reverse" in str(tabs.content)
        overview_reversed_content = str(tabs.content)
        assert "Overview" in overview_reversed_content

        await _goto_torrents(app, pilot)
        torrents_reversed_content = str(tabs.content)
        assert torrents_reversed_content != overview_reversed_content
        assert "reverse" in torrents_reversed_content


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
        details = app.query_one("#main > DetailsPanel", DetailsPanel)
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
        details = app.query_one("#main > DetailsPanel", DetailsPanel)
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

        details = app.query_one("#main > DetailsPanel", DetailsPanel)
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
        details = app.query_one("#main > DetailsPanel", DetailsPanel)
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

            if size[0] < 100:
                await pilot.press("enter")
                await _settle(app, pilot)
                assert isinstance(app.screen, DetailsScreen)
                rendered = _static_text(app.screen.query_one(DetailsPanel))
                assert "Alpha" in rendered
                await pilot.press("escape")
                await pilot.pause()
            else:
                details = app.query_one("#main > DetailsPanel", DetailsPanel)
                rendered = _static_text(details)
                assert "Alpha" in rendered


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

        details = app.query_one("#main > DetailsPanel", DetailsPanel)
        rendered = _static_text(details)
        assert "Alpha" in rendered
        # The hash is shortened for display -- the full hash is reachable
        # via Copy (see test_copy_hash_from_details), not printed here.
        assert torrent_hash[:8] in rendered
        assert torrent_hash not in rendered
        assert "uploading" in rendered
        assert "films" in rendered
        assert "Down:" in rendered and "Up:" in rendered
        assert "fetched" in rendered  # tracker-detail fetched timestamp
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
        overview_text = _static_text(
            app.query_one("#overview-workspace", OverviewPanel)
        )
        assert "Connection" in overview_text
        assert "Transfer" in overview_text
        assert "Activity" in overview_text
        assert "Completion" in overview_text
        assert "Attention" in overview_text
        assert "finding(s)" in overview_text


async def test_torrents_workspace_is_full_width_table_at_every_size() -> None:
    for size in RESPONSIVE_SIZES:
        client = FakeQbitClient(torrents=[make_torrent(name="Alpha")])
        app = _app(client)
        async with app.run_test(size=size) as pilot:
            await _settle(app, pilot)
            await _goto_torrents(app, pilot)
            table = app.query_one("#torrents", DataTable)
            assert table.row_count == 1


async def test_resize_wide_to_narrow_moves_focus_off_hidden_details_panel() -> (
    None
):
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")],
        trackers_by_hash={"a" * 40: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        app.query_one("#main > DetailsPanel", DetailsPanel).focus()
        await pilot.pause()

        await pilot.resize_terminal(*NARROW_SIZE)
        await pilot.pause()

        assert "narrow" in app.screen.classes
        assert app.focused is not None
        assert app.focused.id == "torrents"


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


async def test_wide_mode_adds_detail_panel_without_changing_semantics() -> None:
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
        details = app.query_one("#main > DetailsPanel", DetailsPanel)
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


async def test_focus_a_b_c_only_displays_c_details() -> None:
    hash_a, hash_b, hash_c = "a" * 40, "b" * 40, "c" * 40
    client = BlockingTrackerClient(
        torrents=[
            make_torrent(hash=hash_a, name="Alpha"),
            make_torrent(hash=hash_b, name="Beta"),
            make_torrent(hash=hash_c, name="Gamma"),
        ],
        trackers_by_hash={hash_a: [], hash_b: [], hash_c: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        worker_a = app._focus_torrent(hash_a)
        worker_b = app._focus_torrent(hash_b)
        worker_c = app._focus_torrent(hash_c)

        client.release_event(hash_c).set()
        await _settle_one(app, pilot, worker_c)
        assert app.controller.state.focused_tracker_details is not None

        client.release_event(hash_a).set()
        await _settle_one(app, pilot, worker_a)
        client.release_event(hash_b).set()
        await _settle_one(app, pilot, worker_b)

        assert app.controller.state.focused_hash == hash_c


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
        worker = app._focus_torrent(torrent_hash)

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
        first_worker = app._focus_torrent(torrent_hash)
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
    # "Sel" (the selection marker, TUI 2) is always present at every
    # width -- multi-selection must never lose its only visual
    # indicator just because the terminal is narrow.
    assert _columns_for_width(80) == ("Sel", "Name", "State", "Progress")
    assert _columns_for_width(99) == ("Sel", "Name", "State", "Progress")
    assert _columns_for_width(100) == (
        "Sel",
        "Name",
        "State",
        "Progress",
        "Down",
        "Up",
    )
    assert _columns_for_width(129) == (
        "Sel",
        "Name",
        "State",
        "Progress",
        "Down",
        "Up",
    )
    assert _columns_for_width(130) == (
        "Sel",
        "Name",
        "State",
        "Progress",
        "Down",
        "Up",
        "Ratio",
        "Category",
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
            labels = tuple(str(c.label) for c in table.columns.values())
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
        assert "1 seeding" in overview_text
        # Also 1 stopped (Activity's own, separate line).
        assert "1 stopped" in overview_text
        # Also 1 completed (Completion's own, separate dimension).
        assert "1 completed" in overview_text


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

        details = app.query_one("#main > DetailsPanel", DetailsPanel)
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
        # The automatic focus-triggered fetch is already blocked.
        await asyncio_wait_for_event(client.entered_event(torrent_hash))

        await pilot.press("e")
        await pilot.pause()

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
        await asyncio_wait_for_event(client.entered_event(hash_a))

        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, ExplainScreen)
        explain_screen = app.screen

        # Focus moves to B before A's fetch resolves.
        table = app.query_one("#torrents", DataTable)
        table.move_cursor(row=1)
        await pilot.pause()
        await asyncio_wait_for_event(client.entered_event(hash_b))

        # A's fetch now completes -- its result must never populate the
        # still-open Explain modal (which was opened for A).
        client.release_event(hash_a).set()
        await asyncio.sleep(0)
        await pilot.pause()

        content = str(
            explain_screen.query_one("#explain-content", Static).content
        )
        assert "Fetching tracker data" in content
        assert explain_screen.report is None

        client.release_event(hash_b).set()
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
        await asyncio_wait_for_event(client.entered_event(torrent_hash))

        await pilot.press("e")
        await pilot.pause()
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

        assert actions == {"show_torrents", "toggle_help", "quit"}
        for forbidden in (
            "copy_hash",
            "explain",
            "refresh_details",
            "focus_search",
            "open_filters",
            "show_overview",
        ):
            assert forbidden not in actions


async def test_focused_and_unfocused_torrents_footers_differ() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        focused_actions = _footer_actions(app)
        assert {"copy_hash", "explain", "refresh_details"} <= focused_actions

        await _type_into_search(pilot, "nonexistent-name")
        await pilot.press("escape")  # leave the search Input, back to the
        await pilot.pause()  # table -- Input itself hides ~all footer
        # entries while focused (it would consume every single-letter key
        # as text), which is a separate, pre-existing Textual behavior,
        # not what this test is about.

        unfocused_actions = _footer_actions(app)

        assert "copy_hash" not in unfocused_actions
        assert "explain" not in unfocused_actions
        assert "refresh_details" not in unfocused_actions
        # Search/Filters/Overview/Help/Quit remain regardless of focus.
        assert "focus_search" in unfocused_actions
        assert "open_filters" in unfocused_actions
        assert "show_overview" in unfocused_actions
        assert "quit" in unfocused_actions
        assert "toggle_help" in unfocused_actions


async def test_footer_never_shows_both_overview_and_torrents_at_once() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        overview_actions = _footer_actions(app)
        assert "show_torrents" in overview_actions
        assert "show_overview" not in overview_actions

        await _goto_torrents(app, pilot)
        torrents_actions = _footer_actions(app)
        assert "show_overview" in torrents_actions
        assert "show_torrents" not in torrents_actions


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

        # Patched at `qbit_ops.tui.widgets.overview`, not
        # `qbit_ops.tui.app`: the TUI reorg moved `OverviewPanel`'s
        # `_format_local_time` call there (its new canonical module),
        # so that is where the name is actually looked up at call time.
        with patch(
            "qbit_ops.tui.widgets.overview._format_local_time"
        ) as mocked:
            mocked.side_effect = (
                lambda moment, tz=None: f"stub-time {fixed_tz.tzname(moment)}"
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

        details = app.query_one("#main > DetailsPanel", DetailsPanel)
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

        # At WIDE_SIZE (not narrow), `action_activate` never pushes a
        # DetailsScreen -- it focuses the embedded DetailsPanel instead
        # (see `action_activate`'s docstring). The previous assertion
        # here (`isinstance(app.screen, DetailsScreen) or True`) could
        # never actually pass at this size; the `or True` masked that
        # permanently-vacuous check (F-10). Re-focus the torrents table
        # first: the prior FiltersScreen cancel left focus on its
        # now-unmounted Input widget.
        app.query_one("#torrents", DataTable).focus()
        await pilot.pause()
        await pilot.press("enter")
        await _settle(app, pilot)
        assert len(app.screen_stack) == 1
        assert isinstance(app.focused, DetailsPanel)
        await pilot.press("escape")
        await pilot.pause()

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
        row = table.get_row_at(0)
        # A heavier, bold+colored glyph -- easier to spot at a glance
        # than a plain unstyled "✓" (requested after dogfooding).
        assert "✔" in str(row[0])
        assert row[0].style == "bold green"


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
