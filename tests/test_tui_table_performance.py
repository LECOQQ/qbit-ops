"""Behavioral proof that `QbitOpsTuiApp._render_table` updates the
`#torrents` `DataTable` incrementally instead of a full rebuild.

Counter/spy-based, never timing-sensitive (see `AGENTS.md`/the TUI
performance brief): every assertion here is "this method was called N
times", never a millisecond budget. Wall-clock numbers live in
`scripts/profile_tui_table.py` instead.
"""

from __future__ import annotations

from typing import Any

import pytest
from textual.widgets import DataTable

from qbit_ops.tui.app import QbitOpsTuiApp
from qbit_ops.tui.formatting import NARROW_WIDTH_THRESHOLD
from tests.support import FakeQbitClient, make_torrent

pytestmark = pytest.mark.tui

WIDE_SIZE = (140, 40)
LARGE_INTERVAL = 999.0


def _app(client: FakeQbitClient) -> QbitOpsTuiApp:
    return QbitOpsTuiApp(
        client_factory=lambda: client,
        host="http://localhost:8080",
        refresh_interval=LARGE_INTERVAL,
    )


def _torrents(count: int) -> list[dict[str, Any]]:
    return [
        make_torrent(
            hash=f"{index:040x}",
            name=f"Torrent {index:03d}",
            progress=0.5,
            dlspeed=0,
            upspeed=0,
        )
        for index in range(count)
    ]


class _TableSpy:
    """Wrap the live `#torrents` `DataTable`'s mutating methods with
    call counters, leaving their real behavior untouched."""

    def __init__(self, table: DataTable[Any]) -> None:
        self.table = table
        self.clear_calls: list[bool] = []
        self.add_column_count = 0
        self.add_row_count = 0
        self.update_cell_calls: list[tuple[Any, Any]] = []

        self._real_clear = table.clear
        self._real_add_column = table.add_column
        self._real_add_row = table.add_row
        self._real_update_cell = table.update_cell

        table.clear = self._clear  # type: ignore[method-assign]
        table.add_column = self._add_column  # type: ignore[method-assign]
        table.add_row = self._add_row  # type: ignore[method-assign]
        table.update_cell = self._update_cell  # type: ignore[method-assign]

    def _clear(self, columns: bool = False) -> Any:
        self.clear_calls.append(columns)
        return self._real_clear(columns=columns)

    def _add_column(self, *args: Any, **kwargs: Any) -> Any:
        self.add_column_count += 1
        return self._real_add_column(*args, **kwargs)

    def _add_row(self, *args: Any, **kwargs: Any) -> Any:
        self.add_row_count += 1
        return self._real_add_row(*args, **kwargs)

    def _update_cell(
        self, row_key: Any, column_key: Any, *args: Any, **kwargs: Any
    ) -> Any:
        self.update_cell_calls.append((row_key, column_key))
        return self._real_update_cell(row_key, column_key, *args, **kwargs)


async def test_unchanged_refresh_performs_no_table_rebuild_or_cell_write() -> (
    None
):
    client = FakeQbitClient(torrents=_torrents(20))
    app = _app(client)
    async with app.run_test(size=WIDE_SIZE) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()

        # Settle the row-value cache: switching workspace focused the
        # first row via `_refresh_indicator_cell` (a direct cell write
        # that doesn't itself update `_last_row_values`) -- one more
        # render brings the cache in sync with the table before the
        # spy starts counting the *next*, genuinely unchanged, render.
        app._render_table()

        table = app.query_one("#torrents", DataTable)
        spy = _TableSpy(table)

        # Re-render with an identical snapshot -- the shape of a
        # periodic refresh tick where nothing changed.
        app._render_table()

        assert spy.clear_calls == []
        assert spy.add_column_count == 0
        assert spy.add_row_count == 0
        assert spy.update_cell_calls == []


