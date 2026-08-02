#!/usr/bin/env python3
"""Idempotently seed the disposable demo qBittorrent instance.

Reads `qbit-ops.env` directly (never through `qbit_ops.config`, so this
has no dependency on the real application's env-discovery order),
refuses to run against anything but the pinned demo loopback host, then
adds every fixture from `fixtures.toml` by its canonical infohash and
drives it to its desired broad state. Safe to rerun: existing torrents
are matched by hash, never re-added, and mutated back to their target
state if they drifted.
"""

from __future__ import annotations

import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import qbittorrentapi
from dotenv import dotenv_values

from generate_fixtures import DOWNLOADS_DIR, Fixture, load_fixtures

DEMO_DIR = Path(__file__).parent
EXPECTED_HOSTNAME = "127.0.0.1"
EXPECTED_PORT = 18080
READY_TIMEOUT_SECONDS = 90
STATE_TIMEOUT_SECONDS = 60

TRANSIENT_STATES = {
    "checkingDL", "checkingUP", "checkingResumeData", "allocating",
    "queuedDL", "queuedUP", "metaDL", "moving",
}
RUNNING_STATES = {
    "seeding": {"stalledUP", "uploading", "forcedUP", "queuedUP"},
    "stalled-download": {"stalledDL", "downloading", "forcedDL"},
}
STOPPED_STATES = {
    "completed-paused": {"stoppedUP", "pausedUP"},
    "incomplete-paused": {"stoppedDL", "pausedDL"},
}


class SeedError(RuntimeError):
    pass


def _load_env() -> dict[str, str]:
    values = dotenv_values(DEMO_DIR / "qbit-ops.env")
    missing = [k for k in ("QBIT_HOST", "QBIT_USER", "QBIT_PASSWORD") if not values.get(k)]
    if missing:
        raise SeedError(f"demo/qbit-ops.env is missing {missing}")
    return {k: v for k, v in values.items() if v is not None}


def _assert_demo_host(host: str) -> None:
    parsed = urllib.parse.urlsplit(host)
    if parsed.hostname != EXPECTED_HOSTNAME or parsed.port != EXPECTED_PORT:
        raise SeedError(
            f"refusing to seed {host!r}: expected the disposable demo host "
            f"{EXPECTED_HOSTNAME}:{EXPECTED_PORT}"
        )


def _wait_for_webui(host: str) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"{host}/api/v2/app/webapiVersion", timeout=2
            ) as resp:
                if resp.status == 200:
                    return
        except urllib.error.HTTPError as error:
            if error.code == 403:
                return
            last_error = error
        except (urllib.error.URLError, OSError) as error:
            last_error = error
        time.sleep(1)
    raise SeedError(f"qBittorrent WebUI at {host} did not become ready: {last_error}")


def _ensure_categories(client: qbittorrentapi.Client, fixtures: list[Fixture]) -> None:
    existing = set(client.torrent_categories.categories.keys())
    for category in sorted({f.category for f in fixtures}):
        if category not in existing:
            client.torrents_create_category(name=category)


def _add_missing(client: qbittorrentapi.Client, fixtures: list[Fixture]) -> None:
    present = {t.hash for t in client.torrents_info()}
    for fixture in fixtures:
        if fixture.info_hash in present:
            continue
        client.torrents_add(
            torrent_files=fixture.torrent_path.read_bytes(),
            save_path="/downloads",
            category=fixture.category,
            use_auto_torrent_management=False,
        )
    _wait_for_hashes(client, [f.info_hash for f in fixtures])


def _wait_for_hashes(client: qbittorrentapi.Client, hashes: list[str]) -> None:
    deadline = time.monotonic() + STATE_TIMEOUT_SECONDS
    present: set[str] = set()
    while time.monotonic() < deadline:
        present = {t.hash for t in client.torrents_info()}
        if all(h in present for h in hashes):
            return
        time.sleep(1)
    missing = [h for h in hashes if h not in present]
    raise SeedError(f"torrent(s) never appeared after add: {missing}")


def _reconcile_state(client: qbittorrentapi.Client, fixture: Fixture) -> str:
    running_states = RUNNING_STATES.get(fixture.desired_state)
    stopped_states = STOPPED_STATES.get(fixture.desired_state)
    deadline = time.monotonic() + STATE_TIMEOUT_SECONDS
    last_state = ""
    while time.monotonic() < deadline:
        info = client.torrents_info(torrent_hashes=[fixture.info_hash])
        if not info:
            raise SeedError(f"{fixture.name}: disappeared while reconciling state")
        torrent = info[0]
        last_state = torrent.state

        if torrent.state in TRANSIENT_STATES:
            time.sleep(1)
            continue

        if running_states is not None:
            if torrent.state in running_states:
                return torrent.state
            if torrent.state in STOPPED_STATES.get("completed-paused", set()) | STOPPED_STATES.get(
                "incomplete-paused", set()
            ):
                client.torrents_start(torrent_hashes=[fixture.info_hash])
            time.sleep(1)
            continue

        if stopped_states is not None:
            if torrent.state in stopped_states:
                return torrent.state
            client.torrents_stop(torrent_hashes=[fixture.info_hash])
            time.sleep(1)
            continue

    raise SeedError(
        f"{fixture.name}: never reached desired state {fixture.desired_state!r}, "
        f"last observed state {last_state!r}"
    )


def main() -> None:
    env = _load_env()
    host = env["QBIT_HOST"]
    _assert_demo_host(host)
    _wait_for_webui(host)

    client = qbittorrentapi.Client(
        host=host, username=env["QBIT_USER"], password=env["QBIT_PASSWORD"]
    )
    client.auth_log_in()
    print(f"Connected to {host} -- app {client.app_version()}, "
          f"Web API {client.app_web_api_version()}")

    if not DOWNLOADS_DIR.exists():
        raise SeedError(
            "demo/generated/downloads is missing -- run "
            "`poetry run python demo/generate_fixtures.py` first"
        )

    fixtures = load_fixtures()
    _ensure_categories(client, fixtures)
    _add_missing(client, fixtures)

    print(f"\n{'HASH':10} {'STATE':12} {'CATEGORY':10} NAME")
    for fixture in fixtures:
        state = _reconcile_state(client, fixture)
        print(f"{fixture.info_hash[:8]:10} {state:12} {fixture.category:10} {fixture.name}")


if __name__ == "__main__":
    try:
        main()
    except SeedError as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
