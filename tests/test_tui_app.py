"""Textual `Pilot`-based interface tests for `qbit-ops tui` (TUI 1).

Headless (`App.run_test()`), no real terminal, no real qBittorrent --
every app under test is built with a `client_factory` returning a
`tests.support.FakeQbitClient`. State/refresh-budget assertions live in
`tests/test_tui_state.py`; this file covers what requires an actual
mounted widget tree (navigation, focus, layout, real key sequences) and
-- since the worker-hardening phase -- what requires real OS threads
(responsiveness, serialization, stale-result protection).

Hotfix regression tests (see docs/DECISIONS.md): these exercise full
user-observable event sequences through `Pilot`, not just isolated
`on_*`/`action_*` method calls -- the crash this phase fixes was never
caught by the previous test suite precisely because it only tested
methods directly with well-formed events.

`App.run_test()` defaults to an 80x24 terminal, which is *narrower*
than `NARROW_WIDTH_THRESHOLD` (100) -- i.e. every test that does not
pass an explicit wider `size=` is already exercising the narrow layout,
matching real-world "ordinary terminal size" dogfooding.

Worker-hardening tests (see docs/DECISIONS.md): every qBittorrent call
now runs on a real Textual thread worker, so a completed action is no
longer immediately reflected the instant `pilot.press()`/an action
method returns -- `_settle()` below awaits every in-flight worker
(`app.workers.wait_for_complete()`) and then pumps one more message
cycle. Blocking-client tests use real `threading.Event`s to control
exactly when a fake network call resolves -- never an arbitrary sleep.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from textual.binding import Binding
from textual.pilot import Pilot
from textual.widgets import DataTable, Input, Static
from textual.worker import Worker, WorkerState

from app.tui.app import (
    ConnectionBanner,
    DetailsPanel,
    DetailsScreen,
    FiltersPanel,
    FiltersScreen,
    FilterSummary,
    HelpScreen,
    QbitOpsTuiApp,
    StatusHeader,
    _format_byte_rate,
)
from app.tui.state import ConnectionState
from tests.support import FakeQbitClient, make_torrent

LARGE_INTERVAL = 999.0  # effectively disables the periodic timer mid-test
WIDE_SIZE = (140, 40)
NARROW_SIZE = (80, 24)
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


def _wait_for(event: threading.Event, timeout: float = WAIT_TIMEOUT) -> None:
    assert event.wait(timeout=timeout), "timed out waiting for a real event"


# --- 1. Reproduce before fixing / crash safety -----------------------------


async def test_row_highlighted_with_none_row_key_does_not_crash() -> None:
    """The exact reported crash: `RowHighlighted(cursor_row=-1, row_key=None)`.

    Dispatched directly to reproduce the precise message shape Textual's
    own `DataTable.RowHighlighted` type permits (`row_key: RowKey`, but
    `None` is a legal runtime value, e.g. on an empty table) --
    independent of exactly which real keystroke/resize sequence
    produces it in a given Textual version.
    """
    client = FakeQbitClient(torrents=[make_torrent(name="Alpha")])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        table = app.query_one("#torrents", DataTable)
        # Textual's own type stub declares `row_key: RowKey` (not
        # optional), but `None` is a legal runtime value -- this is
        # exactly the shape that caused the reported crash.
        event = DataTable.RowHighlighted(table, -1, None)  # type: ignore[arg-type]

        # Must not raise. Prior to the fix, this raised AttributeError:
        # 'NoneType' object has no attribute 'value'.
        app.on_data_table_row_highlighted(event)
        await pilot.pause()

        assert app.controller.state.focused_hash is None


async def test_cursor_row_negative_with_real_row_key_does_not_crash() -> None:
    """`cursor_row=-1` must be treated as "nothing highlighted" even if a
    `RowKey` object is somehow still attached."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        table = app.query_one("#torrents", DataTable)
        real_key = table._row_locations.get_key(0)
        assert real_key is not None
        event = DataTable.RowHighlighted(table, -1, real_key)

        app.on_data_table_row_highlighted(event)
        await pilot.pause()

        assert app.controller.state.focused_hash is None


