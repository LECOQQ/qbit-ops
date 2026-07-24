"""Test tracker management helpers."""

from typing import Any

import pytest

from app.trackers import (
    analyze_tracker_health,
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


def test_analyze_tracker_health_reports_variants_and_disabled() -> None:
    """Ensure tracker health reports query variants and disabled trackers."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker.example/announce?sig=a"},
                {"url": "** [DHT] **", "status": "0"},
            ],
            "hash-b": [
                {"url": "https://tracker.example/announce?sig=b"},
                {"url": "https://other.example/announce"},
            ],
        }
    )

    assert analyze_tracker_health(client) == {
        "summary": {
            "scanned": 2,
            "active_tracker_occurrences": 3,
            "disabled_tracker_occurrences": 1,
            "unique_exact_trackers": 3,
            "unique_logical_trackers": 2,
            "query_variant_groups": 1,
        },
        "disabled_trackers": ["** [DHT] **"],
        "query_variant_groups": [
            {
                "tracker": "https://tracker.example/announce",
                "variants": [
                    "https://tracker.example/announce?sig=a",
                    "https://tracker.example/announce?sig=b",
                ],
                "torrents": [
                    "hash-a (hash-a)",
                    "hash-b (hash-b)",
                ],
            }
        ],
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
        "tracker": "https://tracker.example/announce",
        "match": "without-query",
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
                "matching_tracker_urls": [
                    "https://tracker.example/announce?sig=a",
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


def test_plan_tracker_addition_reports_matching_torrents() -> None:
    """Ensure the addition plan reports impacted torrents unconditionally."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [{"url": "https://tracker-a.example/announce"}],
            "hash-b": [{"url": "https://tracker-c.example/announce"}],
        }
    )

    plan = plan_tracker_addition(
        client=client,
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
    """Ensure applying an addition plan calls the add-trackers API."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [{"url": "https://tracker-a.example/announce"}],
        }
    )

    plan = plan_tracker_addition(
        client=client,
        source_tracker="https://tracker-a.example/announce",
        target_tracker="https://tracker-b.example/announce",
    )
    apply_tracker_addition(client, plan)

    assert client.added_trackers == [
        ("hash-a", "https://tracker-b.example/announce")
    ]


def test_plan_tracker_addition_reports_already_had_target() -> None:
    """Ensure torrents that already have the target tracker are reported."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker-a.example/announce"},
                {"url": "https://tracker-b.example/announce"},
            ],
        }
    )

    plan = plan_tracker_addition(
        client=client,
        source_tracker="https://tracker-a.example/announce",
        target_tracker="https://tracker-b.example/announce",
    )

    assert plan.matched_source == 1
    assert plan.changes == ()
    assert len(plan.already_had_target) == 1
    assert plan.already_had_target[0].hash == "hash-a"


def test_plan_tracker_removal_reports_matching_torrents() -> None:
    """Ensure the removal plan reports matching torrents without mutating."""
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
    """Ensure without-query removal matches raw dynamic tracker URLs."""
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
    """Ensure the removal plan carries the raw matching URLs per torrent."""
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
    """Ensure the replacement plan reports matching torrents by default."""
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
    """Ensure applying a replacement plan edits the matching raw source URL."""
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
    """Ensure replacement avoids duplicating an existing target tracker."""
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
    """Ensure dynamic source variants do not become duplicate targets."""
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
    """Ensure a template missing the placeholder is rejected."""
    client = FakeQbitClient(trackers_by_hash={})

    with pytest.raises(RuntimeError, match="placeholder"):
        plan_tracker_passkey_replacement(
            client=client,
            tracker_template="https://tracker.example/announce",
            new_passkey="new",
        )


def test_plan_passkey_replacement_updates_query_placeholder() -> None:
    """Ensure a query-string passkey placeholder is planned in place."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker-query.example/announce?passkey=old"}
            ],
            "hash-b": [{"url": "https://tracker-c.example/announce"}],
        }
    )

    plan = plan_tracker_passkey_replacement(
        client=client,
        tracker_template="https://tracker-query.example/announce?passkey={passkey}",
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
            "https://tracker-query.example/announce?passkey=old",
            "https://tracker-query.example/announce"
            "?passkey=UNMISTAKABLE-NEW-PASSKEY-VALUE",
        )
    ]


def test_plan_passkey_replacement_preserves_other_query_params() -> None:
    """Ensure unrelated query parameters survive the passkey swap."""
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
    """Ensure a passkey path segment placed before 'announce' is planned."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker-prefix.example/old-passkey/announce"}
            ],
        }
    )

    plan = plan_tracker_passkey_replacement(
        client=client,
        tracker_template="https://tracker-prefix.example/{passkey}/announce",
        new_passkey="UNMISTAKABLE-NEW-PASSKEY-VALUE",
    )
    apply_tracker_passkey_replacement(client, plan)

    assert len(plan.changes) == 1
    assert client.edited_trackers == [
        (
            "hash-a",
            "https://tracker-prefix.example/old-passkey/announce",
            "https://tracker-prefix.example/"
            "UNMISTAKABLE-NEW-PASSKEY-VALUE/announce",
        )
    ]


