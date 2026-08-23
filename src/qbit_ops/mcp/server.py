"""Expose the bounded read-only tools over MCP, on stdio.

The only module in the repository allowed to import the MCP SDK, which
`tests/test_layering.py` (R7) enforces the way it already does for
Textual. `qbit_ops.mcp.tools` deliberately does not import it: the
bounded views stay testable, and usable, without the protocol.

Personal spike. Read-only by construction -- there is no mutating tool
to forget to guard, because none is registered.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from qbit_core.shared.selection import STATE_FILTER_VALUES
from qbit_ops.mcp import tools

server = MCPServer(
    name="qbit-ops",
    instructions=(
        "Read-only, bounded access to a qBittorrent library. Start with "
        "library_summary, narrow with find_torrents, then inspect or "
        "explain a single torrent by hash. Results are capped server-side: "
        "a large library is explored by drilling down, never by listing "
        "it whole."
    ),
)

# Derived, never copied: a state token the classifier could not produce
# (an old literal list here once included `stopped`, which does not
# exist -- qBittorrent folds a stopped torrent back into `downloading`
# or `seeding`) would tell the model an argument exists when it does
# not, and `matched: 0` reads as "there are none" rather than "the
# argument was wrong".
_STATE_GROUPS = ", ".join(sorted(STATE_FILTER_VALUES))

_FIND_TORRENTS_DESCRIPTION = (
    "Find torrents by state group, category and/or name fragment.\n\n"
    f"`state_group` is one of qbit-ops's canonical groups ({_STATE_GROUPS}). "
    "There is no separate group for a stopped torrent: qBittorrent folds "
    "a stopped download back into `downloading` and a stopped seed back "
    "into `seeding`, so this is the complete vocabulary. `category` "
    "matches exactly, and is the axis the operator defines himself -- "
    "often what separates two torrents that look identical. Results are "
    "capped server-side; `matched` tells you how many exist beyond what "
    "was returned, and `next_offset` is where to resume -- `null` once "
    "you have seen everything."
)


_client_lock = threading.Lock()
_cached_client: Any | None = None


def _client() -> Any:
    """The one qBittorrent client for this server process, built lazily
    and reused across every tool call.

    Logging in is qBittorrent's most expensive call -- it derives the
    configured password with PBKDF2 on every attempt (measured: ~74ms
    median, versus ~2-11ms to reuse an existing session). Building a
    fresh client per tool call re-paid that cost on every one of them;
    a four-tool drill-down spent 90% of its time re-authenticating the
    same session. Imported lazily so the module can be loaded -- and
    its tools listed -- without credentials present. Goes through
    `app_services`, not `qbit_ops.cli`: the CLI is a surface like this
    one, not a layer beneath it, and `tests/test_cli_architecture.py`
    enforces that.
    """
    global _cached_client
    with _client_lock:
        if _cached_client is None:
            from qbit_ops.app_services import create_qbit_client

            _cached_client = create_qbit_client()
        return _cached_client


def _drop_client() -> None:
    """Force the next `_client()` call to log in again.

    A cached client can outlive its qBittorrent session (an idle
    timeout, a restart of the instance). `_call` clears the cache on
    any recoverable failure so its retry gets a fresh login instead of
    repeating the same broken session.
    """
    global _cached_client
    with _client_lock:
        _cached_client = None


def _call(tool: Callable[..., Any], **kwargs: Any) -> Any:
    """Run one tool body against the cached client, retrying once after
    a dropped session.

    Every `@server.tool()` wrapper goes through this instead of calling
    `tools.<fn>` directly, so the retry-on-expiry policy lives in
    exactly one place. A second failure of the same kind is a real
    outage, not an expired session, and propagates.
    """
    from qbit_ops.app_services import classify_recoverable_qbit_failure

    try:
        return tool(_client(), **kwargs)
    except Exception as error:
        if classify_recoverable_qbit_failure(error) is None:
            raise
        _drop_client()
        return tool(_client(), **kwargs)


@server.tool()
def library_summary() -> dict[str, Any]:
    """Counts, health and alerts for the whole library. Fixed size."""
    return _call(tools.library_summary)


@server.tool(description=_FIND_TORRENTS_DESCRIPTION)
def find_torrents(
    state_group: str | None = None,
    category: str | None = None,
    name_contains: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    return _call(
        tools.find_torrents,
        state_group=state_group,
        category=category,
        name_contains=name_contains,
        limit=limit,
        offset=offset,
    )


@server.tool()
def aggregate_stats(
    state_group: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Totals over a selection: sizes, transfer, ratio, seeding time.

    Use this instead of adding up fields from `find_torrents` yourself.
    Same filters, fixed-size answer whatever the selection.
    """
    return _call(
        tools.aggregate_stats, state_group=state_group, category=category
    )


@server.tool()
def inspect_torrent(torrent_hash: str) -> dict[str, Any] | None:
    """Every field of one torrent, by full infohash."""
    return _call(tools.inspect_torrent, torrent_hash=torrent_hash)


@server.tool()
def explain_torrent(torrent_hash: str) -> dict[str, Any] | None:
    """Why this torrent is in its current state, with evidence.

    Uses qbit-ops's own diagnostic rules, so the answer matches what
    `qbit-ops explain torrent` would say.
    """
    return _call(tools.explain, torrent_hash=torrent_hash)


def main() -> None:
    """Run the server on stdio -- the transport an MCP host launches."""
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
