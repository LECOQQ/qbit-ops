"""Test torrent listing, filtering, and bulk-selection helpers."""

from typing import Any

import pytest

from qbit_ops.features.torrents import (
    TorrentBulkAction,
    TorrentFilter,
    apply_bulk_torrent_action,
    build_torrent_filter,
    describe_torrent_filter,
    inspect_torrent,
    list_category_usage,
    list_torrents_with_trackers,
    plan_bulk_torrent_action,
    search_torrents_by_name,
    select_torrents,
    torrent_filter_to_dict,
    validate_torrent_selector,
)
from qbit_ops.shared.selectors import AmbiguousTorrentHashError


class FakeQbitClient:
    """Provide the qBittorrent methods needed by torrent tests."""

    def __init__(
        self,
        torrents: list[dict[str, Any]],
        trackers_by_hash: dict[str, list[dict[str, str]]] | None = None,
    ) -> None:
        """Store fake torrent and tracker data."""
        self.torrents = torrents
        self.trackers_by_hash = trackers_by_hash or {}
        self.paused_hashes: list[str | list[str]] = []
        self.resumed_hashes: list[str | list[str]] = []
        self.started_hashes: list[str | list[str]] = []
        self.reannounced_hashes: list[str | list[str]] = []
        self.torrents_trackers_calls = 0
        self.torrents_trackers_call_order: list[str] = []

    def torrents_info(self) -> list[dict[str, Any]]:
        """Return fake torrents."""
        return self.torrents

    def torrents_trackers(self, torrent_hash: str) -> list[dict[str, str]]:
        """Return fake trackers for a torrent, recording the call."""
        self.torrents_trackers_calls += 1
        self.torrents_trackers_call_order.append(torrent_hash)
        return self.trackers_by_hash[torrent_hash]

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


def _torrent(**overrides: Any) -> dict[str, Any]:
    """Build a fake torrent record with sane defaults."""
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


# --- select_torrents / TorrentFilter ----------------------------------------


def test_select_torrents_with_empty_filter_matches_everything() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, name="B"),
            _torrent(hash="b" * 40, name="A"),
        ]
    )

    selection = select_torrents(client, TorrentFilter())

    assert selection.scanned == 2
    assert len(selection.matched) == 2
    assert selection.filters == TorrentFilter()
    assert selection.tracker_data_collected is False
    assert client.torrents_trackers_calls == 0


def test_select_torrents_repeated_categories_use_or() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, category="movies"),
            _torrent(hash="b" * 40, category="series"),
            _torrent(hash="c" * 40, category="music"),
        ]
    )

    selection = select_torrents(
        client, TorrentFilter(categories=("movies", "series"))
    )

    assert {t.hash for t in selection.matched} == {"a" * 40, "b" * 40}


def test_select_torrents_different_filter_types_use_and() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, category="movies", state="stalledUP"),
            _torrent(hash="b" * 40, category="movies", state="uploading"),
            _torrent(hash="c" * 40, category="series", state="stalledUP"),
        ]
    )

    selection = select_torrents(
        client,
        TorrentFilter(categories=("movies",), states=("stalled",)),
    )

    assert [t.hash for t in selection.matched] == ["a" * 40]


def test_select_torrents_uncategorized_token_matches_bare_category() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, category=""),
            _torrent(hash="b" * 40, category="movies"),
        ]
    )

    selection = select_torrents(
        client, TorrentFilter(categories=("uncategorized",))
    )

    assert [t.hash for t in selection.matched] == ["a" * 40]
    assert selection.matched[0].category == "(uncategorized)"


def test_select_torrents_display_label_token_also_matches_uncategorized() -> (
    None
):
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, category=""),
            _torrent(hash="b" * 40, category="movies"),
        ]
    )

    selection = select_torrents(
        client, TorrentFilter(categories=("(uncategorized)",))
    )

    assert [t.hash for t in selection.matched] == ["a" * 40]


def test_select_torrents_completed_and_incomplete() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, progress=1.0),
            _torrent(hash="b" * 40, progress=0.5),
        ]
    )

    completed = select_torrents(client, TorrentFilter(completed=True))
    incomplete = select_torrents(client, TorrentFilter(completed=False))

    assert [t.hash for t in completed.matched] == ["a" * 40]
    assert [t.hash for t in incomplete.matched] == ["b" * 40]


