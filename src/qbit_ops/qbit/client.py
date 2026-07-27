"""Construct and authenticate the one qBittorrent client this project builds.

The single canonical client-construction path -- moved here from
`qbit_ops.app_services.create_qbit_client` (constat A-6/A-8's target
shape for this module: "qbit/client.py owns client construction and
client-library connection types"). `qbit_ops.app_services` re-exports
this function by narrow delegation, and `qbit_ops.main` in turn
re-exports it as `_create_qbit_client` under its historical name, so
every existing call site and test seam
(`tests/conftest.py::configure_qbit_backend` monkeypatches
`"qbit_ops.main._create_qbit_client"` by string path) keeps working
unchanged.

No behavior change from the prior implementation: same configuration
semantics, same authentication call, no added endpoint call, no eager
network connection beyond the one `auth_log_in()` this function always
performed, and no compatibility check. The one improvement is
`_is_qbit_error`'s classification, which now uses `isinstance` against
the client library's actual exception types (imported directly, per
this module's mandate to own client-library connection types) instead
of comparing `type(error).__name__` to a set of string literals
(constat P-6) -- an exception-hierarchy-aware check that also matches a
future subclass, with the exact same classification outcome for every
exception the prior string comparison already matched.
"""

from __future__ import annotations

from typing import Any

import qbittorrentapi

from qbit_ops.config import load_qbit_config
from qbit_ops.errors import QbitAuthenticationError, QbitConnectionError


def create_qbit_client() -> Any:
    """Create and authenticate a qBittorrent API client.

    Never prints or exits: raises `QbitAuthenticationError`/
    `QbitConnectionError` for the caller to render or classify.
    """
    config = load_qbit_config()
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
