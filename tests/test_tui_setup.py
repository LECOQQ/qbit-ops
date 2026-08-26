"""The TUI's first-run connection form.

Headless (`App.run_test()`), no real terminal and no real qBittorrent:
the connection attempt is patched at
`qbit_core.features.connection_setup.create_qbit_client`, the same seam
`tests/test_init_cli.py` uses -- so both façades are proven to go
through one domain path rather than each having its own.
"""

from __future__ import annotations

import stat
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, Input, Static

from qbit_core.config import CONNECTION_VARIABLES, QbitConfig
from qbit_core.errors import QbitConnectionError
from qbit_core.features.connection_setup import ENV_FILE_MODE, render_env_file
from qbit_ops.config import APP_ENV_FILE_VARIABLE, get_user_env_file
from qbit_ops.tui.app import QbitOpsTuiApp
from qbit_ops.tui.modals.setup import SetupScreen
from tests.support import FakeQbitClient

pytestmark = pytest.mark.tui

LARGE_INTERVAL = 999.0
HOST = "http://qbit.example:8080"
USER = "operator"
PASSWORD = "s3cr3t"
EXPECTED = QbitConfig(host=HOST, username=USER, password=PASSWORD)
WAIT_TIMEOUT = 5.0  # seconds a test will wait on a real threading.Event


@pytest.fixture
def isolated_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv(APP_ENV_FILE_VARIABLE, raising=False)
    for variable in CONNECTION_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    working_directory = tmp_path / "cwd"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)
    yield get_user_env_file()


def _connection(
    monkeypatch: pytest.MonkeyPatch, *, error: Exception | None = None
) -> list[QbitConfig]:
    attempts: list[QbitConfig] = []

    def _factory(config: QbitConfig) -> Any:
        attempts.append(config)
        if error is not None:
            raise error
        return object()

    monkeypatch.setattr(
        "qbit_core.features.connection_setup.create_qbit_client", _factory
    )
    return attempts


def _app(
    *, needs_setup: bool, clients: list[FakeQbitClient] | None = None
) -> QbitOpsTuiApp:
    created = clients if clients is not None else []

    def _client_factory() -> FakeQbitClient:
        client = FakeQbitClient()
        created.append(client)
        return client

    return QbitOpsTuiApp(
        client_factory=_client_factory,
        host=None,
        refresh_interval=LARGE_INTERVAL,
        needs_setup=needs_setup,
    )


async def _settle(app: QbitOpsTuiApp, pilot: Pilot) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause()


def _form(app: QbitOpsTuiApp) -> SetupScreen:
    screen = app._setup_screen()
    assert isinstance(screen, SetupScreen)
    return screen


async def _fill_form(
    app: QbitOpsTuiApp, pilot: Pilot, *, password: str = PASSWORD
) -> None:
    screen = _form(app)
    screen.query_one("#setup-host", Input).value = HOST
    screen.query_one("#setup-user", Input).value = USER
    screen.query_one("#setup-password", Input).value = password
    await pilot.pause()


async def _press_save(app: QbitOpsTuiApp, pilot: Pilot) -> None:
    _form(app).query_one("#setup-save", Button).press()
    await _settle(app, pilot)


# --- when the form appears -----------------------------------------------


async def test_a_configured_tui_never_shows_the_form(
    isolated_target: Path,
) -> None:
    clients: list[FakeQbitClient] = []
    app = _app(needs_setup=False, clients=clients)

    async with app.run_test() as pilot:
        await _settle(app, pilot)

        assert app._setup_screen() is None
        assert clients, "a configured TUI refreshes immediately"


async def test_an_unconfigured_tui_shows_the_form_and_contacts_nothing(
    isolated_target: Path,
) -> None:
    clients: list[FakeQbitClient] = []
    app = _app(needs_setup=True, clients=clients)

    async with app.run_test() as pilot:
        await _settle(app, pilot)

        assert isinstance(app._setup_screen(), SetupScreen)
        assert clients == [], "nothing may be contacted before setup"
        # The per-second rate sampler is the one timer that reaches
        # qBittorrent on its own, and workspace visibility is rendered
        # before the form is pushed -- so it is gated on the refresh
        # having started, not on the order those two happen in.
        assert app._sample_timer is None