async def test_filtering_to_zero_rows_clears_focus_and_details() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(name="Alpha", category="sonarr")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        assert app.controller.state.focused_hash is not None

        category_input = app.query_one("FiltersPanel .f-category", Input)
        category_input.focus()
        await pilot.press(*"nonexistent")
        await _settle(app, pilot)

        table = app.query_one("#torrents", DataTable)
        assert table.row_count == 0
        assert app.controller.state.focused_hash is None
        details = app.query_one("#main > DetailsPanel", DetailsPanel)
        assert "No torrent focused" in _static_text(details)


async def test_combining_two_filters_does_not_crash() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(
                hash="a" * 40, name="Alpha", category="films", state="stalledUP"
            ),
            make_torrent(
                hash="b" * 40, name="Beta", category="films", state="uploading"
            ),
            make_torrent(
                hash="c" * 40, name="Gamma", category="tv", state="stalledUP"
            ),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        category_input = app.query_one("FiltersPanel .f-category", Input)
        category_input.focus()
        await pilot.press(*"films")
        await _settle(app, pilot)

        state_input = app.query_one("FiltersPanel .f-state", Input)
        state_input.focus()
        await pilot.press(*"stalled")
        await _settle(app, pilot)

        assert app.controller.state.visible is not None
        assert [t.name for t in app.controller.state.visible.matched] == [
            "Alpha"
        ]


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
        category_input = app.query_one("FiltersPanel .f-category", Input)
        category_input.focus()
        await pilot.press(*"films")
        await _settle(app, pilot)
        assert app.query_one("#torrents", DataTable).row_count == 1

        for _ in range(len("films")):
            await pilot.press("backspace")
        await _settle(app, pilot)

        table = app.query_one("#torrents", DataTable)
        assert table.row_count == 2
        assert app.controller.state.visible is not None
        assert len(app.controller.state.visible.matched) == 2


async def test_stale_focused_details_ignored_after_row_disappears() -> None:
    """A tracker-details result for a torrent that has since disappeared
    from the snapshot must never be shown as if it belonged to the
    (possibly different) newly focused torrent."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")],
        trackers_by_hash={"a" * 40: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        assert app.controller.state.focused_hash == "a" * 40
        assert app.controller.state.focused_tracker_details == []

        client.torrents = []
        app._start_periodic_refresh()
        await _settle(app, pilot)

        assert app.controller.state.focused_hash is None
        assert app.controller.state.focused_tracker_details is None
        details = app.query_one("#main > DetailsPanel", DetailsPanel)
        assert "No torrent focused" in _static_text(details)


# --- 2. Bindings ------------------------------------------------------------


async def test_slash_opens_a_visible_search_input_with_table_focused() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await pilot.press("slash")
        await pilot.pause()

        search = app.query_one("#search-input", Input)
        assert search.has_focus


async def test_f_opens_visible_filters_at_wide_width() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await pilot.press("f")
        await pilot.pause()

        assert app.focused is not None
        assert "f-category" in app.focused.classes


async def test_f_opens_filters_modal_at_narrow_width() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=NARROW_SIZE) as pilot:
        await _settle(app, pilot)
        await pilot.press("f")
        await pilot.pause()

        assert isinstance(app.screen, FiltersScreen)
        # The modal's own FiltersPanel must be usable (visible, focusable).
        panel = app.screen.query_one(FiltersPanel)
        assert panel.display


async def test_help_opens_and_closes() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await pilot.press("question_mark")
        await pilot.pause()

        assert isinstance(app.screen, HelpScreen)

        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, HelpScreen)
        assert len(app.screen_stack) == 1


async def test_help_only_lists_working_bindings() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await pilot.press("question_mark")
        await pilot.pause()

        help_text = _static_text(app.screen.query_one("#help-dialog"))
        for token in ("/", "f", "enter", "r", "esc", "q"):
            assert token in help_text
        assert "palette" not in help_text.lower()


async def test_r_with_no_focus_is_safe() -> None:
    client = FakeQbitClient(torrents=[])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        assert app.controller.state.focused_hash is None

        await pilot.press("r")
        await _settle(app, pilot)

        # Must not raise, and must not fabricate a call.
        assert client.torrents_trackers_calls == 0


async def test_r_refreshes_focused_details() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40)],
        trackers_by_hash={"a" * 40: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        calls_before = client.torrents_trackers_calls

        await pilot.press("r")
        await _settle(app, pilot)

        assert client.torrents_trackers_calls == calls_before + 1


async def test_q_exits_from_the_torrent_table() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await pilot.press("q")
        await pilot.pause()

        assert app._exit is True


async def test_q_exits_from_the_details_panel() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await pilot.press(
            "enter"
        )  # wide mode: focuses the inline details panel
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()

        assert app._exit is True


async def test_q_types_literally_while_editing_a_filter_text_input() -> None:
    """Documented exception, not a bug: while actively editing text, `q`
    must be inserted as a character (a category could legitimately be
    named "queue") rather than quit the application -- see
    docs/DECISIONS.md and the `q` binding's docstring."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await pilot.press("f")
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()

        assert app._exit is False
        category_input = app.query_one("FiltersPanel .f-category", Input)
        assert category_input.value == "q"

        await pilot.press("escape")
        await pilot.press("q")
        await pilot.pause()
        assert app._exit is True


