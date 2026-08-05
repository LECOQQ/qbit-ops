"""Reusable qBittorrent operations core, independent of any CLI or TUI.

Never imports Typer, Rich, or Textual; never prints, prompts, or calls
`sys.exit`. Callers get Python objects back or a `QbitCoreError`
subclass raised. See docs/ARCHITECTURE.md.

This module's `__all__` is the intentionally small, stable public
surface: connection setup and the shared error hierarchy. Business
operations (listing, selecting, pausing, resuming, ...) are not
re-exported here -- import them from their explicit feature module,
e.g. `from qbit_core.features.torrents import pause_torrents_by_hash`.

Minimal usage::

    from qbit_core import QbitConfig, create_qbit_client
    from qbit_core.features.torrents import (
        pause_torrents_by_hash,
        resume_torrents_by_hash,
    )

    client = create_qbit_client(
        QbitConfig(
            host="http://localhost:8080", username="admin", password="..."
        )
    )
    torrents = list(client.torrents_info())

    # `should_pause_for_playback` is a policy of the consumer (e.g.
    # Waitarr) -- qbit_core has no opinion on which torrents "consume
    # disk" or deserve pausing, only how to pause/resume exact hashes.
    candidate_hashes = [
        t.hash for t in torrents if should_pause_for_playback(t)
    ]

    pause_result = pause_torrents_by_hash(client, candidate_hashes)

    # Persist only `changed_hashes`: a torrent already paused before
    # this call is `unchanged`, not something this caller should later
    # resume on its own behalf.
    hashes_owned_by_the_caller = pause_result.changed_hashes

    resume_result = resume_torrents_by_hash(
        client, hashes_owned_by_the_caller
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