def test_select_torrents_active_and_inactive() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, state="uploading"),
            _torrent(hash="b" * 40, state="pausedUP"),
            _torrent(hash="c" * 40, state="stoppedDL"),
        ]
    )

    active = select_torrents(client, TorrentFilter(active=True))
    inactive = select_torrents(client, TorrentFilter(active=False))

    assert [t.hash for t in active.matched] == ["a" * 40]
    assert {t.hash for t in inactive.matched} == {"b" * 40, "c" * 40}


def test_select_torrents_stalled() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, state="stalledDL"),
            _torrent(hash="b" * 40, state="downloading"),
        ]
    )

    selection = select_torrents(client, TorrentFilter(stalled=True))

    assert [t.hash for t in selection.matched] == ["a" * 40]


def test_select_torrents_errored() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, state="error"),
            _torrent(hash="b" * 40, state="uploading"),
        ]
    )

    selection = select_torrents(client, TorrentFilter(errored=True))

    assert [t.hash for t in selection.matched] == ["a" * 40]


def test_select_torrents_state_normalization() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, state="pausedUP"),
            _torrent(hash="b" * 40, state="stoppedUP"),
            _torrent(hash="c" * 40, state="downloading"),
        ]
    )

    selection = select_torrents(client, TorrentFilter(states=("seeding",)))

    assert {t.hash for t in selection.matched} == {"a" * 40, "b" * 40}


def test_select_torrents_unknown_state_is_filterable_and_not_discarded() -> (
    None
):
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, state="totallyNewState"),
            _torrent(hash="b" * 40, state="uploading"),
        ]
    )

    unfiltered = select_torrents(client, TorrentFilter())
    assert {t.hash for t in unfiltered.matched} == {"a" * 40, "b" * 40}

    filtered = select_torrents(client, TorrentFilter(states=("unknown",)))
    assert [t.hash for t in filtered.matched] == ["a" * 40]


def test_select_torrents_tracker_matches_by_host() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40),
            _torrent(hash="b" * 40),
        ],
        trackers_by_hash={
            "a"
            * 40: [
                {
                    "url": "https://tracker.example:6969/announce/PASSKEY123",
                    "status": "2",
                }
            ],
            "b"
            * 40: [{"url": "https://other.example/announce", "status": "2"}],
        },
    )

    selection = select_torrents(
        client, TorrentFilter(tracker="tracker.example:6969")
    )

    assert [t.hash for t in selection.matched] == ["a" * 40]
    assert selection.tracker_data_collected is True
    assert selection.matched[0].tracker_count == 1


def test_select_torrents_tracker_filter_never_exposes_secrets() -> None:
    filters = build_torrent_filter(
        tracker="https://tracker.example/announce/SUPER-SECRET-PASSKEY"
    )

    assert "SUPER-SECRET-PASSKEY" not in (filters.tracker or "")
    assert filters.tracker == "tracker.example"

    client = FakeQbitClient(
        torrents=[_torrent(hash="a" * 40)],
        trackers_by_hash={
            "a"
            * 40: [
                {
                    "url": "https://tracker.example/announce/SUPER-SECRET-PASSKEY",
                    "status": "2",
                }
            ]
        },
    )
    selection = select_torrents(client, filters)

    assert "SUPER-SECRET-PASSKEY" not in str(torrent_filter_to_dict(filters))
    assert "SUPER-SECRET-PASSKEY" not in str(selection.matched)


def test_select_torrents_result_is_deterministically_ordered() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, name="zebra"),
            _torrent(hash="b" * 40, name="Apple"),
            _torrent(hash="c" * 40, name="banana"),
        ]
    )

    selection = select_torrents(client, TorrentFilter())

    assert [t.name for t in selection.matched] == ["Apple", "banana", "zebra"]


def test_select_torrents_no_tracker_filter_never_calls_torrents_trackers() -> (
    None
):
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, category="movies"),
            _torrent(hash="b" * 40, category="series"),
        ]
    )

    select_torrents(
        client, TorrentFilter(categories=("movies",), states=("seeding",))
    )

    assert client.torrents_trackers_calls == 0


