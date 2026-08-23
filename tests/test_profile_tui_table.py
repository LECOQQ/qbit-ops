"""Prove the TUI wall-clock benchmark script still runs.

`scripts/profile_tui_table.py` is the repo's only wall-clock instrument
(`.agents/WORKFLOW.md` and this project's own docstrings point to it for
any timing claim), and it rotted silently for three weeks: the refresh
path it drives (`collect_tui_refresh`) gained a fifth call
(`sync_maindata`, then a startup `collect_instance_lists` call needing
`torrents_categories`/`torrents_tags`), and `_BenchClient` never grew
the matching methods, so every invocation raised `AssertionError` on
`state.visible` before printing a single number. Nothing exercised it,
so nothing said so.

Not a timing test -- see `scripts/profile_tui_table.py` itself, run by
hand, for that. This proves only that `_BenchClient` still answers
every call the app's startup workers make, at the smallest torrent
count, fast enough for the ordinary suite. It is the guard that would
have caught this exact regression the day it landed.
"""

import pytest

from scripts.profile_tui_table import _bench_one

pytestmark = pytest.mark.tui


async def test_the_benchmark_script_still_runs() -> None:
    """Not asserting on the printed numbers -- they vary by machine.
    Just proves `_bench_one` completes without the app's startup
    workers ever failing against `_BenchClient`."""
    await _bench_one(5)
