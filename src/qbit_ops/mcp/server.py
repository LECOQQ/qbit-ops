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
from typing import Any

from mcp.server import MCPServer

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


def _client() -> Any:
    """A fresh qBittorrent client for one tool call.

    Imported lazily so the module can be loaded -- and its tools listed
    -- without credentials present. Goes through `app_services`, not
    `qbit_ops.cli`: the CLI is a surface like this one, not a layer
    beneath it, and `tests/test_cli_architecture.py` enforces that.
    """
    from qbit_ops.app_services import create_qbit_client

    return create_qbit_client()


@server.tool()
def library_summary() -> dict[str, Any]:
    """Counts, health and alerts for the whole library. Fixed size."""
    return tools.library_summary(_client())


@server.tool()
def find_torrents(
    state_group: str | None = None,
    category: str | None = None,
    name_contains: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Find torrents by state group, category and/or name fragment.

    `state_group` is one of qbit-ops's canonical groups (`seeding`,
    `downloading`, `stalled`, `errored`, `checking`, `stopped`,
    `unknown`). `category` matches exactly, and is the axis the operator
    defines himself -- often what separates two torrents that look
    identical. Results are capped server-side; `matched` tells you how
    many exist beyond what was returned.
    """
    return tools.find_torrents(
        _client(),
        state_group=state_group,
        category=category,
        name_contains=name_contains,
        limit=limit,
    )


@server.tool()
def inspect_torrent(torrent_hash: str) -> dict[str, Any] | None:
    """Every field of one torrent, by full infohash."""
    return tools.inspect_torrent(_client(), torrent_hash)


@server.tool()
def explain_torrent(torrent_hash: str) -> dict[str, Any] | None:
    """Why this torrent is in its current state, with evidence.

    Uses qbit-ops's own diagnostic rules, so the answer matches what
    `qbit-ops explain torrent` would say.
    """
    return tools.explain(_client(), torrent_hash)


def main() -> None:
    """Run the server on stdio -- the transport an MCP host launches."""
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
