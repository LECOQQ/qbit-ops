"""Test torrent listing, filtering, and bulk-selection helpers."""

from typing import Any

import pytest

from qbit_core.features.torrents import (
    HashActionStatus,
    TorrentBulkAction,
    apply_bulk_torrent_action,
    build_torrent_filter,
    inspect_torrent,
    list_category_usage,
    list_torrents_with_trackers,
    pause_torrents_by_hash,
    plan_bulk_torrent_action,
    resume_torrents_by_hash,
    search_torrents_by_name,
    select_torrents,
    validate_torrent_selector,
)
from qbit_core.features.tracker_status import collect_tracker_status
from qbit_core.shared.inspection import select_and_inspect
from qbit_core.shared.selection import (
    AmbiguousTorrentHashError,
    InvalidTorrentSelectorError,
    SelectionRequest,
    TorrentFilter,
    describe_torrent_filter,
    torrent_filter_to_dict,
)


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
        self.deleted_hashes: list[tuple[str | list[str], bool]] = []
        self.torrents_trackers_calls = 0
        self.torrents_trackers_call_order: list[str] = []
        self.torrents_info_calls = 0

    def torrents_info(self) -> list[dict[str, Any]]:
        """Return fake torrents."""
        self.torrents_info_calls += 1
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

    def torrents_delete(
        self, delete_files: bool, torrent_hashes: str | list[str]
    ) -> None:
        """Record a fake torrent deletion."""
        self.deleted_hashes.append((torrent_hashes, delete_files))


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

    selection, tracker_counts = select_torrents(client, TorrentFilter())

    assert selection.scanned == 2
    assert len(selection.matched) == 2
    assert selection.request.filters == TorrentFilter()
    assert tracker_counts is None
    assert client.torrents_trackers_calls == 0


def test_select_torrents_repeated_categories_use_or() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, category="movies"),
            _torrent(hash="b" * 40, category="series"),
            _torrent(hash="c" * 40, category="music"),
        ]
    )

    selection, _ = select_torrents(
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

    selection, _ = select_torrents(
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

    selection, _ = select_torrents(
        client, TorrentFilter(categories=("uncategorized",))
    )

    assert [t.hash for t in selection.matched] == ["a" * 40]
    assert selection.matched[0].category == ""


def test_select_torrents_display_label_token_also_matches_uncategorized() -> (
    None
):
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, category=""),
            _torrent(hash="b" * 40, category="movies"),
        ]
    )

    selection, _ = select_torrents(
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

    completed, _ = select_torrents(client, TorrentFilter(completed=True))
    incomplete, _ = select_torrents(client, TorrentFilter(completed=False))

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

    active, _ = select_torrents(client, TorrentFilter(active=True))
    inactive, _ = select_torrents(client, TorrentFilter(active=False))

    assert [t.hash for t in active.matched] == ["a" * 40]
    assert {t.hash for t in inactive.matched} == {"b" * 40, "c" * 40}


def test_select_torrents_stalled() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, state="stalledDL"),
            _torrent(hash="b" * 40, state="downloading"),
        ]
    )

    selection, _ = select_torrents(client, TorrentFilter(stalled=True))

    assert [t.hash for t in selection.matched] == ["a" * 40]


def test_select_torrents_errored() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, state="error"),
            _torrent(hash="b" * 40, state="uploading"),
        ]
    )

    selection, _ = select_torrents(client, TorrentFilter(errored=True))

    assert [t.hash for t in selection.matched] == ["a" * 40]


def test_select_torrents_state_normalization() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, state="pausedUP"),
            _torrent(hash="b" * 40, state="stoppedUP"),
            _torrent(hash="c" * 40, state="downloading"),
        ]
    )

    selection, _ = select_torrents(client, TorrentFilter(states=("seeding",)))

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

    unfiltered, _ = select_torrents(client, TorrentFilter())
    assert {t.hash for t in unfiltered.matched} == {"a" * 40, "b" * 40}

    filtered, _ = select_torrents(client, TorrentFilter(states=("unknown",)))
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

    selection, tracker_counts = select_torrents(
        client, TorrentFilter(trackers=("tracker.example:6969",))
    )

    assert [t.hash for t in selection.matched] == ["a" * 40]
    assert tracker_counts == {"a" * 40: 1}


