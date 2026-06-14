"""Test backup export helpers."""

from typing import Any

from app.backup import export_instance_state
from app.config import QbitConfig


def test_export_instance_state_includes_metadata_and_trackers() -> None:
    """Ensure backup export combines torrents, trackers and metadata."""
    client = FakeQbitClient(
        torrents=[
            {
                "hash": "hash-a",
                "name": "Torrent A",
                "state": "uploading",
                "size": 1024,
                "progress": 1,
                "ratio": 2.0,
                "save_path": "/data/torrents",
                "category": "movies",
                "added_on": 1710000000,
            }
        ],
        trackers_by_hash={
            "hash-a": [
                {
                    "url": "https://tracker.example/announce?sig=a",
                    "status": "2",
                },
                {"url": "** [DHT] **", "status": "0"},
            ],
        },
    )
    config = QbitConfig(
        host="http://localhost:8080",
        username="admin",
        password="secret",
    )

    state = export_instance_state(
        client=client,
        config=config,
        qbit_ops_version="0.1.0",
        qbittorrent_version="v4.6.0",
        web_api_version="2.11.0",
        match_mode="without-query",
    )

    assert state["metadata"]["qbit_ops_version"] == "0.1.0"
    assert state["metadata"]["qbit_host"] == "http://localhost:8080"
    assert state["metadata"]["qbit_user"] == "admin"
    assert state["metadata"]["qbittorrent_version"] == "v4.6.0"
    assert state["metadata"]["web_api_version"] == "2.11.0"
    assert state["metadata"]["tracker_match"] == "without-query"
    assert "exported_at" in state["metadata"]
    assert "password" not in state["metadata"]

    assert state["summary"] == {
        "torrents": 1,
        "unique_trackers": 1,
        "tracker_match": "without-query",
    }
    assert state["tracker_usage"] == {
        "https://tracker.example/announce": 1,
    }
    assert state["torrents"] == [
        {
            "hash": "hash-a",
            "name": "Torrent A",
            "state": "uploading",
            "size": 1024,
            "progress": 1.0,
            "ratio": 2.0,
            "save_path": "/data/torrents",
            "category": "movies",
            "added_on": 1710000000,
            "trackers": [
                {
                    "url": "https://tracker.example/announce?sig=a",
                    "status": "2",
                    "disabled": False,
                },
                {
                    "url": "** [DHT] **",
                    "status": "0",
                    "disabled": True,
                },
            ],
            "active_tracker_count": 1,
            "normalized_trackers": [
                "https://tracker.example/announce",
            ],
        }
    ]


class FakeQbitClient:
    """Provide the qBittorrent methods needed by backup tests."""

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
