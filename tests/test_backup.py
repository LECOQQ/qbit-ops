"""Test backup export helpers."""

import json
from pathlib import Path
from typing import Any

import pytest

from qbit_core.features.backup import (
    BackupExportError,
    apply_backup_restore,
    diff_backup_exports,
    export_instance_state,
    has_backup_diff,
    load_export_file,
    plan_backup_restore,
)
from qbit_ops.config import QbitConfig


def test_export_instance_state_includes_metadata_and_trackers() -> None:
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
                "tags": "kids, movies",
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
            "tags": ["kids", "movies"],
            "added_on": 1710000000,
            "trackers": [
                {
                    "url": "https://tracker.example/announce?sig=a",
                    "status": "2",
                    "disabled": False,
                },
            ],
            "peer_discovery": [
                {"mechanism": "DHT", "health": "disabled", "enabled": False}
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
    export_payload = _sample_export()

    report = diff_backup_exports(export_payload, export_payload)

    assert report["summary"]["identical"] is True
    assert has_backup_diff(report) is False
    assert report["added_torrents"] == []
    assert report["removed_torrents"] == []
    assert report["changed_torrents"] == []


def test_diff_backup_exports_reports_added_and_removed_torrents() -> None:
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
    export_file = tmp_path / "export.json"
    export_file.write_text(
        json.dumps(_sample_export()),
        encoding="utf-8",
    )

    assert load_export_file(export_file) == _sample_export()


def test_load_export_file_fails_on_invalid_payload(tmp_path: Path) -> None:
    export_file = tmp_path / "invalid.json"
    export_file.write_text('{"summary": {}}', encoding="utf-8")

    with pytest.raises(BackupExportError, match="torrents"):
        load_export_file(export_file)


def _export_torrent(
    *,
    hash: str = "hash-a",  # noqa: A002
    name: str = "Torrent A",
    category: str = "",
    tags: list[str] | None = None,
    trackers: list[dict[str, Any]] | None = None,
    omit_tags: bool = False,
) -> dict[str, Any]:
    """Build one export torrent entry for restore tests.

    `omit_tags` simulates an older export predating the `tags` field.
    """
    entry: dict[str, Any] = {
        "hash": hash,
        "name": name,
        "category": category,
        "trackers": trackers or [],
    }
    if not omit_tags:
        entry["tags"] = tags or []
    return entry


def test_plan_backup_restore_matches_by_hash_case_insensitively() -> None:
    client = FakeQbitClient(
        torrents=[{"hash": "HASH-A", "name": "Torrent A"}],
        trackers_by_hash={"HASH-A": []},
    )
    export = {
        "torrents": [
            _export_torrent(hash="hash-a"),
            _export_torrent(hash="hash-missing", name="Gone"),
        ]
    }

    plan = plan_backup_restore(client, export)

    assert plan.matched == 1
    assert plan.unmatched_hashes == ("hash-missing",)


def test_plan_backup_restore_fills_blank_category_only() -> None:
    client = FakeQbitClient(
        torrents=[
            {"hash": "hash-a", "name": "A", "category": ""},
            {"hash": "hash-b", "name": "B", "category": "existing"},
        ],
        trackers_by_hash={"hash-a": [], "hash-b": []},
        categories={"movies": {"name": "movies"}},
    )
    export = {
        "torrents": [
            _export_torrent(hash="hash-a", name="A", category="movies"),
            _export_torrent(hash="hash-b", name="B", category="other"),
        ]
    }

    plan = plan_backup_restore(client, export)

    assert [c.hash for c in plan.category_changes] == ["hash-a"]
    assert plan.category_changes[0].category == "movies"
    assert plan.categories_to_create == ()  # "movies" already exists


def test_plan_backup_restore_detects_categories_to_create() -> None:
    client = FakeQbitClient(
        torrents=[{"hash": "hash-a", "name": "A", "category": ""}],
        trackers_by_hash={"hash-a": []},
    )
    export = {
        "torrents": [
            _export_torrent(hash="hash-a", name="A", category="new-category")
        ]
    }

    plan = plan_backup_restore(client, export)

    assert plan.categories_to_create == ("new-category",)


def test_plan_backup_restore_tags_are_additive_union() -> None:
    client = FakeQbitClient(
        torrents=[{"hash": "hash-a", "name": "A", "tags": "kept"}],
        trackers_by_hash={"hash-a": []},
    )
    export = {
        "torrents": [
            _export_torrent(hash="hash-a", name="A", tags=["kept", "new"])
        ]
    }

    plan = plan_backup_restore(client, export)

    assert len(plan.tag_changes) == 1
    assert plan.tag_changes[0].added_tags == ("new",)


def test_plan_backup_restore_absent_tags_field_causes_no_change() -> None:
    """An older export without a `tags` key must never look like "restore
    to zero tags" -- it means "not captured", so no change at all."""
    client = FakeQbitClient(
        torrents=[{"hash": "hash-a", "name": "A", "tags": "kept"}],
        trackers_by_hash={"hash-a": []},
    )
    export = {
        "torrents": [_export_torrent(hash="hash-a", name="A", omit_tags=True)]
    }

    plan = plan_backup_restore(client, export)

    assert plan.tag_changes == ()


def test_plan_backup_restore_trackers_are_additive_and_filter_noise() -> None:
    client = FakeQbitClient(
        torrents=[{"hash": "hash-a", "name": "A"}],
        trackers_by_hash={
            "hash-a": [{"url": "https://kept.example/announce", "status": "2"}]
        },
    )
    export = {
        "torrents": [
            _export_torrent(
                hash="hash-a",
                name="A",
                trackers=[
                    {
                        "url": "https://kept.example/announce",
                        "status": "2",
                        "disabled": False,
                    },
                    {
                        "url": "https://new.example/announce",
                        "status": "2",
                        "disabled": False,
                    },
                    {
                        "url": "https://stale.example/announce",
                        "status": "0",
                        "disabled": True,
                    },
                    {"url": "** [DHT] **", "status": "0", "disabled": False},
                ],
            )
        ]
    }

    plan = plan_backup_restore(client, export)

    assert len(plan.tracker_changes) == 1
    assert plan.tracker_changes[0].added_trackers == (
        "https://new.example/announce",
    )


def test_apply_backup_restore_creates_missing_categories_before_setting() -> (
    None
):
    client = FakeQbitClient(
        torrents=[{"hash": "hash-a", "name": "A", "category": ""}],
        trackers_by_hash={"hash-a": []},
    )
    export = {
        "torrents": [_export_torrent(hash="hash-a", name="A", category="new")]
    }
    plan = plan_backup_restore(client, export)

    result = apply_backup_restore(client, plan)

    assert client.created_categories == ["new"]
    assert client.set_categories == [("hash-a", "new")]
    assert result.categories_created == ("new",)
    assert result.categories_restored == 1
    assert not result.failures


def test_apply_backup_restore_adds_missing_tags_and_trackers() -> None:
    client = FakeQbitClient(
        torrents=[{"hash": "hash-a", "name": "A"}],
        trackers_by_hash={"hash-a": []},
    )
    export = {
        "torrents": [
            _export_torrent(
                hash="hash-a",
                name="A",
                tags=["imported"],
                trackers=[
                    {
                        "url": "https://tracker.example/announce",
                        "status": "2",
                        "disabled": False,
                    }
                ],
            )
        ]
    }
    plan = plan_backup_restore(client, export)

    result = apply_backup_restore(client, plan)

    assert client.added_tags == [("hash-a", ["imported"])]
    assert client.added_trackers == [
        ("hash-a", ["https://tracker.example/announce"])
    ]
    assert result.tags_restored == 1
    assert result.trackers_restored == 1


def test_apply_backup_restore_records_independent_failures() -> None:
    client = FakeQbitClient(
        torrents=[{"hash": "hash-a", "name": "A", "category": ""}],
        trackers_by_hash={"hash-a": []},
        create_category_error=RuntimeError("cannot create"),
        set_category_error=RuntimeError("conflict"),
    )
    export = {
        "torrents": [_export_torrent(hash="hash-a", name="A", category="new")]
    }
    plan = plan_backup_restore(client, export)

    result = apply_backup_restore(client, plan)

    actions = {failure.action for failure in result.failures}
    assert actions == {"create_category", "set_category"}
    assert result.categories_created == ()
    assert result.categories_restored == 0


def test_apply_backup_restore_sanitizes_tracker_failure_messages() -> None:
    client = FakeQbitClient(
        torrents=[{"hash": "hash-a", "name": "A"}],
        trackers_by_hash={"hash-a": []},
        add_trackers_error=RuntimeError(
            "failed for https://tracker.example/announce/SECRET-PASSKEY"
        ),
    )
    export = {
        "torrents": [
            _export_torrent(
                hash="hash-a",
                name="A",
                trackers=[
                    {
                        "url": "https://tracker.example/announce/SECRET-PASSKEY",
                        "status": "2",
                        "disabled": False,
                    }
                ],
            )
        ]
    }
    plan = plan_backup_restore(client, export)

    result = apply_backup_restore(client, plan)

    assert len(result.failures) == 1
    assert "SECRET-PASSKEY" not in result.failures[0].message


class FakeQbitClient:
    """Provide the qBittorrent methods needed by backup tests."""

    def __init__(
        self,
        torrents: list[dict[str, Any]],
        trackers_by_hash: dict[str, list[dict[str, str]]],
        *,
        categories: dict[str, dict[str, Any]] | None = None,
        create_category_error: Exception | None = None,
        set_category_error: Exception | None = None,
        add_tags_error: Exception | None = None,
        add_trackers_error: Exception | None = None,
    ) -> None:
        """Store fake torrent and tracker data."""
        self.torrents = torrents
        self.trackers_by_hash = trackers_by_hash
        self.categories = dict(categories or {})
        self.create_category_error = create_category_error
        self.set_category_error = set_category_error
        self.add_tags_error = add_tags_error
        self.add_trackers_error = add_trackers_error
        self.created_categories: list[str] = []
        self.set_categories: list[tuple[str | list[str], str]] = []
        self.added_tags: list[tuple[str | list[str], list[str]]] = []
        self.added_trackers: list[tuple[str, list[str]]] = []

    def torrents_info(
        self,
        torrent_hashes: list[str] | None = None,
        include_trackers: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Return fake torrents.

        Never embeds a `"trackers"` field regardless of
        `include_trackers`: this double models a server that does not
        support it, so `select_and_inspect` always falls back to
        `torrents_trackers()` -- exactly what this file's tests expect.
        """
        return self.torrents

    def torrents_trackers(self, torrent_hash: str) -> list[dict[str, str]]:
        """Return fake trackers for a torrent."""
        return self.trackers_by_hash[torrent_hash]

    def torrents_categories(self) -> dict[str, dict[str, Any]]:
        """Return fake registered categories."""
        return dict(self.categories)

    def torrents_create_category(self, name: str, **kwargs: Any) -> None:
        """Record a fake category creation."""
        if self.create_category_error is not None:
            raise self.create_category_error
        self.created_categories.append(name)
        self.categories[name] = {"name": name}

    def torrents_set_category(
        self, torrent_hashes: str | list[str], category: str
    ) -> None:
        """Record a fake category assignment."""
        if self.set_category_error is not None:
            raise self.set_category_error
        self.set_categories.append((torrent_hashes, category))

    def torrents_add_tags(
        self, torrent_hashes: str | list[str], tags: str | list[str]
    ) -> None:
        """Record a fake tag addition."""
        if self.add_tags_error is not None:
            raise self.add_tags_error
        tag_list = [tags] if isinstance(tags, str) else list(tags)
        self.added_tags.append((torrent_hashes, tag_list))

    def torrents_add_trackers(
        self, torrent_hash: str, urls: str | list[str]
    ) -> None:
        """Record a fake tracker addition."""
        if self.add_trackers_error is not None:
            raise self.add_trackers_error
        url_list = [urls] if isinstance(urls, str) else list(urls)
        self.added_trackers.append((torrent_hash, url_list))