def test_select_torrents_tracker_filter_never_exposes_secrets() -> None:
    filters = build_torrent_filter(
        trackers=("https://tracker.example/announce/SUPER-SECRET-PASSKEY",)
    )

    assert "SUPER-SECRET-PASSKEY" not in "".join(filters.trackers)
    assert filters.trackers == ("tracker.example",)

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
    selection, _ = select_torrents(client, filters)

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

    selection, _ = select_torrents(client, TorrentFilter())

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

    selection, _ = select_torrents(
        client,
        TorrentFilter(categories=("movies",), trackers=("tracker.example",)),
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
    filters = build_torrent_filter(
        trackers=("HTTP://Tracker.Example:80/announce",)
    )

    assert filters.trackers == ("tracker.example:80",)


def test_describe_torrent_filter_is_concise_and_secret_free() -> None:
    filters = build_torrent_filter(
        categories=["movies", "series"],
        states=["stalled"],
        trackers=("https://tracker.example/announce/PASSKEY",),
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
        filters=TorrentFilter(trackers=("tracker.example",)),
    )
    apply_bulk_torrent_action(client, plan)

    assert plan.matched == 1
    assert len(plan.changes) == 1
    assert client.reannounced_hashes == [["a" * 40]]


def test_plan_bulk_torrent_action_delete_never_skips_any_state() -> None:
    """Unlike pause/resume/start, delete has no no-op state: paused,
    running, or errored torrents are all always a change."""
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, name="A", state="pausedUP"),
            _torrent(hash="b" * 40, name="B", state="uploading"),
            _torrent(hash="c" * 40, name="C", state="error"),
        ]
    )

    plan = plan_bulk_torrent_action(
        client=client, action="delete", select_all=True
    )

    assert plan.matched == 3
    assert len(plan.changes) == 3
    assert plan.skipped == ()


def test_apply_bulk_torrent_action_delete_defaults_to_keeping_data() -> None:
    client = FakeQbitClient(torrents=[_torrent(hash="a" * 40, name="A")])

    plan = plan_bulk_torrent_action(
        client=client, action="delete", select_all=True
    )
    apply_bulk_torrent_action(client, plan)

    assert plan.delete_files is False
    assert client.deleted_hashes == [(["a" * 40], False)]


def test_apply_bulk_torrent_action_delete_with_data() -> None:
    client = FakeQbitClient(torrents=[_torrent(hash="a" * 40, name="A")])

    plan = plan_bulk_torrent_action(
        client=client, action="delete", select_all=True, delete_files=True
    )
    apply_bulk_torrent_action(client, plan)

    assert plan.delete_files is True
    assert client.deleted_hashes == [(["a" * 40], True)]


def test_apply_bulk_torrent_action_delete_with_no_matches_never_calls_api() -> (
    None
):
    client = FakeQbitClient(torrents=[])

    plan = plan_bulk_torrent_action(
        client=client, action="delete", select_all=True
    )
    apply_bulk_torrent_action(client, plan)

    assert client.deleted_hashes == []


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


# --- pause_torrents_by_hash / resume_torrents_by_hash: exact-hash entry
# points for non-CLI callers (e.g. Waitarr).


def test_pause_active_torrent_is_reported_changed() -> None:
    torrent = _torrent(hash="a" * 40, name="Active", state="uploading")
    client = FakeQbitClient(torrents=[torrent])

    result = pause_torrents_by_hash(client, ["a" * 40])

    outcome = result.outcomes[0]
    assert outcome.status is HashActionStatus.CHANGED
    assert outcome.previous_state == "uploading"
    assert result.changed_hashes == ("a" * 40,)
    assert result.unchanged_hashes == ()
    assert client.paused_hashes == [["a" * 40]]


def test_pause_already_stopped_torrent_is_unchanged_with_no_mutator_call() -> (
    None
):
    torrent = _torrent(hash="a" * 40, name="Stopped", state="pausedUP")
    client = FakeQbitClient(torrents=[torrent])

    result = pause_torrents_by_hash(client, ["a" * 40])

    outcome = result.outcomes[0]
    assert outcome.status is HashActionStatus.UNCHANGED
    assert outcome.previous_state == "pausedUP"
    assert outcome.reason == "already_stopped"
    assert "a" * 40 not in result.changed_hashes
    assert result.unchanged_hashes == ("a" * 40,)
    assert client.paused_hashes == []