def test_select_torrents_narrows_before_tracker_lookups() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, category="movies"),
            _torrent(hash="b" * 40, category="series"),
            _torrent(hash="c" * 40, category="movies"),
        ],
        trackers_by_hash={
            "a"
            * 40: [{"url": "https://tracker.example/announce", "status": "2"}],
            "c"
            * 40: [{"url": "https://tracker.example/announce", "status": "2"}],
        },
    )

    selection = select_torrents(
        client,
        TorrentFilter(categories=("movies",), tracker="tracker.example"),
    )

    assert client.torrents_trackers_calls == 2
    assert set(client.torrents_trackers_call_order) == {"a" * 40, "c" * 40}
    assert {t.hash for t in selection.matched} == {"a" * 40, "c" * 40}


def test_build_torrent_filter_rejects_completed_and_incomplete() -> None:
    with pytest.raises(ValueError, match="not both"):
        build_torrent_filter(completed=True, incomplete=True)


def test_build_torrent_filter_rejects_active_and_inactive() -> None:
    with pytest.raises(ValueError, match="not both"):
        build_torrent_filter(active=True, inactive=True)


def test_build_torrent_filter_rejects_unknown_state() -> None:
    with pytest.raises(ValueError, match="Unknown --state value"):
        build_torrent_filter(states=["banana"])


def test_build_torrent_filter_normalizes_tracker_host() -> None:
    filters = build_torrent_filter(tracker="HTTP://Tracker.Example:80/announce")

    assert filters.tracker == "tracker.example:80"


def test_describe_torrent_filter_is_concise_and_secret_free() -> None:
    filters = build_torrent_filter(
        categories=["movies", "series"],
        states=["stalled"],
        tracker="https://tracker.example/announce/PASSKEY",
    )

    description = describe_torrent_filter(filters)

    assert "category=movies|series" in description
    assert "state=stalled" in description
    assert "tracker=tracker.example" in description
    assert "PASSKEY" not in description


def test_describe_torrent_filter_empty() -> None:
    assert describe_torrent_filter(TorrentFilter()) == "none"


# --- Bulk torrent selection / mutation planning -----------------------------


def test_validate_torrent_selector_rejects_hash_with_filters() -> None:
    with pytest.raises(ValueError, match="Use --hash alone"):
        validate_torrent_selector(
            torrent_hash="abc123",
            select_all=False,
            filters=TorrentFilter(categories=("movies",)),
        )


def test_validate_torrent_selector_rejects_all_with_filters() -> None:
    with pytest.raises(ValueError, match="Use --all alone"):
        validate_torrent_selector(
            torrent_hash=None,
            select_all=True,
            filters=TorrentFilter(categories=("movies",)),
        )


def test_validate_torrent_selector_rejects_no_selector_at_all() -> None:
    with pytest.raises(ValueError, match="Provide --hash, --all"):
        validate_torrent_selector(
            torrent_hash=None, select_all=False, filters=TorrentFilter()
        )


def test_validate_torrent_selector_accepts_a_single_filter() -> None:
    validate_torrent_selector(
        torrent_hash=None,
        select_all=False,
        filters=TorrentFilter(categories=("movies",)),
    )


def test_validate_torrent_selector_accepts_combined_filters() -> None:
    validate_torrent_selector(
        torrent_hash=None,
        select_all=False,
        filters=TorrentFilter(categories=("movies",), states=("stalled",)),
    )


def test_plan_bulk_torrent_action_pauses_matching_category() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, name="Torrent A", category="sonarr"),
            _torrent(hash="b" * 40, name="Torrent B", category="radarr"),
        ]
    )

    plan = plan_bulk_torrent_action(
        client=client,
        action="pause",
        filters=TorrentFilter(categories=("sonarr",)),
    )
    apply_bulk_torrent_action(client, plan)

    assert plan.matched == 1
    assert len(plan.changes) == 1
    assert plan.skipped == ()
    assert client.paused_hashes == [["a" * 40]]


