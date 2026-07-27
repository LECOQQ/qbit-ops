"""Seed the synthetic torrent corpus into a running disposable container.

This talks to the container directly via `qbittorrentapi`, bypassing
qbit-ops entirely -- exactly like a test fixture writing rows into a
database before the code under test runs. qbit-ops itself has no
"add torrent" command (verified: no such command exists in
`src/qbit_ops/main.py`), so seeding cannot go through qbit-ops's own
CLI, and must not be mistaken for it in any report.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import qbittorrentapi

from tests.integration._harness import RunningQbitContainer
from tests.integration._torrent_corpus import (
    SyntheticTorrent,
    build_complete_torrent,
    build_incomplete_torrent,
    build_tracked_torrent,
    write_payload_file,
)


@dataclass(frozen=True)
class SeededCorpus:
    """The three synthetic torrents seeded into one running container."""

    complete: SyntheticTorrent
    incomplete: SyntheticTorrent
    tracked: SyntheticTorrent


def seed_torrent_corpus(
    container: RunningQbitContainer, *, tracker_announce_url: str
) -> SeededCorpus:
    """Add the deterministic 3-torrent corpus to `container` and wait for state.

    `complete` is placed on disk before being added (as stopped, to
    avoid announcing anywhere) and rechecked, producing a completed
    state deterministically. `incomplete` is added with no payload on
    disk, producing a non-complete state. `tracked` uses
    `tracker_announce_url` -- the disposable in-network tracker -- and
    is added stopped so it never announces to anything else.
    """
    complete = build_complete_torrent()
    incomplete = build_incomplete_torrent()
    tracked = build_tracked_torrent(announce=tracker_announce_url)

    write_payload_file(complete, container.downloads_dir)

    client = qbittorrentapi.Client(
        host=container.host,
        username=container.username,
        password=container.password,
    )
    client.auth_log_in()

    client.torrents_add(
        torrent_files=complete.torrent_bytes,
        save_path="/downloads",
        is_stopped=True,
        use_auto_torrent_management=False,
    )
    client.torrents_add(
        torrent_files=incomplete.torrent_bytes,
        save_path="/downloads/incomplete-unfetched",
        is_stopped=True,
        use_auto_torrent_management=False,
    )
    client.torrents_add(
        torrent_files=tracked.torrent_bytes,
        save_path="/downloads/tracked",
        is_stopped=True,
        use_auto_torrent_management=False,
    )

    time.sleep(1)  # let the add settle before triggering a recheck
    client.torrents_recheck(torrent_hashes=complete.info_hash_hex)
    _wait_for_state(
        client,
        complete.info_hash_hex,
        acceptable_transient={
            "stoppedDL",
            "pausedDL",
            "checkingResumeData",
            "checkingDL",
            "checkingUP",
            "queuedDL",
        },
        final={"pausedUP", "stoppedUP"},
    )

    return SeededCorpus(
        complete=complete, incomplete=incomplete, tracked=tracked
    )


def _wait_for_state(
    client: qbittorrentapi.Client,
    torrent_hash: str,
    acceptable_transient: set[str],
    *,
    final: set[str],
    timeout_seconds: float = 30.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_state = None
    while time.monotonic() < deadline:
        torrents = list(client.torrents_info(torrent_hashes=torrent_hash))
        if torrents:
            last_state = torrents[0].state
            if last_state in final:
                return
            if last_state not in acceptable_transient:
                break
        time.sleep(0.5)

    raise RuntimeError(
        f"torrent {torrent_hash} did not reach a state in {final} "
        f"(last observed: {last_state!r})"
    )