def test_resume_stopped_torrent_is_reported_changed() -> None:
    torrent = _torrent(hash="a" * 40, name="Stopped", state="pausedDL")
    client = FakeQbitClient(torrents=[torrent])

    result = resume_torrents_by_hash(client, ["a" * 40])

    outcome = result.outcomes[0]
    assert outcome.status is HashActionStatus.CHANGED
    assert result.changed_hashes == ("a" * 40,)
    assert client.started_hashes == [["a" * 40]]


def test_resume_active_torrent_is_unchanged() -> None:
    torrent = _torrent(hash="a" * 40, name="Active", state="downloading")
    client = FakeQbitClient(torrents=[torrent])

    result = resume_torrents_by_hash(client, ["a" * 40])

    assert result.outcomes[0].status is HashActionStatus.UNCHANGED
    assert result.changed_hashes == ()
    assert client.started_hashes == []


def test_unknown_hash_is_reported_not_found_and_excluded_from_changed() -> None:
    client = FakeQbitClient(torrents=[])

    result = pause_torrents_by_hash(client, ["a" * 40])

    outcome = result.outcomes[0]
    assert outcome.status is HashActionStatus.NOT_FOUND
    assert result.not_found_hashes == ("a" * 40,)
    assert "a" * 40 not in result.changed_hashes
    assert client.paused_hashes == []


def test_failed_api_call_is_reported_failed_with_explicit_error() -> None:
    torrent = _torrent(hash="a" * 40, name="Torrent A", state="uploading")
    client = FakeQbitClient(torrents=[torrent])

    def _raise(*_: object, **__: object) -> None:
        raise RuntimeError("qBittorrent unreachable")

    client.torrents_pause = _raise  # type: ignore[method-assign]

    result = pause_torrents_by_hash(client, ["a" * 40])

    outcome = result.outcomes[0]
    assert outcome.status is HashActionStatus.FAILED
    assert outcome.error is not None
    assert "qBittorrent unreachable" in outcome.error
    assert result.changed_hashes == ()
    assert result.failed_hashes == ("a" * 40,)


def test_mixed_batch_success_populates_changed_unchanged_and_not_found() -> (
    None
):
    changed = _torrent(hash="a" * 40, name="Active", state="uploading")
    unchanged = _torrent(hash="b" * 40, name="Stopped", state="pausedUP")
    client = FakeQbitClient(torrents=[changed, unchanged])

    result = pause_torrents_by_hash(client, ["a" * 40, "b" * 40, "c" * 40])

    assert result.changed_hashes == ("a" * 40,)
    assert result.unchanged_hashes == ("b" * 40,)
    assert result.not_found_hashes == ("c" * 40,)
    assert result.failed_hashes == ()
    assert set(result.successful_hashes) == {"a" * 40, "b" * 40}


def test_mixed_batch_failure_marks_every_attempted_change_as_failed() -> None:
    """qBittorrent's bulk endpoints accept multiple hashes in one HTTP
    request and report success/failure for that request as a whole --
    never per torrent. So when two torrents both need pausing and the
    single grouped call fails, both are `FAILED` together; a torrent
    that needed no call (`unchanged`) or was never found is unaffected
    by that failure."""
    changed_a = _torrent(hash="a" * 40, name="Active A", state="uploading")
    changed_c = _torrent(hash="c" * 40, name="Active C", state="uploading")
    unchanged = _torrent(hash="b" * 40, name="Stopped", state="pausedUP")
    client = FakeQbitClient(torrents=[changed_a, unchanged, changed_c])

    def _raise(*_: object, **__: object) -> None:
        raise RuntimeError("boom")

    client.torrents_pause = _raise  # type: ignore[method-assign]

    result = pause_torrents_by_hash(
        client, ["a" * 40, "b" * 40, "c" * 40, "d" * 40]
    )

    assert set(result.failed_hashes) == {"a" * 40, "c" * 40}
    assert result.unchanged_hashes == ("b" * 40,)
    assert result.not_found_hashes == ("d" * 40,)
    assert result.changed_hashes == ()


def test_pause_and_resume_by_hash_do_not_accept_an_action_parameter() -> None:
    """The public wrappers statically fix the action -- there is no
    parameter through which a caller could pass an unsupported or
    misspelled action string."""
    import inspect

    assert list(inspect.signature(pause_torrents_by_hash).parameters) == [
        "client",
        "hashes",
    ]
    assert list(inspect.signature(resume_torrents_by_hash).parameters) == [
        "client",
        "hashes",
    ]


