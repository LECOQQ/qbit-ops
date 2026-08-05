"""Connection settings qbit_core needs to build a qBittorrent client.

No `.env`/environment discovery here -- that stays a `qbit_ops`
(CLI) concern, since a future consumer (e.g. Waitarr) will load its
own configuration and only needs to build this value.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class QbitConfig:
    """Store qBittorrent connection settings."""

    host: str
    username: str
    password: str
