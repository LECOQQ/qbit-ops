"""The TUI's first-run connection form.

Headless (`App.run_test()`), no real terminal and no real qBittorrent:
the connection attempt is patched at
`qbit_core.features.connection_setup.create_qbit_client`, the same seam
`tests/test_init_cli.py` uses -- so both façades are proven to go
through one domain path rather than each having its own.
"""

from __future__ import annotations

import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, Input

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
