# 🧯 qbit-ops

<p align="center">
  <img src="https://raw.githubusercontent.com/LECOQQ/qbit-ops/main/docs/assets/qbit-ops-logo.png" alt="qbit-ops logo" width="200">
</p>

<p align="center">
  <a href="https://github.com/LECOQQ/qbit-ops/actions/workflows/ci.yml"><img src="https://github.com/LECOQQ/qbit-ops/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/LECOQQ/qbit-ops/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://github.com/LECOQQ/qbit-ops/blob/main/pyproject.toml"><img src="https://img.shields.io/badge/python-3.12-blue.svg" alt="Python"></a>
  <a href="https://pypi.org/project/qbit-ops/"><img src="https://img.shields.io/pypi/v/qbit-ops.svg" alt="PyPI version"></a>
  <a href="https://pypi.org/project/qbit-ops/"><img src="https://img.shields.io/pypi/dm/qbit-ops.svg" alt="PyPI downloads"></a>
  <a href="https://github.com/LECOQQ/homebrew-qbit-ops"><img src="https://img.shields.io/badge/Homebrew-brew%20install-FBB040?logo=homebrew&logoColor=white" alt="Homebrew tap"></a>
  <a href="https://github.com/LECOQQ/homebrew-qbit-ops/actions/workflows/lint.yml"><img src="https://img.shields.io/github/actions/workflow/status/LECOQQ/homebrew-qbit-ops/lint.yml?label=tap&logo=homebrew&logoColor=white" alt="Tap lint"></a>
  <a href="https://github.com/LECOQQ/qbit-ops/pkgs/container/qbit-ops"><img src="https://img.shields.io/badge/ghcr.io-amd64%20%7C%20arm64-2496ED?logo=docker&logoColor=white" alt="Container image"></a>
</p>

🧯 **A tiny qBittorrent CLI and TUI for people who don't want to nuke their
seedbox by accident.**

Inspect, diagnose and automate qBittorrent at scale - with composable filters, bulk operations, and dry-run safeguards.

