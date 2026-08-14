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
</p>

🧯 **A tiny qBittorrent CLI and TUI for people who don't want to nuke their
seedbox by accident.**

Inspect, diagnose and automate qBittorrent at scale - with composable filters, bulk operations, and dry-run safeguards.

> ✨ Featured in [Self-Host Weekly](https://selfh.st/weekly/2026-08-07/) by selfh.st.

[![qbit-ops TUI demo](https://raw.githubusercontent.com/LECOQQ/qbit-ops/main/docs/assets/qbit-ops-demo-poster.webp)](https://github.com/LECOQQ/qbit-ops/blob/main/docs/assets/qbit-ops-demo.gif)

<p align="center"><em>▶ Click for the animated demo.</em></p>

## 🚀 Get started

Requires Python 3.12+ and a qBittorrent instance with the Web UI enabled.

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

To upgrade later:

```bash
uv tool upgrade qbit-ops
pipx upgrade qbit-ops
```

Create `~/.config/qbit-ops/.env`:

```env
QBIT_HOST=http://localhost:8080
QBIT_USER=admin
QBIT_PASSWORD=change-me
```

Then:

```bash
qbit-ops status
qbit-ops doctor
qbit-ops tui
```

🐳 Prefer containers? `ghcr.io/lecoqq/qbit-ops:latest` runs the same CLI on
amd64 and arm64 — see the
[Compose example](https://github.com/LECOQQ/qbit-ops/blob/main/docs/examples/docker-compose.yml).

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

**Know what your library actually weighs.**

```bash
qbit-ops torrents stats
qbit-ops torrents stats --category sonarr
```

Size, transfer and seeding time over exactly the torrents you filter
for. Without a filter it also shows qBittorrent's all-time counters -
with one, it doesn't, so a filtered total is never sat next to a global
one.

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
ratio, progress, age, tracker and more: the same selector everywhere,
listing or mutating.

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

The optional Textual interface provides a branded overview, torrent browsing, filters, details, explanations, and previewed low-risk bulk actions.

```bash
qbit-ops tui
```

| Overview | Torrents |
|---|---|
| ![Overview workspace](https://raw.githubusercontent.com/LECOQQ/qbit-ops/main/docs/assets/overview.webp) | ![Torrent table](https://raw.githubusercontent.com/LECOQQ/qbit-ops/main/docs/assets/torrents.webp) |

| Search | Preview before Apply |
|---|---|
| ![Search results](https://raw.githubusercontent.com/LECOQQ/qbit-ops/main/docs/assets/torrent-details.webp) | ![Frozen bulk-action preview](https://raw.githubusercontent.com/LECOQQ/qbit-ops/main/docs/assets/action-preview.webp) |

Press `?` inside the TUI to see the available controls.

## 🧩 Compatibility

Container integration is tested against these exact qBittorrent releases:

| qBittorrent | Web API |
|---|---:|
| 4.6.7 | 2.9.3 |
| 5.0.0 | 2.11.2 |
| 5.1.4 | 2.11.4 |
| 5.2.3 | 2.15.1 |

This is evidence for those exact versions, not a claim for the whole 4.6–5.2 range. Run `qbit-ops doctor` to compare your instance with the packaged evidence.

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
