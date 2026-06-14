"""Test backup export helpers."""

import json
from pathlib import Path
from typing import Any

import pytest

from app.backup import (
    BackupExportError,
    diff_backup_exports,
    export_instance_state,
    has_backup_diff,
    load_export_file,
)
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


def _sample_export(
    *,
    torrent_hash: str = "hash-a",
    name: str = "Torrent A",
    normalized_trackers: list[str] | None = None,
    tracker_usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Build a minimal export payload for diff tests."""
    trackers = normalized_trackers or ["https://tracker.example/announce"]
    return {
        "torrents": [
            {
                "hash": torrent_hash,
                "name": name,
                "normalized_trackers": trackers,
            }
        ],
        "tracker_usage": tracker_usage
        or {"https://tracker.example/announce": 1},
    }


def test_diff_backup_exports_reports_identical_payloads() -> None:
    """Ensure identical exports produce an empty diff report."""
    export_payload = _sample_export()

    report = diff_backup_exports(export_payload, export_payload)

    assert report["summary"]["identical"] is True
    assert has_backup_diff(report) is False
    assert report["added_torrents"] == []
    assert report["removed_torrents"] == []
    assert report["changed_torrents"] == []


def test_diff_backup_exports_reports_added_and_removed_torrents() -> None:
    """Ensure torrent additions and removals are reported."""
    baseline = _sample_export(
        torrent_hash="hash-a",
        name="Torrent A",
        normalized_trackers=["https://tracker-a.example/announce"],
        tracker_usage={"https://tracker-a.example/announce": 1},
    )
    target = _sample_export(
        torrent_hash="hash-b",
        name="Torrent B",
        normalized_trackers=["https://tracker-b.example/announce"],
        tracker_usage={"https://tracker-b.example/announce": 1},
    )

    report = diff_backup_exports(baseline, target)

    assert report["summary"]["identical"] is False
    assert report["added_torrents"] == [
        {"hash": "hash-b", "name": "Torrent B"},
    ]
    assert report["removed_torrents"] == [
        {"hash": "hash-a", "name": "Torrent A"},
    ]


def test_diff_backup_exports_reports_tracker_changes() -> None:
    """Ensure per-torrent tracker changes are reported."""
    baseline = _sample_export(
        normalized_trackers=["https://tracker-a.example/announce"],
    )
    target = _sample_export(
        normalized_trackers=[
            "https://tracker-a.example/announce",
            "https://tracker-b.example/announce",
        ],
    )

    report = diff_backup_exports(baseline, target)

    assert report["summary"]["changed_torrents"] == 1
    assert report["changed_torrents"] == [
        {
            "hash": "hash-a",
            "name": "Torrent A",
            "normalized_trackers": {
                "added": ["https://tracker-b.example/announce"],
                "removed": [],
            },
        }
    ]


def test_diff_backup_exports_reports_tracker_usage_changes() -> None:
    """Ensure tracker usage differences are reported."""
    baseline = _sample_export(
        tracker_usage={"https://tracker-a.example/announce": 2},
    )
    target = _sample_export(
        tracker_usage={"https://tracker-a.example/announce": 3},
    )

    report = diff_backup_exports(baseline, target)

    assert report["tracker_usage"]["changed"] == [
        {
            "tracker": "https://tracker-a.example/announce",
            "baseline": 2,
            "target": 3,
        }
    ]


def test_load_export_file_reads_valid_payload(tmp_path: Path) -> None:
    """Ensure valid export files can be loaded."""
    export_file = tmp_path / "export.json"
    export_file.write_text(
        json.dumps(_sample_export()),
        encoding="utf-8",
    )

    assert load_export_file(export_file) == _sample_export()


def test_load_export_file_fails_on_invalid_payload(tmp_path: Path) -> None:
    """Ensure invalid export files fail explicitly."""
    export_file = tmp_path / "invalid.json"
    export_file.write_text('{"summary": {}}', encoding="utf-8")

    with pytest.raises(BackupExportError, match="torrents"):
        load_export_file(export_file)


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