def test_plan_bulk_torrent_action_skips_already_paused_torrents() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(
                hash="a" * 40,
                name="Torrent A",
                category="sonarr",
                state="pausedUP",
            )
        ]
    )

    plan = plan_bulk_torrent_action(
        client=client,
        action="pause",
        filters=TorrentFilter(categories=("sonarr",)),
    )
    apply_bulk_torrent_action(client, plan)

    assert plan.action == "pause"
    assert plan.torrent_hash is None
    assert plan.select_all is False
    assert plan.filters == TorrentFilter(categories=("sonarr",))
    assert plan.scanned == 1
    assert plan.matched == 1
    assert plan.changes == ()
    assert len(plan.skipped) == 1
    assert plan.skipped[0].reason == "already_stopped"
    assert client.paused_hashes == []


def test_plan_bulk_torrent_action_resumes_paused_torrents() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(
                hash="a" * 40,
                name="Torrent A",
                category="sonarr",
                state="pausedUP",
            ),
            _torrent(
                hash="b" * 40,
                name="Torrent B",
                category="sonarr",
                state="uploading",
            ),
        ]
    )

    plan = plan_bulk_torrent_action(
        client=client,
        action="resume",
        filters=TorrentFilter(categories=("sonarr",)),
    )
    apply_bulk_torrent_action(client, plan)

    assert len(plan.changes) == 1
    assert len(plan.skipped) == 1
    assert client.started_hashes == [["a" * 40]]


def test_plan_bulk_torrent_action_resumes_stopped_up_torrents() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(
                hash="a" * 40,
                name="Torrent A",
                category="sonarr",
                state="stoppedUP",
                progress=1.0,
            )
        ]
    )

    plan = plan_bulk_torrent_action(
        client=client, action="resume", select_all=True
    )
    apply_bulk_torrent_action(client, plan)

    assert len(plan.changes) == 1
    assert client.started_hashes == [["a" * 40]]


def test_bulk_resume_calls_torrents_start_without_torrents_resume() -> None:
    """Constat P-4: `torrents_start` is called directly, with no fallback
    to `torrents_resume`. A client exposing only `torrents_start` (the
    real qbittorrent-api client's actual shape -- `torrents_resume` and
    `torrents_start` are the same bound method, see
    `tests/test_qbit_library_http_boundary.py`) must still work."""

    class _StartOnlyClient(FakeQbitClient):
        def __getattribute__(self, name: str) -> Any:
            if name == "torrents_resume":
                raise AttributeError(name)
            return super().__getattribute__(name)

    client = _StartOnlyClient(
        torrents=[
            _torrent(
                hash="a" * 40,
                name="Torrent A",
                category="sonarr",
                state="stoppedUP",
                progress=1.0,
            )
        ]
    )

    plan = plan_bulk_torrent_action(
        client=client, action="resume", select_all=True
    )
    apply_bulk_torrent_action(client, plan)

    assert client.started_hashes == [["a" * 40]]


def test_plan_bulk_torrent_action_starts_completed_torrents() -> None:
    """Now available as a general filter rather than a start-only flag."""
    client = FakeQbitClient(
        torrents=[
            _torrent(
                hash="a" * 40,
                name="Completed stopped",
                state="stoppedUP",
                progress=1.0,
            ),
            _torrent(
                hash="b" * 40,
                name="Completed running",
                state="stalledUP",
                progress=1.0,
            ),
            _torrent(
                hash="c" * 40,
                name="Incomplete stopped",
                state="stoppedDL",
                progress=0.5,
            ),
        ]
    )

    plan = plan_bulk_torrent_action(
        client=client, action="start", filters=TorrentFilter(completed=True)
    )
    apply_bulk_torrent_action(client, plan)

    assert plan.filters == TorrentFilter(completed=True)
    assert plan.matched == 2
    assert len(plan.changes) == 1
    assert len(plan.skipped) == 1
    assert client.started_hashes == [["a" * 40]]


def test_plan_bulk_torrent_action_reannounces_by_tracker() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, name="Torrent A"),
            _torrent(hash="b" * 40, name="Torrent B"),
        ],
        trackers_by_hash={
            "a"
            * 40: [
                {"url": "https://tracker.example/announce", "status": "2"},
            ],
            "b"
            * 40: [
                {"url": "https://other.example/announce", "status": "2"},
            ],
        },
    )

    plan = plan_bulk_torrent_action(
        client=client,
        action="reannounce",
        filters=TorrentFilter(tracker="tracker.example"),
    )
    apply_bulk_torrent_action(client, plan)

    assert plan.matched == 1
    assert len(plan.changes) == 1
    assert client.reannounced_hashes == [["a" * 40]]