# --- Audit: empty/duplicate/invalid/large hash collections, determinism ----


def test_empty_hash_collection_makes_no_api_call() -> None:
    client = FakeQbitClient(torrents=[_torrent()])

    result = pause_torrents_by_hash(client, [])

    assert result.outcomes == ()
    assert client.torrents_info_calls == 0
    assert client.paused_hashes == []


def test_case_insensitive_duplicate_hashes_collapse_to_one_outcome() -> None:
    torrent = _torrent(hash="a" * 40, name="Torrent A", state="uploading")
    client = FakeQbitClient(torrents=[torrent])

    result = pause_torrents_by_hash(client, ["A" * 40, "a" * 40, "A" * 40])

    assert len(result.outcomes) == 1
    assert result.outcomes[0].torrent_hash == "a" * 40
    assert result.changed_hashes == ("a" * 40,)
    # A single bulk call, not one per requested (duplicate) spelling.
    assert client.paused_hashes == [["a" * 40]]


def test_blank_hash_is_rejected_before_any_api_call() -> None:
    client = FakeQbitClient(torrents=[_torrent()])

    with pytest.raises(InvalidTorrentSelectorError):
        pause_torrents_by_hash(client, ["   "])

    assert client.torrents_info_calls == 0


def test_unmatched_well_formed_hash_is_not_found_not_an_error() -> None:
    client = FakeQbitClient(torrents=[_torrent(hash="a" * 40)])

    result = pause_torrents_by_hash(client, ["b" * 40])

    assert result.not_found_hashes == ("b" * 40,)


def test_large_hash_batch_uses_one_torrents_info_and_one_mutator_call() -> None:
    torrents = [
        _torrent(hash=f"{i:040x}", state="uploading") for i in range(500)
    ]
    client = FakeQbitClient(torrents=torrents)
    requested = [f"{i:040x}" for i in range(1_000)]  # half unmatched

    result = pause_torrents_by_hash(client, requested)

    assert client.torrents_info_calls == 1
    assert len(client.paused_hashes) == 1
    assert len(result.changed_hashes) == 500
    assert len(result.not_found_hashes) == 500
    assert len(result.outcomes) == 1_000


def test_outcomes_are_sorted_by_hash_regardless_of_request_order() -> None:
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="c" * 40, state="uploading"),
            _torrent(hash="a" * 40, state="uploading"),
            _torrent(hash="b" * 40, state="uploading"),
        ]
    )

    result = pause_torrents_by_hash(
        client, ["c" * 40, "a" * 40, "d" * 40, "b" * 40]
    )

    assert [outcome.torrent_hash for outcome in result.outcomes] == [
        "a" * 40,
        "b" * 40,
        "c" * 40,
        "d" * 40,
    ]


def test_result_is_identical_across_repeated_calls_with_the_same_input() -> (
    None
):
    client = FakeQbitClient(
        torrents=[
            _torrent(hash="a" * 40, state="uploading"),
            _torrent(hash="b" * 40, state="pausedUP"),
        ]
    )
    hashes = ["b" * 40, "a" * 40, "c" * 40]

    first = pause_torrents_by_hash(client, hashes)
    second = pause_torrents_by_hash(client, hashes)

    assert first.outcomes == second.outcomes


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
        ],
        "peer_discovery": [
            {"mechanism": "DHT", "health": "disabled", "enabled": False}
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
            "tags": [],
            "added_on": 1710000000,
            "trackers": [
                {
                    "url": "https://tracker.example/announce",
                    "status": "2",
                    "disabled": False,
                },
            ],
            "peer_discovery": [
                {"mechanism": "DHT", "health": "disabled", "enabled": False}
            ],
            "active_tracker_count": 1,
        }
    ]


# --- Pseudo-trackers count nowhere, and are reported separately -------------
#
# qBittorrent reports DHT/PeX/LSD as `** [X] **` markers, and on 5.2.3
# every one of them is *working* (status 2) -- so "skip the disabled
# ones" was never enough. They are peer-discovery mechanisms, not
# announce endpoints: they must not reach a tracker count, a tracker
# list, or a selection, on any command. `peer_discovery` is where they
# belong.

_PEER_DISCOVERY_HASH = "e" * 40
_REAL_ANNOUNCE = "https://tracker.example/announce"


