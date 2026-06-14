"""Test torrent listing helpers."""

from typing import Any

from app.torrents import (
    inspect_torrent,
    list_category_usage,
    list_torrents,
    list_torrents_by_category,
    list_torrents_with_trackers,
    search_torrents_by_name,
)


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
            "category": "(uncategorized)",
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
            "category": "(uncategorized)",
            "state": "pausedDL",
            "size": 0,
            "progress": 0.0,
            "ratio": 0.0,
            "tracker_count": 0,
        }
    ]


def test_inspect_torrent_returns_detailed_report() -> None:
    """Ensure torrent inspection returns useful audit fields and trackers."""
    client = FakeQbitClient(
        torrents=[
            {
                "hash": "HASH-A",
                "name": "Torrent A",
                "state": "uploading",
                "size": 2048,
                "progress": 1,
                "ratio": 1.5,
                "save_path": "/data/torrents",
                "category": "movies",
                "added_on": 1710000000,
            }
        ],
        trackers_by_hash={
            "HASH-A": [
                {"url": "https://tracker.example/announce", "status": "2"},
                {"url": "** [DHT] **", "status": "0"},
            ],
        },
    )

    assert inspect_torrent(client, "hash-a") == {
        "hash": "HASH-A",
        "name": "Torrent A",
        "state": "uploading",
        "size": 2048,
        "progress": 1.0,
        "ratio": 1.5,
        "save_path": "/data/torrents",
        "category": "movies",
        "added_on": 1710000000,
        "trackers": [
            {
                "url": "https://tracker.example/announce",
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
    }


def test_inspect_torrent_returns_none_when_hash_is_missing() -> None:
    """Ensure torrent inspection fails explicitly when no hash matches."""
    client = FakeQbitClient(
        torrents=[{"hash": "hash-a", "name": "Torrent A"}],
        trackers_by_hash={"hash-a": []},
    )

    assert inspect_torrent(client, "missing-hash") is None


def test_search_torrents_by_name_ranks_best_matches_first() -> None:
    """Ensure name search ranks exact and substring matches first."""
    client = FakeQbitClient(
        torrents=[
            {
                "hash": "hash-a",
                "name": "L.amour.est.dans.le.pre.S20E02",
                "state": "uploading",
                "progress": 1,
                "ratio": 1.0,
            },
            {
                "hash": "hash-b",
                "name": "L.amour.est.dans.le.pre.S19E01",
                "state": "pausedUP",
                "progress": 0.5,
                "ratio": 0.5,
            },
            {
                "hash": "hash-c",
                "name": "Something completely different",
                "state": "stoppedUP",
                "progress": 1,
                "ratio": 2.0,
            },
        ],
        trackers_by_hash={
            "hash-a": [],
            "hash-b": [],
            "hash-c": [],
        },
    )

    report = search_torrents_by_name(client, "S20E02")

    assert report["query"] == "S20E02"
    assert report["summary"]["matched"] == 1
    assert report["matches"] == [
        {
            "hash": "hash-a",
            "name": "L.amour.est.dans.le.pre.S20E02",
            "state": "uploading",
            "progress": 1.0,
            "ratio": 1.0,
            "match_score": 0.85,
        }
    ]


def test_search_torrents_by_name_supports_prefix_and_limit() -> None:
    """Ensure broader name searches return multiple ranked matches."""
    client = FakeQbitClient(
        torrents=[
            {
                "hash": "hash-a",
                "name": "L.amour.est.dans.le.pre.S20E02",
                "state": "uploading",
                "progress": 1,
                "ratio": 1.0,
            },
            {
                "hash": "hash-b",
                "name": "L.amour.est.dans.le.pre.S19E01",
                "state": "pausedUP",
                "progress": 0.5,
                "ratio": 0.5,
            },
        ],
        trackers_by_hash={"hash-a": [], "hash-b": []},
    )

    report = search_torrents_by_name(
        client,
        "L.amour.est",
        limit=2,
    )

    assert report["summary"] == {"matched": 2, "limit": 2}
    assert [match["hash"] for match in report["matches"]] == [
        "hash-b",
        "hash-a",
    ]
    assert report["matches"][0]["match_score"] == 0.95

    limited_report = search_torrents_by_name(
        client,
        "L.amour.est",
        limit=1,
    )
    assert limited_report["summary"] == {"matched": 1, "limit": 1}
    assert limited_report["matches"][0]["hash"] == "hash-b"


def test_search_torrents_by_name_returns_empty_when_nothing_matches() -> None:
    """Ensure name search returns an empty result set explicitly."""
    client = FakeQbitClient(
        torrents=[{"hash": "hash-a", "name": "Torrent A"}],
        trackers_by_hash={"hash-a": []},
    )

    report = search_torrents_by_name(client, "missing-name")

    assert report["summary"]["matched"] == 0
    assert report["matches"] == []


def test_list_category_usage_counts_torrents_per_category() -> None:
    """Ensure category listing aggregates torrent counts."""
    client = FakeQbitClient(
        torrents=[
            {"hash": "hash-a", "name": "Torrent A", "category": "sonarr"},
            {"hash": "hash-b", "name": "Torrent B", "category": "radarr"},
            {"hash": "hash-c", "name": "Torrent C"},
        ],
        trackers_by_hash={"hash-a": [], "hash-b": [], "hash-c": []},
    )

    assert list_category_usage(client) == {
        "(uncategorized)": 1,
        "radarr": 1,
        "sonarr": 1,
    }


def test_list_torrents_by_category_filters_case_insensitively() -> None:
    """Ensure category filtering returns matching torrent audit fields."""
    client = FakeQbitClient(
        torrents=[
            {
                "hash": "hash-a",
                "name": "Torrent A",
                "category": "sonarr",
                "state": "uploading",
                "size": 1024,
                "progress": 1,
                "ratio": 2.0,
            },
            {
                "hash": "hash-b",
                "name": "Torrent B",
                "category": "radarr",
                "state": "pausedUP",
                "size": 2048,
                "progress": 0.5,
                "ratio": 1.0,
            },
        ],
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker.example/announce", "status": "2"},
            ],
            "hash-b": [],
        },
    )

    report = list_torrents_by_category(client, "SONARR")

    assert report == {
        "category": "SONARR",
        "scanned": 2,
        "matched": 1,
        "torrents": [
            {
                "hash": "hash-a",
                "name": "Torrent A",
                "category": "sonarr",
                "state": "uploading",
                "size": 1024,
                "progress": 1.0,
                "ratio": 2.0,
                "tracker_count": 1,
            }
        ],
    }


