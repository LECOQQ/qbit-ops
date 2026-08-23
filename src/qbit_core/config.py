"""Connection settings qbit_core needs to build a qBittorrent client.

No `.env`/environment discovery here -- that stays a `qbit_ops` concern.
The variable *names* live here anyway, so the reader and the writer of
the user env file cannot disagree about what they are called.
"""

from dataclasses import dataclass

__all__ = [
    "CONNECTION_VARIABLES",
    "HOST_VARIABLE",
    "PASSWORD_VARIABLE",
    "QbitConfig",
    "USER_VARIABLE",
]

HOST_VARIABLE = "QBIT_HOST"
USER_VARIABLE = "QBIT_USER"
PASSWORD_VARIABLE = "QBIT_PASSWORD"
CONNECTION_VARIABLES: tuple[str, str, str] = (
    HOST_VARIABLE,
    USER_VARIABLE,
    PASSWORD_VARIABLE,
)


@dataclass(frozen=True)
class QbitConfig:
    """Store qBittorrent connection settings."""

    host: str
    username: str
    password: str
