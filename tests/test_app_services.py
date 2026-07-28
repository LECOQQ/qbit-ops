"""Characterize `classify_recoverable_qbit_failure`'s classification contract.

This is the single shared recoverable/internal boundary the CLI's
status/watch collection (`qbit_ops.cli.commands.status`) and the TUI's
refresh worker both apply (see
`qbit_ops.app_services`). An unclassifiable exception must return `None`
so the caller lets it propagate as an internal error -- never silently
degraded to "unavailable" by a broad catch-all.
"""

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
    """An unexpected programming defect must never be silently degraded.

    `classify_recoverable_qbit_failure` returning anything other than
    `None` here would mean a broad `except Exception` swallowed a real
    bug into a fake "unavailable" -- callers rely on `None` to let it
    propagate as an internal error instead.
    """
    assert classify_recoverable_qbit_failure(RuntimeError("bug")) is None
    assert classify_recoverable_qbit_failure(TypeError("bug")) is None
    assert classify_recoverable_qbit_failure(ValueError("bug")) is None