def test_plan_passkey_replacement_updates_path_segment_after_announce() -> None:
    """Ensure a passkey path segment placed after 'announce' is planned."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [{"url": "https://tracker-suffix.example/announce/old-passkey"}],
        }
    )

    plan = plan_tracker_passkey_replacement(
        client=client,
        tracker_template="https://tracker-suffix.example/announce/{passkey}",
        new_passkey="UNMISTAKABLE-NEW-PASSKEY-VALUE",
    )
    apply_tracker_passkey_replacement(client, plan)

    assert len(plan.changes) == 1
    assert client.edited_trackers == [
        (
            "hash-a",
            "https://tracker-suffix.example/announce/old-passkey",
            "https://tracker-suffix.example/announce/UNMISTAKABLE-NEW-PASSKEY-VALUE",
        )
    ]


def test_plan_passkey_replacement_preserves_dynamic_query_params() -> None:
    """Ensure per-torrent query params survive a path-based passkey swap."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {
                    "url": "http://tracker-port.example:8080/old-passkey"
                    "/announce?announce_sig=abc&announce_ts=1"
                },
            ],
        }
    )

    plan = plan_tracker_passkey_replacement(
        client=client,
        tracker_template="http://tracker-port.example:8080/{passkey}/announce",
        new_passkey="UNMISTAKABLE-NEW-PASSKEY-VALUE",
    )
    apply_tracker_passkey_replacement(client, plan)

    assert len(plan.changes) == 1
    assert client.edited_trackers == [
        (
            "hash-a",
            "http://tracker-port.example:8080/old-passkey/announce"
            "?announce_sig=abc&announce_ts=1",
            "http://tracker-port.example:8080/UNMISTAKABLE-NEW-PASSKEY-VALUE"
            "/announce?announce_sig=abc&announce_ts=1",
        )
    ]


def test_plan_passkey_replacement_never_leaks_secret_in_repr() -> None:
    """Ensure the plan's repr never exposes the old or new passkey.

    `PasskeyReplacementChange.stale_urls` uses `field(repr=False)`
    specifically so an accidental `print(change)` or `logger.debug(plan)`
    cannot leak a passkey; this asserts that guarantee directly with an
    unmistakable secret fixture.
    """
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [{"url": "https://tracker-suffix.example/announce/OLD-SECRET"}],
        }
    )

    plan = plan_tracker_passkey_replacement(
        client=client,
        tracker_template="https://tracker-suffix.example/announce/{passkey}",
        new_passkey="UNMISTAKABLE-NEW-PASSKEY-VALUE",
    )

    rendered = repr(plan.changes[0])
    assert "UNMISTAKABLE-NEW-PASSKEY-VALUE" not in rendered
    assert "OLD-SECRET" not in rendered


def test_plan_passkey_replacement_is_pure_and_does_not_mutate() -> None:
    """Ensure planning a passkey replacement never calls the edit API."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [{"url": "https://tracker-suffix.example/announce/old"}],
        }
    )

    plan = plan_tracker_passkey_replacement(
        client=client,
        tracker_template="https://tracker-suffix.example/announce/{passkey}",
        new_passkey="UNMISTAKABLE-NEW-PASSKEY-VALUE",
    )

    assert plan.scanned == 1
    assert plan.matched_source == 1
    assert plan.already_up_to_date == 0
    assert plan.replaced_url_count == 1
    assert client.edited_trackers == []


def test_plan_passkey_replacement_skips_torrents_already_up_to_date() -> None:
    """Ensure torrents already using the new passkey are left untouched."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [
                {"url": "https://tracker-suffix.example/announce/ALREADY-CURRENT"}
            ],
        }
    )

    plan = plan_tracker_passkey_replacement(
        client=client,
        tracker_template="https://tracker-suffix.example/announce/{passkey}",
        new_passkey="ALREADY-CURRENT",
    )
    apply_tracker_passkey_replacement(client, plan)

    assert plan.scanned == 1
    assert plan.matched_source == 1
    assert plan.already_up_to_date == 1
    assert plan.changes == ()
    assert client.edited_trackers == []


def test_plan_passkey_replacement_ignores_unmatched_trackers() -> None:
    """Ensure torrents without the target tracker host/path are skipped."""
    client = FakeQbitClient(
        trackers_by_hash={
            "hash-a": [{"url": "https://tracker-b.example/announce/old"}],
        }
    )

    plan = plan_tracker_passkey_replacement(
        client=client,
        tracker_template="https://tracker-suffix.example/announce/{passkey}",
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
# match nothing at all (NO_MATCH). Callers (app/main.py::_run_mutation)
# use `matched == 0` to report NO_MATCH and `not plan.changes` (with
# matched > 0) to report NO_CHANGES — never APPLIED for either.


def test_plan_tracker_addition_matches_nothing() -> None:
    """Ensure an addition plan with no matching torrents reports zero."""
    client = FakeQbitClient(
        trackers_by_hash={"hash-a": [{"url": "https://other.example/announce"}]}
    )

    plan = plan_tracker_addition(
        client=client,
        source_tracker="https://tracker-a.example/announce",
        target_tracker="https://tracker-b.example/announce",
    )

    assert plan.matched_source == 0
    assert plan.changes == ()
    assert plan.already_had_target == ()


def test_plan_tracker_removal_matches_nothing() -> None:
    """Ensure a removal plan with no matching torrents reports zero.

    `matched_tracker` always equals `len(changes)` for removal (a match
    always implies a removal) so this plan type never reaches
    NO_CHANGES — only NO_MATCH or a real change.
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
    """Ensure a replacement plan with no matching torrents reports zero.

    `matched_source` always equals `len(changes)` for replacement (a
    match always implies at least a removal), so this plan type never
    reaches NO_CHANGES either — only NO_MATCH or a real change.
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
