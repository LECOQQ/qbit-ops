"""Adversarial regression tests from the TUI bulk-mutation safety audit.

See docs/audits/2026-07-26-tui-bulk-mutation-audit.md for the original
findings and
docs/audits/2026-07-26-tui-bulk-mutation-remediation.md for their
closure. Everything here is now an ordinary regression test, in one
file so the audit's evidence stays together:

* Properties the audit attacked and could not break -- the frozen plan
  surviving a refresh, a reordered table, and a reconciled selection;
  `Ctrl+A`/`Ctrl+D` inside a text `Input`; a late worker result after
  shutdown; the import boundary.
* Findings F-1 through F-5 and risks R-1 through R-3, each of which was
  encoded as `xfail(strict=True)` while the defect stood. Every marker
  has since been removed after the underlying behaviour was corrected
  -- no audit defect remains encoded as an expected failure.

Every concurrency proof uses real `threading.Event` barriers, never a
sleep, matching `tests/test_tui_app.py`'s existing style.
"""

from __future__ import annotations

import ast
import asyncio
import threading
import time
from pathlib import Path
from typing import Any

from textual.widgets import Button, Input, Static
from textual.worker import Worker, WorkerState

import app.tui
from app.config import ConfigError
from app.errors import ErrorCategory, QbitConnectionError
from app.execution import MutationStatus
from app.tui.app import (
    MUTATION_WORKER_GROUP,
    HelpScreen,
    LastActionBar,
    MutationUiResult,
    PreviewScreen,
    ResultScreen,
    _classify_mutation_error,
    _format_result_text,
)
from tests.support import FakeQbitClient, make_torrent
from tests.test_tui_app import (
    WIDE_SIZE,
    _app,
    _FakeCompletedWorker,
    _goto_torrents,
    _settle,
    asyncio_wait_for_event,
)

HASH_A = "a" * 40
HASH_B = "b" * 40


async def _select_all_and_open_preview(
    app: Any, pilot: Any, button_id: str = "actions-pause"
) -> None:
    """Select every visible row, choose an action, and land on Preview."""
    await pilot.press("ctrl+a")
    await pilot.press("a")
    await pilot.pause()
    await pilot.click(app.screen.query_one(f"#{button_id}", Button))
    await pilot.pause()
    assert isinstance(app.screen, PreviewScreen)


def _current_plan(app: Any) -> Any:
    """The plan owned by the `PreviewScreen` currently on top."""
    screen = app.screen
    assert isinstance(screen, PreviewScreen)
    return screen.plan


async def _pump_until(pilot: Any, predicate: Any, limit: int = 200) -> None:
    """Pump the message loop until `predicate()` is true, or give up."""
    for _ in range(limit):
        if predicate():
            return
        await pilot.pause()


def _result_text(app: Any) -> str:
    return str(app.screen.query_one("#result-content", Static).content)


async def _await_flag(
    flag: threading.Event, what: str, timeout: float = 5.0
) -> None:
    """Wait for a *positive* fact, with a deadline that can fail.

    Replaces the previous `_drain_workers` helper, which polled
    `all(worker.is_finished for worker in app.workers)`. Textual empties
    its worker registry on shutdown, so after `action_quit()` that
    `all(...)` ran over an **empty** collection, was vacuously true, and
    returned in ~0 ms -- before an unguarded mutation could possibly
    land. The shutdown assertions therefore ran too early to observe
    anything: measured 0 detections out of 8 with the authority guard
    deliberately bypassed (final closure report, finding T-1).

    Waiting on a real event that the worker itself sets, with a bounded
    deadline that raises on timeout, cannot pass vacuously: either the
    fact happened, or the test fails saying which one did not.
    """
    deadline = time.monotonic() + timeout
    while not flag.is_set():
        if time.monotonic() > deadline:
            raise AssertionError(
                f"timed out after {timeout}s waiting for: {what}"
            )
        await asyncio.sleep(0.002)


class _AuthorityProbe:
    """Test seam observing the post-lock authority check and worker exit.

    Wraps `TuiController.apply_bulk_plan`, replacing its `should_proceed`
    predicate with an instrumented one. The wrapper is transparent: it
    calls the real predicate and forwards its verdict unchanged, so the
    production decision is observed, never altered -- and no permanent
    instrumentation is added to production code.

    `returned` is set from a `finally`, i.e. strictly *after* the real
    `apply_bulk_plan` (including any qBittorrent call it makes) has
    completed. Waiting on it is therefore the positive fact a shutdown
    test needs: if the guard were bypassed, the endpoint call would have
    already happened by the time the flag fires, and the zero-call
    assertion would catch it.
    """

    def __init__(self, app: Any) -> None:
        self.log: list[str] = []
        self.evaluated = threading.Event()
        self.entered = threading.Event()
        self.returned = threading.Event()
        self.verdicts: list[bool] = []
        self._original = app.controller.apply_bulk_plan
        app.controller.apply_bulk_plan = self._wrapper

    def _wrapper(
        self, plan: Any, *, should_proceed: Any = None, **kwargs: Any
    ) -> bool:
        self.entered.set()
        self.log.append("mutation-worker-entered")

        def probe() -> bool:
            verdict = True if should_proceed is None else bool(should_proceed())
            self.verdicts.append(verdict)
            self.log.append(f"authority:{str(verdict).lower()}")
            self.evaluated.set()
            return verdict

        try:
            return self._original(plan, should_proceed=probe, **kwargs)
        finally:
            self.log.append("mutation-worker-returned")
            self.returned.set()


# -- properties the audit attacked and could not break ---------------------