async def test_escape_returns_focus_from_search_to_table() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await pilot.press("slash")
        await pilot.pause()
        assert app.query_one("#search-input", Input).has_focus

        await pilot.press("escape")
        await pilot.pause()

        assert app.query("#search-input").__len__() == 0
        table = app.query_one("#torrents", DataTable)
        assert table.has_focus


async def test_escape_closes_filters_modal() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=NARROW_SIZE) as pilot:
        await _settle(app, pilot)
        await pilot.press("f")
        await pilot.pause()
        assert isinstance(app.screen, FiltersScreen)

        await pilot.press("escape")
        await pilot.pause()

        assert len(app.screen_stack) == 1


async def test_command_palette_is_disabled_and_absent() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    assert QbitOpsTuiApp.ENABLE_COMMAND_PALETTE is False

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        assert app.use_command_palette is False

        await pilot.press("ctrl+p")
        await pilot.pause()

        # No command palette screen should ever appear.
        assert len(app.screen_stack) == 1


# --- 3. Narrow layout --------------------------------------------------------


async def test_narrow_layout_hides_inline_filters_and_details() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=NARROW_SIZE) as pilot:
        await _settle(app, pilot)
        assert "narrow" in app.screen.classes
        filters = app.query_one("#main > FiltersPanel", FiltersPanel)
        details = app.query_one("#main > DetailsPanel", DetailsPanel)
        assert not filters.display
        assert not details.display


async def test_narrow_layout_retains_filter_access() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", category="films"),
            make_torrent(hash="b" * 40, name="Beta", category="tv"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=NARROW_SIZE) as pilot:
        await _settle(app, pilot)
        await pilot.press("f")
        await pilot.pause()

        modal_input = app.screen.query_one("FiltersPanel .f-category", Input)
        modal_input.focus()
        await pilot.press(*"films")
        await _settle(app, pilot)

        assert app.controller.state.visible is not None
        assert [t.name for t in app.controller.state.visible.matched] == [
            "Alpha"
        ]


async def test_narrow_layout_retains_detail_access() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")],
        trackers_by_hash={"a" * 40: []},
    )
    app = _app(client)

    async with app.run_test(size=NARROW_SIZE) as pilot:
        await _settle(app, pilot)
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, DetailsScreen)
        rendered = _static_text(app.screen.query_one(DetailsPanel))
        assert "Alpha" in rendered

        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1


