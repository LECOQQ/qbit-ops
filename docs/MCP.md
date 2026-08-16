# 🤖 MCP (experimental)

> **MCP dogfood status: experimental / personal.**
> Not a supported surface. It may be extended, or deleted, depending on
> whether it turns out to be used.

An MCP server exposing a **read-only, bounded** view of a qBittorrent
library, for talking to it through an agent.

## 🚫 What it is not

It is not the qbit-ops CLI over MCP. The CLI exposes dozens of filter
options on `torrents list` alone; this has four tools, and a fraction of
what they can do. That is the design, not a gap to close.

The reason: an MCP tool result cannot be piped through `jq` or `head` --
it lands in the model's context whole. Listing a large library would
spend the agent's whole budget before it has a question. So the surface
is built around drill-down:

    library_summary  ->  find_torrents  ->  inspect_torrent
                                        ->  explain_torrent

Each step returns enough to choose the next one.

## 🧰 Tools

| Tool | Answers |
|---|---|
| `library_summary` | counts, health and alerts, in fixed size |
| `find_torrents` | by state group, category and/or name, one page at a time |
| `aggregate_stats` | totals over a selection, so nobody adds bytes by hand |
| `inspect_torrent` | every field of one torrent, by hash |
| `explain_torrent` | why it is in that state, with its evidence and limitations |

**No tool mutates**, and none is planned here. `pause`, `delete`,
tracker edits and imports are absent by construction rather than
guarded: what a mutation means without a human at the keyboard is a
design question this spike deliberately does not answer.

The cap is applied server-side: asking for 5000 torrents returns a page
of 50, and `next_offset` says where to resume. Every match stays
reachable -- depth is paid in calls, never in context.

## 🚀 Install and run

The server uses the official
[Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk),
declared as an optional extra so a normal install never pulls it in:

```bash
uv tool install "qbit-ops[mcp]"
pipx install "qbit-ops[mcp]"
```

From a clone, `poetry install -E mcp`.

Then point an MCP host at the `stdio` entry point. For Claude Desktop,
in its config file:

```json
{
  "mcpServers": {
    "qbit-ops": {
      "command": "qbit-ops-mcp"
    }
  }
}
```

It reads the same configuration the CLI does (`.env`, or
`QBIT_HOST`/`QBIT_USER`/`QBIT_PASSWORD`), so if `qbit-ops status` works,
this works.

## 🧹 Removing it

`rm -rf src/qbit_ops/mcp tests/test_mcp_tools.py docs/MCP.md`, drop the
`mcp` extra from `pyproject.toml`, and the R7 rule from
`tests/test_layering.py`. Nothing else references it: no domain logic
lives here, and `qbit_core` does not know it exists.
