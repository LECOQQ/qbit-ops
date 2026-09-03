#!/usr/bin/env python3
"""Developer-only benchmark for the Torrents `DataTable` update path.

Measures, at 100 / 1,100 / 5,000 synthetic torrents, under a real
headless `App.run_test()` (so `DataTable` operations are genuine, not
simulated):

    derive        TuiController._recompute_visible()
    format        _torrent_row_values() for every visible torrent
    rebuild       clear(columns=True) + add_column x N + add_row x N (current)
    rows_only     clear() (rows only) + add_row x N
    update_cells  update_cell() for every existing row, no clear/add

Never contacts qBittorrent and never reads `.env`. Not part of
`make check`/`make check-fast` -- run directly:

    python3 scripts/profile_tui_table.py
"""

from __future__ import annotations

import asyncio
import statistics
import time
from typing import Any

from textual.widgets import DataTable

from qbit_ops.tui.app import QbitOpsTuiApp
from qbit_ops.tui.formatting import (
    _columns_for_width,
    _name_column_width,
    _torrent_row_values,
)

WIDE_SIZE = (140, 40)


class _BenchClient:
    """Minimal fake client for the two worker paths this script drives
    on startup: `collect_tui_refresh` (`torrents_info()`/`transfer_info()`/
    `sync_maindata()`/version calls) and `collect_instance_lists`
    (`torrents_categories()`/`torrents_tags()`)."""

    def __init__(self, torrents: list[dict[str, Any]]) -> None:
        self.torrents = torrents

    def app_version(self) -> str:
        return "5.0.1"

    def app_web_api_version(self) -> str:
        return "2.9.3"

    def transfer_info(self) -> dict[str, int]:
        return {"dl_info_speed": 1_000_000, "up_info_speed": 500_000}

    def torrents_info(self) -> list[dict[str, Any]]:
        return self.torrents

    def torrents_trackers(self, torrent_hash: str) -> list[dict[str, Any]]:
        return []

    def sync_maindata(self, rid: str | int = 0) -> dict[str, Any]:
        return {"server_state": {}, "rid": 1}

    def torrents_categories(self) -> dict[str, Any]:
        return {}

    def torrents_tags(self) -> list[str]:
        return []


_STATES = ("downloading", "uploading", "pausedDL", "pausedUP", "stalledDL")
_CATEGORIES = ("movies", "shows", "linux", "")


def _make_torrents(count: int) -> list[dict[str, Any]]:
    """Deterministic synthetic torrents -- stable across runs so before/
    after benchmarks compare like for like."""
    torrents = []
    for index in range(count):
        torrents.append(
            {
                "hash": f"{index:040x}",
                "name": f"Synthetic.Torrent.{index:05d}.Release.Group",
                "state": _STATES[index % len(_STATES)],
                "progress": (index % 101) / 100,
                "ratio": (index % 50) / 10,
                "size": 1_000_000 * (index + 1),
                "category": _CATEGORIES[index % len(_CATEGORIES)],
                "dlspeed": (index * 137) % 5_000_000,
                "upspeed": (index * 91) % 2_000_000,
            }
        )
    return torrents


def _timeit(func: Any, repeats: int = 5) -> tuple[float, float]:
    """Run `func` `repeats` times, discarding one warmup call. Returns
    (median_ms, p95_ms)."""
    func()  # warmup
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        func()
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    median = statistics.median(samples)
    p95_index = min(len(samples) - 1, int(round(0.95 * (len(samples) - 1))))
    return median, samples[p95_index]


