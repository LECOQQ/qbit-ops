"""Test tracker management helpers."""

from typing import Any

from app.trackers import (
    add_tracker_if_source_present,
    export_tracker_state,
    has_tracker,
    inspect_tracker,
    list_tracker_usage,
    normalize_tracker_url,
    remove_tracker_from_all,
    replace_tracker_in_all,
)

TrackersByHash = dict[str, list[dict[str, str]]]


def test_normalize_tracker_url_strips_spaces() -> None:
    """Ensure surrounding spaces are removed from tracker URLs."""
    assert (
        normalize_tracker_url("  https://tracker.example/announce  ")
        == "https://tracker.example/announce"
    )


def test_normalize_tracker_url_removes_trailing_slash() -> None:
    """Ensure trailing slashes do not affect tracker comparisons."""
    assert (
        normalize_tracker_url("https://tracker.example/announce/")
        == "https://tracker.example/announce"
    )


def test_normalize_tracker_url_keeps_query_in_exact_mode() -> None:
    """Ensure exact mode keeps query parameters for comparisons."""
    tracker_url = "https://tracker.example/announce?sig=a&announce_ts=1"

    assert normalize_tracker_url(tracker_url) == tracker_url


def test_normalize_tracker_url_removes_query_in_without_query_mode() -> None:
    """Ensure without-query mode ignores dynamic query parameters."""
    assert (
        normalize_tracker_url(
            "https://tracker.example/announce/?sig=a&announce_ts=1",
            match_mode="without-query",
        )
        == "https://tracker.example/announce"
    )


def test_has_tracker_matches_existing_tracker_with_trailing_slash() -> None:
    """Ensure tracker lookup normalizes existing tracker URLs."""
    trackers = ["https://tracker.example/announce/"]

    assert has_tracker(trackers, "https://tracker.example/announce")


def test_has_tracker_returns_false_when_absent() -> None:
    """Ensure tracker lookup fails when no normalized URL matches."""
    trackers = ["https://tracker-a.example/announce"]

    assert not has_tracker(trackers, "https://tracker-b.example/announce")


def test_has_tracker_matches_query_variants_without_query() -> None:
    """Ensure dynamic tracker URLs require explicit without-query matching."""
    trackers = ["https://tracker.example/announce?sig=a&announce_ts=1"]

    assert not has_tracker(trackers, "https://tracker.example/announce")
    assert has_tracker(
        trackers,
        "https://tracker.example/announce",
        match_mode="without-query",
    )


def test_list_tracker_usage_counts_unique_trackers_per_torrent() -> None:
    """Ensure tracker listing is normalized and counted per torrent."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker-a.example/announce/"},
                {"url": " https://tracker-b.example/announce "},
                {"url": "https://tracker-b.example/announce/"},
            ],
            "hash-b": [
                {"url": "https://tracker-a.example/announce"},
            ],
        }
    )

    assert list_tracker_usage(client) == {
        "https://tracker-a.example/announce": 2,
        "https://tracker-b.example/announce": 1,
    }


def test_list_tracker_usage_groups_query_variants_with_without_query() -> None:
    """Ensure tracker listing can group dynamic query variants."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker.example/announce?sig=a"},
                {"url": "https://tracker.example/announce?sig=b"},
            ],
            "hash-b": [
                {"url": "https://tracker.example/announce?sig=c"},
            ],
        }
    )

    assert list_tracker_usage(client, match_mode="without-query") == {
        "https://tracker.example/announce": 2,
    }


def test_list_tracker_usage_ignores_disabled_trackers() -> None:
    """Ensure disabled qBittorrent trackers are not listed."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "** [DHT] **", "status": "0"},
                {"url": "** [PeX] **", "status": "disabled"},
                {"url": "https://tracker.example/announce", "status": "2"},
            ],
        }
    )

    assert list_tracker_usage(client) == {
        "https://tracker.example/announce": 1,
    }


def test_inspect_tracker_lists_matching_torrents() -> None:
    """Ensure tracker inspection reports matching torrents and raw URLs."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker.example/announce?sig=a"},
                {"url": "https://other.example/announce"},
            ],
            "hash-b": [
                {"url": "https://tracker.example/announce?sig=b"},
            ],
        }
    )

    report = inspect_tracker(
        client=client,
        tracker="https://tracker.example/announce",
        match_mode="without-query",
    )

    assert report == {
        "scanned": 2,
        "matched_tracker": 2,
        "torrents": [
            {
                "hash": "hash-a",
                "name": "hash-a",
                "matching_tracker_urls": [
                    "https://tracker.example/announce?sig=a",
                ],
            },
            {
                "hash": "hash-b",
                "name": "hash-b",
                "matching_tracker_urls": [
                    "https://tracker.example/announce?sig=b",
                ],
            },
        ],
    }