def test_list_torrents_by_category_supports_uncategorized_label() -> None:
    """Ensure uncategorized torrents can be filtered explicitly."""
    client = FakeQbitClient(
        torrents=[
            {"hash": "hash-a", "name": "Torrent A", "category": "sonarr"},
            {"hash": "hash-b", "name": "Torrent B"},
        ],
        trackers_by_hash={"hash-a": [], "hash-b": []},
    )

    report = list_torrents_by_category(client, "(uncategorized)")

    assert report["matched"] == 1
    assert report["torrents"][0]["hash"] == "hash-b"
    assert report["torrents"][0]["category"] == "(uncategorized)"


def test_list_torrents_with_trackers_returns_tracker_details() -> None:
    """Ensure detailed torrent listing includes tracker metadata."""
    client = FakeQbitClient(
        torrents=[
            {
                "hash": "hash-a",
                "name": "Torrent A",
                "state": "uploading",
                "size": 1024,
                "progress": 1,
                "ratio": 2.5,
                "save_path": "/data/torrents",
                "category": "movies",
                "added_on": 1710000000,
            }
        ],
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker.example/announce", "status": "2"},
                {"url": "** [DHT] **", "status": "0"},
            ],
        },
    )

    assert list_torrents_with_trackers(client) == [
        {
            "hash": "hash-a",
            "name": "Torrent A",
            "state": "uploading",
            "size": 1024,
            "progress": 1.0,
            "ratio": 2.5,
            "save_path": "/data/torrents",
            "category": "movies",
            "added_on": 1710000000,
            "trackers": [
                {
                    "url": "https://tracker.example/announce",
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
