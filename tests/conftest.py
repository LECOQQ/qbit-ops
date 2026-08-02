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

from collections.abc import Callable
from typing import Any

import pytest
from typer.testing import CliRunner

import qbit_ops.cli.error_boundary
import qbit_ops.qbit.client
from qbit_ops.config import QbitConfig
from tests.support import make_config

ConfigureQbitBackend = Callable[..., list[dict[str, Any]]]


@pytest.fixture
def runner() -> CliRunner:
    """Provide a Typer CLI test runner."""
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
            # reach `qbit_ops.qbit.client`'s own imported reference, not
            # this module's -- patch both seams so every command sees
            # the injected error, not the real on-disk config.
            monkeypatch.setattr(
                "qbit_ops.cli.error_boundary.load_qbit_config",
                _raise_config_error,
            )
            monkeypatch.setattr(
                "qbit_ops.qbit.client.load_qbit_config",
                _raise_config_error,
            )
            return calls

        monkeypatch.setattr(
            "qbit_ops.cli.error_boundary.load_qbit_config",
            lambda: config or make_config(),
        )
        monkeypatch.setattr(
            "qbit_ops.qbit.client.load_qbit_config",
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
                qbit_ops.qbit.client,
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
            qbit_ops.qbit.client,
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