def test_plan_bulk_torrent_action_combines_filters_with_and() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(
                hash="a" * 40,
                name="A",
                category="sonarr",
                state="stalledUP",
            ),
            _torrent(
                hash="b" * 40,
                name="B",
                category="sonarr",
                state="uploading",
            ),
            _torrent(
                hash="c" * 40,
                name="C",
                category="radarr",
                state="stalledUP",
            ),
        ]
    )

    plan = plan_bulk_torrent_action(
        client=client,
        action="reannounce",
        filters=TorrentFilter(categories=("sonarr",), states=("stalled",)),
    )

    assert plan.matched == 1
    assert plan.changes[0].hash == "a" * 40


def test_plan_bulk_torrent_action_selects_all_torrents() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, name="Torrent A", category="sonarr"),
            _torrent(hash="b" * 40, name="Torrent B", category="radarr"),
        ]
    )

    plan = plan_bulk_torrent_action(
        client=client, action="pause", select_all=True
    )
    apply_bulk_torrent_action(client, plan)

    assert plan.select_all is True
    assert plan.torrent_hash is None
    assert plan.matched == 2
    assert len(plan.changes) == 2
    assert client.paused_hashes == [["a" * 40, "b" * 40]]


def test_plan_bulk_torrent_action_pauses_by_hash_prefix() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="abc123def456" + "0" * 28, name="Torrent A"),
            _torrent(hash="zz00112233ff" + "0" * 28, name="Torrent B"),
        ]
    )

    plan = plan_bulk_torrent_action(
        client=client, action="pause", torrent_hash="abc123"
    )
    apply_bulk_torrent_action(client, plan)

    assert plan.torrent_hash == "abc123def456" + "0" * 28
    assert plan.matched == 1
    assert len(plan.changes) == 1
    assert client.paused_hashes == [["abc123def456" + "0" * 28]]


def test_plan_bulk_torrent_action_with_ambiguous_hash_raises() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="abc123def456" + "0" * 28, name="Torrent A"),
            _torrent(hash="abc987fed654" + "0" * 28, name="Torrent B"),
        ]
    )

    with pytest.raises(AmbiguousTorrentHashError):
        plan_bulk_torrent_action(
            client=client, action="pause", torrent_hash="abc"
        )

    assert client.paused_hashes == []


def test_plan_bulk_torrent_action_with_unknown_hash_matches_nothing() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="abc123def456" + "0" * 28, name="Torrent A"),
        ]
    )

    plan = plan_bulk_torrent_action(
        client=client, action="pause", torrent_hash="doesnotexist"
    )
    apply_bulk_torrent_action(client, plan)

    assert plan.matched == 0
    assert plan.changes == ()
    assert client.paused_hashes == []


@pytest.mark.parametrize(
    "action,state",
    [
        ("pause", "pausedUP"),
        ("resume", "uploading"),
        ("start", "uploading"),
    ],
)
def test_plan_bulk_torrent_action_matches_but_no_changes(
    action: TorrentBulkAction, state: str
) -> None:
    """`matched > 0` with `changes == ()` is the NO_CHANGES case: the selector
    found the torrent, but the requested state was already true, so there is
    nothing to apply.
    """
    client = FakeQbitClient(
        torrents=[_torrent(hash="a" * 40, name="Torrent A", state=state)]
    )

    plan = plan_bulk_torrent_action(
        client=client, action=action, select_all=True
    )
    apply_bulk_torrent_action(client, plan)

    assert plan.matched == 1
    assert plan.changes == ()
    assert len(plan.skipped) == 1
    assert client.paused_hashes == []
    assert client.resumed_hashes == []
    assert client.started_hashes == []


def test_apply_bulk_torrent_action_reuses_the_planned_target_set() -> None:
    client = FakeQbitClient(
        torrents=[_torrent(hash="abc123def456" + "0" * 28, name="Torrent A")]
    )

    plan = plan_bulk_torrent_action(
        client=client, action="pause", torrent_hash="abc123"
    )
    assert plan.matched == 1
    assert plan.changes[0].hash == "abc123def456" + "0" * 28

    # A torrent added after planning must not affect what gets applied.
    client.torrents.append(
        _torrent(hash="zz00112233ff" + "0" * 28, name="Torrent B")
    )

    apply_bulk_torrent_action(client, plan)

    assert client.paused_hashes == [["abc123def456" + "0" * 28]]


