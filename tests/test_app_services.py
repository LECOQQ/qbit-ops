"""Tests for `classify_recoverable_qbit_failure`'s classification contract."""

from __future__ import annotations

from qbit_ops.app_services import (
    RecoverableFailure,
    classify_recoverable_qbit_failure,
)
from qbit_ops.errors import QbitAuthenticationError, QbitConnectionError


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
