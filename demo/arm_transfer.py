#!/usr/bin/env python3
"""(Re)start the live seeder -> leecher transfer the Overview demo plots.

Deletes the transfer fixture from the leecher if it is already present
-- discarding whatever bytes it had downloaded -- and re-adds it from
zero, rate-limited, pointed at the seeder over the private Docker
network. Reproducibility matters more than speed here: every
invocation (`make demo-up`, `make demo-tui`, `make demo-record`)
restarts the transfer from scratch rather than resuming a partial one,
so the same torrent takes the same ~30s ramp every time.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import qbittorrentapi
from _demo_support import SeedError, assert_demo_host, wait_for_webui
from generate_fixtures import (
    LEECHER_WEBUI_PORT,
    WEBUI_PASSWORD,
    WEBUI_USERNAME,
    load_fixtures,
)

DEMO_DIR = Path(__file__).parent
LEECHER_HOST = f"http://127.0.0.1:{LEECHER_WEBUI_PORT}"
# Must match compose.yml's `qbittorrent` container_name and Session\Port.
SEEDER_CONTAINER_NAME = "qbit-ops-demo"
SEEDER_BT_PORT = 6881
READY_TIMEOUT_SECONDS = 90
PRESENCE_TIMEOUT_SECONDS = 60
# 3 MB at 100 kB/s draws a ~30s ramp: fast enough not to stall a
# recording, slow enough to actually show as a line instead of a spike.
# The leecher-side download limit alone bounds the observed rate --
# qBittorrent throttles on read, so no seeder-side limit is needed.
TRANSFER_RATE_LIMIT_BYTES_PER_SEC = 100_000


def _seeder_container_ip() -> str:
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "-f",
                "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                SEEDER_CONTAINER_NAME,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as error:
        raise SeedError(
            f"could not inspect the {SEEDER_CONTAINER_NAME!r} container -- "
            f"is `make demo-up` running? ({error})"
        ) from error
    ip = result.stdout.strip()
    if not ip:
        raise SeedError(
            f"{SEEDER_CONTAINER_NAME!r} has no address on its Docker network"
        )
    return ip


def _wait_for_hash(client: qbittorrentapi.Client, info_hash: str) -> None:
    deadline = time.monotonic() + PRESENCE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if any(t.hash == info_hash for t in client.torrents_info()):
            return
        time.sleep(1)
    raise SeedError(f"{info_hash}: never appeared on the leecher after add")


def main() -> None:
    fixtures = load_fixtures()
    transfer_fixtures = [f for f in fixtures if f.transfer_demo]
    if len(transfer_fixtures) != 1:
        raise SeedError(
            "expected exactly one fixture with transfer_demo = true in "
            f"fixtures.toml, found {len(transfer_fixtures)}"
        )
    fixture = transfer_fixtures[0]

    assert_demo_host(LEECHER_HOST, expected_port=LEECHER_WEBUI_PORT)
    wait_for_webui(LEECHER_HOST, timeout_seconds=READY_TIMEOUT_SECONDS)

    leecher = qbittorrentapi.Client(
        host=LEECHER_HOST, username=WEBUI_USERNAME, password=WEBUI_PASSWORD
    )
    leecher.auth_log_in()

    existing = {t.hash for t in leecher.torrents_info()}
    if fixture.info_hash in existing:
        leecher.torrents_delete(
            delete_files=True, torrent_hashes=[fixture.info_hash]
        )

    leecher.torrents_add(
        torrent_files=fixture.torrent_path.read_bytes(),
        save_path="/downloads",
        use_auto_torrent_management=False,
    )
    _wait_for_hash(leecher, fixture.info_hash)

    leecher.torrents_set_download_limit(
        limit=TRANSFER_RATE_LIMIT_BYTES_PER_SEC,
        torrent_hashes=[fixture.info_hash],
    )

    seeder_ip = _seeder_container_ip()
    leecher.torrents_add_peers(
        peers=f"{seeder_ip}:{SEEDER_BT_PORT}",
        torrent_hashes=[fixture.info_hash],
    )

    print(
        f"leecher: pulling {fixture.name!r} ({fixture.info_hash[:8]}) from "
        f"{seeder_ip}:{SEEDER_BT_PORT}, capped at "
        f"{TRANSFER_RATE_LIMIT_BYTES_PER_SEC // 1000} kB/s"
    )


if __name__ == "__main__":
    try:
        main()
    except SeedError as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
