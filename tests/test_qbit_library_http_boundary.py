"""Characterize qbittorrent-api's own internal HTTP behavior (P-4/5/7/8).

Every other test in this suite exercises qbit-ops's code against
`tests.support.FakeQbitClient`, a Python-level double that never goes
near the real `qbittorrentapi.Client`/`requests` machinery -- which is
exactly why constats P-4, P-5, P-7 and P-8 went unnoticed: they are
about what the *library* does internally, not what qbit-ops calls.

This file instead exercises the **real** `qbittorrentapi.Client`
against a hermetic fake HTTP layer: only `QbittorrentSession.request`
(the one method that would open a real socket) is replaced, so
`qbittorrentapi`'s own URL building, Web API version negotiation
(`_is_endpoint_supported_for_version`), and response casting all run
for real. No real network access, no real qBittorrent instance -- see
`AGENTS.md`'s hermeticity rule.

This distinguishes two different "call counts" that earlier
documentation conflated:

- **qbit-ops's own call count** -- how many `client.*()` methods
  `qbit_core.features.torrents`/`qbit_core.features.status`/etc. call.
  This is what `tests/test_progress_call_parity.py` and `FakeQbitClient.calls`
  measure, and it is unaffected by anything in this file.
- **HTTP requests qbittorrentapi issues internally** -- what actually
  goes over the wire for one `client.*()` call. This file measures
  *that*, at the one seam where it is observable without a real
  server.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest
import qbittorrentapi
import qbittorrentapi.request as qbit_request
from requests import Response


def _make_response(body: bytes) -> Response:
    response = Response()
    response.status_code = 200
    response._content = body
    return response


# Canned bodies for every endpoint this harness exercises. Keyed by the
# last path segment qbittorrentapi's URL builder produces (e.g. "login",
# "webapiVersion", "stop") -- see `QbittorrentURL.build`.
_RESPONSE_BODY_BY_ENDPOINT: dict[str, bytes] = {
    "login": b"Ok.",
    "version": b"v5.0.1",
    "webapiVersion": b"2.11.4",
    "info": b"[]",
    "trackers": b"[]",
}


@dataclass
class _RecordedHttpCalls:
    """The ordered `(method, url)` pairs the fake HTTP layer observed."""

    calls: list[tuple[str, str]] = field(default_factory=list)

    def endpoints(self) -> list[str]:
        """Return just the trailing path segment of each recorded URL."""
        return [
            url.rstrip("/").rsplit("/", 1)[-1] for _method, url in self.calls
        ]


@pytest.fixture
def real_qbit_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[qbittorrentapi.Client, _RecordedHttpCalls]]:
    """A real `qbittorrentapi.Client` whose HTTP layer is fully faked.

    `FORCE_SCHEME_FROM_HOST=True` plus an explicit `http://` scheme
    skips the library's own scheme-detection HEAD probe (itself an
    HTTP call this harness does not need to fake), so the only request
    ever made is the one under test.
    """
    recorded = _RecordedHttpCalls()

    def _fake_session_request(
        self: qbit_request.QbittorrentSession,
        method: str,
        url: str,
        **kwargs: object,
    ) -> Response:
        recorded.calls.append((method, url))
        endpoint = url.rstrip("/").rsplit("/", 1)[-1]
        return _make_response(_RESPONSE_BODY_BY_ENDPOINT.get(endpoint, b""))

    monkeypatch.setattr(
        qbit_request.QbittorrentSession, "request", _fake_session_request
    )

    client = qbittorrentapi.Client(
        host="http://fake-qbittorrent.invalid:8080",
        username="fake-user",
        password="fake-password",
        FORCE_SCHEME_FROM_HOST=True,
    )
    yield client, recorded


# --- P-7: auth_log_in() is exercised against the real library flow ---------


def test_auth_log_in_performs_exactly_one_login_request(
    real_qbit_client: tuple[qbittorrentapi.Client, _RecordedHttpCalls],
) -> None:
    """P-7: `auth_log_in()` was previously exercised by no test at all
    (`tests/conftest.py` replaces `_create_qbit_client` wholesale). This
    proves it performs exactly the one expected `POST .../auth/login`
    against the real library, hermetically."""
    client, recorded = real_qbit_client

    client.auth_log_in()

    assert recorded.calls == [
        ("post", "http://fake-qbittorrent.invalid:8080/api/v2/auth/login")
    ]


# --- P-4: torrents_start/torrents_resume are the same bound method ----------


def test_torrents_start_and_torrents_resume_are_the_same_method(
    real_qbit_client: tuple[qbittorrentapi.Client, _RecordedHttpCalls],
) -> None:
    """P-4: the installed qbittorrent-api aliases `torrents_resume` to
    `torrents_start` (verified directly against the library, not
    inferred) -- `qbit_core.features.torrents._call_bulk_torrent_action`'s
    `getattr(client, "torrents_start", None)` fallback to
    `client.torrents_resume(...)` is therefore dead: `torrents_start` is
    never absent from a real client."""
    client, _recorded = real_qbit_client

    assert client.torrents_start == client.torrents_resume


def test_torrents_resume_call_reaches_the_start_endpoint(
    real_qbit_client: tuple[qbittorrentapi.Client, _RecordedHttpCalls],
) -> None:
    """Confirms the observable consequence of the P-4 aliasing: calling
    `torrents_resume` reaches `POST .../torrents/start`, never a
    separate `.../torrents/resume` endpoint."""
    client, recorded = real_qbit_client

    client.torrents_resume(["abc123"])

    assert "torrents/resume" not in {url for _method, url in recorded.calls}
    assert any(
        url.endswith("/torrents/start") for _method, url in recorded.calls
    )


# --- P-8: the real endpoint reached for pause/resume on Web API >= 2.11.0 ---


def test_torrents_pause_reaches_the_stop_endpoint_not_pause(
    real_qbit_client: tuple[qbittorrentapi.Client, _RecordedHttpCalls],
) -> None:
    """P-8: on this Web API version (2.11.4, fixed by
    `_RESPONSE_BODY_BY_ENDPOINT["webapiVersion"]`), `torrents_pause()`
    reaches `POST .../torrents/stop` -- the endpoint that exists in
    code and in every test double is `torrents_pause`/`torrents_stop`,
    but the real wire endpoint for 5.x is `stop`, which appeared in
    neither before this test."""
    client, recorded = real_qbit_client

    client.torrents_pause(["abc123"])

    endpoints = {
        url.rstrip("/").rsplit("/", 1)[-1] for _method, url in recorded.calls
    }
    assert "stop" in endpoints
    assert "pause" not in endpoints


# --- P-5: every version-gated mutation makes an uncounted extra request ----


@pytest.mark.parametrize(
    ("method_name", "call_args"),
    [
        ("torrents_pause", (["abc123"],)),
        ("torrents_resume", (["abc123"],)),
        ("torrents_reannounce", (["abc123"],)),
    ],
)
def test_torrent_state_mutation_makes_one_hidden_webapiversion_request(
    real_qbit_client: tuple[qbittorrentapi.Client, _RecordedHttpCalls],
    method_name: str,
    call_args: tuple[object, ...],
) -> None:
    """P-5: qbit-ops calls `client.torrents_pause(hashes)` exactly once
    (see `tests/test_progress_call_parity.py`), but the library issues
    **two** HTTP requests for it: a `GET .../app/webapiVersion` (via
    `_is_endpoint_supported_for_version`) it never caches, followed by
    the actual mutation. This is invisible to any test built on
    `FakeQbitClient`, which has no such internal check."""
    client, recorded = real_qbit_client

    getattr(client, method_name)(*call_args)

    assert len(recorded.calls) == 2
    assert recorded.calls[0][1].endswith("/app/webapiVersion")


@pytest.mark.parametrize(
    ("method_name", "kwargs"),
    [
        (
            "torrents_remove_trackers",
            {
                "torrent_hash": "abc123",
                "urls": ["https://tracker.example/announce"],
            },
        ),
        (
            "torrents_edit_tracker",
            {
                "torrent_hash": "abc123",
                "original_url": "https://tracker.example/announce",
                "new_url": "https://tracker.example/other",
            },
        ),
    ],
)
def test_tracker_mutation_with_declared_version_floor_makes_hidden_request(
    real_qbit_client: tuple[qbittorrentapi.Client, _RecordedHttpCalls],
    method_name: str,
    kwargs: dict[str, object],
) -> None:
    """`torrents_remove_trackers`/`torrents_edit_tracker` declare
    `version_introduced="2.2.0"` in the library, which triggers the
    same hidden `webapiVersion` check as the torrent-state mutations
    above -- `torrents_add_trackers` (next test) has no such floor and
    is the one mutation that does not pay this cost."""
    client, recorded = real_qbit_client

    getattr(client, method_name)(**kwargs)

    assert len(recorded.calls) == 2
    assert recorded.calls[0][1].endswith("/app/webapiVersion")


def test_add_trackers_has_no_declared_version_floor_and_makes_one_request(
    real_qbit_client: tuple[qbittorrentapi.Client, _RecordedHttpCalls],
) -> None:
    """Contrast case: `torrents_add_trackers` declares no
    `version_introduced` in the library, so it never triggers the
    hidden `webapiVersion` request the other mutations do."""
    client, recorded = real_qbit_client

    client.torrents_add_trackers(
        torrent_hash="abc123", urls="https://tracker.example/announce"
    )

    assert len(recorded.calls) == 1
    assert recorded.calls[0][1].endswith("/torrents/addTrackers")


def test_read_only_torrents_info_makes_no_hidden_version_request(
    real_qbit_client: tuple[qbittorrentapi.Client, _RecordedHttpCalls],
) -> None:
    """Read commands' documented call budgets are unaffected by P-5:
    `torrents_info()` declares no version floor either."""
    client, recorded = real_qbit_client

    client.torrents_info()

    assert len(recorded.calls) == 1
    assert recorded.calls[0][1].endswith("/torrents/info")
