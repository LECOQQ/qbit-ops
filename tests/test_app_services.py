"""Tests for `classify_recoverable_qbit_failure`'s classification contract
and the `create_qbit_client()` config-loading wrapper."""

from __future__ import annotations

from typing import Any

import pytest

import qbit_ops.app_services as app_services
from qbit_core.errors import QbitAuthenticationError, QbitConnectionError
from qbit_ops.app_services import (
    RecoverableFailure,
    classify_recoverable_qbit_failure,
)
from tests.support import make_config


def test_create_qbit_client_loads_config_then_builds_the_core_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The zero-argument CLI/TUI entry point loads `.env`/environment
    configuration, then delegates to `qbit_core.qbit.client.create_qbit_client`
    -- the only place that knows how to build a `qbittorrentapi.Client`."""
    config = make_config()
    calls: list[Any] = []

    monkeypatch.setattr(app_services, "load_qbit_config", lambda: config)
    monkeypatch.setattr(
        app_services,
        "_create_qbit_client",
        lambda passed_config: calls.append(passed_config) or "the-client",
    )

    result = app_services.create_qbit_client()

    assert result == "the-client"
    assert calls == [config]


def test_authentication_error_is_classified_as_recoverable() -> None:
    result = classify_recoverable_qbit_failure(
        QbitAuthenticationError("bad credentials")
    )
    assert result == RecoverableFailure(
        "authentication_failed", "bad credentials"
    )


def test_connection_error_is_classified_as_recoverable() -> None:
    result = classify_recoverable_qbit_failure(
        QbitConnectionError("unreachable")
    )
    assert result == RecoverableFailure(
        "qbittorrent_unavailable", "unreachable"
    )


def test_bare_os_error_is_classified_as_recoverable() -> None:
    """`qbittorrentapi.APIConnectionError` is itself an `OSError` subclass."""
    result = classify_recoverable_qbit_failure(OSError("connection reset"))
    assert result == RecoverableFailure(
        "qbittorrent_unavailable", "connection reset"
    )


def test_unclassifiable_exception_returns_none_not_a_broad_catch() -> None:
    assert classify_recoverable_qbit_failure(RuntimeError("bug")) is None
    assert classify_recoverable_qbit_failure(TypeError("bug")) is None
    assert classify_recoverable_qbit_failure(ValueError("bug")) is None