async def test_apply_sends_frozen_plan_despite_refresh_and_reorder() -> None:
    """The central invariant, attacked from three sides at once.

    While the Preview is open, a periodic refresh (a) renames the
    torrents so the table's name-sorted order flips, (b) makes one
    selected torrent invisible to the active search so
    `reconcile_selection` drops it from `selected_hashes`. Apply must
    still send exactly the two hashes the frozen preview displayed.
    """
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="Alpha", state="downloading"),
            make_torrent(hash=HASH_B, name="Beta", state="downloading"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _select_all_and_open_preview(app, pilot)

        plan_hashes = [change.hash for change in _current_plan(app).changes]
        assert sorted(plan_hashes) == [HASH_A, HASH_B]

        # A refresh lands while the Preview is open: names swap (so the
        # name-sorted table order reverses) and a search hides Alpha.
        client.torrents = [
            make_torrent(hash=HASH_A, name="Zulu", state="downloading"),
            make_torrent(hash=HASH_B, name="Aaron", state="downloading"),
        ]
        app.controller.state.search = "aaron"
        app._start_periodic_refresh()
        await _settle(app, pilot)

        assert app.controller.state.selected_hashes == {HASH_B}
        assert [c.hash for c in _current_plan(app).changes] == plan_hashes

        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await _settle(app, pilot)

        assert client.paused_hashes == [plan_hashes]
        assert isinstance(app.screen, ResultScreen)
        # Wording is deliberately "submitted", not "applied": qBittorrent's
        # bulk endpoints confirm acceptance of the request, not a per-hash
        # state transition (documented accepted limitation).
        text = _result_text(app)
        assert "Submitted" in text
        assert f"submitted for {len(plan_hashes)} torrent(s)" in text


async def test_skipped_hashes_never_reach_the_client() -> None:
    """A mixed plan sends `plan.changes` only -- never a skipped hash."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="Alpha", state="downloading"),
            make_torrent(hash=HASH_B, name="Beta", state="pausedUP"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _select_all_and_open_preview(app, pilot)

        plan = _current_plan(app)
        assert [c.hash for c in plan.changes] == [HASH_A]
        assert [(s.hash, s.reason) for s in plan.skipped] == [
            (HASH_B, "already_stopped")
        ]

        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await _settle(app, pilot)

        assert client.paused_hashes == [[HASH_A]]


async def test_ctrl_a_and_ctrl_d_in_a_text_input_never_touch_selection() -> (
    None
):
    """`Ctrl+A`/`Ctrl+D` typed in the search box edit text, nothing else.

    Textual's own `Input.BINDINGS` shadows both chords (`home` /
    `delete_right`), which is what keeps the App-level
    `select_all_visible`/`deselect_all` bindings from firing while a text
    field has focus. qbit-ops does not enforce that shadowing itself, so
    this test is the guard against a future Textual release changing it.
    """
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="Alpha"),
            make_torrent(hash=HASH_B, name="Beta"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")  # select Alpha from the table
        assert app.controller.state.selected_hashes == {HASH_A}

        await pilot.press("slash")
        await pilot.pause()
        search = app.query_one("#search-input", Input)
        await pilot.press(*"al")
        await pilot.pause()

        await pilot.press("ctrl+a")
        await pilot.pause()
        assert app.controller.state.selected_hashes == {HASH_A}
        assert search.cursor_position == 0

        await pilot.press("ctrl+d")
        await pilot.pause()
        assert app.controller.state.selected_hashes == {HASH_A}
        assert search.value == "l"


async def test_late_mutation_result_after_shutdown_never_touches_the_ui() -> (
    None
):
    """A mutation worker resolving after `q` is dropped, not applied."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _select_all_and_open_preview(app, pilot)

        await app.action_quit()
        await pilot.pause()
        assert app.is_running is False

        calls_before = client.torrents_info_calls
        fake_worker = _FakeCompletedWorker(
            MUTATION_WORKER_GROUP, (1, True, None)
        )
        event = Worker.StateChanged(fake_worker, WorkerState.SUCCESS)  # type: ignore[arg-type]
        app.on_worker_state_changed(event)

        assert client.torrents_info_calls == calls_before
        assert client.paused_hashes == []


