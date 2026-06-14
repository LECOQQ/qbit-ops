"""Test torrent listing helpers."""

from typing import Any

from app.torrents import list_torrents


def test_list_torrents_returns_audit_fields() -> None:
    """Ensure torrent listing extracts useful audit fields."""
    client = FakeQbitClient(
        torrents=[
            {
                "hash": "hash-a",
                "name": "Torrent A",
                "state": "uploading",
                "size": 1024,
                "progress": 1,
                "ratio": 2.5,
            }
        ],
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker.example/announce", "status": "2"},
                {"url": "** [DHT] **", "status": "0"},
            ],
        },
    )

    assert list_torrents(client) == [
        {
            "hash": "hash-a",
            "name": "Torrent A",
            "state": "uploading",
            "size": 1024,
            "progress": 1.0,
            "ratio": 2.5,
            "tracker_count": 1,
        }
    ]


def test_list_torrents_defaults_invalid_numeric_fields() -> None:
    """Ensure invalid numeric qBittorrent values do not break listing."""
    client = FakeQbitClient(
        torrents=[
            {
                "hash": "hash-a",
                "name": "Torrent A",
                "state": "pausedDL",
                "size": None,
                "progress": "invalid",
                "ratio": None,
            }
        ],
        trackers_by_hash={"hash-a": []},
    )

    assert list_torrents(client) == [
        {
            "hash": "hash-a",
            "name": "Torrent A",
            "state": "pausedDL",
            "size": 0,
            "progress": 0.0,
            "ratio": 0.0,
            "tracker_count": 0,
        }
    ]


class FakeQbitClient:
    """Provide the qBittorrent methods needed by torrent tests."""

    def __init__(
        self,
        torrents: list[dict[str, Any]],
        trackers_by_hash: dict[str, list[dict[str, str]]],
    ) -> None:
        """Store fake torrent and tracker data."""
        self.torrents = torrents
        self.trackers_by_hash = trackers_by_hash

    def torrents_info(self) -> list[dict[str, Any]]:
        """Return fake torrents."""
        return self.torrents

    def torrents_trackers(self, torrent_hash: str) -> list[dict[str, str]]:
        """Return fake trackers for a torrent."""
        return self.trackers_by_hash[torrent_hash]
