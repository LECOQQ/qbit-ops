"""Non-Textual tests for the TUI's pure state/refresh controller.

These exercise `qbit_ops.tui.state.TuiController` directly against
`tests.support.FakeQbitClient` -- no terminal, no Textual `App`, no
event loop. Interface-level (Pilot) tests live in `tests/test_tui_app.py`.
"""

from __future__ import annotations

from typing import Any

import pytest

from qbit_core.errors import (
    ErrorCategory,
    QbitAuthenticationError,
    QbitConnectionError,
)
from qbit_core.features.torrents import build_torrent_filter
from qbit_core.shared.execution import MutationStatus
from qbit_ops.config import ConfigError
from qbit_ops.tui.state import ConnectionState, TuiController, Workspace
from tests.support import FakeQbitClient, make_torrent


class FlakyQbitClient(FakeQbitClient):
    """A `FakeQbitClient` whose `torrents_info()` can be made to fail once.

    Used to simulate a temporary connection drop *during* the TUI's
    periodic refresh, then a recovery on the next tick.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.next_torrents_info_error: Exception | None = None

    def torrents_info(self) -> list[dict[str, Any]]:
        if self.next_torrents_info_error is not None:
            error = self.next_torrents_info_error
            self.next_torrents_info_error = None
            raise error
        return super().torrents_info()


def _controller(client: FakeQbitClient) -> TuiController:
    return TuiController(client_factory=lambda: client, host="http://x")


def test_refresh_performs_exactly_one_torrents_info_call() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    controller = _controller(client)

    controller.refresh()

    assert client.torrents_info_calls == 1


def test_refresh_uses_the_documented_five_call_budget() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(), make_torrent(hash="b" * 40)]
    )
    controller = _controller(client)

    controller.refresh()

    assert client.app_version_calls == 1
    assert client.app_web_api_version_calls == 1
    assert client.transfer_info_calls == 1
    assert client.torrents_info_calls == 1
    assert client.sync_maindata_calls == 1
    assert client.torrents_trackers_calls == 0


def test_refresh_populates_status_and_torrent_snapshot_from_one_fetch() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(name="Alpha"),
            make_torrent(hash="b" * 40, name="Beta"),
        ]
    )
    controller = _controller(client)

    controller.refresh()

    assert controller.state.status is not None
    assert controller.state.status.counts.total == 2
    assert controller.state.torrent_snapshot is not None
    assert len(controller.state.torrent_snapshot.matched) == 2
    assert controller.state.connection is ConnectionState.CONNECTED
    assert controller.state.stale is False


def test_filter_change_performs_zero_api_calls() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(name="Alpha", category="sonarr"),
            make_torrent(hash="b" * 40, name="Beta", category="radarr"),
        ]
    )
    controller = _controller(client)
    controller.refresh()
    calls_before = len(client.calls)

    controller.set_filters(build_torrent_filter(categories=["sonarr"]))

    assert len(client.calls) == calls_before
    assert controller.state.visible is not None
    assert [t.name for t in controller.state.visible.matched] == ["Alpha"]


def test_filter_handles_uncategorized_token_against_cached_raw_items() -> None:
    """Re-filtering resolves the `uncategorized` token against raw
    categories. Once a regression risk (matching ran against
    display-formatted values); now structurally impossible, since
    selections carry raw `TorrentSnapshot.category`. Kept as the
    behavioural guarantee, independent of how it is achieved.
    """
    client = FakeQbitClient(torrents=[make_torrent(name="Alpha", category="")])
    controller = _controller(client)
    controller.refresh()

    controller.set_filters(build_torrent_filter(categories=["uncategorized"]))

    assert controller.state.visible is not None
    assert [t.name for t in controller.state.visible.matched] == ["Alpha"]


def test_search_change_performs_zero_api_calls() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(name="Debian ISO"),
            make_torrent(hash="b" * 40, name="Ubuntu ISO"),
        ]
    )
    controller = _controller(client)
    controller.refresh()
    calls_before = len(client.calls)

    controller.set_search("debian")

    assert len(client.calls) == calls_before
    assert controller.state.visible is not None
    assert [t.name for t in controller.state.visible.matched] == ["Debian ISO"]


def test_focus_change_performs_exactly_one_tracker_call() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={
            torrent_hash: [
                {"url": "https://tracker.example/announce/SECRET", "status": 2}
            ]
        },
    )
    controller = _controller(client)
    controller.refresh()

    controller.set_focus(torrent_hash)

    assert client.torrents_trackers_calls == 1
    assert controller.state.focused_tracker_details is not None
    assert controller.state.focused_details_fetched_at is not None


def test_unchanged_focus_performs_no_additional_tracker_call() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash)],
        trackers_by_hash={torrent_hash: []},
    )
    controller = _controller(client)
    controller.refresh()
    controller.set_focus(torrent_hash)
    assert client.torrents_trackers_calls == 1

    controller.set_focus(torrent_hash)

    assert client.torrents_trackers_calls == 1


def test_periodic_refresh_performs_zero_tracker_calls_even_with_focus() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash)],
        trackers_by_hash={torrent_hash: []},
    )
    controller = _controller(client)
    controller.refresh()
    controller.set_focus(torrent_hash)
    assert client.torrents_trackers_calls == 1

    controller.refresh()

    assert client.torrents_trackers_calls == 1


def test_failed_refresh_preserves_last_good_data_and_marks_stale() -> None:
    client = FlakyQbitClient(torrents=[make_torrent(name="Alpha")])
    controller = _controller(client)
    controller.refresh()
    good_status = controller.state.status
    good_snapshot = controller.state.torrent_snapshot

    client.next_torrents_info_error = QbitConnectionError("connection lost")
    controller.refresh()

    assert controller.state.status is good_status
    assert controller.state.torrent_snapshot is good_snapshot
    assert controller.state.stale is True
    assert controller.state.connection is ConnectionState.RECONNECTING
    assert controller.state.last_error is not None
    assert controller.state.last_error.category is ErrorCategory.UNAVAILABLE


def test_recovery_clears_stale() -> None:
    client = FlakyQbitClient(torrents=[make_torrent(name="Alpha")])
    controller = _controller(client)
    controller.refresh()
    client.next_torrents_info_error = QbitConnectionError("connection lost")
    controller.refresh()
    assert controller.state.stale is True

    controller.refresh()

    assert controller.state.stale is False
    assert controller.state.connection is ConnectionState.CONNECTED
    assert controller.state.last_error is None


def test_authentication_failure_becomes_blocking() -> None:
    client = FlakyQbitClient(torrents=[make_torrent()])
    controller = _controller(client)
    controller.refresh()

    client.next_torrents_info_error = QbitAuthenticationError("bad credentials")
    controller.refresh()

    assert controller.state.connection is ConnectionState.AUTH_FAILED
    assert controller.state.last_error is not None
    assert controller.state.last_error.category is ErrorCategory.AUTHENTICATION


def test_authentication_failure_on_first_refresh_also_blocks() -> None:
    def _raise_auth() -> Any:
        raise QbitAuthenticationError("bad credentials")

    controller = TuiController(client_factory=_raise_auth)

    controller.refresh()

    assert controller.state.connection is ConnectionState.AUTH_FAILED
    assert controller.state.status is None


def test_internal_error_is_not_converted_to_unavailable() -> None:
    client = FlakyQbitClient(torrents=[make_torrent()])
    controller = _controller(client)
    controller.refresh()

    client.next_torrents_info_error = TypeError("a real programming defect")

    with pytest.raises(TypeError):
        controller.refresh()

    # A real bug must not be silently relabelled as a degraded snapshot.
    assert controller.state.connection is ConnectionState.CONNECTED


def test_focused_torrent_disappearing_clears_focus_and_details() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={torrent_hash: []},
    )
    controller = _controller(client)
    controller.refresh()
    controller.set_focus(torrent_hash)
    assert controller.state.focused_hash == torrent_hash

    client.torrents = []
    controller.refresh()

    assert controller.state.focused_hash is None
    assert controller.state.focused_tracker_details is None
    assert controller.state.focused_details_fetched_at is None


def test_failed_tracker_fetch_does_not_stay_stuck_loading_forever() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={torrent_hash: []},
    )
    controller = _controller(client)
    controller.refresh()
    request_id = controller.begin_focus_change(torrent_hash)
    assert request_id is not None
    assert controller.state.focused_tracker_details is None
    assert controller.state.focused_tracker_fetch_failed is False

    controller.apply_tracker_details_failure(
        request_id, QbitConnectionError("connection lost")
    )

    assert controller.state.focused_tracker_details is None
    assert controller.state.focused_tracker_fetch_failed is True

    # A later successful fetch clears the failure flag again.
    controller.apply_tracker_details_success(request_id, torrent_hash, [])
    assert controller.state.focused_tracker_fetch_failed is False


def test_new_focus_change_clears_a_prior_fetch_failure() -> None:
    hash_a, hash_b = "a" * 40, "b" * 40
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=hash_a, name="Alpha"),
            make_torrent(hash=hash_b, name="Beta"),
        ],
        trackers_by_hash={hash_a: [], hash_b: []},
    )
    controller = _controller(client)
    controller.refresh()
    request_id = controller.begin_focus_change(hash_a)
    assert request_id is not None
    controller.apply_tracker_details_failure(
        request_id, QbitConnectionError("connection lost")
    )
    assert controller.state.focused_tracker_fetch_failed is True

    controller.begin_focus_change(hash_b)

    assert controller.state.focused_tracker_fetch_failed is False


def test_focus_hidden_by_filter_change_is_cleared() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=torrent_hash, name="Alpha", category="sonarr")
        ],
        trackers_by_hash={torrent_hash: []},
    )
    controller = _controller(client)
    controller.refresh()
    controller.set_focus(torrent_hash)

    controller.set_filters(build_torrent_filter(categories=["radarr"]))

    assert controller.state.focused_hash is None
    assert controller.state.focused_tracker_details is None


def test_search_matches_full_hash_case_insensitively() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=torrent_hash, name="Debian ISO"),
            make_torrent(hash="b" * 40, name="Ubuntu ISO"),
        ]
    )
    controller = _controller(client)
    controller.refresh()

    controller.set_search(torrent_hash.upper())

    assert controller.state.visible is not None
    assert [t.name for t in controller.state.visible.matched] == ["Debian ISO"]


def test_search_matches_leading_hash_prefix_only() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="deadbeef" + "0" * 32, name="Debian ISO"),
            make_torrent(hash="b" * 40, name="Ubuntu ISO"),
        ]
    )
    controller = _controller(client)
    controller.refresh()

    controller.set_search("DEADBEEF")
    assert controller.state.visible is not None
    assert [t.name for t in controller.state.visible.matched] == ["Debian ISO"]

    controller.set_search("eadbeef")  # not a leading prefix
    assert controller.state.visible is not None
    assert controller.state.visible.matched == ()


def test_focus_hidden_by_search_is_cleared() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name="Debian ISO")],
        trackers_by_hash={torrent_hash: []},
    )
    controller = _controller(client)
    controller.refresh()
    controller.set_focus(torrent_hash)

    controller.set_search("ubuntu")

    assert controller.state.focused_hash is None


def test_safe_tracker_details_contain_no_secrets() -> None:
    torrent_hash = "a" * 40
    secret_url = "https://tracker.example/announce/TOPSECRETPASSKEY?passkey=abc"
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash)],
        trackers_by_hash={torrent_hash: [{"url": secret_url, "status": 2}]},
    )
    controller = _controller(client)
    controller.refresh()

    controller.set_focus(torrent_hash)

    details = controller.state.focused_tracker_details
    assert details is not None
    rendered = repr(details)
    assert "TOPSECRETPASSKEY" not in rendered
    assert "passkey=abc" not in rendered
    assert secret_url not in rendered


def test_manual_refresh_focused_details_refetches() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash)],
        trackers_by_hash={torrent_hash: []},
    )
    controller = _controller(client)
    controller.refresh()
    controller.set_focus(torrent_hash)
    assert client.torrents_trackers_calls == 1

    controller.refresh_focused_details()

    assert client.torrents_trackers_calls == 2


def test_refresh_focused_details_noop_without_focus() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    controller = _controller(client)
    controller.refresh()

    controller.refresh_focused_details()

    assert client.torrents_trackers_calls == 0


def test_configuration_failure_becomes_blocking() -> None:
    def _raise_config() -> Any:
        raise ConfigError("QBIT_HOST is not set")

    controller = TuiController(client_factory=_raise_config)

    controller.refresh()

    assert controller.state.connection is ConnectionState.CONFIG_FAILED
    assert controller.state.last_error is not None
    assert controller.state.last_error.category is ErrorCategory.CONFIGURATION


def test_blocking_states_never_retry() -> None:
    calls = {"count": 0}

    def _raise_config() -> Any:
        calls["count"] += 1
        raise ConfigError("QBIT_HOST is not set")

    controller = TuiController(client_factory=_raise_config)

    controller.refresh()
    controller.refresh()
    controller.refresh()

    assert calls["count"] == 1


def test_auth_failure_never_retries() -> None:
    client = FlakyQbitClient(torrents=[make_torrent()])
    controller = _controller(client)
    controller.refresh()
    client.next_torrents_info_error = QbitAuthenticationError("bad creds")
    controller.refresh()
    calls_before = client.torrents_info_calls

    controller.refresh()

    assert controller.state.connection is ConnectionState.AUTH_FAILED
    assert client.torrents_info_calls == calls_before


def test_default_workspace_is_overview() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    controller = _controller(client)

    assert controller.state.workspace is Workspace.OVERVIEW


def test_set_workspace_performs_zero_api_calls_and_preserves_state() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")],
        trackers_by_hash={"a" * 40: []},
    )
    controller = _controller(client)
    controller.refresh()
    controller.set_search("alpha")
    controller.set_focus("a" * 40)
    calls_before = len(client.calls)

    controller.set_workspace(Workspace.TORRENTS)

    assert controller.state.workspace is Workspace.TORRENTS
    assert controller.state.search == "alpha"
    assert controller.state.focused_hash == "a" * 40
    assert len(client.calls) == calls_before

    controller.set_workspace(Workspace.OVERVIEW)

    assert controller.state.workspace is Workspace.OVERVIEW
    assert controller.state.search == "alpha"
    assert controller.state.focused_hash == "a" * 40
    assert len(client.calls) == calls_before


def test_stopped_count_reflects_paused_and_stopped_torrents() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", state="pausedUP"),
            make_torrent(hash="b" * 40, name="Beta", state="stoppedDL"),
            make_torrent(hash="c" * 40, name="Gamma", state="downloading"),
        ]
    )
    controller = _controller(client)

    controller.refresh()

    assert controller.state.stopped_count == 2


def test_build_explanation_zero_api_calls_when_details_loaded() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=torrent_hash, name="Alpha", state="uploading")
        ],
        trackers_by_hash={torrent_hash: []},
    )
    controller = _controller(client)
    controller.refresh()
    controller.set_focus(torrent_hash)
    calls_before = len(client.calls)

    report = controller.build_explanation()

    assert report is not None
    assert report.target_identity == torrent_hash
    assert len(client.calls) == calls_before


def test_build_explanation_none_when_nothing_focused() -> None:
    client = FakeQbitClient(torrents=[make_torrent()])
    controller = _controller(client)
    controller.refresh()

    assert controller.build_explanation() is None


def test_build_explanation_none_when_torrent_disappeared() -> None:
    torrent_hash = "a" * 40
    client = FakeQbitClient(
        torrents=[make_torrent(hash=torrent_hash, name="Alpha")],
        trackers_by_hash={torrent_hash: []},
    )
    controller = _controller(client)
    controller.refresh()
    controller.set_focus(torrent_hash)
    # Simulate the torrent vanishing from the raw snapshot without going
    # through a full refresh (defensive edge case).
    controller._raw_torrents = []

    assert controller.build_explanation() is None


def test_detail_request_id_bumps_on_focus_change() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha"),
            make_torrent(hash="b" * 40, name="Beta"),
        ],
        trackers_by_hash={"a" * 40: [], "b" * 40: []},
    )
    controller = _controller(client)
    controller.refresh()

    before = controller.detail_request_id
    controller.begin_focus_change("b" * 40)

    assert controller.detail_request_id == before + 1


def test_raw_torrent_by_hash_is_case_insensitive() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    controller = _controller(client)
    controller.refresh()

    found = controller.raw_torrent_by_hash("A" * 40)

    assert found is not None


# --- TUI 2: explicit multi-selection + LOW-risk bulk actions ------------------


def test_focusing_a_torrent_does_not_select_it() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    controller = _controller(client)
    controller.refresh()

    controller.set_focus("a" * 40)

    assert controller.state.selected_hashes == set()


def test_toggle_selection_toggles_exactly_one_hash() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha"),
            make_torrent(hash="b" * 40, name="Beta"),
        ]
    )
    controller = _controller(client)
    controller.refresh()

    controller.toggle_selection("a" * 40)
    assert controller.state.selected_hashes == {"a" * 40}

    controller.toggle_selection("b" * 40)
    assert controller.state.selected_hashes == {"a" * 40, "b" * 40}

    controller.toggle_selection("a" * 40)
    assert controller.state.selected_hashes == {"b" * 40}


def test_select_all_visible_selects_only_visible_hashes() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", category="films"),
            make_torrent(hash="b" * 40, name="Beta", category="tv"),
        ]
    )
    controller = _controller(client)
    controller.refresh()
    controller.set_filters(build_torrent_filter(categories=["films"]))

    controller.select_all_visible()

    assert controller.state.selected_hashes == {"a" * 40}


def test_zero_selection_never_implies_all() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha"),
            make_torrent(hash="b" * 40, name="Beta"),
        ]
    )
    controller = _controller(client)
    controller.refresh()

    assert controller.state.selected_hashes == set()
    plan = controller.build_bulk_plan(
        "pause", tuple(controller.state.selected_hashes)
    )
    assert plan.changes == ()
    assert plan.matched == 0


def test_filter_change_does_not_auto_reconcile_selection() -> None:
    """`set_filters` deliberately does not reconcile the selection by
    itself: the Filters modal applies live, on every keystroke, and a
    category filter is exact-match -- typing "films" one letter at a
    time would otherwise transiently wipe a selection before the user
    finishes typing the very filter that keeps it visible. The caller
    (`qbit_ops.tui.app`) reconciles once, explicitly, at each real commit
    point (Apply/Clear/Cancel) via `reconcile_selection()` -- see
    `test_reconcile_selection_removes_hidden_hashes_on_demand`.
    """
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", category="films"),
            make_torrent(hash="b" * 40, name="Beta", category="tv"),
        ]
    )
    controller = _controller(client)
    controller.refresh()
    controller.toggle_selection("a" * 40)
    controller.toggle_selection("b" * 40)

    controller.set_filters(build_torrent_filter(categories=["films"]))

    assert controller.state.selected_hashes == {"a" * 40, "b" * 40}


def test_reconcile_selection_removes_hidden_hashes_on_demand() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", category="films"),
            make_torrent(hash="b" * 40, name="Beta", category="tv"),
        ]
    )
    controller = _controller(client)
    controller.refresh()
    controller.toggle_selection("a" * 40)
    controller.toggle_selection("b" * 40)
    controller.set_filters(build_torrent_filter(categories=["films"]))

    removed = controller.reconcile_selection()

    assert removed == 1
    assert controller.state.selected_hashes == {"a" * 40}


def test_search_change_removes_hidden_selections() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha"),
            make_torrent(hash="b" * 40, name="Beta"),
        ]
    )
    controller = _controller(client)
    controller.refresh()
    controller.toggle_selection("a" * 40)
    controller.toggle_selection("b" * 40)

    removed = controller.set_search("alpha")

    assert removed == 1
    assert controller.state.selected_hashes == {"a" * 40}


def test_periodic_refresh_removes_missing_selections() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha"),
            make_torrent(hash="b" * 40, name="Beta"),
        ]
    )
    controller = _controller(client)
    controller.refresh()
    controller.toggle_selection("a" * 40)
    controller.toggle_selection("b" * 40)

    client.torrents = [make_torrent(hash="a" * 40, name="Alpha")]
    controller.refresh()

    assert controller.state.selected_hashes == {"a" * 40}


def test_build_bulk_plan_freezes_a_sorted_hash_tuple() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="b" * 40, name="Beta"),
            make_torrent(hash="a" * 40, name="Alpha"),
        ]
    )
    controller = _controller(client)
    controller.refresh()
    controller.toggle_selection("b" * 40)
    controller.toggle_selection("a" * 40)

    snapshot = tuple(sorted(controller.state.selected_hashes))
    plan = controller.build_bulk_plan("pause", snapshot)

    assert [c.hash for c in plan.changes] == ["a" * 40, "b" * 40]


def test_plan_is_frozen_against_later_selection_changes() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha"),
            make_torrent(hash="b" * 40, name="Beta"),
        ]
    )
    controller = _controller(client)
    controller.refresh()
    controller.toggle_selection("a" * 40)
    snapshot = tuple(sorted(controller.state.selected_hashes))

    plan = controller.build_bulk_plan("pause", snapshot)

    controller.toggle_selection("b" * 40)
    controller.clear_selection()

    assert [c.hash for c in plan.changes] == ["a" * 40]


def test_build_bulk_plan_performs_zero_api_calls() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", state="downloading")
        ]
    )
    controller = _controller(client)
    controller.refresh()
    calls_before = len(client.calls)

    controller.build_bulk_plan("pause", ("a" * 40,))

    assert len(client.calls) == calls_before


def test_apply_bulk_plan_mutates_exactly_the_plan_hashes() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", state="downloading"),
            make_torrent(hash="b" * 40, name="Beta", state="downloading"),
        ]
    )
    controller = _controller(client)
    controller.refresh()
    plan = controller.build_bulk_plan("pause", ("a" * 40,))

    controller.apply_bulk_plan(plan)

    assert client.paused_hashes == [["a" * 40]]


def test_pause_plan_skips_already_stopped_torrents() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", state="downloading"),
            make_torrent(hash="b" * 40, name="Beta", state="pausedDL"),
        ]
    )
    controller = _controller(client)
    controller.refresh()

    plan = controller.build_bulk_plan("pause", ("a" * 40, "b" * 40))

    assert [c.hash for c in plan.changes] == ["a" * 40]
    assert plan.skipped[0].reason == "already_stopped"


def test_resume_plan_skips_already_running_torrents() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", state="pausedDL"),
            make_torrent(hash="b" * 40, name="Beta", state="downloading"),
        ]
    )
    controller = _controller(client)
    controller.refresh()

    plan = controller.build_bulk_plan("resume", ("a" * 40, "b" * 40))

    assert [c.hash for c in plan.changes] == ["a" * 40]
    assert plan.skipped[0].reason == "already_running"


def test_reannounce_plan_uses_exact_selected_hashes() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", state="downloading"),
            make_torrent(hash="b" * 40, name="Beta", state="pausedDL"),
        ]
    )
    controller = _controller(client)
    controller.refresh()

    plan = controller.build_bulk_plan("reannounce", ("a" * 40, "b" * 40))
    controller.apply_bulk_plan(plan)

    assert sorted(client.reannounced_hashes[0]) == ["a" * 40, "b" * 40]


def test_classify_plan_status_no_match_is_truthful() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha")]
    )
    controller = _controller(client)
    controller.refresh()

    plan = controller.build_bulk_plan("pause", ())

    assert controller.classify_plan_status(plan) is MutationStatus.NO_MATCH


def test_classify_plan_status_no_changes_is_truthful() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Alpha", state="pausedDL")]
    )
    controller = _controller(client)
    controller.refresh()

    plan = controller.build_bulk_plan("pause", ("a" * 40,))

    assert controller.classify_plan_status(plan) is MutationStatus.NO_CHANGES


def test_apply_bulk_plan_propagates_failure_untruthfully_unclaimed() -> None:
    """A failed apply must never be silently swallowed -- the caller
    (qbit_ops.tui.app) needs the exception to classify and report failure
    truthfully instead of assuming success."""

    class _FailingClient(FakeQbitClient):
        def torrents_pause(self, torrent_hashes):  # type: ignore[override]
            raise ConnectionError("boom")

    client = _FailingClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha", state="downloading")
        ]
    )
    controller = _controller(client)
    controller.refresh()
    plan = controller.build_bulk_plan("pause", ("a" * 40,))

    with pytest.raises(RuntimeError):
        controller.apply_bulk_plan(plan)


def test_clear_selection_for_only_removes_given_hashes() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Alpha"),
            make_torrent(hash="b" * 40, name="Beta"),
        ]
    )
    controller = _controller(client)
    controller.refresh()
    controller.toggle_selection("a" * 40)
    controller.toggle_selection("b" * 40)

    controller.clear_selection_for(["a" * 40])

    assert controller.state.selected_hashes == {"b" * 40}