def test_tui_imports_no_mutation_surface_beyond_the_two_allowed_names() -> None:
    """Rule-based companion to `tests/test_tui_security.py`.

    That module intersects the TUI's imports with a *fixed* forbidden
    list, so a mutation function that does not yet exist (a future
    `apply_torrent_deletion`, `plan_torrent_removal`, ...) would be
    invisible to it. This checks the shape of every imported name
    instead: anything that looks like a planner or an applier must be
    one of the two sanctioned LOW-risk functions.
    """
    allowed = {
        "apply_bulk_torrent_action",
        "build_bulk_action_plan_from_snapshot",
    }
    prefixes = ("plan_", "apply_", "delete_", "remove_", "edit_", "set_")
    offenders: set[str] = set()

    tui_files = sorted(
        path
        for path in Path(app.tui.__file__).parent.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    assert tui_files, "expected at least one .py file under app/tui/"

    for path in tui_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if not node.module.startswith("app."):
                continue
            for alias in node.names:
                name = alias.name
                if name in allowed:
                    continue
                if name.startswith(prefixes):
                    offenders.add(f"{node.module}.{name}")

    assert not offenders, (
        "TUI modules import mutation-shaped names beyond the two "
        f"sanctioned LOW-risk functions: {sorted(offenders)}"
    )


# -- confirmed findings, deliberately not remediated ----------------------


async def test_refresh_during_a_filter_draft_preserves_the_selection() -> None:
    """F-1 closed: `apply_refresh_success(reconcile=False)` while a
    `FiltersScreen` holds an uncommitted draft, so a periodic tick can
    no longer erase a selection mid-typing."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="Alpha", category="films"),
            make_torrent(hash=HASH_B, name="Beta", category="tv"),
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
        app.screen.query_one(".f-category", Input).focus()
        await pilot.press("f")  # incomplete draft of "films": matches nothing
        await pilot.pause()

        app._start_periodic_refresh()  # one ordinary tick
        await _settle(app, pilot)

        await pilot.press(*"ilms")
        await pilot.pause()
        await pilot.press("enter")  # commit the filter the user meant
        await pilot.pause()

        assert app.controller.state.selected_hashes == {HASH_A}


class _CallLogClient(FakeQbitClient):
    """Records an ordered `+name` / `-name` log of every remote call.

    Proving "no overlap" alone is vacuous: a mutation that never
    happened also never overlaps anything (closure review, §3 "Qualité
    de preuve"). Asserting the *order* of entries and exits, plus the
    exact hashes finally sent, makes the serialization tests fail if
    Apply were silently refused.

    Reads and tracker fetches can each be blocked on a real
    `threading.Event`. Deliberately one class rather than a subclass
    per blocked call: an override that logs and then delegates to a
    logging parent would record the same call twice and look like
    concurrency that never happened.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.log: list[str] = []
        self._depth = 0
        self.max_depth = 0
        self._lock = threading.Lock()
        self.block_reads = False
        self.read_entered = threading.Event()
        self.read_release = threading.Event()
        self.block_trackers = False
        self.tracker_entered = threading.Event()
        self.tracker_release = threading.Event()
        self.pause_calls = 0

    def _enter(self, name: str) -> None:
        with self._lock:
            self.log.append(f"+{name}")
            self._depth += 1
            self.max_depth = max(self.max_depth, self._depth)

    def _exit(self, name: str) -> None:
        with self._lock:
            self._depth -= 1
            self.log.append(f"-{name}")

    def _wait(self, entered: threading.Event, release: threading.Event) -> None:
        entered.set()
        if not release.wait(timeout=5.0):
            raise TimeoutError("test forgot to release a blocked call")

    def torrents_info(self) -> list[dict[str, Any]]:
        self._enter("read")
        try:
            if self.block_reads:
                self._wait(self.read_entered, self.read_release)
            return FakeQbitClient.torrents_info(self)
        finally:
            self._exit("read")

    def torrents_trackers(self, torrent_hash: str) -> list[dict[str, Any]]:
        self._enter("trackers")
        try:
            if self.block_trackers:
                self._wait(self.tracker_entered, self.tracker_release)
            return FakeQbitClient.torrents_trackers(self, torrent_hash)
        finally:
            self._exit("trackers")

    def torrents_pause(self, torrent_hashes: Any) -> None:
        self._enter("pause")
        self.pause_calls += 1
        try:
            FakeQbitClient.torrents_pause(self, torrent_hashes)
        finally:
            self._exit("pause")


def _assert_serialized_after(log: list[str], blocker: str) -> None:
    """Assert the mutation started strictly after `blocker` finished."""
    assert f"+{blocker}" in log, f"the {blocker} never started: {log}"
    assert "+pause" in log, f"the mutation was never dispatched: {log}"
    assert log.index("+pause") > log.index(
        f"-{blocker}"
    ), f"the mutation did not wait for the {blocker} to finish: {log}"
    assert log.count("+pause") == 1, f"mutation dispatched twice: {log}"


async def test_apply_is_serialized_after_an_in_flight_refresh() -> None:
    """F-2, proven non-vacuously.

    Asserts the full chain, not merely the absence of overlap: the read
    started, the mutation waited for it to finish, the mutation was
    eventually dispatched exactly once with exactly `plan.changes`, no
    skipped hash was sent, one post-mutation refresh followed, and the
    observed peak concurrency on the client was 1. Any of those failing
    -- including Apply being silently ignored -- fails the test.
    """
    client = _CallLogClient(
        torrents=[
            make_torrent(hash=HASH_A, name="Alpha", state="downloading"),
            make_torrent(hash=HASH_B, name="Beta", state="pausedUP"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _select_all_and_open_preview(app, pilot)
        plan_hashes = [c.hash for c in _current_plan(app).changes]
        assert plan_hashes == [HASH_A]  # Beta is skipped, already stopped

        client.block_reads = True
        app._start_periodic_refresh()
        await pilot.pause()
        await asyncio_wait_for_event(client.read_entered)

        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await pilot.pause()
        # Still queued: the blocked read owns the client.
        assert client.paused_hashes == []

        client.block_reads = False
        client.read_release.set()
        await _settle(app, pilot)
        await _pump_until(pilot, lambda: bool(client.paused_hashes))
        await _settle(app, pilot)

        _assert_serialized_after(client.log, "read")
        assert client.max_depth == 1, client.log
        assert app.controller.max_concurrent_remote_operations == 1
        # Exactly the frozen plan, once -- never the skipped hash.
        assert client.paused_hashes == [plan_hashes]
        assert HASH_B not in client.paused_hashes[0]
        # One post-mutation refresh followed the mutation.
        assert client.log.index("+read", client.log.index("-pause")) > 0


async def test_apply_is_serialized_after_an_in_flight_detail_fetch() -> None:
    """F-2, detail-fetch half, proven non-vacuously -- same full chain
    as the refresh test above."""
    client = _CallLogClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")],
        trackers_by_hash={HASH_A: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _settle(app, pilot)
        await _select_all_and_open_preview(app, pilot)
        plan_hashes = [c.hash for c in _current_plan(app).changes]
        assert plan_hashes == [HASH_A]

        client.log.clear()
        client.block_trackers = True
        app.action_refresh_details()
        await pilot.pause()
        await asyncio_wait_for_event(client.tracker_entered)

        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await pilot.pause()
        assert client.paused_hashes == []

        client.block_trackers = False
        client.tracker_release.set()
        await _settle(app, pilot)
        await _pump_until(pilot, lambda: bool(client.paused_hashes))
        await _settle(app, pilot)

        _assert_serialized_after(client.log, "trackers")
        assert client.max_depth == 1, client.log
        assert app.controller.max_concurrent_remote_operations == 1
        assert client.paused_hashes == [plan_hashes]


# -- cancellation tests: zero dispatch is the *expected* outcome ----------
#
# Deliberately separate from the serialization-success tests above. These
# assert `paused_hashes == []` because the mutation must be abandoned,
# not because serialization was proven -- conflating the two is exactly
# the vacuity the closure review flagged.


async def test_shutdown_while_queued_behind_a_refresh_sends_nothing() -> None:
    """N-2 cancellation proof, behind a blocked refresh.

    **Cancellation test**: the expected endpoint call count is zero, and
    that zero is only meaningful because every preceding milestone is
    positively observed. Asserting `paused_hashes == []` on its own
    would pass trivially (T-1) -- the point is to prove the mutation
    worker really did wake up, really did reach the post-lock authority
    check, really was told "no", and really returned without calling
    qBittorrent.
    """
    client = _CallLogClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _select_all_and_open_preview(app, pilot)
        probe = _AuthorityProbe(app)

        # 1. A blocking refresh starts and takes the shared remote lock.
        client.block_reads = True
        app._start_periodic_refresh()
        await pilot.pause()
        await asyncio_wait_for_event(client.read_entered)
        # The refresh actually started -- and since the client call runs
        # inside `_remote_operation()`, being inside it means the shared
        # remote lock is held. No production introspection hook needed.
        assert "+read" in client.log

        # 2. Apply dispatches a mutation worker, which queues behind it.
        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await pilot.pause()
        probe.log.append("apply-requested")
        await _await_flag(
            probe.entered, "the mutation worker to enter apply_bulk_plan"
        )
        # It is waiting for the lock: it has entered, but the authority
        # check (which happens *after* acquisition) has not run yet.
        assert probe.evaluated.is_set() is False
        assert client.paused_hashes == []

        # 3. Shutdown removes the worker's authority while it waits.
        await app.action_quit()
        await pilot.pause()
        probe.log.append("shutdown-requested")
        assert app.is_running is False

        # 4. The refresh releases the lock; the mutation may now proceed.
        client.block_reads = False
        client.read_release.set()

        # 5. Wait for a *positive* fact with a failing deadline: the
        #    worker returning. Never the worker registry, which Textual
        #    empties on shutdown (see `_await_flag`).
        await _await_flag(
            probe.evaluated, "the post-lock authority check to be evaluated"
        )
        await _await_flag(probe.returned, "the mutation worker to return")

        # 6. The guard was reached and said no...
        assert probe.verdicts == [False], probe.log
        # ...and nothing whatsoever reached qBittorrent.
        assert client.paused_hashes == []
        assert "+pause" not in client.log
        assert client.pause_calls == 0

        # Ordering: shutdown preceded the release, which preceded the
        # authority check, which preceded the worker returning.
        assert probe.log.index("shutdown-requested") < probe.log.index(
            "authority:false"
        )
        assert probe.log.index("authority:false") < probe.log.index(
            "mutation-worker-returned"
        )


async def test_shutdown_while_queued_behind_details_sends_nothing() -> None:
    """N-2 cancellation proof, behind a blocked tracker-detail fetch.

    **Cancellation test**: same full lifecycle observation as the
    refresh variant, with `torrents_trackers()` owning the shared lock.
    """
    client = _CallLogClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")],
        trackers_by_hash={HASH_A: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _settle(app, pilot)
        await _select_all_and_open_preview(app, pilot)
        probe = _AuthorityProbe(app)

        client.log.clear()
        client.block_trackers = True
        app.action_refresh_details()
        await pilot.pause()
        await asyncio_wait_for_event(client.tracker_entered)
        assert "+trackers" in client.log  # holds the shared lock

        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await pilot.pause()
        probe.log.append("apply-requested")
        await _await_flag(
            probe.entered, "the mutation worker to enter apply_bulk_plan"
        )
        assert probe.evaluated.is_set() is False
        assert client.paused_hashes == []

        await app.action_quit()
        await pilot.pause()
        probe.log.append("shutdown-requested")
        assert app.is_running is False

        client.block_trackers = False
        client.tracker_release.set()

        await _await_flag(
            probe.evaluated, "the post-lock authority check to be evaluated"
        )
        await _await_flag(probe.returned, "the mutation worker to return")

        assert probe.verdicts == [False], probe.log
        assert client.paused_hashes == []
        assert "+pause" not in client.log
        assert client.pause_calls == 0
        assert probe.log.index("shutdown-requested") < probe.log.index(
            "authority:false"
        )


# -- positive controls: without shutdown, the mutation *must* land --------
#
# Deliberately separate from the cancellation tests above: these assert
# an endpoint call count of exactly one with exactly `plan.changes`.
# Sharing an assertion helper that tolerated both zero and one call is
# precisely how a zero-dispatch result could masquerade as proof that
# serialization works.


async def test_normal_queue_behind_refresh_dispatches_exactly_once() -> None:
    """Positive control for the refresh path: the authority check is
    reached, returns true, and the frozen plan is dispatched once."""
    client = _CallLogClient(
        torrents=[
            make_torrent(hash=HASH_A, name="Alpha", state="downloading"),
            make_torrent(hash=HASH_B, name="Beta", state="pausedUP"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _select_all_and_open_preview(app, pilot)
        plan_hashes = [c.hash for c in _current_plan(app).changes]
        assert plan_hashes == [HASH_A]  # Beta is skipped: already stopped
        probe = _AuthorityProbe(app)

        client.block_reads = True
        app._start_periodic_refresh()
        await pilot.pause()
        await asyncio_wait_for_event(client.read_entered)

        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await pilot.pause()
        await _await_flag(
            probe.entered, "the mutation worker to enter apply_bulk_plan"
        )
        # Queued behind the read: nothing dispatched yet.
        assert probe.evaluated.is_set() is False
        assert client.paused_hashes == []

        client.block_reads = False
        client.read_release.set()
        await _await_flag(
            probe.evaluated, "the post-lock authority check to be evaluated"
        )
        await _await_flag(probe.returned, "the mutation worker to return")
        await _settle(app, pilot)

        # The guard was reached and allowed the dispatch.
        assert probe.verdicts == [True], probe.log
        # Exactly once, with exactly the frozen plan.
        assert client.pause_calls == 1
        assert client.paused_hashes == [plan_hashes]
        assert HASH_B not in client.paused_hashes[0]  # skipped hash absent
        # Serialization ordering, and the post-mutation refresh.
        _assert_serialized_after(client.log, "read")
        assert client.max_depth == 1, client.log
        assert app.controller.max_concurrent_remote_operations == 1
        assert "+read" in client.log[client.log.index("-pause") :], client.log


async def test_normal_queue_behind_details_dispatches_exactly_once() -> None:
    """Positive control for the tracker-detail path."""
    client = _CallLogClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")],
        trackers_by_hash={HASH_A: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _settle(app, pilot)
        await _select_all_and_open_preview(app, pilot)
        plan_hashes = [c.hash for c in _current_plan(app).changes]
        probe = _AuthorityProbe(app)

        client.log.clear()
        client.block_trackers = True
        app.action_refresh_details()
        await pilot.pause()
        await asyncio_wait_for_event(client.tracker_entered)

        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await pilot.pause()
        await _await_flag(
            probe.entered, "the mutation worker to enter apply_bulk_plan"
        )
        assert probe.evaluated.is_set() is False
        assert client.paused_hashes == []

        client.block_trackers = False
        client.tracker_release.set()
        await _await_flag(
            probe.evaluated, "the post-lock authority check to be evaluated"
        )
        await _await_flag(probe.returned, "the mutation worker to return")
        await _settle(app, pilot)

        assert probe.verdicts == [True], probe.log
        assert client.pause_calls == 1
        assert client.paused_hashes == [plan_hashes]
        _assert_serialized_after(client.log, "trackers")
        assert client.max_depth == 1, client.log
        assert app.controller.max_concurrent_remote_operations == 1


async def test_max_simultaneous_remote_client_operations_is_one() -> None:
    """F-2 closed, measured directly and non-vacuously.

    A detail fetch, a periodic refresh and a mutation all contend at
    once. Asserts not only that the controller's high-water mark of
    concurrent remote operations stays at 1, but that the client itself
    never saw two overlapping calls *and* that the mutation genuinely
    happened -- a peak of 1 is otherwise trivially satisfied by a single
    operation, or by an Apply that was silently refused.
    """
    client = _CallLogClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")],
        trackers_by_hash={HASH_A: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _settle(app, pilot)
        await _select_all_and_open_preview(app, pilot)
        plan_hashes = [c.hash for c in _current_plan(app).changes]

        client.block_trackers = True
        app.action_refresh_details()
        await pilot.pause()
        await asyncio_wait_for_event(client.tracker_entered)

        app._start_periodic_refresh()
        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await pilot.pause()

        client.block_trackers = False
        client.tracker_release.set()
        await _settle(app, pilot)
        await _pump_until(pilot, lambda: bool(client.paused_hashes))
        await _settle(app, pilot)

        assert app.controller.max_concurrent_remote_operations == 1
        assert client.max_depth == 1, client.log
        # ...and the mutation really did happen, with exactly the plan.
        assert client.paused_hashes == [plan_hashes]


async def test_vanished_torrents_are_not_reported_as_already_satisfied() -> (
    None
):
    """F-3 closed: an all-`not_found` plan classifies as NO_MATCH and
    says the torrents were not found, never 'already satisfied'."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="Alpha", state="downloading"),
            make_torrent(hash=HASH_B, name="Beta", state="downloading"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("space")  # select Alpha only
        await pilot.press("a")
        await pilot.pause()

        # Alpha disappears between the Actions snapshot and the plan build.
        client.torrents = [
            make_torrent(hash=HASH_B, name="Beta", state="downloading")
        ]
        app._start_periodic_refresh()
        await _settle(app, pilot)

        await pilot.click(app.screen.query_one("#actions-pause", Button))
        await pilot.pause()
        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await _settle(app, pilot)

        assert client.paused_hashes == []
        text = _result_text(app)
        assert "already satisfied" not in text


class _RaisingMutationClient(FakeQbitClient):
    """Raises a chosen error from the mutating call itself.

    Replaces the audit's original trigger (break client *creation* via a
    failed refresh). That path is no longer reachable: after R-2, a
    failed refresh makes the open preview sticky-stale and Apply is
    withdrawn before any client work happens -- a strictly stronger
    safety property. Raising from the mutation keeps the connection
    healthy so Apply genuinely dispatches, which is what these two
    findings are actually about (how the failure is *classified*).
    """

    def __init__(self, *args: Any, error: BaseException, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._error = error

    def torrents_pause(self, torrent_hashes: Any) -> None:
        raise self._error


async def test_config_error_during_apply_is_not_an_internal_defect() -> None:
    """F-4 closed: `ConfigError` is classified CONFIGURATION, never as
    a qbit-ops defect."""
    text = await _apply_failure_result_text(
        ConfigError("QBIT_HOST is not set.")
    )
    assert "Internal error" not in text
    assert "qbit-ops defect" not in text
    assert "Configuration invalid" in text
    assert "Fix .env" in text


async def test_connection_error_with_opaque_cause_stays_unavailable() -> None:
    """F-5 closed: the *outer* error is classified before its cause, so a
    recoverable connection failure stays UNAVAILABLE even when it carries
    an opaque, unclassifiable cause."""
    connection_error = QbitConnectionError("Unable to connect to qBittorrent.")
    connection_error.__cause__ = ValueError("malformed JSON in login response")

    # The ordering itself, asserted directly on the pure classifier: the
    # outer QbitConnectionError must win over the ValueError cause.
    category, _message = _classify_mutation_error(connection_error)
    assert category is ErrorCategory.UNAVAILABLE

    text = await _apply_failure_result_text(connection_error)
    assert "Internal error" not in text
    assert "qbit-ops defect" not in text
    assert "Unavailable" in text


async def test_unexpected_runtime_defect_is_still_reported_as_internal() -> (
    None
):
    """The safe direction must not be over-corrected: a genuine
    programming defect stays INTERNAL rather than being laundered into
    a recoverable remote failure."""
    text = await _apply_failure_result_text(TypeError("a real defect"))
    assert "Internal error" in text
    assert "qbit-ops defect" in text


async def _apply_failure_result_text(error: BaseException) -> str:
    """Apply against a client whose mutation raises `error`; return the
    Result modal's rendered text."""
    client = _RaisingMutationClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")],
        error=error,
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _select_all_and_open_preview(app, pilot)

        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await _settle(app, pilot)

        assert isinstance(app.screen, ResultScreen)
        return _result_text(app)


# -- R-1: targeted modal lifecycle ----------------------------------------


class _BlockingPauseClient(FakeQbitClient):
    """Blocks inside `torrents_pause()` until released."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.entered = threading.Event()
        self.release = threading.Event()

    def torrents_pause(self, torrent_hashes: Any) -> None:
        self.entered.set()
        if not self.release.wait(timeout=5.0):
            raise TimeoutError("test forgot to release the pause")
        super().torrents_pause(torrent_hashes)


async def test_completion_never_pops_an_unrelated_modal() -> None:
    """R-1 closed: a mutation completing while another modal sits above
    its Preview must not pop that unrelated modal, and must not strand a
    `PreviewScreen` stuck in `applying=True`.

    The extra screen is pushed programmatically because no key reaches
    it today -- the audit's point is precisely that the old guard relied
    on that unreachability rather than on targeting.
    """
    client = _BlockingPauseClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _select_all_and_open_preview(app, pilot)

        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await pilot.pause()
        await asyncio_wait_for_event(client.entered)

        # Something else lands on top of the applying Preview.
        app.push_screen(HelpScreen())
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)

        client.release.set()
        await _settle(app, pilot)

        # The unrelated modal is untouched...
        assert isinstance(app.screen, HelpScreen)
        # ...and the mutation still really happened...
        assert client.paused_hashes == [[HASH_A]]
        # ...and its outcome was preserved rather than dropped (R-3).
        assert app._last_mutation_result is not None
        assert app._last_mutation_result.submitted_hashes == (HASH_A,)


async def test_old_operation_completion_cannot_replace_a_newer_preview() -> (
    None
):
    """R-1 closed: a completion carrying a superseded `operation_id`
    never touches a newer Preview."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _select_all_and_open_preview(app, pilot)

        stale_preview = app.screen
        assert isinstance(stale_preview, PreviewScreen)
        app._preview_screen = stale_preview
        app._active_mutation_plan = stale_preview.plan

        # A completion arrives quoting an operation id that is not this
        # preview's -- it must be discarded outright.
        superseded = _FakeCompletedWorker(
            MUTATION_WORKER_GROUP,
            (stale_preview.operation_id + 999, True, None),
        )
        event = Worker.StateChanged(superseded, WorkerState.SUCCESS)  # type: ignore[arg-type]
        app.on_worker_state_changed(event)
        await pilot.pause()

        assert app.screen is stale_preview
        assert app._last_mutation_result is None


# -- R-2: stale preview cannot Apply --------------------------------------


class _FailAfterFirstReadClient(FakeQbitClient):
    """Succeeds once, then fails every later `torrents_info()`."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fail_reads = False

    def torrents_info(self) -> list[dict[str, Any]]:
        if self.fail_reads:
            raise OSError("connection reset")
        return super().torrents_info()


async def test_stale_preview_refuses_apply_by_button_and_keyboard() -> None:
    """R-2 closed: once the snapshot goes stale the Preview stays
    readable but Apply is genuinely withdrawn -- by button *and* by
    keyboard, since `action_apply_plan` checks `can_apply` itself."""
    client = _FailAfterFirstReadClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _select_all_and_open_preview(app, pilot)
        preview = app.screen
        assert isinstance(preview, PreviewScreen)
        assert preview.can_apply is True

        client.fail_reads = True
        app._start_periodic_refresh()
        await _settle(app, pilot)

        assert preview.stale is True
        assert preview.can_apply is False
        assert app.screen.query_one("#preview-apply", Button).disabled is True
        text = str(app.screen.query_one("#preview-content", Static).content)
        assert "Snapshot stale" in text
        assert "rebuild the preview" in text.lower()

        # Neither the button nor the keyboard may dispatch.
        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await _settle(app, pilot)
        await pilot.press("enter")
        await _settle(app, pilot)
        app.action_apply_plan()
        await _settle(app, pilot)

        assert client.paused_hashes == []
        assert isinstance(app.screen, PreviewScreen)


async def test_recovery_does_not_reactivate_a_stale_preview() -> None:
    """R-2 closed: staleness is sticky. Recovery leaves the old preview
    non-applicable; a rebuilt one is applicable again."""
    client = _FailAfterFirstReadClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _select_all_and_open_preview(app, pilot)
        preview = app.screen
        assert isinstance(preview, PreviewScreen)

        client.fail_reads = True
        app._start_periodic_refresh()
        await _settle(app, pilot)
        assert preview.can_apply is False

        # Recovery.
        client.fail_reads = False
        app._start_periodic_refresh()
        await _settle(app, pilot)
        assert app.controller.state.stale is False
        # The old preview remains non-applicable, by design.
        assert preview.can_apply is False
        assert client.paused_hashes == []

        # Rebuilding it restores Apply.
        await pilot.press("escape")
        await pilot.pause()
        await _select_all_and_open_preview(app, pilot)
        rebuilt = app.screen
        assert isinstance(rebuilt, PreviewScreen)
        assert rebuilt.can_apply is True

        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await _settle(app, pilot)
        assert client.paused_hashes == [[HASH_A]]


# -- F-1 hardening: Cancel after refreshes --------------------------------


async def test_cancel_after_refreshes_preserves_filter_and_selection() -> None:
    """F-1 closed, Cancel half: several ticks landing while a draft is
    open leave both the committed filter and the selection untouched."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="Alpha", category="films"),
            make_torrent(hash=HASH_B, name="Beta", category="tv"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await pilot.press("ctrl+a")
        await pilot.pause()
        before = set(app.controller.state.selected_hashes)
        assert len(before) == 2

        await pilot.press("f")
        await pilot.pause()
        app.screen.query_one(".f-category", Input).focus()
        await pilot.press(*"fil")
        await pilot.pause()

        for _ in range(3):
            app._start_periodic_refresh()
            await _settle(app, pilot)

        await pilot.press("escape")  # Cancel
        await pilot.pause()

        assert app.controller.state.selected_hashes == before
        assert app.controller.state.filters.categories == ()


# -- selection policy after each outcome ----------------------------------


async def test_applied_clears_only_submitted_hashes() -> None:
    """APPLIED drops exactly what the frozen plan submitted; a skipped
    (already-satisfied) torrent keeps its selection."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="Alpha", state="downloading"),
            make_torrent(hash=HASH_B, name="Beta", state="pausedUP"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _select_all_and_open_preview(app, pilot)

        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await _settle(app, pilot)
        await pilot.press("escape")  # dismiss the Result
        await _settle(app, pilot)

        assert client.paused_hashes == [[HASH_A]]
        assert app.controller.state.selected_hashes == {HASH_B}


async def test_failure_retains_the_live_selection() -> None:
    """Any failure keeps the selection so the operator can retry
    deliberately -- nothing is silently dropped."""
    client = _RaisingMutationClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")],
        error=QbitConnectionError("Unable to connect to qBittorrent."),
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _select_all_and_open_preview(app, pilot)

        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await _settle(app, pilot)
        assert isinstance(app.screen, ResultScreen)
        await pilot.press("escape")
        await _settle(app, pilot)

        assert app.controller.state.selected_hashes == {HASH_A}


async def test_dismissing_a_result_never_dispatches_again() -> None:
    """Closing the Result modal must not re-apply, rebuild, or mutate."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _select_all_and_open_preview(app, pilot)

        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await _settle(app, pilot)
        assert client.paused_hashes == [[HASH_A]]

        for _ in range(3):
            await pilot.press("escape")
            await _settle(app, pilot)

        assert client.paused_hashes == [[HASH_A]]


async def test_one_refresh_follows_a_successful_mutation() -> None:
    """Exactly one immediate refresh is triggered after a real
    mutation -- not zero (state would look stale), not several."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _select_all_and_open_preview(app, pilot)
        reads_before = client.torrents_info_calls

        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await _settle(app, pilot)

        assert client.torrents_info_calls == reads_before + 1


# -- N-1: hidden Preview must never stay stuck in `applying` ---------------


async def test_hidden_preview_is_not_stranded_after_completion() -> None:
    """N-1 closed.

    A mutation completing while an unrelated modal sits above its
    Preview must (a) leave that unrelated modal completely alone, and
    (b) still terminate the Preview's logical `applying` state -- so
    that once the overlay is closed, Escape and Cancel can dismiss it.
    Previously the Preview stayed `applying=True` forever, with both its
    buttons disabled and every dismissal path refusing: the whole TUI
    was stuck after a mutation that had really been submitted.
    """
    client = _BlockingPauseClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _select_all_and_open_preview(app, pilot)
        preview = app.screen
        assert isinstance(preview, PreviewScreen)

        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await pilot.pause()
        await asyncio_wait_for_event(client.entered)

        app.push_screen(HelpScreen())
        await pilot.pause()

        client.release.set()
        await _settle(app, pilot)

        # The unrelated modal is untouched and still on top.
        assert isinstance(app.screen, HelpScreen)
        # The Preview exists, is no longer applying, and its controls
        # are usable again.
        assert preview in app.screen_stack
        assert preview.applying is False
        assert preview.query_one("#preview-cancel", Button).disabled is False
        assert "Applying" not in str(
            preview.query_one("#preview-apply", Button).label
        )

        await pilot.press("escape")  # close Help
        await pilot.pause()
        assert app.screen is preview

        await pilot.press("escape")  # dismiss the revealed Preview
        await pilot.pause()
        assert not any(isinstance(s, PreviewScreen) for s in app.screen_stack)
        # Still exactly one dispatch, and the result survived.
        assert client.paused_hashes == [[HASH_A]]
        assert app._last_mutation_result is not None


async def test_hidden_preview_cancel_button_dismisses_after_reveal() -> None:
    """N-1 closed: the visible Cancel control works too, not just Escape."""
    client = _BlockingPauseClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _select_all_and_open_preview(app, pilot)
        preview = app.screen

        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await pilot.pause()
        await asyncio_wait_for_event(client.entered)
        app.push_screen(HelpScreen())
        await pilot.pause()
        client.release.set()
        await _settle(app, pilot)

        await pilot.press("escape")  # close Help
        await pilot.pause()
        assert app.screen is preview

        await pilot.click(app.screen.query_one("#preview-cancel", Button))
        await pilot.pause()
        assert not any(isinstance(s, PreviewScreen) for s in app.screen_stack)


async def test_completion_with_no_surviving_preview_touches_no_screen() -> None:
    """N-1 closed, missing-Preview case: the result is preserved
    globally, no other screen is manipulated, the Preview is not
    recreated."""
    client = _BlockingPauseClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _select_all_and_open_preview(app, pilot)

        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await pilot.pause()
        await asyncio_wait_for_event(client.entered)

        app.pop_screen()  # the Preview is gone entirely
        app.push_screen(HelpScreen())
        await pilot.pause()

        client.release.set()
        await _settle(app, pilot)

        assert isinstance(app.screen, HelpScreen)
        assert not any(isinstance(s, PreviewScreen) for s in app.screen_stack)
        assert app._last_mutation_result is not None
        assert app._last_mutation_result.submitted_hashes == (HASH_A,)


# -- N-3: persistent last-action indicator --------------------------------


async def test_persistent_result_outlives_the_transient_notification() -> None:
    """N-3 closed: the durable record is a rendered line in the Torrents
    workspace, not a five-second toast.

    Textual toasts are dropped from the DOM on timeout; the last-action
    line is asserted to still be mounted, visible, and truthful after
    every notification has been cleared.
    """
    client = _BlockingPauseClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        bar = app.query_one("#last-action", LastActionBar)
        assert "visible" not in bar.classes  # hidden until a result exists

        await _select_all_and_open_preview(app, pilot)
        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await pilot.pause()
        await asyncio_wait_for_event(client.entered)
        app.push_screen(HelpScreen())  # the Preview is hidden
        await pilot.pause()
        client.release.set()
        await _settle(app, pilot)

        # Simulate every transient notification expiring.
        app._notifications.clear()
        app.refresh()
        await pilot.pause()

        assert "visible" in bar.classes
        text = str(bar.content)
        assert "Last action" in text
        assert "Pause" in text
        assert "submitted for 1 torrent(s)" in text


async def test_persistent_result_reflects_only_the_latest_operation() -> None:
    """N-3 closed: a stored result never alters a later operation, and
    the indicator shows the latest contract only -- no history."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="Alpha", state="downloading"),
            make_torrent(hash=HASH_B, name="Beta", state="pausedUP"),
        ]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)

        # Operation A: pause Alpha (Beta is skipped).
        await _select_all_and_open_preview(app, pilot)
        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await _settle(app, pilot)
        await pilot.press("escape")
        await _settle(app, pilot)
        first = app._last_mutation_result
        assert first is not None and first.submitted_hashes == (HASH_A,)

        # Operation B: resume Beta.
        await pilot.press("ctrl+d")
        await pilot.pause()
        await _settle(app, pilot)
        visible = app.controller.state.visible
        assert visible is not None
        rows = {t.hash for t in visible.matched}
        assert HASH_B in rows
        await pilot.press("ctrl+a")
        await pilot.press("a")
        await pilot.pause()
        await pilot.click(app.screen.query_one("#actions-resume", Button))
        await pilot.pause()
        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await _settle(app, pilot)

        second = app._last_mutation_result
        assert second is not None
        # The stored result was replaced, not appended to, and the older
        # one could not influence the newer operation.
        assert second.operation_id != first.operation_id
        assert second.action == "resume"
        bar = app.query_one("#last-action", LastActionBar)
        assert "Resume" in str(bar.content)
        assert "Pause" not in str(bar.content)


# -- R-2 residual: unexpected refresh exception fails closed --------------


class _UnclassifiableRefreshClient(FakeQbitClient):
    """Raises an exception `classify_recoverable_qbit_failure` rejects."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fail_reads = False

    def torrents_info(self) -> list[dict[str, Any]]:
        if self.fail_reads:
            raise RuntimeError("unclassifiable refresh defect")
        return super().torrents_info()


async def test_unexpected_refresh_exception_invalidates_open_preview() -> None:
    """R-2 residual closed: an unclassifiable refresh exception used to
    `return` before freshness invalidation, leaving an open Preview
    applicable while the TUI had stopped refreshing. Every unsuccessful
    refresh now fails closed."""
    client = _UnclassifiableRefreshClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _select_all_and_open_preview(app, pilot)
        preview = app.screen
        assert isinstance(preview, PreviewScreen)
        assert preview.can_apply is True

        client.fail_reads = True
        app._start_periodic_refresh()
        await _settle(app, pilot)

        assert preview.stale is True
        assert preview.can_apply is False
        assert preview.query_one("#preview-apply", Button).disabled is True

        # Keyboard Apply is rejected too, not just the button.
        app.action_apply_plan()
        await _settle(app, pilot)
        await pilot.press("enter")
        await _settle(app, pilot)
        assert client.paused_hashes == []

        # A later successful refresh does not revive the old Preview.
        client.fail_reads = False
        app._start_periodic_refresh()
        await _settle(app, pilot)
        assert preview.can_apply is False

        # But a rebuilt Preview is applicable again.
        await pilot.press("escape")
        await pilot.pause()
        await _select_all_and_open_preview(app, pilot)
        rebuilt = app.screen
        assert isinstance(rebuilt, PreviewScreen)
        assert rebuilt.can_apply is True
        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await _settle(app, pilot)
        assert client.paused_hashes == [[HASH_A]]


# -- cancelled-before-dispatch is a distinct, truthful outcome ------------


async def test_cancelled_before_dispatch_is_distinct_from_unavailable() -> None:
    """The queued-then-shutdown outcome must be distinguishable from a
    remote failure: nothing failed, nothing was sent."""
    client = _CallLogClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _select_all_and_open_preview(app, pilot)
        selected_before = set(app.controller.state.selected_hashes)
        probe = _AuthorityProbe(app)

        client.block_reads = True
        app._start_periodic_refresh()
        await pilot.pause()
        await asyncio_wait_for_event(client.read_entered)
        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await pilot.pause()
        await _await_flag(
            probe.entered, "the mutation worker to enter apply_bulk_plan"
        )

        await app.action_quit()
        await pilot.pause()
        client.block_reads = False
        client.read_release.set()
        await _await_flag(probe.returned, "the mutation worker to return")

        # Zero dispatch, and the selection was never treated as submitted.
        assert probe.verdicts == [False], probe.log
        assert client.pause_calls == 0
        assert client.paused_hashes == []
        assert app.controller.state.selected_hashes == selected_before

        # The classification itself is distinct from UNAVAILABLE.
        plan = app._active_mutation_plan or _frozen_plan_for(app)
        outcome = app._classify_mutation_outcome(plan, None, False)
        assert outcome.cancelled_before_dispatch is True
        assert outcome.error_category is None
        assert outcome.status is not MutationStatus.APPLIED
        assert outcome.submitted_hashes == ()
        text = _format_result_text(outcome)
        assert "Cancelled before dispatch" in text
        assert "Unavailable" not in text


def _frozen_plan_for(app: Any) -> Any:
    """The plan of the last preview the app owned, for classification."""
    return app.controller.build_bulk_plan("pause", (HASH_A,))


def test_cancelled_before_dispatch_never_reports_success() -> None:
    """Pure classification check, no UI: a non-dispatched mutation is
    never APPLIED and never carries submitted hashes."""
    from app.torrents import build_bulk_action_plan_from_snapshot

    plan = build_bulk_action_plan_from_snapshot(
        [make_torrent(hash=HASH_A, name="Alpha", state="downloading")],
        "pause",
        (HASH_A,),
    )
    outcome = MutationUiResult.from_plan(
        plan,
        MutationStatus.CANCELLED,
        operation_id=1,
        cancelled_before_dispatch=True,
    )
    assert outcome.submitted_hashes == ()
    assert outcome.cancelled_before_dispatch is True
    assert outcome.error_category is None
    assert "submitted for" not in _format_result_text(outcome)