async def test_resize_wide_to_narrow_moves_focus_off_hidden_widget() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await pilot.press("f")
        await pilot.pause()
        assert "f-category" in (app.focused.classes if app.focused else set())

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
        await pilot.resize_terminal(*WIDE_SIZE)
        await pilot.pause()
        assert "narrow" not in app.screen.classes

        await pilot.resize_terminal(*NARROW_SIZE)
        await pilot.pause()
        assert "narrow" in app.screen.classes

        # No extra API calls from resizing alone.
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


# --- 4. Search/filter correctness -------------------------------------------


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
        summary = app.query_one("#filter-summary", FilterSummary)
        assert "2 shown / 2" in str(summary.content)

        category_input = app.query_one("FiltersPanel .f-category", Input)
        category_input.focus()
        await pilot.press(*"films")
        await _settle(app, pilot)

        summary_text = str(summary.content)
        assert "1 shown / 2" in summary_text
        assert "films" in summary_text


async def test_filter_and_search_changes_perform_zero_api_calls() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Debian ISO", category="films"),
            make_torrent(hash="b" * 40, name="Ubuntu ISO", category="tv"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        scans_before = (
            client.torrents_info_calls,
            client.transfer_info_calls,
            client.app_version_calls,
            client.app_web_api_version_calls,
        )

        category_input = app.query_one("FiltersPanel .f-category", Input)
        category_input.focus()
        await pilot.press(*"films")
        await _settle(app, pilot)
        await pilot.press("slash")
        await pilot.press(*"debian")
        await pilot.press("enter")
        await _settle(app, pilot)

        scans_after = (
            client.torrents_info_calls,
            client.transfer_info_calls,
            client.app_version_calls,
            client.app_web_api_version_calls,
        )
        assert scans_after == scans_before


async def test_periodic_refresh_api_budget_unchanged() -> None:
    """Deterministic: drive several ticks manually, awaiting each
    worker, so the assertion cannot flake against thread scheduling."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        # One legitimate tracker call from the initial auto-focused row.
        tracker_calls_after_mount = client.torrents_trackers_calls

        for _ in range(3):
            app._start_periodic_refresh()
            await _settle(app, pilot)

        assert client.torrents_trackers_calls == tracker_calls_after_mount
        # One call each per tick, however many ticks fired.
        assert client.torrents_info_calls == client.app_version_calls
        assert client.torrents_info_calls == client.transfer_info_calls
        assert client.torrents_info_calls == client.app_web_api_version_calls
        assert client.torrents_info_calls == 4  # initial + 3 manual ticks


# --- 5. Existing behavior retained ------------------------------------------


async def test_application_launches_and_shows_status() -> None:
    client = FakeQbitClient(torrents=[make_torrent(name="Alpha")])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        header = app.query_one("#status-header", StatusHeader)
        assert "healthy" in str(header.content).lower()


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
        first_focus = app.controller.state.focused_hash

        await pilot.press("down")
        await _settle(app, pilot)

        assert app.controller.state.focused_hash != first_focus
        details = app.query_one("#main > DetailsPanel", DetailsPanel)
        rendered = _static_text(details)
        focused_torrent = app.controller.state.focused_torrent()
        assert focused_torrent is not None
        assert focused_torrent.name in rendered


async def test_unavailable_banner_shown_while_table_data_retained() -> None:
    client = FakeQbitClient(torrents=[make_torrent(name="Alpha")])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        table = app.query_one("#torrents", DataTable)
        assert table.row_count == 1

        def _boom() -> Any:
            raise ConnectionError("connection lost")

        client.torrents_info = _boom  # type: ignore[method-assign]
        app._start_periodic_refresh()
        await _settle(app, pilot)

        banner = app.query_one("#banner", ConnectionBanner)
        assert "visible" in banner.classes
        assert table.row_count == 1
        assert app.controller.state.stale is True


def test_no_mutation_binding_is_reachable() -> None:
    mutation_keywords = {
        "pause",
        "resume",
        "start",
        "reannounce",
        "remove",
        "replace",
        "apply",
        "mutate",
        "delete",
    }
    for binding in QbitOpsTuiApp.BINDINGS:
        action = binding.action if isinstance(binding, Binding) else binding[1]
        assert not any(
            keyword in action.lower() for keyword in mutation_keywords
        ), f"binding action {action!r} looks like a mutation"


async def test_no_tracker_secrets_appear_in_details() -> None:
    torrent_hash = "a" * 40
    secret_url = "https://tracker.example/announce/TOPSECRET?passkey=abc"
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={torrent_hash: [{"url": secret_url, "status": 2}]},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        details = app.query_one("#main > DetailsPanel", DetailsPanel)
        rendered = _static_text(details)
        assert "TOPSECRET" not in rendered
        assert "passkey=abc" not in rendered
        assert secret_url not in rendered


def test_format_byte_rate_matches_expected_units() -> None:
    assert _format_byte_rate(0) == "0 B/s"
    assert _format_byte_rate(2048) == "2.0 KiB/s"


# --- 6. Worker hardening: responsiveness ------------------------------------


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
        await pilot.press("slash")
        await pilot.pause()
        assert app.query_one("#search-input", Input).has_focus
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("f")
        await pilot.pause()
        assert app.focused is not None
        assert "f-category" in app.focused.classes
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

        # Start a second, slow refresh and filter/search while it blocks.
        client.release.clear()
        client.entered.clear()
        app._start_periodic_refresh()
        await asyncio_wait_for_event(client.entered)

        category_input = app.query_one("FiltersPanel .f-category", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()

        assert app.controller.state.visible is not None
        assert [t.name for t in app.controller.state.visible.matched] == [
            "Debian ISO"
        ]

        client.release.set()
        await _settle(app, pilot)


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


# --- 7. Worker hardening: serialization -------------------------------------


async def test_periodic_refreshes_never_overlap() -> None:
    client = BlockingClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await asyncio_wait_for_event(client.entered)
        assert client.entry_count == 1

        # A second tick firing while the first is still in flight must
        # not start a second call.
        app._start_periodic_refresh()
        app._start_periodic_refresh()
        await pilot.pause()

        assert client.entry_count == 1

        client.release.set()
        await _settle(app, pilot)
        assert client.entry_count == 1
        assert client.torrents_info_calls == 1


async def test_skipped_tick_is_deterministic_and_cadence_continues() -> None:
    client = BlockingClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await asyncio_wait_for_event(client.entered)

        # Several ticks fire while blocked -- all skipped, none queued.
        for _ in range(5):
            app._start_periodic_refresh()
        await pilot.pause()
        assert client.entry_count == 1

        client.release.set()
        await _settle(app, pilot)
        assert client.entry_count == 1
        assert client.torrents_info_calls == 1

        # Normal cadence resumes: the next tick starts a fresh call.
        client.release.clear()
        client.entered.clear()
        app._start_periodic_refresh()
        await asyncio_wait_for_event(client.entered)
        assert client.entry_count == 2
        client.release.set()
        await _settle(app, pilot)
        assert client.torrents_info_calls == 2


# --- 8. Worker hardening: stale-result protection ---------------------------


async def test_late_periodic_result_is_ignored_after_shutdown() -> None:
    client = BlockingClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await asyncio_wait_for_event(client.entered)
        status_before = app.controller.state.status

        app.exit()
        await pilot.pause()
        assert app.is_running is False

        # Craft the exact message the worker would eventually post, and
        # deliver it directly -- proving the handler's own guard (not
        # incidental message-queue timing) is what prevents the mutation.
        assert app._refresh_worker is not None
        event = Worker.StateChanged(app._refresh_worker, WorkerState.SUCCESS)
        app.on_worker_state_changed(event)

        assert app.controller.state.status is status_before

        client.release.set()  # let the real thread finish, don't leak it


async def test_focus_a_b_c_only_displays_c_details() -> None:
    hash_a, hash_b, hash_c = "a" * 40, "b" * 40, "c" * 40
    client = BlockingTrackerClient(
        torrents=[
            make_torrent(hash=hash_a, name="Alpha"),
            make_torrent(hash=hash_b, name="Beta"),
            make_torrent(hash=hash_c, name="Gamma"),
        ],
        trackers_by_hash={
            hash_a: [{"url": "https://a.example/announce", "status": 2}],
            hash_b: [{"url": "https://b.example/announce", "status": 2}],
            hash_c: [{"url": "https://c.example/announce", "status": 2}],
        },
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)  # initial refresh + auto-focus on Alpha

        # The initial mount auto-focused Alpha and dispatched its own
        # detail fetch (not captured above) -- resolve it cleanly before
        # starting the controlled B -> C sequence below, so it never
        # holds a thread-pool slot indefinitely.
        await asyncio_wait_for_event(client.entered_event(hash_a))
        client.release_event(hash_a).set()
        await _settle(app, pilot)

        worker_b = app._focus_torrent(hash_b)
        await asyncio_wait_for_event(client.entered_event(hash_b))
        worker_c = app._focus_torrent(hash_c)
        await asyncio_wait_for_event(client.entered_event(hash_c))
        assert worker_b is not None
        assert worker_c is not None

        # Release out of order: C's own move (begin_focus_change) has
        # already invalidated B's request id, so B's result is discarded
        # the instant it arrives, regardless of arrival order -- release
        # B first (still discarded) then C (applies), each awaited
        # individually (`_settle_one`, not `_settle`), since the other
        # is still deliberately blocked.
        client.release_event(hash_b).set()
        await _settle_one(app, pilot, worker_b)
        discarded_details = app.controller.state.focused_tracker_details
        assert discarded_details is None

        client.release_event(hash_c).set()
        await _settle_one(app, pilot, worker_c)
        details = app.controller.state.focused_tracker_details
        assert details is not None
        assert any(endpoint["tracker"] == "c.example" for endpoint in details)


async def test_clearing_focus_ignores_pending_detail_result() -> None:
    torrent_hash = "a" * 40
    client = BlockingTrackerClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={
            torrent_hash: [{"url": "https://a.example/announce", "status": 2}]
        },
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await asyncio_wait_for_event(client.entered_event(torrent_hash))

        app.controller.clear_focus()
        app._render_details_panels()

        client.release_event(torrent_hash).set()
        await _settle(app, pilot)

        assert app.controller.state.focused_hash is None
        assert app.controller.state.focused_tracker_details is None


async def test_manual_refresh_wins_over_an_earlier_slower_request() -> None:
    torrent_hash = "a" * 40
    old_event = threading.Event()
    new_event = threading.Event()
    old_payload = [{"url": "https://old.example/announce", "status": 2}]
    new_payload = [{"url": "https://new.example/announce", "status": 2}]
    client = OrderedTrackerClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        responses=[(old_event, old_payload), (new_event, new_payload)],
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)  # dispatches call index 0 (auto-focus)
        await asyncio_wait_for_event(client.entered[0])

        # Manual refresh dispatches call index 1 before index 0 resolves.
        manual_worker = app.action_refresh_details()
        assert manual_worker is not None
        await asyncio_wait_for_event(client.entered[1])

        # The newer (manual) request completes first -- await only its
        # own worker; call index 0 (the automatic fetch) is still
        # deliberately blocked on `old_event`.
        new_event.set()
        await _settle_one(app, pilot, manual_worker)
        details = app.controller.state.focused_tracker_details
        assert details is not None
        assert any(e["tracker"] == "new.example" for e in details)

        # The older (automatic) request completes after -- must not
        # overwrite the manual result. `_settle` is safe here: it is
        # the only worker left in flight.
        old_event.set()
        await _settle(app, pilot)
        details = app.controller.state.focused_tracker_details
        assert details is not None
        assert any(e["tracker"] == "new.example" for e in details)
        assert not any(e["tracker"] == "old.example" for e in details)


# --- 9. Worker hardening: failure states ------------------------------------


async def test_connection_failure_produces_stale_state() -> None:
    client = FakeQbitClient(torrents=[make_torrent(name="Alpha")])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        assert app.controller.state.stale is False

        def _boom() -> Any:
            raise ConnectionError("connection lost")

        client.torrents_info = _boom  # type: ignore[method-assign]
        app._start_periodic_refresh()
        await _settle(app, pilot)

        assert app.controller.state.stale is True
        assert app.controller.state.connection is ConnectionState.RECONNECTING
        assert app.controller.state.torrent_snapshot is not None


async def test_recovery_clears_stale() -> None:
    client = FakeQbitClient(torrents=[make_torrent(name="Alpha")])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        original_torrents_info = client.torrents_info

        def _boom() -> Any:
            raise ConnectionError("connection lost")

        client.torrents_info = _boom  # type: ignore[method-assign]
        app._start_periodic_refresh()
        await _settle(app, pilot)
        assert app.controller.state.stale is True

        client.torrents_info = original_torrents_info
        app._start_periodic_refresh()
        await _settle(app, pilot)

        assert app.controller.state.stale is False
        assert app.controller.state.connection is ConnectionState.CONNECTED


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
        # Must not be classified as a recoverable connection issue.
        assert app.controller.state.connection != ConnectionState.RECONNECTING


# --- 10. Search acceptance contract ------------------------------------------
#
# `/` used to mount a real `Input` but Enter never worked: the App's own
# `enter` binding (`action_open_details`) is `priority=True`, which wins
# key resolution *before* the focused `Input`'s own declarative `enter`
# -> `submit` binding is ever considered (Textual dispatch order,
# verified empirically -- see `action_open_details`'s docstring). Typed
# characters were never affected (`Input._on_key` consumes printable
# characters directly, bypassing bindings resolution), but the search
# text was never actually applied because `on_input_submitted` -- the
# only place that called `TuiController.set_search` -- was unreachable.
# Fixed by filtering live on every keystroke (`on_input_changed`) and
# having `action_open_details` special-case the search input's `enter`
# to simply return focus to the table. See docs/DECISIONS.md.


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


async def test_slash_focuses_a_visible_editable_input_from_the_table() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        table = app.query_one("#torrents", DataTable)
        assert table.has_focus

        await pilot.press("slash")
        await pilot.pause()

        search = app.query_one("#search-input", Input)
        assert isinstance(search, Input)
        assert search.has_focus


async def test_typed_characters_appear_in_the_search_input() -> None:
    client = FakeQbitClient(torrents=[make_torrent(name="Ubuntu ISO")])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
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
        await _type_into_search(pilot, "UBUNTU")

        assert _visible_names(app) == ["Ubuntu ISO"]
        table = app.query_one("#torrents", DataTable)
        assert table.row_count == 1


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
        await _type_into_search(pilot, "DEADBEEF")

        assert _visible_names(app) == ["Debian ISO"]


async def test_search_ignores_non_leading_hash_substring() -> None:
    """Hashes use leading-prefix matching only, never a full substring
    scan -- typing a fragment from the middle of a hash must not match."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="00000000deadbeef" + "0" * 24, name="Alpha")
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _type_into_search(pilot, "deadbeef")

        assert _visible_names(app) == []


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
        category_input = app.query_one("FiltersPanel .f-category", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()
        await pilot.press("escape")  # back to the table before opening search
        await pilot.pause()

        await _type_into_search(pilot, "debian")

        assert _visible_names(app) == ["Debian ISO"]


async def test_zero_result_search_does_not_crash() -> None:
    client = FakeQbitClient(torrents=[make_torrent(name="Debian ISO")])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _type_into_search(pilot, "nonexistent-torrent-name")

        assert _visible_names(app) == []
        table = app.query_one("#torrents", DataTable)
        assert table.row_count == 0

        # The app is still alive and processing input afterwards.
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
        await _type_into_search(pilot, "debian")
        assert _visible_names(app) == ["Debian ISO"]

        search = app.query_one("#search-input", Input)
        search.focus()
        await pilot.press("ctrl+u")
        await pilot.pause()

        assert search.value == ""
        assert sorted(_visible_names(app)) == ["Debian ISO", "Ubuntu ISO"]
        table = app.query_one("#torrents", DataTable)
        assert table.row_count == 2


async def test_search_performs_zero_qbittorrent_api_calls() -> None:
    """Typing, applying, and clearing search must never itself call the
    qBittorrent client -- `torrents_info`/`transfer_info`/`app_version`/
    `app_web_api_version` (the periodic refresh budget) stay exactly as
    they were. `torrents_trackers` is deliberately excluded from this
    comparison: narrowing the table can move the row cursor, and
    focus-change detail fetches are a separate, already-covered concern
    (see `test_filter_and_search_changes_perform_zero_api_calls`).
    """
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Debian ISO"),
            make_torrent(hash="b" * 40, name="Ubuntu ISO"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
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
        await pilot.press("slash")
        await pilot.pause()
        search = app.query_one("#search-input", Input)

        await pilot.press("q", "f", "r", "question_mark")
        await pilot.pause()

        assert search.value == "qfr?"
        assert app._exit is False
        assert len(app.screen_stack) == 1
        assert search.has_focus


async def test_search_remains_available_in_narrow_layout() -> None:
    client = FakeQbitClient(torrents=[make_torrent(name="Ubuntu ISO")])
    app = _app(client)

    async with app.run_test(size=NARROW_SIZE) as pilot:
        await _settle(app, pilot)
        assert "narrow" in app.screen.classes

        await pilot.press("slash")
        await pilot.pause()

        search = app.query_one("#search-input", Input)
        assert search.has_focus
        await pilot.press("u", "b", "u")
        await pilot.pause()

        assert search.value == "ubu"
        assert _visible_names(app) == ["Ubuntu ISO"]


async def test_search_hiding_focused_torrent_clears_focus_and_details() -> None:
    """Search narrows to zero matches, so the table stays empty and no
    subsequent `RowHighlighted` re-focuses a different, still-visible
    torrent -- isolates the invalidation itself from the DataTable's
    own row-0-autoselect behavior (covered separately by
    `test_zero_result_search_does_not_crash`)."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Debian ISO")],
        trackers_by_hash={"a" * 40: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        table = app.query_one("#torrents", DataTable)
        table.move_cursor(row=0)
        await _settle(app, pilot)
        assert app.controller.state.focused_hash == "a" * 40

        await _type_into_search(pilot, "nonexistent-torrent-name")

        assert app.controller.state.focused_hash is None
        assert app.controller.state.focused_tracker_details is None
        assert table.row_count == 0
        details = app.query_one("#main > DetailsPanel", DetailsPanel)
        assert "No torrent focused" in _static_text(details)


async def test_enter_in_search_keeps_text_and_returns_focus_to_table() -> None:
    client = FakeQbitClient(torrents=[make_torrent(name="Ubuntu ISO")])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _type_into_search(pilot, "ubuntu")

        await pilot.press("enter")
        await pilot.pause()

        table = app.query_one("#torrents", DataTable)
        assert table.has_focus
        search = app.query_one("#search-input", Input)
        assert search.value == "ubuntu"
        assert app.controller.state.search == "ubuntu"
        # Details never opened for the search's `enter`.
        assert len(app.screen_stack) == 1


async def test_escape_leaves_search_editing_without_crashing() -> None:
    client = FakeQbitClient(torrents=[make_torrent(name="Ubuntu ISO")])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _type_into_search(pilot, "ubuntu")

        await pilot.press("escape")
        await pilot.pause()

        assert len(app.query("#search-input")) == 0
        table = app.query_one("#torrents", DataTable)
        assert table.has_focus
        # App still alive and responsive afterwards.
        await pilot.press("j")
        await pilot.pause()
