"""Test tracker management helpers."""

from typing import Any

import pytest

from qbit_core.features.trackers import (
    apply_tracker_addition,
    apply_tracker_passkey_replacement,
    apply_tracker_removal,
    apply_tracker_replacement,
    export_tracker_state,
    has_tracker,
    inspect_tracker,
    list_tracker_usage,
    normalize_tracker_url,
    plan_tracker_addition,
    plan_tracker_passkey_replacement,
    plan_tracker_removal,
    plan_tracker_replacement,
)
from qbit_core.shared.inspection import Inspection, select_and_inspect
from qbit_core.shared.selection import SelectionRequest

TrackersByHash = dict[str, list[dict[str, Any]]]


def test_normalize_tracker_url_strips_spaces() -> None:
    assert (
        normalize_tracker_url("  https://tracker.example/announce  ")
        == "https://tracker.example/announce"
    )


def test_normalize_tracker_url_removes_trailing_slash() -> None:
    assert (
        normalize_tracker_url("https://tracker.example/announce/")
        == "https://tracker.example/announce"
    )


def test_normalize_tracker_url_keeps_query_in_exact_mode() -> None:
    tracker_url = "https://tracker.example/announce?sig=a&announce_ts=1"

    assert normalize_tracker_url(tracker_url) == tracker_url


def test_normalize_tracker_url_removes_query_in_without_query_mode() -> None:
    assert (
        normalize_tracker_url(
            "https://tracker.example/announce/?sig=a&announce_ts=1",
            match_mode="without-query",
        )
        == "https://tracker.example/announce"
    )


def test_has_tracker_matches_existing_tracker_with_trailing_slash() -> None:
    trackers = ["https://tracker.example/announce/"]

    assert has_tracker(trackers, "https://tracker.example/announce")


def test_has_tracker_returns_false_when_absent() -> None:
    trackers = ["https://tracker-a.example/announce"]

    assert not has_tracker(trackers, "https://tracker-b.example/announce")


def test_has_tracker_matches_query_variants_without_query() -> None:
    trackers = ["https://tracker.example/announce?sig=a&announce_ts=1"]

    assert not has_tracker(trackers, "https://tracker.example/announce")
    assert has_tracker(
        trackers,
        "https://tracker.example/announce",
        match_mode="without-query",
    )


def test_list_tracker_usage_counts_unique_trackers_per_torrent() -> None:
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
    """Two different query-string variants on the same host both match --
    inspection is host[:port]-based, not exact-URL-based -- and the output never
    carries a raw URL, only structural fields.
    """
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {
                    "url": "https://tracker.example/announce?sig=a",
                    "status": 2,
                },
                {"url": "https://other.example/announce", "status": 2},
            ],
            "hash-b": [
                {
                    "url": "https://tracker.example/announce?sig=b",
                    "status": 4,
                },
            ],
        }
    )

    report = inspect_tracker(client=client, tracker="tracker.example")

    assert report == {
        "tracker": "tracker.example",
        "scanned": 2,
        "matched_tracker": 2,
        "torrents": [
            {
                "hash": "hash-a",
                "name": "hash-a",
                "state": "",
                "size": 0,
                "progress": 0.0,
                "ratio": 0.0,
                "active_tracker_count": 2,
                "matching_endpoints": [
                    {
                        "raw_status": 2,
                        "health": "healthy",
                        "enabled": True,
                        "message": None,
                        "scheme": "https",
                        "path_shape": "/<secret>",
                        "query_keys": ["sig"],
                    },
                ],
            },
            {
                "hash": "hash-b",
                "name": "hash-b",
                "state": "",
                "size": 0,
                "progress": 0.0,
                "ratio": 0.0,
                "active_tracker_count": 1,
                "matching_endpoints": [
                    {
                        "raw_status": 4,
                        "health": "critical",
                        "enabled": True,
                        "message": None,
                        "scheme": "https",
                        "path_shape": "/<secret>",
                        "query_keys": ["sig"],
                    },
                ],
            },
        ],
    }


def test_inspect_tracker_reports_a_disabled_matching_endpoint() -> None:
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker.example/announce", "status": 0},
            ],
        }
    )

    report = inspect_tracker(client=client, tracker="tracker.example")

    assert report["matched_tracker"] == 1
    endpoint = report["torrents"][0]["matching_endpoints"][0]
    assert endpoint["enabled"] is False
    assert endpoint["health"] == "disabled"
    # A disabled endpoint is never counted in active_tracker_count either.
    assert report["torrents"][0]["active_tracker_count"] == 0


