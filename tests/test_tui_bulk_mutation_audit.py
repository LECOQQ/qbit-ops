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
import threading
from pathlib import Path
from typing import Any

from textual.widgets import Button, Input, Static
from textual.worker import Worker, WorkerState

import app.tui
from app.config import ConfigError
from app.errors import ErrorCategory, QbitConnectionError
from app.tui.app import (
    MUTATION_WORKER_GROUP,
    HelpScreen,
    PreviewScreen,
    ResultScreen,
    _classify_mutation_error,
)
from tests.support import FakeQbitClient, make_torrent
from tests.test_tui_app import (
    WIDE_SIZE,
    BlockingTrackerClient,
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
        fake_worker = _FakeCompletedWorker(MUTATION_WORKER_GROUP, (True, None))
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

    for path in sorted(Path(app.tui.__file__).parent.glob("*.py")):
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


class _RefreshOverlapClient(FakeQbitClient):
    """Reports whether `torrents_pause()` ran while `torrents_info()` was
    still inside the call, on the same client object."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.armed = False
        self.inside_read = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.pause_overlapped_read = False

    def torrents_info(self) -> list[dict[str, Any]]:
        if not self.armed:
            return super().torrents_info()
        self.inside_read = True
        self.entered.set()
        try:
            if not self.release.wait(timeout=5.0):
                raise TimeoutError("test forgot to release the read")
            return super().torrents_info()
        finally:
            self.inside_read = False

    def torrents_pause(self, torrent_hashes: Any) -> None:
        if self.inside_read:
            self.pause_overlapped_read = True
        super().torrents_pause(torrent_hashes)


async def test_apply_never_overlaps_an_in_flight_refresh() -> None:
    """F-2 closed: `TuiController._remote_lock` serializes every
    blocking client call, so Apply now waits behind an in-flight
    `torrents_info()` instead of sharing the `requests.Session`."""
    client = _RefreshOverlapClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")]
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _select_all_and_open_preview(app, pilot)

        client.armed = True
        app._start_periodic_refresh()
        await pilot.pause()
        await asyncio_wait_for_event(client.entered)

        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await pilot.pause()
        await _pump_until(pilot, lambda: bool(client.paused_hashes))

        client.release.set()
        await _settle(app, pilot)

        assert client.pause_overlapped_read is False


class _DetailOverlapClient(BlockingTrackerClient):
    """Reports whether `torrents_pause()` ran while `torrents_trackers()`
    was still inside the call, on the same client object."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.inside_tracker = False
        self.pause_overlapped_detail = False

    def torrents_trackers(self, torrent_hash: str) -> list[dict[str, Any]]:
        self.inside_tracker = True
        try:
            return super().torrents_trackers(torrent_hash)
        finally:
            self.inside_tracker = False

    def torrents_pause(self, torrent_hashes: Any) -> None:
        if self.inside_tracker:
            self.pause_overlapped_detail = True
        super().torrents_pause(torrent_hashes)


async def test_apply_never_overlaps_an_in_flight_detail_fetch() -> None:
    """F-2 closed, detail-fetch half: same single coordinator, so Apply
    also waits behind an in-flight `torrents_trackers()`."""
    client = _DetailOverlapClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")],
        trackers_by_hash={HASH_A: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        client.release_event(HASH_A).set()  # let the mount-time fetch through
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _settle(app, pilot)

        # Arm a blocking manual detail refresh, then apply on top of it.
        client.release_event(HASH_A).clear()
        client.entered_event(HASH_A).clear()
        await pilot.press("r")
        await pilot.pause()
        await asyncio_wait_for_event(client.entered_event(HASH_A))

        await _select_all_and_open_preview(app, pilot)
        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await pilot.pause()
        await _pump_until(pilot, lambda: bool(client.paused_hashes))

        client.release_event(HASH_A).set()
        await _settle(app, pilot)

        assert client.pause_overlapped_detail is False


async def test_max_simultaneous_remote_client_operations_is_one() -> None:
    """F-2 closed, measured directly: the controller's own high-water
    mark of concurrent remote operations never exceeds 1, across a
    refresh, a detail fetch, and a mutation all contending at once.

    This measures actual overlap inside `_remote_operation()` rather
    than merely asserting a lock object exists.
    """
    client = _DetailOverlapClient(
        torrents=[make_torrent(hash=HASH_A, name="Alpha", state="downloading")],
        trackers_by_hash={HASH_A: []},
    )
    app = _app(client)

    async with app.run_test(size=WIDE_SIZE) as pilot:
        client.release_event(HASH_A).set()
        await _settle(app, pilot)
        await _goto_torrents(app, pilot)
        await _settle(app, pilot)

        # Contend: a manual detail refresh, a periodic refresh, and a
        # mutation, dispatched back-to-back without awaiting each other.
        client.release_event(HASH_A).clear()
        client.entered_event(HASH_A).clear()
        await pilot.press("r")
        await pilot.pause()
        await asyncio_wait_for_event(client.entered_event(HASH_A))

        app._start_periodic_refresh()
        await _select_all_and_open_preview(app, pilot)
        await pilot.click(app.screen.query_one("#preview-apply", Button))
        await pilot.pause()

        client.release_event(HASH_A).set()
        await _settle(app, pilot)

        assert app.controller.max_concurrent_remote_operations == 1


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
            (stale_preview.operation_id + 999, None),
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