async def test_one_changed_torrent_updates_only_its_own_cells() -> None:
    torrents = _torrents(20)
    client = FakeQbitClient(torrents=torrents)
    app = _app(client)
    async with app.run_test(size=WIDE_SIZE) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        app._render_table()  # settle the row-value cache, see above

        changed_hash = torrents[5]["hash"]
        torrents[5] = {**torrents[5], "progress": 0.9}
        # Simulate the next periodic refresh observing new data for
        # exactly one torrent -- `apply_refresh_success` recomputes
        # `visible` from the raw snapshot.
        client.torrents = torrents
        result = client_refresh_result(app)
        app.controller.apply_refresh_success(result)

        table = app.query_one("#torrents", DataTable)
        spy = _TableSpy(table)
        app._render_table()

        assert spy.clear_calls == []
        assert spy.add_column_count == 0
        assert spy.add_row_count == 0
        touched_rows = {
            _key_value(row_key) for row_key, _ in spy.update_cell_calls
        }
        assert touched_rows == {changed_hash}


async def test_added_and_removed_torrents_update_incrementally() -> None:
    torrents = _torrents(20)
    client = FakeQbitClient(torrents=torrents)
    app = _app(client)
    async with app.run_test(size=WIDE_SIZE) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()

        # Drop one torrent, add a new one -- membership changes, order
        # of the remaining hashes stays the same.
        new_torrents = torrents[1:] + [
            make_torrent(hash="f" * 40, name="New Torrent", progress=0.1)
        ]
        client.torrents = new_torrents
        result = client_refresh_result(app)
        app.controller.apply_refresh_success(result)

        table = app.query_one("#torrents", DataTable)
        spy = _TableSpy(table)
        app._render_table()

        # Row rebuild (membership changed) but never a column rebuild.
        assert spy.clear_calls == [False]
        assert spy.add_column_count == 0
        assert spy.add_row_count == len(new_torrents)


async def test_focus_movement_never_touches_the_table_widget() -> None:
    client = FakeQbitClient(torrents=_torrents(20))
    app = _app(client)
    async with app.run_test(size=WIDE_SIZE) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()

        table = app.query_one("#torrents", DataTable)
        spy = _TableSpy(table)

        await pilot.press("down")
        await pilot.press("down")
        await pilot.pause()

        assert spy.clear_calls == []
        assert spy.add_column_count == 0
        assert spy.add_row_count == 0
        # Only the old/new focused rows' `Sel` indicator cell per move
        # (two presses -> at most 2 cells each), never a blanket
        # cell rewrite across the whole table.
        assert len(spy.update_cell_calls) <= 4
        assert all(
            _key_value(col) == "Sel" for _row, col in spy.update_cell_calls
        )


async def test_resize_with_unchanged_width_does_not_rebuild_columns() -> None:
    """A height-only resize (window taller/shorter, same width -- the
    common "maximize"/"restore" case) never needs a new `Name` width,
    since `_name_column_width` is purely a function of *width*. This is
    the resize shape the column-rebuild gate actually saves: `Name`'s
    width tracks the exact terminal width continuously (never
    overflowing horizontally is non-negotiable, see
    `qbit_ops.tui.formatting`), so a resize that *does* change width
    legitimately needs new column widths -- only a same-width resize
    is free to skip entirely.
    """
    client = FakeQbitClient(torrents=_torrents(20))
    app = _app(client)
    async with app.run_test(size=(150, 40)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()

        table = app.query_one("#torrents", DataTable)
        spy = _TableSpy(table)

        await pilot.resize_terminal(150, 46)
        await pilot.pause()

        assert spy.clear_calls == []
        assert spy.add_column_count == 0


async def test_resize_across_layout_class_rebuilds_columns_once() -> None:
    client = FakeQbitClient(torrents=_torrents(20))
    app = _app(client)
    async with app.run_test(size=(150, 40)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()

        table = app.query_one("#torrents", DataTable)
        spy = _TableSpy(table)

        assert 150 >= NARROW_WIDTH_THRESHOLD
        await pilot.resize_terminal(80, 24)
        await pilot.pause()

        assert spy.clear_calls == [True]


def _key_value(key: Any) -> Any:
    """Unwrap a Textual `RowKey`/`ColumnKey`, or return a plain `str`
    key unchanged -- `update_cell` accepts either."""
    return key.value if hasattr(key, "value") else key


def client_refresh_result(app: QbitOpsTuiApp) -> Any:
    """Collect a fresh `TuiRefreshResult` synchronously from the app's
    own `client_factory`-backed controller, bypassing the worker thread
    -- safe here since the fake client never blocks."""
    return app.controller.collect_refresh()