def test_hash_selector_rejects_combined_filters() -> None:
    client = FakeQbitClient(
        torrents=[_torrent(hash="a" * 40, name="Torrent A", category="sonarr")]
    )

    with pytest.raises(ValueError, match="Use --hash alone"):
        plan_bulk_torrent_action(
            client=client,
            action="pause",
            torrent_hash="a" * 40,
            filters=TorrentFilter(categories=("sonarr",)),
        )


def test_select_all_rejects_combined_filters() -> None:
    client = FakeQbitClient(
        torrents=[_torrent(hash="a" * 40, name="Torrent A", category="sonarr")]
    )

    with pytest.raises(ValueError, match="Use --all alone"):
        plan_bulk_torrent_action(
            client=client,
            action="pause",
            filters=TorrentFilter(categories=("sonarr",)),
            select_all=True,
        )


def test_no_selector_at_all_is_rejected_before_mutation_planning() -> None:
    client = FakeQbitClient(
        torrents=[_torrent(hash="a" * 40, name="Torrent A")]
    )

    with pytest.raises(ValueError, match="Provide --hash, --all"):
        plan_bulk_torrent_action(client=client, action="pause")

    assert client.paused_hashes == []


# --- Unrelated read-only helpers ---------------------------------------


def test_inspect_torrent_returns_detailed_report() -> None:
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
                "tracker": "tracker.example",
                "health": "healthy",
                "enabled": True,
                "scheme": "https",
                "path_shape": "/<secret>",
                "query_keys": [],
                "message": None,
            },
            {
                "tracker": "DHT",
                "health": "disabled",
                "enabled": False,
                "scheme": None,
                "path_shape": None,
                "query_keys": [],
                "message": None,
            },
        ],
        "active_tracker_count": 1,
    }


def test_inspect_torrent_returns_none_when_hash_is_missing() -> None:
    client = FakeQbitClient(
        torrents=[{"hash": "hash-a", "name": "Torrent A"}],
        trackers_by_hash={"hash-a": []},
    )

    assert inspect_torrent(client, "missing-hash") is None


def test_inspect_torrent_resolves_a_unique_hash_prefix() -> None:
    client = FakeQbitClient(
        torrents=[{"hash": "abc123def456", "name": "Torrent A"}],
        trackers_by_hash={"abc123def456": []},
    )

    report = inspect_torrent(client, "abc123")

    assert report is not None
    assert report["hash"] == "abc123def456"


def test_inspect_torrent_raises_for_an_ambiguous_prefix() -> None:
    client = FakeQbitClient(
        torrents=[
            {"hash": "abc123def456", "name": "Torrent A"},
            {"hash": "abc987fed654", "name": "Torrent B"},
        ],
        trackers_by_hash={"abc123def456": [], "abc987fed654": []},
    )

    with pytest.raises(AmbiguousTorrentHashError):
        inspect_torrent(client, "abc")


def test_search_torrents_by_name_ranks_best_matches_first() -> None:
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
        trackers_by_hash={"hash-a": [], "hash-b": [], "hash-c": []},
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

    report = search_torrents_by_name(client, "L.amour.est", limit=2)

    assert report["summary"] == {"matched": 2, "limit": 2}
    assert [match["hash"] for match in report["matches"]] == [
        "hash-b",
        "hash-a",
    ]
    assert report["matches"][0]["match_score"] == 0.95

    limited_report = search_torrents_by_name(client, "L.amour.est", limit=1)
    assert limited_report["summary"] == {"matched": 1, "limit": 1}
    assert limited_report["matches"][0]["hash"] == "hash-b"


def test_search_torrents_by_name_returns_empty_when_nothing_matches() -> None:
    client = FakeQbitClient(
        torrents=[{"hash": "hash-a", "name": "Torrent A"}],
        trackers_by_hash={"hash-a": []},
    )

    report = search_torrents_by_name(client, "missing-name")

    assert report["summary"]["matched"] == 0
    assert report["matches"] == []


def test_list_category_usage_counts_torrents_per_category() -> None:
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


def test_list_torrents_with_trackers_returns_tracker_details() -> None:
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
