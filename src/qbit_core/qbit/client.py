"""Construct and authenticate the one qBittorrent client this project builds."""

from __future__ import annotations

from typing import Any

import qbittorrentapi

from qbit_core.config import QbitConfig
from qbit_core.errors import (
    InvalidInputError,
    QbitAuthenticationError,
    QbitConnectionError,
)

__all__ = [
    "create_qbit_client",
    "is_qbit_error",
]


def create_qbit_client(
    config: QbitConfig, *, request_timeout: float | None = None
) -> Any:
    """Create and authenticate a qBittorrent API client from `config`.

    Never prints or exits: raises `QbitAuthenticationError`/
    `QbitConnectionError` for the caller. No `.env`/environment
    knowledge -- callers resolve a `QbitConfig` first.

    `request_timeout` (seconds, applied to every request including the
    login) keeps a frozen qBittorrent from blocking a long-running
    consumer; `None` keeps the library default (no timeout).
    """
    extra: dict[str, Any] = {}
    if request_timeout is not None:
        if request_timeout <= 0:
            raise InvalidInputError(
                "request_timeout must be greater than 0 seconds."
            )
        extra["REQUESTS_ARGS"] = {"timeout": request_timeout}
    client = qbittorrentapi.Client(
        host=config.host,
        username=config.username,
        password=config.password,
        **extra,
    )

    try:
        client.auth_log_in()
    except Exception as error:
        if isinstance(error, qbittorrentapi.LoginFailed):
            raise QbitAuthenticationError(
                "Authentication to qBittorrent failed. Check QBIT_USER and "
                "QBIT_PASSWORD."
            ) from error
        if isinstance(error, qbittorrentapi.APIConnectionError):
            raise QbitConnectionError(
                f"Unable to connect to qBittorrent at {config.host}."
            ) from error

        raise QbitConnectionError(
            f"Unable to initialize qBittorrent client: {error}"
        ) from error

    return client


def is_qbit_error(error: Exception) -> bool:
    """Return whether an exception is a `qbittorrentapi.APIError`.

    Deliberately narrow: callers still handle
    `QbitAuthenticationError`/`QbitConnectionError`/`OSError` themselves.
    """
    return isinstance(error, qbittorrentapi.APIError)
