"""Shared pytest fixtures for CLI-level tests."""

from collections.abc import Callable
from typing import Any

import pytest
from typer.testing import CliRunner

import app.main
from app.config import QbitConfig
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
    """Return a function that stubs `app.main`'s qBittorrent client wiring.

    This replaces `app.main.load_qbit_config` and
    `app.main._create_qbit_client` directly, which is the smallest seam
    that lets CLI tests exercise every command without a real
    qBittorrent instance or `.env` file.

    Returns the list of kwargs each `_create_qbit_client()` call
    received, so tests can assert on the call path itself (e.g.
    `quiet=True`) rather than only on rendered output — CliRunner's
    non-terminal stderr already makes `spinner()` a no-op regardless of
    `quiet`, so output-only assertions cannot catch a silence-contract
    regression there.
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

            monkeypatch.setattr(
                "app.main.load_qbit_config",
                _raise_config_error,
            )
            return calls

        monkeypatch.setattr(
            "app.main.load_qbit_config",
            lambda: config or make_config(),
        )

        if client_error is not None:

            def _raise_client_error(**kwargs: Any) -> Any:
                calls.append(kwargs)
                raise client_error

            monkeypatch.setattr(
                "app.main._create_qbit_client",
                _raise_client_error,
            )
            return calls

        def _fake_create_qbit_client(**kwargs: Any) -> Any:
            calls.append(kwargs)
            return client

        monkeypatch.setattr(
            "app.main._create_qbit_client",
            _fake_create_qbit_client,
        )
        return calls

    return _configure


@pytest.fixture
def configure_qbit_backend_by_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> ConfigureQbitBackend:
    """Same seam as `configure_qbit_backend`, patched by module reference.

    `configure_qbit_backend` patches `"app.main.load_qbit_config"` and
    `"app.main._create_qbit_client"` by dotted string, which couples
    every test using it to that exact module path (constant A-7 in
    `docs/audits/2026-07-codebase-architecture-inventory.md`) -- a
    future package move breaks every such patch simultaneously. This
    fixture patches the same two names on the imported `app.main`
    module object instead, so a future migration only has to update the
    single `import app.main` above rather than every string literal
    across the suite. Not a replacement: existing tests keep using
    `configure_qbit_backend` unchanged; new tests may opt into this one.
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

            monkeypatch.setattr(
                app.main,
                "load_qbit_config",
                _raise_config_error,
            )
            return calls

        monkeypatch.setattr(
            app.main,
            "load_qbit_config",
            lambda: config or make_config(),
        )

        if client_error is not None:

            def _raise_client_error(**kwargs: Any) -> Any:
                calls.append(kwargs)
                raise client_error

            monkeypatch.setattr(
                app.main,
                "_create_qbit_client",
                _raise_client_error,
            )
            return calls

        def _fake_create_qbit_client(**kwargs: Any) -> Any:
            calls.append(kwargs)
            return client

        monkeypatch.setattr(
            app.main,
            "_create_qbit_client",
            _fake_create_qbit_client,
        )
        return calls

    return _configure