def test_export_tracker_state_exports_normalized_identities_only() -> None:
    """The DHT pseudo-tracker is excluded entirely (no host to normalize)."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker.example/announce?sig=a"},
                {"url": "** [DHT] **", "status": "0"},
            ],
        }
    )

    assert export_tracker_state(client) == {
        "summary": {
            "torrents": 1,
            "unique_trackers": 1,
        },
        "torrents": [
            {
                "hash": "hash-a",
                "name": "hash-a",
                "normalized_trackers": [
                    "tracker.example",
                ],
            },
        ],
    }


def test_plan_tracker_addition_reports_matching_torrents() -> None:
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [{"url": "https://tracker-a.example/announce"}],
            "hash-b": [{"url": "https://tracker-c.example/announce"}],
        }
    )

    plan = plan_tracker_addition(
        _inspection(client),
        source_tracker="https://tracker-a.example/announce",
        target_tracker="https://tracker-b.example/announce",
    )

    assert plan.scanned == 2
    assert plan.matched_source == 1
    assert plan.already_had_target == ()
    assert len(plan.changes) == 1
    assert plan.changes[0].hash == "hash-a"
    assert plan.changes[0].name == "hash-a"


def test_apply_tracker_addition_adds_target_tracker() -> None:
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [{"url": "https://tracker-a.example/announce"}],
        }
    )

    plan = plan_tracker_addition(
        _inspection(client),
        source_tracker="https://tracker-a.example/announce",
        target_tracker="https://tracker-b.example/announce",
    )
    apply_tracker_addition(client, plan)

    assert client.added_trackers == [
        ("hash-a", "https://tracker-b.example/announce")
    ]


def test_plan_tracker_addition_reports_already_had_target() -> None:
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker-a.example/announce"},
                {"url": "https://tracker-b.example/announce"},
            ],
        }
    )

    plan = plan_tracker_addition(
        _inspection(client),
        source_tracker="https://tracker-a.example/announce",
        target_tracker="https://tracker-b.example/announce",
    )

    assert plan.matched_source == 1
    assert plan.changes == ()
    assert len(plan.already_had_target) == 1
    assert plan.already_had_target[0].hash == "hash-a"


def test_plan_tracker_removal_reports_matching_torrents() -> None:
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [{"url": "https://tracker.example/announce/"}],
            "hash-b": [{"url": "https://other.example/announce"}],
        }
    )

    plan = plan_tracker_removal(
        client=client,
        tracker="https://tracker.example/announce",
    )

    assert plan.scanned == 2
    assert plan.matched_tracker == 1
    assert plan.removed_url_count == 1
    assert client.removed_trackers == []


def test_apply_tracker_removal_matches_query_variants_without_query() -> None:
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker.example/announce?sig=a"},
                {"url": "https://tracker.example/announce?sig=b"},
            ],
        }
    )

    plan = plan_tracker_removal(
        client=client,
        tracker="https://tracker.example/announce",
        match_mode="without-query",
    )
    apply_tracker_removal(client, plan)

    assert plan.scanned == 1
    assert plan.matched_tracker == 1
    assert plan.removed_url_count == 2
    assert client.removed_trackers == [
        (
            "hash-a",
            [
                "https://tracker.example/announce?sig=a",
                "https://tracker.example/announce?sig=b",
            ],
        )
    ]


def test_plan_tracker_removal_reports_matching_urls_per_torrent() -> None:
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [{"url": "https://tracker.example/announce/"}],
        }
    )

    plan = plan_tracker_removal(
        client=client,
        tracker="https://tracker.example/announce",
    )

    assert plan.matched_tracker == 1
    assert len(plan.changes) == 1
    assert plan.changes[0].hash == "hash-a"
    assert plan.changes[0].urls == ("https://tracker.example/announce/",)


def test_apply_tracker_removal_removes_matching_raw_urls() -> None:
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker.example/announce/"},
                {"url": " https://tracker.example/announce "},
            ],
            "hash-b": [{"url": "https://other.example/announce"}],
        }
    )

    plan = plan_tracker_removal(
        client=client,
        tracker="https://tracker.example/announce",
    )
    apply_tracker_removal(client, plan)

    assert plan.scanned == 2
    assert plan.matched_tracker == 1
    assert plan.removed_url_count == 2
    assert client.removed_trackers == [
        (
            "hash-a",
            [
                "https://tracker.example/announce/",
                " https://tracker.example/announce ",
            ],
        )
    ]


def test_plan_tracker_replacement_reports_matching_torrents() -> None:
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [{"url": "https://tracker-a.example/announce"}],
            "hash-b": [{"url": "https://tracker-c.example/announce"}],
        }
    )

    plan = plan_tracker_replacement(
        client=client,
        source_tracker="https://tracker-a.example/announce",
        target_tracker="https://tracker-b.example/announce",
    )

    assert plan.scanned == 2
    assert plan.matched_source == 1
    assert plan.replaced_url_count == 1
    assert plan.removed_url_count == 0
    assert client.edited_trackers == []
    assert client.removed_trackers == []


def test_apply_tracker_replacement_replaces_source_url() -> None:
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [{"url": "https://tracker-a.example/announce/"}],
        }
    )

    plan = plan_tracker_replacement(
        client=client,
        source_tracker="https://tracker-a.example/announce",
        target_tracker="https://tracker-b.example/announce",
    )
    apply_tracker_replacement(client, plan)

    assert plan.matched_source == 1
    assert plan.replaced_url_count == 1
    assert plan.removed_url_count == 0
    assert client.edited_trackers == [
        (
            "hash-a",
            "https://tracker-a.example/announce/",
            "https://tracker-b.example/announce",
        )
    ]


def test_plan_tracker_replacement_removes_source_when_target_exists() -> None:
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker-a.example/announce"},
                {"url": "https://tracker-b.example/announce"},
            ],
        }
    )

    plan = plan_tracker_replacement(
        client=client,
        source_tracker="https://tracker-a.example/announce",
        target_tracker="https://tracker-b.example/announce",
    )
    apply_tracker_replacement(client, plan)

    assert plan.matched_source == 1
    assert len(plan.changes) == 1
    change = plan.changes[0]
    assert change.already_had_target is True
    assert change.replace_url is None
    assert change.remove_urls == ("https://tracker-a.example/announce",)
    assert client.edited_trackers == []
    assert client.removed_trackers == [
        ("hash-a", ["https://tracker-a.example/announce"])
    ]


def test_apply_tracker_replacement_removes_extra_variants() -> None:
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker-a.example/announce?sig=a"},
                {"url": "https://tracker-a.example/announce?sig=b"},
            ],
        }
    )

    plan = plan_tracker_replacement(
        client=client,
        source_tracker="https://tracker-a.example/announce",
        target_tracker="https://tracker-b.example/announce",
        match_mode="without-query",
    )
    apply_tracker_replacement(client, plan)

    assert plan.matched_source == 1
    assert plan.replaced_url_count == 1
    assert plan.removed_url_count == 1
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


def test_replace_tracker_passkey_rejects_template_without_placeholder() -> None:
    client = FakeQbitClient(trackers_by_hash={})

    with pytest.raises(RuntimeError, match="placeholder"):
        plan_tracker_passkey_replacement(
            client=client,
            tracker_template="https://tracker.example/announce",
            new_passkey="new",
        )


def test_plan_passkey_replacement_updates_query_placeholder() -> None:
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker.la-cale.space/announce?passkey=old"}
            ],
            "hash-b": [{"url": "https://tracker-c.example/announce"}],
        }
    )

    plan = plan_tracker_passkey_replacement(
        client=client,
        tracker_template="https://tracker.la-cale.space/announce?passkey={passkey}",
        new_passkey="UNMISTAKABLE-NEW-PASSKEY-VALUE",
    )
    apply_tracker_passkey_replacement(client, plan)

    assert plan.scanned == 2
    assert plan.matched_source == 1
    assert plan.already_up_to_date == 0
    assert len(plan.changes) == 1
    assert plan.changes[0].stale_url_count == 1
    assert client.edited_trackers == [
        (
            "hash-a",
            "https://tracker.la-cale.space/announce?passkey=old",
            "https://tracker.la-cale.space/announce"
            "?passkey=UNMISTAKABLE-NEW-PASSKEY-VALUE",
        )
    ]


def test_plan_passkey_replacement_preserves_other_query_params() -> None:
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker.example/announce?a=1&passkey=old&b=2"},
            ],
        }
    )

    plan = plan_tracker_passkey_replacement(
        client=client,
        tracker_template="https://tracker.example/announce?passkey={passkey}",
        new_passkey="UNMISTAKABLE-NEW-PASSKEY-VALUE",
    )
    apply_tracker_passkey_replacement(client, plan)

    assert len(plan.changes) == 1
    assert client.edited_trackers == [
        (
            "hash-a",
            "https://tracker.example/announce?a=1&passkey=old&b=2",
            "https://tracker.example/announce"
            "?a=1&passkey=UNMISTAKABLE-NEW-PASSKEY-VALUE&b=2",
        )
    ]


def test_plan_passkey_replacement_updates_path_before_announce() -> None:
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker.yggreborn.org/old-passkey/announce"}
            ],
        }
    )

    plan = plan_tracker_passkey_replacement(
        client=client,
        tracker_template="https://tracker.yggreborn.org/{passkey}/announce",
        new_passkey="UNMISTAKABLE-NEW-PASSKEY-VALUE",
    )
    apply_tracker_passkey_replacement(client, plan)

    assert len(plan.changes) == 1
    assert client.edited_trackers == [
        (
            "hash-a",
            "https://tracker.yggreborn.org/old-passkey/announce",
            "https://tracker.yggreborn.org/"
            "UNMISTAKABLE-NEW-PASSKEY-VALUE/announce",
        )
    ]


def test_plan_passkey_replacement_updates_path_segment_after_announce() -> None:
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [{"url": "https://tk.tr4ker.net/announce/old-passkey"}],
        }
    )

    plan = plan_tracker_passkey_replacement(
        client=client,
        tracker_template="https://tk.tr4ker.net/announce/{passkey}",
        new_passkey="UNMISTAKABLE-NEW-PASSKEY-VALUE",
    )
    apply_tracker_passkey_replacement(client, plan)

    assert len(plan.changes) == 1
    assert client.edited_trackers == [
        (
            "hash-a",
            "https://tk.tr4ker.net/announce/old-passkey",
            "https://tk.tr4ker.net/announce/UNMISTAKABLE-NEW-PASSKEY-VALUE",
        )
    ]


def test_plan_passkey_replacement_preserves_dynamic_query_params() -> None:
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {
                    "url": "http://connect.maxp2p.org:8080/old-passkey"
                    "/announce?announce_sig=abc&announce_ts=1"
                },
            ],
        }
    )

    plan = plan_tracker_passkey_replacement(
        client=client,
        tracker_template="http://connect.maxp2p.org:8080/{passkey}/announce",
        new_passkey="UNMISTAKABLE-NEW-PASSKEY-VALUE",
    )
    apply_tracker_passkey_replacement(client, plan)

    assert len(plan.changes) == 1
    assert client.edited_trackers == [
        (
            "hash-a",
            "http://connect.maxp2p.org:8080/old-passkey/announce"
            "?announce_sig=abc&announce_ts=1",
            "http://connect.maxp2p.org:8080/UNMISTAKABLE-NEW-PASSKEY-VALUE"
            "/announce?announce_sig=abc&announce_ts=1",
        )
    ]


def test_plan_passkey_replacement_never_leaks_secret_in_repr() -> None:
    """`PasskeyReplacementChange.stale_urls` uses `field(repr=False)`
    specifically so an accidental `print(change)` or `logger.debug(plan)`
    cannot leak a passkey; this asserts that guarantee directly with an
    unmistakable secret fixture.
    """
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [{"url": "https://tk.tr4ker.net/announce/OLD-SECRET"}],
        }
    )

    plan = plan_tracker_passkey_replacement(
        client=client,
        tracker_template="https://tk.tr4ker.net/announce/{passkey}",
        new_passkey="UNMISTAKABLE-NEW-PASSKEY-VALUE",
    )

    rendered = repr(plan.changes[0])
    assert "UNMISTAKABLE-NEW-PASSKEY-VALUE" not in rendered
    assert "OLD-SECRET" not in rendered


def test_plan_passkey_replacement_is_pure_and_does_not_mutate() -> None:
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [{"url": "https://tk.tr4ker.net/announce/old"}],
        }
    )

    plan = plan_tracker_passkey_replacement(
        client=client,
        tracker_template="https://tk.tr4ker.net/announce/{passkey}",
        new_passkey="UNMISTAKABLE-NEW-PASSKEY-VALUE",
    )

    assert plan.scanned == 1
    assert plan.matched_source == 1
    assert plan.already_up_to_date == 0
    assert plan.replaced_url_count == 1
    assert client.edited_trackers == []


def test_plan_passkey_replacement_skips_torrents_already_up_to_date() -> None:
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tk.tr4ker.net/announce/ALREADY-CURRENT"}
            ],
        }
    )

    plan = plan_tracker_passkey_replacement(
        client=client,
        tracker_template="https://tk.tr4ker.net/announce/{passkey}",
        new_passkey="ALREADY-CURRENT",
    )
    apply_tracker_passkey_replacement(client, plan)

    assert plan.scanned == 1
    assert plan.matched_source == 1
    assert plan.already_up_to_date == 1
    assert plan.changes == ()
    assert client.edited_trackers == []


def test_plan_passkey_replacement_ignores_unmatched_trackers() -> None:
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [{"url": "https://tracker-b.example/announce/old"}],
        }
    )

    plan = plan_tracker_passkey_replacement(
        client=client,
        tracker_template="https://tk.tr4ker.net/announce/{passkey}",
        new_passkey="UNMISTAKABLE-NEW-PASSKEY-VALUE",
    )
    apply_tracker_passkey_replacement(client, plan)

    assert plan.scanned == 1
    assert plan.matched_source == 0
    assert plan.already_up_to_date == 0
    assert plan.changes == ()
    assert client.edited_trackers == []


# --- NO_MATCH vs NO_CHANGES: matched count vs actual changes -----------
#
# `matched` and `len(plan.changes)` are deliberately different signals:
# a plan can match targets with nothing left to change (NO_CHANGES), or
# match nothing at all (NO_MATCH). Callers (qbit_ops/main.py::_run_mutation)
# use `matched == 0` to report NO_MATCH and `not plan.changes` (with
# matched > 0) to report NO_CHANGES — never APPLIED for either.


def test_plan_tracker_addition_matches_nothing() -> None:
    client = FakeQbitClient(
        trackers_by_hash={"hash-a": [{"url": "https://other.example/announce"}]}
    )

    plan = plan_tracker_addition(
        _inspection(client),
        source_tracker="https://tracker-a.example/announce",
        target_tracker="https://tracker-b.example/announce",
    )

    assert plan.matched_source == 0
    assert plan.changes == ()
    assert plan.already_had_target == ()


def test_plan_tracker_removal_matches_nothing() -> None:
    """`matched_tracker` always equals `len(changes)` for removal (a match
    always implies a removal) so this plan type never reaches NO_CHANGES --
    only NO_MATCH or a real change.
    """
    client = FakeQbitClient(
        trackers_by_hash={"hash-a": [{"url": "https://other.example/announce"}]}
    )

    plan = plan_tracker_removal(
        client=client, tracker="https://tracker-a.example/announce"
    )

    assert plan.matched_tracker == 0
    assert plan.changes == ()


def test_plan_tracker_replacement_matches_nothing() -> None:
    """`matched_source` always equals `len(changes)` for replacement (a match
    always implies at least a removal), so this plan type never reaches
    NO_CHANGES either -- only NO_MATCH or a real change.
    """
    client = FakeQbitClient(
        trackers_by_hash={"hash-a": [{"url": "https://other.example/announce"}]}
    )

    plan = plan_tracker_replacement(
        client=client,
        source_tracker="https://tracker-a.example/announce",
        target_tracker="https://tracker-b.example/announce",
    )

    assert plan.matched_source == 0
    assert plan.changes == ()


def _inspection(client: "FakeQbitClient") -> Inspection:
    """Run the real SELECT + INSPECT stages against a fake client.

    Planning itself needs no client; this only builds the input the
    planner consumes, exercising the same composition the CLI uses.
    """
    return select_and_inspect(client, SelectionRequest())


class FakeQbitClient:
    """Provide the qBittorrent methods needed by tracker tests."""

    def __init__(self, trackers_by_hash: TrackersByHash) -> None:
        """Store fake tracker data by torrent hash."""
        self.trackers_by_hash = trackers_by_hash
        self.removed_trackers: list[tuple[str, list[str]]] = []
        self.edited_trackers: list[tuple[str, str, str]] = []
        self.added_trackers: list[tuple[str, str]] = []

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
        self.added_trackers.append((torrent_hash, urls))

    def torrents_edit_tracker(
        self,
        torrent_hash: str,
        original_url: str,
        new_url: str,
    ) -> None:
        """Record fake tracker replacements."""
        self.edited_trackers.append((torrent_hash, original_url, new_url))
