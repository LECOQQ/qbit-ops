"""Textual `Pilot`-based interface tests for `qbit-ops tui` (TUI 1).

Headless (`App.run_test()`), no real terminal, no real qBittorrent --
every app under test is built with a `client_factory` returning a
`tests.support.FakeQbitClient`. State/refresh-budget assertions live in
`tests/test_tui_state.py`; this file only covers what requires an
actual mounted widget tree (navigation, focus, rendered content).
"""

from __future__ import annotations

from typing import Any

from textual.binding import Binding
from textual.widgets import DataTable, Input, Static

from app.tui.app import (
    ConnectionBanner,
    DetailsPanel,
    QbitOpsTuiApp,
    StatusHeader,
    _format_byte_rate,
)
from tests.support import FakeQbitClient, make_torrent

LARGE_INTERVAL = 999.0  # effectively disables the periodic timer mid-test


def _app(client: FakeQbitClient) -> QbitOpsTuiApp:
    return QbitOpsTuiApp(
        client_factory=lambda: client,
        host="http://localhost:8080",
        refresh_interval=LARGE_INTERVAL,
    )


async def test_application_launches_and_shows_status() -> None:
    client = FakeQbitClient(torrents=[make_torrent(name="Alpha")])
    app = _app(client)

    async with app.run_test() as pilot:
        await pilot.pause()
        header = app.query_one("#status-header", StatusHeader)
        assert "healthy" in str(header.content).lower()


async def test_clean_quit_with_q() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.is_running is True
        await pilot.press("q")
        await pilot.pause()

        # `_exit` is set as soon as a quit is requested; the process
        # only fully stops once this `async with` block ends -- this
        # asserts the request was accepted without raising, which is
        # what "clean q exit" means for a headless test.
        assert app._exit is True


async def test_keyboard_navigation_moves_focus_and_updates_details() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha"),
            make_torrent(hash="b" * 40, name="Beta"),
        ],
        trackers_by_hash={"a" * 40: [], "b" * 40: []},
    )
    app = _app(client)

    async with app.run_test() as pilot:
        await pilot.pause()
        first_focus = app.controller.state.focused_hash

        await pilot.press("down")
        await pilot.pause()

        assert app.controller.state.focused_hash != first_focus
        details = app.query_one("#details", DetailsPanel)
        rendered = "\n".join(
            str(child.content)
            for child in details.children
            if isinstance(child, Static)
        )
        focused_torrent = app.controller.state.focused_torrent()
        assert focused_torrent is not None
        assert focused_torrent.name in rendered


async def test_pressing_f_focuses_the_category_filter_input() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause()

        assert app.focused is not None
        assert app.focused.id == "filter-category"


async def test_filter_interaction_narrows_table_without_extra_scans() -> None:
    """Filtering must never trigger a new `torrents_info()`/`transfer_info()`
    call. It legitimately *can* trigger one new `torrents_trackers()` call:
    typing narrows through empty intermediate states (no category is named
    just "s" or "so"), which clears focus, and the table re-highlighting its
    new first row once "sonarr" matches again is a genuine new focus event
    -- not a periodic rescan. The precise "filtering itself is zero API
    calls" guarantee is verified directly at the state layer in
    `tests/test_tui_state.py` without this UI-driven refocus artifact.
    """
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", category="sonarr"),
            make_torrent(hash="b" * 40, name="Beta", category="radarr"),
        ]
    )
    app = _app(client)

    async with app.run_test() as pilot:
        await pilot.pause()
        scans_before = (
            client.torrents_info_calls,
            client.transfer_info_calls,
            client.app_version_calls,
            client.app_web_api_version_calls,
        )

        category_input = app.query_one("#filter-category", Input)
        category_input.focus()
        await pilot.press(*"sonarr")
        await pilot.pause()

        scans_after = (
            client.torrents_info_calls,
            client.transfer_info_calls,
            client.app_version_calls,
            client.app_web_api_version_calls,
        )
        assert scans_after == scans_before
        assert app.controller.state.visible is not None
        assert [t.name for t in app.controller.state.visible.matched] == [
            "Alpha"
        ]


async def test_search_interaction_narrows_table() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Debian ISO"),
            make_torrent(hash="b" * 40, name="Ubuntu ISO"),
        ]
    )
    app = _app(client)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        await pilot.press(*"debian")
        await pilot.press("enter")
        await pilot.pause()

        assert app.controller.state.visible is not None
        assert [t.name for t in app.controller.state.visible.matched] == [
            "Debian ISO"
        ]


async def test_empty_filter_result_shows_no_rows() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(name="Alpha", category="sonarr")]
    )
    app = _app(client)

    async with app.run_test() as pilot:
        await pilot.pause()
        category_input = app.query_one("#filter-category", Input)
        category_input.focus()
        await pilot.press(*"nonexistent")
        await pilot.pause()

        table = app.query_one("#torrents", DataTable)
        assert table.row_count == 0


async def test_unavailable_banner_shown_while_table_data_retained() -> None:
    client = FakeQbitClient(torrents=[make_torrent(name="Alpha")])
    app = _app(client)

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#torrents", DataTable)
        assert table.row_count == 1

        # Simulate a connection drop discovered on the next tick.
        def _boom() -> Any:
            raise ConnectionError("connection lost")

        client.torrents_info = _boom  # type: ignore[method-assign]
        app._on_tick()
        await pilot.pause()

        banner = app.query_one("#banner", ConnectionBanner)
        assert "visible" in banner.classes
        # Stale data must remain visible underneath the banner.
        assert table.row_count == 1
        assert app.controller.state.stale is True


async def test_narrow_terminal_collapses_side_panels() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=(60, 24)) as pilot:
        await pilot.pause()
        assert "narrow" in app.screen.classes


async def test_wide_terminal_shows_all_panels() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=(140, 24)) as pilot:
        await pilot.pause()
        assert "narrow" not in app.screen.classes


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


def test_format_byte_rate_matches_expected_units() -> None:
    assert _format_byte_rate(0) == "0 B/s"
    assert _format_byte_rate(2048) == "2.0 KiB/s"