def test_export_tracker_state_exports_active_trackers() -> None:
    """Ensure tracker export includes raw and normalized active trackers."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker.example/announce?sig=a"},
                {"url": "** [DHT] **", "status": "0"},
            ],
        }
    )

    assert export_tracker_state(client, match_mode="without-query") == {
        "summary": {
            "torrents": 1,
            "match": "without-query",
        },
        "torrents": [
            {
                "hash": "hash-a",
                "name": "hash-a",
                "trackers": [
                    "https://tracker.example/announce?sig=a",
                ],
                "normalized_trackers": [
                    "https://tracker.example/announce",
                ],
            },
        ],
    }


def test_add_tracker_if_source_present_returns_verbose_details() -> None:
    """Ensure add operations can report impacted torrents."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [{"url": "https://tracker-a.example/announce"}],
            "hash-b": [{"url": "https://tracker-c.example/announce"}],
        }
    )

    summary = add_tracker_if_source_present(
        client=client,
        source_tracker="https://tracker-a.example/announce",
        target_tracker="https://tracker-b.example/announce",
        verbose=True,
    )

    assert summary == {
        "scanned": 2,
        "matched_source": 1,
        "already_had_target": 0,
        "modified": 1,
        "dry_run": True,
        "details": [
            {
                "hash": "hash-a",
                "name": "hash-a",
                "action": "would_add",
            },
        ],
    }


def test_remove_tracker_from_all_is_dry_run_by_default() -> None:
    """Ensure tracker removal previews matching torrents by default."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [{"url": "https://tracker.example/announce/"}],
            "hash-b": [{"url": "https://other.example/announce"}],
        }
    )

    summary = remove_tracker_from_all(
        client=client,
        tracker="https://tracker.example/announce",
    )

    assert summary == {
        "scanned": 2,
        "matched_tracker": 1,
        "modified": 1,
        "removed_urls": 1,
        "dry_run": True,
    }
    assert client.removed_trackers == []


def test_remove_tracker_matches_query_variants_without_query() -> None:
    """Ensure without-query removal matches raw dynamic tracker URLs."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker.example/announce?sig=a"},
                {"url": "https://tracker.example/announce?sig=b"},
            ],
        }
    )

    summary = remove_tracker_from_all(
        client=client,
        tracker="https://tracker.example/announce",
        dry_run=False,
        match_mode="without-query",
    )

    assert summary == {
        "scanned": 1,
        "matched_tracker": 1,
        "modified": 1,
        "removed_urls": 2,
        "dry_run": False,
    }
    assert client.removed_trackers == [
        (
            "hash-a",
            [
                "https://tracker.example/announce?sig=a",
                "https://tracker.example/announce?sig=b",
            ],
        )
    ]


def test_remove_tracker_from_all_returns_verbose_details() -> None:
    """Ensure removal operations can report impacted torrents."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [{"url": "https://tracker.example/announce/"}],
        }
    )

    summary = remove_tracker_from_all(
        client=client,
        tracker="https://tracker.example/announce",
        verbose=True,
    )

    assert summary == {
        "scanned": 1,
        "matched_tracker": 1,
        "modified": 1,
        "removed_urls": 1,
        "dry_run": True,
        "details": [
            {
                "hash": "hash-a",
                "name": "hash-a",
                "action": "would_remove",
                "matching_tracker_urls": [
                    "https://tracker.example/announce/",
                ],
            },
        ],
    }


def test_remove_tracker_from_all_removes_matching_raw_urls() -> None:
    """Ensure real removal uses raw qBittorrent tracker URLs."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker.example/announce/"},
                {"url": " https://tracker.example/announce "},
            ],
            "hash-b": [{"url": "https://other.example/announce"}],
        }
    )

    summary = remove_tracker_from_all(
        client=client,
        tracker="https://tracker.example/announce",
        dry_run=False,
    )

    assert summary == {
        "scanned": 2,
        "matched_tracker": 1,
        "modified": 1,
        "removed_urls": 2,
        "dry_run": False,
    }
    assert client.removed_trackers == [
        (
            "hash-a",
            [
                "https://tracker.example/announce/",
                " https://tracker.example/announce ",
            ],
        )
    ]