> ✨ Featured in [Self-Host Weekly](https://selfh.st/weekly/2026-08-07/) by selfh.st.

[![qbit-ops TUI demo](https://raw.githubusercontent.com/LECOQQ/qbit-ops/main/docs/assets/qbit-ops-demo-poster.webp)](https://github.com/LECOQQ/qbit-ops/blob/main/docs/assets/qbit-ops-demo.gif)

<p align="center"><em>▶ Click for the animated demo.</em></p>

## 🚀 Get started

Requires Python 3.12+ and a qBittorrent instance with the Web UI enabled.

### 🐍 From PyPI

Recommended, with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install qbit-ops
```

Or with pipx:

```bash
pipx install qbit-ops
```

With the optional TUI:

```bash
uv tool install "qbit-ops[tui]"
pipx install "qbit-ops[tui]"
```

With the experimental MCP server, to talk to your library through an
agent:

```bash
uv tool install "qbit-ops[mcp]"
pipx install "qbit-ops[mcp]"
```

### 🍺 With Homebrew

```bash
brew install LECOQQ/qbit-ops/qbit-ops
```

The tap ships the TUI, so there is no extra to pick. Upgrades follow the
usual `brew upgrade qbit-ops`.

### 🐳 With Docker

Runs the same CLI on amd64 and arm64, with nothing installed on the
host:

```bash
docker run --rm -it \
  -e QBIT_HOST -e QBIT_USER -e QBIT_PASSWORD \
  ghcr.io/lecoqq/qbit-ops:latest status
```

The TUI needs a TTY, which `-it` already gives you:

```bash
docker run --rm -it \
  -e QBIT_HOST -e QBIT_USER -e QBIT_PASSWORD \
  ghcr.io/lecoqq/qbit-ops:latest tui
```

For a long-lived setup, see the
[Compose example](https://github.com/LECOQQ/qbit-ops/blob/main/docs/examples/docker-compose.yml).

### ⬆️ Upgrading

```bash
uv tool upgrade qbit-ops
pipx upgrade qbit-ops
brew upgrade qbit-ops
```

### 🔌 Connecting

Then set up the connection:

```bash
qbit-ops init
```

It asks, tests, and remembers. `qbit-ops tui` offers the same form when
nothing is configured yet.

Then:

```bash
qbit-ops status
qbit-ops doctor
qbit-ops tui
```


## 🎸 Greatest hits

**Reclaim disk without touching what matters.**

```bash
qbit-ops torrents delete --ratio-min 2 --seeded-for 90d --exclude-tag keep
```

```text
scanned   2418
matched   407
status    PREVIEW (dry-run)
```

Nothing is deleted: review it, add `--no-dry-run`, and *that exact
selection* is what will run.

**Find out which tracker is hurting you.**

```bash
qbit-ops trackers status
qbit-ops explain tracker --tracker tracker.example
```

One line per tracker, health aggregated across every torrent that
announces to it, and then the reasoning behind the verdict.

**Act on every torrent stuck behind a dead tracker.**

```bash
qbit-ops torrents list --tracker-health critical
qbit-ops torrents pause --tracker-health critical --no-dry-run
```

Selects on *each torrent's own* trackers, and gives the same verdict
`explain torrent` does - it is the same computation. How a torrent with
mixed tracker health is scored, and why a non-answer never matches, is
in [docs/COMMANDS.md](https://github.com/LECOQQ/qbit-ops/blob/main/docs/COMMANDS.md).

**See what each tracker is actually carrying.**

```bash
qbit-ops trackers list
qbit-ops trackers list --category sonarr
```

Torrents, size, transferred bytes, ratio and seeding time per tracker,
plus `EXCL` - the torrents that tracker is the *only* home of, which is
the real answer to "what would I lose by leaving?". The columns
deliberately do not sum to your library total; the reason is in
[docs/COMMANDS.md](https://github.com/LECOQQ/qbit-ops/blob/main/docs/COMMANDS.md).

**Know what your library actually weighs.**

```bash
qbit-ops torrents stats
qbit-ops torrents stats --category sonarr
```

Size, transfer and seeding time over exactly the torrents you filter
for. How qBittorrent's all-time counters are handled is in
[docs/COMMANDS.md](https://github.com/LECOQQ/qbit-ops/blob/main/docs/COMMANDS.md).

**Find a torrent without remembering its exact name.**

```bash
qbit-ops torrents search "amour est dnas le pre"   # typos, accents, word order
qbit-ops torrents pause --hash 3f2a1b               # copy the hash, then act
```

Ranked, tolerant, and read-only by construction: a search result is
never a mutation target. `--name-contains`/`--name-regex` stay the
exact, deterministic option for that.

**Unstick a queue that stopped moving.**

```bash
qbit-ops torrents list --stalled
qbit-ops torrents reannounce --stalled --no-dry-run
```

**Rotate a leaked passkey everywhere at once.**

```bash
qbit-ops trackers replace-passkey \
  --tracker "https://tracker.example/announce/{passkey}" \
  --new-passkey "$NEW_PASSKEY"
```

`{passkey}` is a template: it never prints anything that can harm you.

## 🛡️ Safety by default

- 🧪 **Dry-run first.** Nothing changes unless `--no-dry-run` is explicit.
- 🎯 **Hash-based targeting.** Mutations never guess from a fuzzy torrent name.
- 🧊 **Frozen plans.** The previewed selection is the selection that gets applied.
- 🚫 **No silent “all”.** Bulk actions require a hash, `--all`, or an explicit filter.
- ❔ **Unknown is never a match.** A value qBittorrent didn't report never widens a selection.
- 🔒 **Secret-safe output.** Tracker passkeys and announce URLs stay redacted in normal output.
- ✅ **Honest results.** qbit-ops reports what was submitted or observed, not what it cannot prove.

## 🎯 Target exactly what you mean

Filters compose - repeat one for **or**, mix different ones for **and**,
exclude with `--exclude-*`. Category, tag, save path, name, state, size,
ratio, progress, age, tracker, tracker health and more: the same
selector everywhere, listing or mutating.

Full grammar in [docs/COMMANDS.md](https://github.com/LECOQQ/qbit-ops/blob/main/docs/COMMANDS.md).

## 🔍 Watch, explain, script

```bash
qbit-ops status --watch
qbit-ops explain torrent --hash abc123
```

`explain` answers *why* a torrent or tracker is in the state it is, from
observed evidence - never a guess.

Read commands support machine-friendly output where it makes sense:

```bash
qbit-ops torrents list --format json
qbit-ops torrents stats --format json
qbit-ops status --format jsonl
```

## 🧭 How is `qbit-ops` different?

| | [qbit-ops](https://github.com/LECOQQ/qbit-ops) | [qbittorrent-cli](https://github.com/ludviglundgren/qbittorrent-cli) | [qbit_manage](https://github.com/StuffAnThings/qbit_manage) | [qbittools](https://gitlab.com/AlexKM/qbittools) |
| --- | --- | --- | --- | --- |
| Model | Safe operational toolkit | General-purpose qBittorrent CLI | Rules / background management | Task-oriented utilities |
| Interface | CLI + TUI | CLI | Config + scheduler + Web UI | CLI |
| Targeting | Composable selectors | Command / torrent specific | Rule / workflow specific | Command specific |
| Execution | SELECT → INSPECT → PLAN → APPLY | Direct qBittorrent operations | Apply configured rules | Run specialized commands |
| Safety | Dry-run-first, explicit apply | Command-dependent | Rule-driven automation | Command-dependent |
| Automation | Structured JSON + stable exit behavior | CLI / scripting | Scheduled workflows | CLI / scripting |
| Best fit | Safe bulk ops, inspection & scripting | General qBittorrent control from a terminal | Continuous library management | Specialized maintenance tasks |


> `qbit-ops` is deliberately not a daemon or background rules engine: select precisely, inspect the scope, preview the plan, then apply it.

See [PHILOSOPHY.md](https://github.com/LECOQQ/qbit-ops/blob/main/PHILOSOPHY.md) for the reasoning behind that design.

## 🖥️ TUI

The optional Textual interface provides an operational overview, torrent browsing, filters, details, explanations, and previewed low-risk bulk actions.

The overview answers "what is this machine doing" without reading a
number: a live trace of transfer in both directions, per-tracker
activity, and the instance's own counters.

```bash
qbit-ops tui
```

The trace samples once a second, one column per second, and only while
the overview is on screen -- it costs nothing on the torrents page.
Its axis label states the window it actually shows, which is as many
seconds as the panel is columns wide.

Window titles are set in Unicode small capitals. Those letters live in
three unrelated Unicode blocks, and a font covering only some of them
renders the rest at a different size. Run `qbit-ops doctor` to see what
a font needs; `qbit-ops tui --ascii-titles`, or `QBIT_OPS_ASCII_TITLES=1`,
renders plain capitals instead.

| Overview | Torrents |
|---|---|
| ![Overview workspace](https://raw.githubusercontent.com/LECOQQ/qbit-ops/main/docs/assets/overview.webp) | ![Torrent table](https://raw.githubusercontent.com/LECOQQ/qbit-ops/main/docs/assets/torrents.webp) |

| Search | Preview before Apply |
|---|---|
| ![Search results](https://raw.githubusercontent.com/LECOQQ/qbit-ops/main/docs/assets/torrent-details.webp) | ![Frozen bulk-action preview](https://raw.githubusercontent.com/LECOQQ/qbit-ops/main/docs/assets/action-preview.webp) |

Press `?` inside the TUI to see the available controls.

## 🤖 Agent-ready (experimental)

An MCP server exposes a **read-only, bounded** view of your library, so
an agent can explore it conversationally:

> *"What's the state of my library?"* -> *"Which torrents look
> stalled?"* -> *"Why is this one stalled?"*

```bash
uv tool install "qbit-ops[mcp]"
```

Then point an MCP host at the `stdio` entry point -- for Claude Desktop,
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

It reads the same configuration the CLI does, so if `qbit-ops status`
works, this works.

**Five tools, no mutation.** `library_summary`, `find_torrents`,
`aggregate_stats`, `inspect_torrent`, `explain_torrent` -- nothing that
pauses, deletes or edits anything. Answers stay small whatever the library size, so a
large one is explored by drilling down rather than dumped whole.

> **Experimental and personal.** This is a spike, not a supported
> surface: it may be extended, or removed. See
> [docs/MCP.md](https://github.com/LECOQQ/qbit-ops/blob/main/docs/MCP.md).

## 🧩 Compatibility

Container integration is tested against a fixed set of exact
qBittorrent releases -- evidence for those versions, never a claim for a
range. The matrix is in
[docs/COMPATIBILITY.md](https://github.com/LECOQQ/qbit-ops/blob/main/docs/COMPATIBILITY.md).

Run `qbit-ops doctor` to compare your instance with the packaged
evidence.

## 🧰 Commands

```bash
qbit-ops --help
qbit-ops --version
qbit-ops version
qbit-ops torrents --help
qbit-ops trackers --help
```

A compact command overview is available in [docs/COMMANDS.md](https://github.com/LECOQQ/qbit-ops/blob/main/docs/COMMANDS.md).

## 🗺️ Roadmap

See [ROADMAP.md](https://github.com/LECOQQ/qbit-ops/blob/main/ROADMAP.md) for where the project is heading.

## 🧑‍💻 Development

```bash
git clone https://github.com/LECOQQ/qbit-ops.git
cd qbit-ops
make install
make check-fast
make check
```

See [CONTRIBUTING.md](https://github.com/LECOQQ/qbit-ops/blob/main/CONTRIBUTING.md).

## 📄 License

MIT - see [LICENSE](https://github.com/LECOQQ/qbit-ops/blob/main/LICENSE).
