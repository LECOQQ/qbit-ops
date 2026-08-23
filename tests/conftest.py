"""Shared pytest fixtures for CLI-level tests."""

import os

# Typer force-enables Rich's colored/styled `--help` rendering whenever
# `GITHUB_ACTIONS` is set (so it reads nicely in the Actions log
# viewer). That styling splits option flags like `--format` across
# multiple ANSI spans, breaking plain-text substring assertions on
# `result.output` in CI only. Must run before `typer.rich_utils` is
# first imported (lazily, on the first `--help` render) -- setting it
# here, before any other import, guarantees that.
os.environ.setdefault("_TYPER_FORCE_DISABLE_TERMINAL", "1")

# Rich derives its render width from `COLUMNS`, falling back to whatever
# the ambient terminal reports. Assertions that look for a full torrent
# hash or a wide table cell therefore pass or fail on terminal geometry:
# under `pytest -n`, xdist workers inherit a narrower default, Rich wraps
# the cell, and the substring is split across two lines. Pinned (not
# `setdefault`) so the rendered width is identical in a terminal, in CI,
# and across xdist workers -- an ambient `COLUMNS` must not leak in.
os.environ["COLUMNS"] = "200"

import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import qbit_ops.app_services
import qbit_ops.cli.error_boundary
from qbit_ops.config import APP_ENV_FILE_VARIABLE, QbitConfig
from tests.support import make_config

ConfigureQbitBackend = Callable[..., list[dict[str, Any]]]

# The fake instance every test resolves to. Unroutable on purpose: if
# isolation ever breaks and something really dials out, it fails fast
# instead of reaching a live qBittorrent.
ISOLATED_QBIT_HOST = "http://qbit-ops-isolated.invalid:1"


@pytest.fixture(autouse=True, scope="session")
def _isolate_qbit_configuration() -> Any:
    """Cut every path `qbit_ops.config` can take to a real instance.

    `_get_default_env_files()` resolves `cwd()/.env` and
    `$XDG_CONFIG_HOME|~/.config/qbit-ops/.env`, and a developer machine
    has both. Nothing in the suite is required to opt into isolation, so
    a single test that builds a client without `configure_qbit_backend`
    would reach the operator's own qBittorrent.

    Session-scoped and `autouse`, so isolation is the default rather
    than something each test remembers. Four seams, because any one of
    them alone leaves a way through:

    - `QBIT_OPS_ENV_FILE` short-circuits file resolution entirely
      (`config._load_env_files` returns right after loading it), so
      pointing it at a path that cannot exist neutralizes both default
      locations at once;
    - `HOME` and `XDG_CONFIG_HOME` are redirected anyway, so anything
      resolving a user path outside that short-circuit lands nowhere;
    - `QBIT_HOST`/`QBIT_USER`/`QBIT_PASSWORD` are set to fakes, and
      `load_dotenv(override=False)` means a real `.env` can never
      overwrite them.

    Enforced by `tests/test_config_isolation.py`, which exercises the
    real resolution rather than asserting on these variables.

    This covers `pytest`. It cannot cover an arbitrary Python or shell
    command run outside it -- see `.agents/WORKFLOW.md`.
    """
    monkeypatch = pytest.MonkeyPatch()
    isolated_home = Path(tempfile.mkdtemp(prefix="qbit-ops-isolated-home-"))
    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_home / ".config"))
    monkeypatch.setenv(
        APP_ENV_FILE_VARIABLE, str(isolated_home / "absent" / ".env")
    )
    monkeypatch.setenv("QBIT_HOST", ISOLATED_QBIT_HOST)
    monkeypatch.setenv("QBIT_USER", "isolated-user")
    monkeypatch.setenv("QBIT_PASSWORD", "isolated-password")
    yield
    monkeypatch.undo()
    shutil.rmtree(isolated_home, ignore_errors=True)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def configure_qbit_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> ConfigureQbitBackend:
    """Stub the CLI's qBittorrent client wiring.

    Returns the list of kwargs each `_create_qbit_client()` call received,
    so tests can assert on the call path itself (e.g. `quiet=True`) rather
    than only on rendered output.
    """

    def _configure(
        *,
        client: Any | None = None,
        config: QbitConfig | None = None,
        config_error: Exception | None = None,
        client_error: Exception | None = None,
    ) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []

        if config_error is not None:

            def _raise_config_error() -> QbitConfig:
                raise config_error

            # Commands that call `error_boundary.create_qbit_client()`
            # directly (skipping their own `load_qbit_config()` call)
            # reach `qbit_ops.app_services`'s own imported reference, not
            # this module's -- patch both seams so every command sees
            # the injected error, not the real on-disk config.
            monkeypatch.setattr(
                "qbit_ops.cli.error_boundary.load_qbit_config",
                _raise_config_error,
            )
            monkeypatch.setattr(
                "qbit_ops.app_services.load_qbit_config",
                _raise_config_error,
            )
            return calls

        monkeypatch.setattr(
            "qbit_ops.cli.error_boundary.load_qbit_config",
            lambda: config or make_config(),
        )
        monkeypatch.setattr(
            "qbit_ops.app_services.load_qbit_config",
            lambda: config or make_config(),
        )

        if client_error is not None:

            def _raise_client_error(**kwargs: Any) -> Any:
                calls.append(kwargs)
                raise client_error

            monkeypatch.setattr(
                "qbit_ops.cli.error_boundary.create_qbit_client",
                _raise_client_error,
            )
            return calls

        def _fake_create_qbit_client(**kwargs: Any) -> Any:
            calls.append(kwargs)
            return client

        monkeypatch.setattr(
            "qbit_ops.cli.error_boundary.create_qbit_client",
            _fake_create_qbit_client,
        )
        return calls

    return _configure


@pytest.fixture
def configure_qbit_backend_by_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> ConfigureQbitBackend:
    """Same seam as `configure_qbit_backend`, patched via the module object
    instead of a dotted string.
    """

    def _configure(
        *,
        client: Any | None = None,
        config: QbitConfig | None = None,
        config_error: Exception | None = None,
        client_error: Exception | None = None,
    ) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []

        if config_error is not None:

            def _raise_config_error() -> QbitConfig:
                raise config_error

            # See `configure_qbit_backend` above: patch both the
            # `error_boundary` and `qbit.client` seams, since not every
            # command reaches config loading through `error_boundary`.
            monkeypatch.setattr(
                qbit_ops.cli.error_boundary,
                "load_qbit_config",
                _raise_config_error,
            )
            monkeypatch.setattr(
                qbit_ops.app_services,
                "load_qbit_config",
                _raise_config_error,
            )
            return calls

        monkeypatch.setattr(
            qbit_ops.cli.error_boundary,
            "load_qbit_config",
            lambda: config or make_config(),
        )
        monkeypatch.setattr(
            qbit_ops.app_services,
            "load_qbit_config",
            lambda: config or make_config(),
        )

        if client_error is not None:

            def _raise_client_error(**kwargs: Any) -> Any:
                calls.append(kwargs)
                raise client_error

            monkeypatch.setattr(
                qbit_ops.cli.error_boundary,
                "create_qbit_client",
                _raise_client_error,
            )
            return calls

        def _fake_create_qbit_client(**kwargs: Any) -> Any:
            calls.append(kwargs)
            return client

        monkeypatch.setattr(
            qbit_ops.cli.error_boundary,
            "create_qbit_client",
            _fake_create_qbit_client,
        )
        return calls

    return _configure