def _client_with_peer_discovery() -> FakeQbitClient:
    """One torrent announcing to a single real tracker, plus the three
    pseudo-trackers qBittorrent lists next to it."""
    return FakeQbitClient(
        torrents=[
            {
                "hash": _PEER_DISCOVERY_HASH,
                "name": "Torrent A",
                "state": "uploading",
                "progress": 1,
                "trackers_count": 1,
            }
        ],
        trackers_by_hash={
            _PEER_DISCOVERY_HASH: [
                {"url": "** [DHT] **", "status": 2},
                {"url": "** [PeX] **", "status": 2},
                {"url": "** [LSD] **", "status": 2},
                {"url": _REAL_ANNOUNCE, "status": 2},
            ],
        },
    )


def test_every_tracker_counter_agrees_on_one_tracker() -> None:
    """The bug this pins down was a *disagreement*: `trackers_count` said
    1 while four other counters said 4 for the same torrent. They now
    share one definition, `get_active_tracker_urls`."""
    listed = list_torrents_with_trackers(_client_with_peer_discovery())[0]
    inspected = inspect_torrent(
        _client_with_peer_discovery(), _PEER_DISCOVERY_HASH
    )
    assert inspected is not None
    inspection = select_and_inspect(
        _client_with_peer_discovery(), SelectionRequest(select_all=True)
    )
    status = collect_tracker_status(
        _client_with_peer_discovery(), build_torrent_filter()
    )

    assert listed["active_tracker_count"] == 1
    assert inspected["active_tracker_count"] == 1
    assert inspection.torrents[0].tracker_count == 1
    assert [aggregate.endpoint_count for aggregate in status.trackers] == [1]
    assert [aggregate.identity for aggregate in status.trackers] == [
        "tracker.example"
    ]


def test_pseudo_trackers_are_reported_under_peer_discovery() -> None:
    """Excluded from the counts, not hidden: an operator still needs to
    read "DHT is off" somewhere."""
    listed = list_torrents_with_trackers(_client_with_peer_discovery())[0]
    inspected = inspect_torrent(
        _client_with_peer_discovery(), _PEER_DISCOVERY_HASH
    )
    assert inspected is not None

    expected = [
        {"mechanism": "DHT", "health": "healthy", "enabled": True},
        {"mechanism": "PeX", "health": "healthy", "enabled": True},
        {"mechanism": "LSD", "health": "healthy", "enabled": True},
    ]
    assert listed["peer_discovery"] == expected
    assert inspected["peer_discovery"] == expected
    assert [tracker["url"] for tracker in listed["trackers"]] == [
        _REAL_ANNOUNCE
    ]
    assert [tracker["tracker"] for tracker in inspected["trackers"]] == [
        "tracker.example"
    ]


def test_a_disabled_mechanism_stays_visible_in_peer_discovery() -> None:
    client = FakeQbitClient(
        torrents=[{"hash": _PEER_DISCOVERY_HASH, "name": "Torrent A"}],
        trackers_by_hash={
            _PEER_DISCOVERY_HASH: [
                {"url": "** [DHT] **", "status": 0},
                {"url": _REAL_ANNOUNCE, "status": 2},
            ],
        },
    )

    listed = list_torrents_with_trackers(client)[0]

    assert listed["peer_discovery"] == [
        {"mechanism": "DHT", "health": "disabled", "enabled": False}
    ]
    assert listed["active_tracker_count"] == 1


def test_a_torrent_with_only_pseudo_trackers_has_no_tracker() -> None:
    """`--no-tracker` must find it: three working pseudo-trackers are
    still zero trackers."""
    client = FakeQbitClient(
        torrents=[
            {
                "hash": _PEER_DISCOVERY_HASH,
                "name": "Torrent A",
                "trackers_count": 0,
            }
        ],
        trackers_by_hash={
            _PEER_DISCOVERY_HASH: [
                {"url": "** [DHT] **", "status": 2},
                {"url": "** [PeX] **", "status": 2},
            ],
        },
    )

    listed = list_torrents_with_trackers(client)[0]
    inspection = select_and_inspect(client, SelectionRequest(select_all=True))
    selection, _ = select_torrents(
        client, build_torrent_filter(has_trackers=False)
    )

    assert listed["trackers"] == []
    assert listed["active_tracker_count"] == 0
    assert inspection.torrents[0].tracker_count == 0
    assert [torrent.name for torrent in selection.matched] == ["Torrent A"]
