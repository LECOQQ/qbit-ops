"""Textual `Pilot`-based interface tests for `qbit-ops tui` (TUI 1).

Headless (`App.run_test()`), no real terminal, no real qBittorrent --
every app under test is built with a `client_factory` returning a
`tests.support.FakeQbitClient`. State/refresh-budget assertions live in
`tests/test_tui_state.py`; this file covers what requires an actual
mounted widget tree (navigation, focus, layout, real key sequences).

Hotfix regression tests (see docs/DECISIONS.md): these exercise full
user-observable event sequences through `Pilot`, not just isolated
`on_*`/`action_*` method calls -- the crash this phase fixes was never
caught by the previous test suite precisely because it only tested
methods directly with well-formed events.

`App.run_test()` defaults to an 80x24 terminal, which is *narrower*
than `NARROW_WIDTH_THRESHOLD` (100) -- i.e. every test that does not
pass an explicit wider `size=` is already exercising the narrow layout,
matching real-world "ordinary terminal size" dogfooding.
"""

from __future__ import annotations

from typing import Any

from textual.binding import Binding
from textual.widgets import DataTable, Input, Static

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
from tests.support import FakeQbitClient, make_torrent

LARGE_INTERVAL = 999.0  # effectively disables the periodic timer mid-test
WIDE_SIZE = (140, 40)
NARROW_SIZE = (80, 24)


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
        await pilot.pause()
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
        await pilot.pause()
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
        await pilot.pause()
        assert app.controller.state.focused_hash is not None

        category_input = app.query_one("FiltersPanel .f-category", Input)
        category_input.focus()
        await pilot.press(*"nonexistent")
        await pilot.pause()

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
        await pilot.pause()
        category_input = app.query_one("FiltersPanel .f-category", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()

        state_input = app.query_one("FiltersPanel .f-state", Input)
        state_input.focus()
        await pilot.press(*"stalled")
        await pilot.pause()

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
        await pilot.pause()
        category_input = app.query_one("FiltersPanel .f-category", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()
        assert app.query_one("#torrents", DataTable).row_count == 1

        for _ in range(len("films")):
            await pilot.press("backspace")
        await pilot.pause()

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
        await pilot.pause()
        assert app.controller.state.focused_hash == "a" * 40
        assert app.controller.state.focused_tracker_details == []

        client.torrents = []
        app._on_tick()
        await pilot.pause()

        assert app.controller.state.focused_hash is None
        assert app.controller.state.focused_tracker_details is None
        details = app.query_one("#main > DetailsPanel", DetailsPanel)
        assert "No torrent focused" in _static_text(details)


# --- 2. Bindings ------------------------------------------------------------


async def test_slash_opens_a_visible_search_input_with_table_focused() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()

        search = app.query_one("#search-input", Input)
        assert search.has_focus


async def test_f_opens_visible_filters_at_wide_width() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause()

        assert app.focused is not None
        assert "f-category" in app.focused.classes


async def test_f_opens_filters_modal_at_narrow_width() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=NARROW_SIZE) as pilot:
        await pilot.pause()
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
        await pilot.pause()
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
        await pilot.pause()
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
        await pilot.pause()
        assert app.controller.state.focused_hash is None

        await pilot.press("r")
        await pilot.pause()

        # Must not raise, and must not fabricate a call.
        assert client.torrents_trackers_calls == 0


async def test_r_refreshes_focused_details() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40)],
        trackers_by_hash={"a" * 40: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await pilot.pause()
        calls_before = client.torrents_trackers_calls

        await pilot.press("r")
        await pilot.pause()

        assert client.torrents_trackers_calls == calls_before + 1


async def test_q_exits_from_the_torrent_table() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()

        assert app._exit is True


async def test_q_exits_from_the_details_panel() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await pilot.pause()
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
        await pilot.pause()
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
        await pilot.pause()
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
        await pilot.pause()
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
        await pilot.pause()
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
        await pilot.pause()
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
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause()

        modal_input = app.screen.query_one("FiltersPanel .f-category", Input)
        modal_input.focus()
        await pilot.press(*"films")
        await pilot.pause()

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
        await pilot.pause()
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
        await pilot.pause()
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
        await pilot.pause()
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
        await pilot.pause()
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
        await pilot.pause()
        summary = app.query_one("#filter-summary", FilterSummary)
        assert "2 shown / 2" in str(summary.content)

        category_input = app.query_one("FiltersPanel .f-category", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()

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
        await pilot.pause()
        scans_before = (
            client.torrents_info_calls,
            client.transfer_info_calls,
            client.app_version_calls,
            client.app_web_api_version_calls,
        )

        category_input = app.query_one("FiltersPanel .f-category", Input)
        category_input.focus()
        await pilot.press(*"films")
        await pilot.pause()
        await pilot.press("slash")
        await pilot.press(*"debian")
        await pilot.press("enter")
        await pilot.pause()

        scans_after = (
            client.torrents_info_calls,
            client.transfer_info_calls,
            client.app_version_calls,
            client.app_web_api_version_calls,
        )
        assert scans_after == scans_before


async def test_periodic_refresh_api_budget_unchanged() -> None:
    """Deterministic (no real wall-clock timer): drive several ticks
    manually so the assertion cannot flake against test/teardown timing."""
    client = FakeQbitClient(torrents=[make_torrent()])
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await pilot.pause()
        # One legitimate tracker call from the initial auto-focused row.
        tracker_calls_after_mount = client.torrents_trackers_calls

        for _ in range(3):
            app._on_tick()
            await pilot.pause()

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
        await pilot.pause()
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
        await pilot.pause()
        first_focus = app.controller.state.focused_hash

        await pilot.press("down")
        await pilot.pause()

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
        await pilot.pause()
        table = app.query_one("#torrents", DataTable)
        assert table.row_count == 1

        def _boom() -> Any:
            raise ConnectionError("connection lost")

        client.torrents_info = _boom  # type: ignore[method-assign]
        app._on_tick()
        await pilot.pause()

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
        await pilot.pause()
        details = app.query_one("#main > DetailsPanel", DetailsPanel)
        rendered = _static_text(details)
        assert "TOPSECRET" not in rendered
        assert "passkey=abc" not in rendered
        assert secret_url not in rendered


def test_format_byte_rate_matches_expected_units() -> None:
    assert _format_byte_rate(0) == "0 B/s"
    assert _format_byte_rate(2048) == "2.0 KiB/s"
