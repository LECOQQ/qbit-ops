"""Non-Textual tests for the TUI's pure state/refresh controller.

These exercise `app.tui.state.TuiController` directly against
`tests.support.FakeQbitClient` -- no terminal, no Textual `App`, no
event loop. Interface-level (Pilot) tests live in `tests/test_tui_app.py`.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.config import ConfigError
from app.errors import (
    ErrorCategory,
    QbitAuthenticationError,
    QbitConnectionError,
)
from app.torrents import build_torrent_filter
from app.tui.state import ConnectionState, TuiController
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


def test_refresh_uses_the_documented_four_call_budget() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(), make_torrent(hash="b" * 40)]
    )
    controller = _controller(client)

    controller.refresh()

    assert client.app_version_calls == 1
    assert client.app_web_api_version_calls == 1
    assert client.transfer_info_calls == 1
    assert client.torrents_info_calls == 1
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
    """Regression: re-filtering must use raw items, not display-formatted
    `SelectedTorrent.category`, or the `uncategorized` token would never
    match anything (see app.app_services.TuiRefreshResult's docstring).
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
