"""Reusable qBittorrent operations core, independent of any CLI or TUI.

Never imports Typer, Rich, or Textual; never prints, prompts, or calls
`sys.exit`. `__all__` is deliberately small (connection + errors);
business operations live in their explicit feature module, e.g.
`from qbit_core.features.torrents import pause_torrents_by_hash`.

Minimal usage::

    from qbit_core import QbitConfig, create_qbit_client
    from qbit_core.features.torrents import (
        list_torrent_snapshots,
        pause_torrents_by_hash,
        resume_torrents_by_hash,
    )

    client = create_qbit_client(
        QbitConfig(
            host="http://localhost:8080", username="admin", password="..."
        )
    )
    torrents = list_torrent_snapshots(client)

    # should_pause_for_playback is a consumer policy, not qbit_core's.
    candidate_hashes = [
        t.hash for t in torrents if should_pause_for_playback(t)
    ]
    pause_result = pause_torrents_by_hash(client, candidate_hashes)

    # Only changed_hashes were actually paused by this call.
    resume_result = resume_torrents_by_hash(
        client, pause_result.changed_hashes
    )
"""

from qbit_core.config import QbitConfig
from qbit_core.errors import (
    InvalidInputError,
    QbitAuthenticationError,
    QbitConnectionError,
    QbitCoreError,
)
from qbit_core.qbit.client import create_qbit_client

__all__ = [
    "QbitConfig",
    "create_qbit_client",
    "QbitCoreError",
    "InvalidInputError",
    "QbitConnectionError",
    "QbitAuthenticationError",
]
