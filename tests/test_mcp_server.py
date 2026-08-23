"""Client caching, session recovery and derived vocabulary of the MCP server.

Separate from `tests/test_mcp_tools.py`, which runs on a normal install:
everything here imports `qbit_ops.mcp.server`, so the whole module skips
when the `mcp` extra is absent.
"""

from __future__ import annotations

import asyncio
import re

import pytest

pytest.importorskip("mcp")

import qbit_ops.app_services as app_services
import qbit_ops.mcp.server as server
from qbit_core.errors import QbitAuthenticationError
from qbit_core.shared.selection import STATE_FILTER_VALUES
from tests.support import FakeQbitClient, make_torrent


@pytest.fixture(autouse=True)
def _reset_cached_client():
    """The client cache is a module-level global by design (it must
    outlive one tool call); reset it around every test so none leaks
    into the next."""
    server._cached_client = None
    yield
    server._cached_client = None


def _counting_client_factory():
    """Return a factory that hands out a distinct object each call, and
    the list it appends to -- so a test can tell "reused" from "rebuilt"
    by identity, and count logins by length."""
    created: list[object] = []

    def _factory():
        client = object()
        created.append(client)
        return client

    return _factory, created


# --- caching -----------------------------------------------------------


def test_client_is_built_once_and_reused_across_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, created = _counting_client_factory()
    monkeypatch.setattr(app_services, "create_qbit_client", factory)

    first = server._client()
    second = server._client()
    third = server._client()

    assert first is second is third
    assert len(created) == 1, "one login must serve every tool call"


def test_find_torrents_reuses_the_client_across_two_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The end-to-end path a tool call actually takes, not just `_client()`
    in isolation: the login this measures is the one the audit priced at
    87-97% of a tool call's time.
    """
    login_count = 0
    fake_client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="A", state="uploading")]
    )

    def _factory():
        nonlocal login_count
        login_count += 1
        return fake_client

    monkeypatch.setattr(app_services, "create_qbit_client", _factory)

    server.find_torrents()
    server.find_torrents()

    assert login_count == 1


# --- session recovery ----------------------------------------------------


def test_call_retries_once_after_a_dropped_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cached client can outlive its qBittorrent session. The first
    failure classified as recoverable drops the cache and retries with a
    fresh login; the tool call itself still succeeds.
    """
    factory, created = _counting_client_factory()
    monkeypatch.setattr(app_services, "create_qbit_client", factory)

    attempts: list[object] = []

    def _flaky_tool(client: object) -> str:
        attempts.append(client)
        if len(attempts) == 1:
            raise QbitAuthenticationError("session expired")
        return "ok"

    result = server._call(_flaky_tool)

    assert result == "ok"
    assert len(created) == 2, "exactly one relogin after the drop"
    assert attempts == created, "the retry must use the freshly built client"


def test_call_does_not_retry_a_non_recoverable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrecognized exception is a programming error, not an expired
    session: propagate it unchanged and leave the cache alone, rather
    than silently retrying (and paying a second login) for something a
    retry can never fix.
    """
    factory, created = _counting_client_factory()
    monkeypatch.setattr(app_services, "create_qbit_client", factory)

    def _broken_tool(client: object) -> None:
        raise ValueError("not a session problem")

    with pytest.raises(ValueError, match="not a session problem"):
        server._call(_broken_tool)

    assert (
        len(created) == 1
    ), "a non-recoverable error must not trigger a relogin"
    assert server._cached_client is created[0], "the cache must survive"


# --- vocabulary, derived not copied --------------------------------------


def test_find_torrents_description_derives_its_state_vocabulary() -> None:
    """`stopped` doesn't exist as a `state_group`: qBittorrent folds a
    stopped torrent back into `downloading`/`seeding`, and a hand-copied
    list once claimed otherwise. Deriving the advertised vocabulary from
    `STATE_FILTER_VALUES` instead of a literal makes that drift
    structurally impossible.
    """
    tool = next(
        tool
        for tool in asyncio.run(server.server.list_tools())
        if tool.name == "find_torrents"
    )

    match = re.search(r"canonical groups \(([^)]*)\)", tool.description or "")
    assert match is not None, "description must name the vocabulary"

    listed = {token.strip("` ") for token in match.group(1).split(",")}
    assert listed == set(STATE_FILTER_VALUES)
