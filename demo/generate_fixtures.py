#!/usr/bin/env python3
"""Generate deterministic synthetic torrents and both demo container configs.

Reads `fixtures.toml`, writes one `.torrent` file per fixture into
`generated/torrents/`, writes complete payloads for `seeding` and
`completed-paused` fixtures into `generated/downloads/` (the host side
of the seeder container's `/downloads` bind mount), and writes
`generated/config/qBittorrent.conf` for the seeder and
`generated/leecher-config/qBittorrent.conf` for the leecher. Every
byte, name, and hash is derived only from `fixtures.toml` -- nothing
is downloaded and nothing uses the system clock or randomness, so
reruns and `demo-reset` reproduce identical infohashes.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path

from _bencode import bencode
from _qbit_conf import build_conf

DEMO_DIR = Path(__file__).parent
GENERATED_DIR = DEMO_DIR / "generated"
TORRENTS_DIR = GENERATED_DIR / "torrents"
DOWNLOADS_DIR = GENERATED_DIR / "downloads"
CONFIG_DIR = GENERATED_DIR / "config"
LEECHER_DOWNLOADS_DIR = GENERATED_DIR / "leecher-downloads"
LEECHER_CONFIG_DIR = GENERATED_DIR / "leecher-config"

PIECE_LENGTH = 256 * 1024
PRIMARY_WEBUI_PORT = 18080
LEECHER_WEBUI_PORT = 18081
WEBUI_USERNAME = "demo"
WEBUI_PASSWORD = "demo-only"

WRITE_PAYLOAD_STATES = {"seeding", "completed-paused"}


@dataclass(frozen=True)
class Fixture:
    """One resolved demo torrent, ready to add through the Web API."""

    name: str
    category: str
    desired_state: str
    file_name: str
    info_hash: str
    torrent_path: Path
    has_payload: bool
    transfer_demo: bool


def _make_payload(label: str, size: int) -> bytes:
    """Deterministic filler bytes derived only from `label` and `size`."""
    filler = f"qbit-ops synthetic demo payload -- {label} -- ".encode()
    repeated = filler * (size // len(filler) + 1)
    return repeated[:size]


def _pieces(payload: bytes) -> bytes:
    return b"".join(
        hashlib.sha1(
            payload[offset : offset + PIECE_LENGTH], usedforsecurity=False
        ).digest()
        for offset in range(0, len(payload), PIECE_LENGTH)
    )


def _build_torrent(*, file_name: str, payload: bytes) -> tuple[bytes, str]:
    info = {
        "name": file_name,
        "piece length": PIECE_LENGTH,
        "pieces": _pieces(payload),
        "length": len(payload),
    }
    info_hash = hashlib.sha1(bencode(info), usedforsecurity=False).hexdigest()
    torrent = {
        "info": info,
        "created by": "qbit-ops demo (deterministic, synthetic)",
    }
    return bencode(torrent), info_hash


def load_fixtures() -> list[Fixture]:
    """Resolve `fixtures.toml` into `Fixture`s, without touching disk.

    Used by both this script (to know what to write) and
    `seed_instance.py` (to know what hash/state to expect) so the two
    never compute torrent bytes differently.
    """
    raw = tomllib.loads((DEMO_DIR / "fixtures.toml").read_text())["fixture"]
    fixtures = []
    for entry in raw:
        name = entry["name"]
        file_name = f"{name}.{entry['extension']}"
        payload = _make_payload(name, entry["payload_size"])
        _, info_hash = _build_torrent(file_name=file_name, payload=payload)
        fixtures.append(
            Fixture(
                name=name,
                category=entry["category"],
                desired_state=entry["desired_state"],
                file_name=file_name,
                info_hash=info_hash,
                torrent_path=TORRENTS_DIR / f"{name}.torrent",
                has_payload=entry["desired_state"] in WRITE_PAYLOAD_STATES,
                transfer_demo=entry.get("transfer_demo", False),
            )
        )
    return fixtures


def main() -> None:
    raw = tomllib.loads((DEMO_DIR / "fixtures.toml").read_text())["fixture"]

    TORRENTS_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LEECHER_DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    LEECHER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    written_payload_bytes = 0
    for entry in raw:
        name = entry["name"]
        file_name = f"{name}.{entry['extension']}"
        payload = _make_payload(name, entry["payload_size"])
        torrent_bytes, info_hash = _build_torrent(
            file_name=file_name, payload=payload
        )

        torrent_path = TORRENTS_DIR / f"{name}.torrent"
        torrent_path.write_bytes(torrent_bytes)

        if entry["desired_state"] in WRITE_PAYLOAD_STATES:
            (DOWNLOADS_DIR / file_name).write_bytes(payload)
            written_payload_bytes += len(payload)

        print(f"{info_hash} {name}")

    conf_text = build_conf(
        webui_port=PRIMARY_WEBUI_PORT,
        username=WEBUI_USERNAME,
        password=WEBUI_PASSWORD,
    )
    (CONFIG_DIR / "qBittorrent.conf").write_text(conf_text, encoding="utf-8")

    leecher_conf_text = build_conf(
        webui_port=LEECHER_WEBUI_PORT,
        username=WEBUI_USERNAME,
        password=WEBUI_PASSWORD,
    )
    (LEECHER_CONFIG_DIR / "qBittorrent.conf").write_text(
        leecher_conf_text, encoding="utf-8"
    )

    written_payload_mb = written_payload_bytes / 1_000_000
    print(
        f"\nGenerated {len(raw)} torrent(s), "
        f"{written_payload_mb:.1f} MB of payload written to disk."
    )


if __name__ == "__main__":
    main()