def test_replace_tracker_in_all_is_dry_run_by_default() -> None:
    """Ensure tracker replacement previews matching torrents by default."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [{"url": "https://tracker-a.example/announce"}],
            "hash-b": [{"url": "https://tracker-c.example/announce"}],
        }
    )

    summary = replace_tracker_in_all(
        client=client,
        source_tracker="https://tracker-a.example/announce",
        target_tracker="https://tracker-b.example/announce",
    )

    assert summary == {
        "scanned": 2,
        "matched_source": 1,
        "already_had_target": 0,
        "modified": 1,
        "replaced_urls": 1,
        "removed_urls": 0,
        "dry_run": True,
    }
    assert client.edited_trackers == []
    assert client.removed_trackers == []


def test_replace_tracker_in_all_replaces_source_url() -> None:
    """Ensure real replacement edits the matching raw source URL."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [{"url": "https://tracker-a.example/announce/"}],
        }
    )

    summary = replace_tracker_in_all(
        client=client,
        source_tracker="https://tracker-a.example/announce",
        target_tracker="https://tracker-b.example/announce",
        dry_run=False,
    )

    assert summary == {
        "scanned": 1,
        "matched_source": 1,
        "already_had_target": 0,
        "modified": 1,
        "replaced_urls": 1,
        "removed_urls": 0,
        "dry_run": False,
    }
    assert client.edited_trackers == [
        (
            "hash-a",
            "https://tracker-a.example/announce/",
            "https://tracker-b.example/announce",
        )
    ]


def test_replace_tracker_removes_source_when_target_already_exists() -> None:
    """Ensure replacement avoids duplicating an existing target tracker."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker-a.example/announce"},
                {"url": "https://tracker-b.example/announce"},
            ],
        }
    )

    summary = replace_tracker_in_all(
        client=client,
        source_tracker="https://tracker-a.example/announce",
        target_tracker="https://tracker-b.example/announce",
        dry_run=False,
        verbose=True,
    )

    assert summary == {
        "scanned": 1,
        "matched_source": 1,
        "already_had_target": 1,
        "modified": 1,
        "replaced_urls": 0,
        "removed_urls": 1,
        "dry_run": False,
        "details": [
            {
                "hash": "hash-a",
                "name": "hash-a",
                "action": "removed_source",
                "replaced_tracker_url": "",
                "matching_tracker_urls": [
                    "https://tracker-a.example/announce",
                ],
                "removed_tracker_urls": [
                    "https://tracker-a.example/announce",
                ],
            }
        ],
    }
    assert client.edited_trackers == []
    assert client.removed_trackers == [
        ("hash-a", ["https://tracker-a.example/announce"])
    ]


def test_replace_tracker_removes_extra_without_query_variants() -> None:
    """Ensure dynamic source variants do not become duplicate targets."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker-a.example/announce?sig=a"},
                {"url": "https://tracker-a.example/announce?sig=b"},
            ],
        }
    )

    summary = replace_tracker_in_all(
        client=client,
        source_tracker="https://tracker-a.example/announce",
        target_tracker="https://tracker-b.example/announce",
        dry_run=False,
        match_mode="without-query",
    )

    assert summary == {
        "scanned": 1,
        "matched_source": 1,
        "already_had_target": 0,
        "modified": 1,
        "replaced_urls": 1,
        "removed_urls": 1,
        "dry_run": False,
    }
    assert client.edited_trackers == [
        (
            "hash-a",
            "https://tracker-a.example/announce?sig=a",
            "https://tracker-b.example/announce",
        )
    ]
    assert client.removed_trackers == [
        ("hash-a", ["https://tracker-a.example/announce?sig=b"])
    ]


class FakeQbitClient:
    """Provide the qBittorrent methods needed by tracker tests."""

    def __init__(self, trackers_by_hash: TrackersByHash) -> None:
        """Store fake tracker data by torrent hash."""
        self.trackers_by_hash = trackers_by_hash
        self.removed_trackers: list[tuple[str, list[str]]] = []
        self.edited_trackers: list[tuple[str, str, str]] = []

    def torrents_info(self) -> list[dict[str, str]]:
        """Return fake torrents."""
        return [
            {"hash": torrent_hash, "name": torrent_hash}
            for torrent_hash in self.trackers_by_hash
        ]

    def torrents_trackers(self, torrent_hash: str) -> list[dict[str, Any]]:
        """Return fake trackers for a torrent."""
        return self.trackers_by_hash[torrent_hash]

    def torrents_remove_trackers(
        self,
        torrent_hash: str,
        urls: list[str],
    ) -> None:
        """Record fake tracker removals."""
        self.removed_trackers.append((torrent_hash, urls))

    def torrents_add_trackers(
        self,
        torrent_hash: str,
        urls: str,
    ) -> None:
        """Record fake tracker additions."""

    def torrents_edit_tracker(
        self,
        torrent_hash: str,
        original_url: str,
        new_url: str,
    ) -> None:
        """Record fake tracker replacements."""
        self.edited_trackers.append((torrent_hash, original_url, new_url))