async def _bench_one(count: int) -> None:
    torrents = _make_torrents(count)
    client = _BenchClient(torrents)
    app = QbitOpsTuiApp(
        client_factory=lambda: client,
        host="http://localhost:8080",
        refresh_interval=999.0,
    )
    async with app.run_test(size=WIDE_SIZE) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()

        controller = app.controller
        state = controller.state
        table = app.query_one("#torrents", DataTable)

        def derive() -> None:
            controller._recompute_visible()

        def do_format() -> None:
            visible = state.visible
            assert visible is not None
            columns = _columns_for_width(app.size.width)
            name_width = _name_column_width(
                app.size.width,
                tuple(c for c in columns if c != "Name"),
                bar=True,
            )
            for torrent in visible.matched:
                _torrent_row_values(
                    torrent,
                    focused=False,
                    selected=False,
                    bar=True,
                    name_width=name_width,
                    search="",
                )

        def rebuild() -> None:
            app._render_table()

        columns = _columns_for_width(app.size.width)
        name_width = _name_column_width(
            app.size.width, tuple(c for c in columns if c != "Name"), bar=True
        )
        visible = state.visible
        assert visible is not None
        row_values = [
            _torrent_row_values(
                t,
                focused=False,
                selected=False,
                bar=True,
                name_width=name_width,
                search="",
            )
            for t in visible.matched
        ]

        def rows_only() -> None:
            # Suppress RowHighlighted while rows are torn down/rebuilt,
            # same guard `_render_table` itself uses -- otherwise a
            # transient cursor move mid-clear reaches the controller.
            app._rebuilding_table = True
            try:
                table.clear()
                for torrent, values in zip(
                    visible.matched, row_values, strict=True
                ):
                    table.add_row(
                        *(values[name] for name in columns), key=torrent.hash
                    )
            finally:
                app._rebuilding_table = False

        # Ensure the table is populated with matching row keys before
        # timing update_cell-only.
        app._render_table()

        def update_cells() -> None:
            for torrent, values in zip(
                visible.matched, row_values, strict=True
            ):
                for name in columns:
                    table.update_cell(
                        torrent.hash, name, values[name], update_width=False
                    )

        results = {
            "derive": _timeit(derive),
            "format": _timeit(do_format),
            "raw: clear(columns=True)+add_column+add_row": _timeit(rebuild),
            "raw: clear()+add_row (rows only)": _timeit(rows_only),
            "raw: update_cell x N x cols (every cell)": _timeit(update_cells),
        }

        print(f"\n=== {count:,} torrents -- raw DataTable op costs ===")
        for label, (median, p95) in results.items():
            print(f"  {label:<55} median={median:7.2f} ms   p95={p95:7.2f} ms")

        # Flush the table back to a clean, in-sync state before timing
        # the real `_render_table` scenarios below.
        app._rebuilding_table = True
        app._render_table()
        app._rebuilding_table = False
        await pilot.pause()

        print(f"\n=== {count:,} torrents -- app._render_table() scenarios ===")

        def scenario_no_change() -> None:
            app._render_table()

        median, p95 = _timeit(scenario_no_change)
        print(
            f"  {'refresh: nothing changed':<55} "
            f"median={median:7.2f} ms   p95={p95:7.2f} ms"
        )

        def make_change_scenario(fraction: float) -> Any:
            changed_count = max(1, int(count * fraction))

            def scenario() -> None:
                for i in range(changed_count):
                    torrents[i]["dlspeed"] = (
                        torrents[i]["dlspeed"] + 1
                    ) % 5_000_000
                controller._recompute_visible()
                app._render_table()

            return scenario

        for label, fraction in (
            ("refresh: 1 torrent changed", 1 / count),
            ("refresh: ~10% torrents changed", 0.10),
            ("refresh: all torrents changed", 1.0),
        ):
            median, p95 = _timeit(make_change_scenario(fraction))
            print(f"  {label:<55} median={median:7.2f} ms   p95={p95:7.2f} ms")

        def scenario_sort_toggle() -> None:
            from qbit_core.shared.sorting import (
                SortDirection,
                SortField,
                SortOrder,
            )

            current = state.sort
            next_direction = (
                SortDirection.DESCENDING
                if current.direction is SortDirection.ASCENDING
                else SortDirection.ASCENDING
            )
            controller.set_sort(SortOrder(SortField.NAME, next_direction))
            app._render_table()

        median, p95 = _timeit(scenario_sort_toggle)
        print(
            f"  {'sort: toggle direction (full reorder)':<55} "
            f"median={median:7.2f} ms   p95={p95:7.2f} ms"
        )

        def scenario_set_search_only() -> None:
            # Isolates what one recompute costs on the UI thread with no
            # render and no widget I/O -- the cost `SEARCH_DEBOUNCE_
            # SECONDS` exists to pay once per settled search instead of
            # once per keystroke.
            controller.set_search("synthetic.torrent.001")

        median, p95 = _timeit(scenario_set_search_only)
        print(
            f"  {'search: set_search() only, no render (1 keystroke)':<55} "
            f"median={median:7.2f} ms   p95={p95:7.2f} ms"
        )
        controller.set_search("")
        app._render_table()

        def scenario_search_keystroke() -> None:
            controller.set_search("synthetic.torrent.001")
            app._render_table()
            controller.set_search("")
            app._render_table()

        median, p95 = _timeit(scenario_search_keystroke)
        print(
            f"  {'search: narrow then clear (2 renders)':<55} "
            f"median={median:7.2f} ms   p95={p95:7.2f} ms"
        )

        # What `SEARCH_DEBOUNCE_SECONDS` actually buys: a realistic
        # 9-character burst, undebounced (one recompute+render per
        # keystroke, `app._apply_search`'s own path) against debounced
        # (the same 9 keystrokes only ever schedule; one flush does the
        # single recompute+render the settled text needs).
        burst_text = "synthetic"

        def scenario_burst_undebounced() -> None:
            typed = ""
            for char in burst_text:
                typed += char
                controller.set_search(typed)
                app._render_table()
            controller.set_search("")
            app._render_table()

        median, p95 = _timeit(scenario_burst_undebounced)
        print(
            f"  {'search: 9-key burst, undebounced (9 recompute+render)':<55} "
            f"median={median:7.2f} ms   p95={p95:7.2f} ms"
        )

        def scenario_burst_debounced() -> None:
            typed = ""
            for char in burst_text:
                typed += char
                app._schedule_search(typed)
            app._flush_pending_search()
            controller.set_search("")
            app._render_table()

        median, p95 = _timeit(scenario_burst_debounced)
        print(
            f"  {'search: 9-key burst, debounced (1 recompute+render)':<55} "
            f"median={median:7.2f} ms   p95={p95:7.2f} ms"
        )

        # Flush any RowHighlighted messages queued by the scenarios
        # above before the pilot/app tear down, or a late handler can
        # run against an already-popped screen stack.
        await pilot.pause()


async def main() -> None:
    for count in (100, 1_100, 5_000):
        await _bench_one(count)


if __name__ == "__main__":
    asyncio.run(main())