async def test_escape_does_not_dismiss_the_form(isolated_target: Path) -> None:
    app = _app(needs_setup=True)

    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await pilot.press("escape")
        await pilot.pause()

        assert isinstance(app.screen, SetupScreen)


# --- the happy path ------------------------------------------------------


async def test_a_successful_form_writes_and_then_shows_the_library(
    isolated_target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = _connection(monkeypatch)
    clients: list[FakeQbitClient] = []
    app = _app(needs_setup=True, clients=clients)

    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await _fill_form(app, pilot)
        await _press_save(app, pilot)

        assert attempts == [EXPECTED]
        assert app._setup_screen() is None
        assert clients, "the library refresh starts once the file is written"

    assert isolated_target.read_text(encoding="utf-8") == render_env_file(
        EXPECTED
    )


async def test_the_file_the_form_writes_is_owner_only(
    isolated_target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _connection(monkeypatch)
    app = _app(needs_setup=True)

    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await _fill_form(app, pilot)
        await _press_save(app, pilot)

    assert stat.S_IMODE(isolated_target.stat().st_mode) == ENV_FILE_MODE


async def test_the_written_host_becomes_the_reported_instance(
    isolated_target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale host would mislabel every refresh that follows."""
    _connection(monkeypatch)
    app = _app(needs_setup=True)

    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await _fill_form(app, pilot)
        await _press_save(app, pilot)

        assert app.controller._host == HOST


# --- a failed test informs, it does not cancel ---------------------------


async def test_a_failed_connection_offers_the_write_instead_of_cancelling(
    isolated_target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _connection(monkeypatch, error=QbitConnectionError("Connection refused."))
    app = _app(needs_setup=True)

    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await _fill_form(app, pilot)
        await _press_save(app, pilot)

        screen = app._setup_screen()
        assert isinstance(screen, SetupScreen)
        assert screen.confirming is True
        assert not isolated_target.exists()

        await _press_save(app, pilot)

        assert app._setup_screen() is None

    assert isolated_target.is_file()


# --- an existing file is never overwritten unasked ------------------------


async def test_an_existing_file_is_confirmed_before_being_overwritten(
    isolated_target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reached whenever the file exists but does not load -- including
    a file where only QBIT_PASSWORD is missing."""
    isolated_target.parent.mkdir(parents=True)
    isolated_target.write_text("QBIT_HOST=http://old:1\n", encoding="utf-8")
    _connection(monkeypatch)
    app = _app(needs_setup=True)

    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await _fill_form(app, pilot)
        await _press_save(app, pilot)

        screen = app._setup_screen()
        assert isinstance(screen, SetupScreen)
        assert screen.confirming is True
        assert (
            isolated_target.read_text(encoding="utf-8")
            == "QBIT_HOST=http://old:1\n"
        )

        await _press_save(app, pilot)

    assert isolated_target.read_text(encoding="utf-8") == render_env_file(
        EXPECTED
    )


# --- local validation ----------------------------------------------------


async def test_a_blank_answer_writes_nothing_and_keeps_the_form_open(
    isolated_target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = _connection(monkeypatch)
    app = _app(needs_setup=True)

    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await _fill_form(app, pilot, password="   ")
        await _press_save(app, pilot)

        screen = app._setup_screen()
        assert isinstance(screen, SetupScreen)
        assert screen.confirming is False
        assert attempts == [], "an invalid form must not be tested remotely"

    assert not isolated_target.exists()


# --- reconfiguring an already-running TUI ---------------------------------
#
# `SetupScreen` and `_complete_setup` are shared with first run; what is
# new here is what happens to the *running* connection: `TuiController.
# _client` is cached, so pointing `_host` at a new instance is not enough
# to make the next refresh actually use it (see `TuiController.
# reset_connection`).

OLD_HOST = "http://old.example:8080"
NEW_HOST = "http://new.example:8080"
NEW_USER = "newop"
NEW_PASSWORD = "s3cr3t2"


def _sequential_client_factory(
    clients: list[FakeQbitClient],
) -> tuple[Any, list[FakeQbitClient]]:
    """A `client_factory` that hands out `clients` in order, repeating
    the last one past the end -- so a stray extra refresh tick never
    raises `StopIteration` and fails the test for the wrong reason."""
    created: list[FakeQbitClient] = []
    remaining = iter(clients)

    def _factory() -> FakeQbitClient:
        client = next(remaining, clients[-1])
        created.append(client)
        return client

    return _factory, created


def _running_app(
    clients: list[FakeQbitClient],
) -> tuple[QbitOpsTuiApp, list[FakeQbitClient]]:
    """An already-configured app (`needs_setup=False`): the dashboard is
    live, exactly the state a reconfigure starts from."""
    factory, created = _sequential_client_factory(clients)
    app = QbitOpsTuiApp(
        client_factory=factory,
        host=OLD_HOST,
        refresh_interval=LARGE_INTERVAL,
        needs_setup=False,
    )
    return app, created


async def _open_reconfigure(app: QbitOpsTuiApp, pilot: Pilot) -> None:
    await pilot.press("ctrl+o")
    await _settle(app, pilot)


def _write_existing_env_file(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f'QBIT_HOST="{OLD_HOST}"\n', encoding="utf-8")


async def test_reconfiguring_a_running_tui_talks_to_the_new_instance(
    isolated_target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The central behaviour of this work item: reconfiguring while
    connected must not leave the TUI silently talking to the instance it
    was already pointed at.

    `TuiController._client` is only ever created once and then cached
    (`_ensure_client`); if `reset_connection` did not drop it, every
    refresh after this test's reconfigure would keep calling into
    `old_client`, and the assertions below on the *second* client and on
    `created` would fail instead.
    """
    _write_existing_env_file(isolated_target)
    _connection(monkeypatch)
    old_client = FakeQbitClient(categories={"old-cat": {}}, tags=["old-tag"])
    new_client = FakeQbitClient(categories={"new-cat": {}}, tags=["new-tag"])
    app, created = _running_app([old_client, new_client])

    async with app.run_test() as pilot:
        await _settle(app, pilot)
        assert created == [old_client]
        assert app.controller.state.categories_available == ("old-cat",)
        # The Overview's own per-second sampler is already running
        # against the old instance -- its reading must not survive into
        # the new instance's graph window.
        assert app.controller.state.rate_history.measured >= 1

        await _open_reconfigure(app, pilot)
        assert isinstance(app.screen, SetupScreen)
        screen = _form(app)
        screen.query_one("#setup-host", Input).value = NEW_HOST
        screen.query_one("#setup-user", Input).value = NEW_USER
        screen.query_one("#setup-password", Input).value = NEW_PASSWORD
        await pilot.pause()

        # First Save: the file already exists (this is a reconfigure,
        # not a first run) -- reported, not written yet.
        await _press_save(app, pilot)
        assert isinstance(app.screen, SetupScreen)
        assert app._setup_screen() is not None
        assert screen.confirming is True

        # Second Save: confirms the overwrite.
        await _press_save(app, pilot)

        assert app._setup_screen() is None
        assert created == [old_client, new_client], (
            "reset_connection must drop the cached client so the next "
            "refresh reconnects instead of reusing the old instance"
        )
        assert app.controller._host == NEW_HOST
        assert app.controller.state.categories_available == ("new-cat",)
        assert app.controller.state.tags_available == ("new-tag",)
        assert app.controller.state.rate_history.measured == 0

    assert isolated_target.read_text(encoding="utf-8") == render_env_file(
        QbitConfig(host=NEW_HOST, username=NEW_USER, password=NEW_PASSWORD)
    )


async def test_reconfiguring_does_not_stack_a_second_refresh_timer(
    isolated_target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: `_begin_refreshing` used to call `set_interval`
    unconditionally, so every reconfigure registered one more periodic-
    refresh timer on top of the one from startup -- permanently doubling
    (then tripling, ...) the refresh cadence rather than replacing it.
    """
    _write_existing_env_file(isolated_target)
    _connection(monkeypatch)
    app, _created = _running_app([FakeQbitClient(), FakeQbitClient()])

    interval_calls: list[Any] = []
    real_set_interval = QbitOpsTuiApp.set_interval

    def _spy(
        self: QbitOpsTuiApp, interval: float, callback: Any, **kwargs: Any
    ) -> Any:
        if callback == self._start_periodic_refresh:
            interval_calls.append(interval)
        return real_set_interval(self, interval, callback, **kwargs)

    monkeypatch.setattr(QbitOpsTuiApp, "set_interval", _spy)

    async with app.run_test() as pilot:
        await _settle(app, pilot)
        assert len(interval_calls) == 1

        await _open_reconfigure(app, pilot)
        screen = _form(app)
        screen.query_one("#setup-host", Input).value = NEW_HOST
        screen.query_one("#setup-user", Input).value = NEW_USER
        screen.query_one("#setup-password", Input).value = NEW_PASSWORD
        await pilot.pause()
        await _press_save(app, pilot)
        await _press_save(app, pilot)

        assert app._setup_screen() is None
        assert (
            len(interval_calls) == 1
        ), "a reconfigure must not register a second periodic-refresh timer"


async def test_escape_cancels_a_reconfigure_and_keeps_the_old_connection(
    isolated_target: Path,
) -> None:
    """Unlike the first-run form, a reconfigure has a dashboard to
    return to -- escape must cancel back to it instead of being a
    no-op (see `test_escape_does_not_dismiss_the_form` for the first-run
    case, which stays a no-op)."""
    app, created = _running_app([FakeQbitClient()])

    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await _open_reconfigure(app, pilot)
        assert isinstance(app.screen, SetupScreen)

        await pilot.press("escape")
        await _settle(app, pilot)

        assert app._setup_screen() is None
        assert app.controller._host == OLD_HOST
        assert len(created) == 1, "cancelling must not reconnect"

    assert not isolated_target.exists()


async def test_escaping_a_reconfigure_drops_a_stale_test_result(
    isolated_target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A setup-test worker still in flight when the operator cancels
    must not later apply its result to whatever `SetupScreen` opens
    next -- see `action_dismiss_overlay`'s `SetupScreen` branch.

    Uses a real `threading.Event` (never an arbitrary sleep, per this
    module's docstring) to hold the background connection attempt open
    until escape has definitely already been pressed.
    """
    _write_existing_env_file(isolated_target)
    release = threading.Event()
    attempts: list[QbitConfig] = []

    def _blocking_factory(config: QbitConfig) -> Any:
        attempts.append(config)
        assert release.wait(timeout=WAIT_TIMEOUT), "test setup deadlocked"
        return object()

    monkeypatch.setattr(
        "qbit_core.features.connection_setup.create_qbit_client",
        _blocking_factory,
    )
    app, _created = _running_app([FakeQbitClient()])

    async with app.run_test() as pilot:
        await _settle(app, pilot)

        await _open_reconfigure(app, pilot)
        first = _form(app)
        first.query_one("#setup-host", Input).value = NEW_HOST
        first.query_one("#setup-user", Input).value = NEW_USER
        first.query_one("#setup-password", Input).value = NEW_PASSWORD
        await pilot.pause()
        first.query_one("#setup-save", Button).press()
        await pilot.pause()
        assert attempts, "the worker should already be testing the connection"

        # Cancel while that worker is still blocked on `release`.
        await pilot.press("escape")
        await pilot.pause()
        assert app._setup_screen() is None

        # Not `_open_reconfigure`/`_settle`: the first worker is still
        # blocked on `release`, so waiting for every worker here would
        # wait for it too.
        await pilot.press("ctrl+o")
        await pilot.pause()
        second = _form(app)
        assert second is not first

        release.set()
        await _settle(app, pilot)

        # The abandoned worker's result (a stale "file already exists")
        # must not have reached the screen open when it landed.
        assert second.confirming is False
        assert str(second.query_one("#setup-status", Static).content) == ""
