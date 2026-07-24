"""Shared test doubles and factories for CLI-level tests."""

from typing import Any

from app.config import QbitConfig


class FakeQbitClient:
    """Provide the qBittorrent methods needed by CLI-level tests.

    Records `torrents_trackers()` calls so `status` tests can assert
    that it never scans trackers per torrent (no N+1 pattern), and
    records bulk mutation calls so `torrents` command tests can assert
    dry-run performs no mutation and `--no-dry-run` mutates exactly the
    resolved target.
    """

    def __init__(
        self,
        *,
        torrents: list[dict[str, Any]] | None = None,
        trackers_by_hash: dict[str, list[dict[str, Any]]] | None = None,
        qbittorrent_version: str = "5.0.1",
        api_version: str = "2.9.3",
        download_speed: int = 0,
        upload_speed: int = 0,
    ) -> None:
        """Store fake instance data returned to callers."""
        self.torrents = torrents or []
        self.trackers_by_hash = trackers_by_hash or {}
        self.qbittorrent_version = qbittorrent_version
        self.api_version = api_version
        self.download_speed = download_speed
        self.upload_speed = upload_speed
        self.torrents_trackers_calls = 0
        self.torrents_info_calls = 0
        self.transfer_info_calls = 0
        self.paused_hashes: list[str | list[str]] = []
        self.resumed_hashes: list[str | list[str]] = []
        self.started_hashes: list[str | list[str]] = []
        self.reannounced_hashes: list[str | list[str]] = []

    def app_version(self) -> str:
        """Return the fake qBittorrent version."""
        return self.qbittorrent_version

    def app_web_api_version(self) -> str:
        """Return the fake Web API version."""
        return self.api_version

    def transfer_info(self) -> dict[str, int]:
        """Return fake global transfer speeds."""
        self.transfer_info_calls += 1
        return {
            "dl_info_speed": self.download_speed,
            "up_info_speed": self.upload_speed,
        }

    def torrents_info(self) -> list[dict[str, Any]]:
        """Return fake torrents."""
        self.torrents_info_calls += 1
        return self.torrents

    def torrents_trackers(self, torrent_hash: str) -> list[dict[str, Any]]:
        """Record a per-torrent tracker call that `status` must avoid."""
        self.torrents_trackers_calls += 1
        return self.trackers_by_hash.get(torrent_hash, [])

    def torrents_pause(self, torrent_hashes: str | list[str]) -> None:
        """Record fake torrent pauses."""
        self.paused_hashes.append(torrent_hashes)

    def torrents_resume(self, torrent_hashes: str | list[str]) -> None:
        """Record fake torrent resumes."""
        self.resumed_hashes.append(torrent_hashes)

    def torrents_start(self, torrent_hashes: str | list[str]) -> None:
        """Record fake torrent starts."""
        self.started_hashes.append(torrent_hashes)

    def torrents_reannounce(self, torrent_hashes: str | list[str]) -> None:
        """Record fake torrent reannouncements."""
        self.reannounced_hashes.append(torrent_hashes)


def make_config(**overrides: Any) -> QbitConfig:
    """Build a fake qBittorrent configuration for tests."""
    defaults: dict[str, Any] = {
        "host": "http://admin:secret-password@localhost:8080",
        "username": "admin",
        "password": "secret-password",
    }
    defaults.update(overrides)
    return QbitConfig(**defaults)


def make_torrent(**overrides: Any) -> dict[str, Any]:
    """Build a fake torrent record for tests."""
    defaults: dict[str, Any] = {
        "hash": "0123456789abcdef0123456789abcdef01234567",
        "name": "Fake Torrent",
        "state": "uploading",
        "progress": 1.0,
        "ratio": 1.0,
        "size": 1_000_000,
        "category": "",
    }
    defaults.update(overrides)
    return defaults
