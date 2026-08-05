"""Construct and authenticate the one qBittorrent client this project builds."""

from __future__ import annotations

from typing import Any

import qbittorrentapi

from qbit_core.config import QbitConfig
from qbit_core.errors import QbitAuthenticationError, QbitConnectionError


def create_qbit_client(config: QbitConfig) -> Any:
    """Create and authenticate a qBittorrent API client from `config`.

    Never prints or exits: raises `QbitAuthenticationError`/
    `QbitConnectionError` for the caller to render or classify. Callers
    that load configuration from the environment (the CLI) resolve a
    `QbitConfig` first -- this function has no knowledge of `.env` or
    environment variables.
    """
    client = qbittorrentapi.Client(
        host=config.host,
        username=config.username,
        password=config.password,
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
