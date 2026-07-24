# 🧯 qbit-ops

[![CI](https://github.com/LECOQQ/qbit-ops/actions/workflows/ci.yml/badge.svg)](https://github.com/LECOQQ/qbit-ops/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](pyproject.toml)
[![Version](https://img.shields.io/github/v/release/LECOQQ/qbit-ops?label=version&display_name=tag)](CHANGELOG.md)

**A tiny qBittorrent CLI for people who don't want to nuke their seedbox by
accident.**

Every bulk action previews before it runs. Nothing is destructive unless you
explicitly say so.

```bash
qbit-ops trackers replace-passkey \
  --tracker "https://tracker.example/announce/{passkey}" \
  --new-passkey "NEW_PASSKEY"
# Summary:
# - scanned: 128
# - matched_source: 40
# - modified: 40
# - dry_run: true   <- nothing changed yet
```

## ✨ Features

- 📊 **`status`** — a bounded, read-only operational snapshot (connection,
  transfer counts, alerts) with stable health-based exit codes, in
  table/JSON/JSONL/CSV.
- 🔍 **Audit** torrents, categories, trackers and connectivity — one shared
  `--format table|json|jsonl|csv` across every read-only command.
- 🧭 **Bulk torrent control** — pause, resume, start, reannounce, targeted
  by hash (complete or unique prefix), category, tracker, completed or all.
- 🔗 **Bulk tracker management** — add conditionally, remove, replace, or
  swap a tracker's passkey across every matching torrent.
- 💾 **Backup & diff** — export full instance state and compare two exports.
- 🧩 **Dynamic tracker matching** — ignore volatile query parameters (`sig`,
  `announce_ts`, ...) when comparing trackers.
- 🎨 **Rich terminal output** — colored tables and summaries, plus shell tab
  completion.

## 🛡️ Safety Model

- `--dry-run` is **on by default** for every modifying command.
- Real changes require an explicit `--no-dry-run`.
- Bulk torrent actions require exactly one targeting mode (`--hash`,
  `--category`, `--tracker`, `--all`, or `--completed`).
- **The infohash is the primary identifier for mutations.** `--hash` accepts
  a complete hash or an unambiguous prefix; an ambiguous prefix is rejected
  with the candidate list instead of guessing. Fuzzy name matching is
  read-only (`torrents inspect --name`) and is no longer accepted by
  `pause`, `resume`, `start`, or `reannounce` — see the migration note in
  [docs/COMMANDS.md](docs/COMMANDS.md#torrents).
- Tracker URLs are normalized for comparison but raw URLs are preserved for
  API calls.
- Credentials never live in `.env`-less environments or CLI arguments — only
  `.env` / environment variables.

## 📦 Install

Requires Python 3.12+, [Poetry](https://python-poetry.org), and a
qBittorrent instance with the Web UI/API enabled. Run `make doctor` to
check local tooling.

**Development:**

```bash
git clone https://github.com/LECOQQ/qbit-ops.git
cd qbit-ops
make install
poetry run qbit-ops --help
```

**As a regular command, with [pipx](https://pipx.pypa.io):**

```bash
git clone https://github.com/LECOQQ/qbit-ops.git
cd qbit-ops
pipx install .
qbit-ops --help
```

Update later with `pipx reinstall qbit-ops` (run from the repo).

Enable tab-completion for your shell (bash/zsh/fish):

```bash
qbit-ops --install-completion
```

## ⚙️ Configuration

```bash
cp .env.example .env
```

```env
QBIT_HOST=http://localhost:8080
QBIT_USER=admin
QBIT_PASSWORD=change-me
```

`.env` is git-ignored. For a `pipx` install, prefer a user-level config file:

```bash
mkdir -p ~/.config/qbit-ops
cp .env.example ~/.config/qbit-ops/.env
```

Lookup order: existing environment variables → `$QBIT_OPS_ENV_FILE` → `.env`
in the working directory → `~/.config/qbit-ops/.env`.

## 🚀 Quickstart

```bash
qbit-ops status
# qbit-ops · healthy
#
# qBittorrent
#   Version       5.0.1
#   API           2.9.3
#   Connected     yes
# ...
qbit-ops status --quiet; echo $?   # healthchecks: 0 healthy, 1 warning, 2 critical, 3 unavailable

qbit-ops connection check
qbit-ops config doctor

qbit-ops torrents list
qbit-ops torrents list --category sonarr

qbit-ops trackers list
qbit-ops trackers health

qbit-ops torrents pause --category sonarr --dry-run --verbose

qbit-ops torrents inspect --name "debian"        # discover a hash
qbit-ops torrents reannounce --hash abc123 --dry-run   # act on it
```

👉 Full command reference, use cases and output formats:
**[docs/COMMANDS.md](docs/COMMANDS.md)**

## 🧪 Development

```bash
make format   # format and autofix
make check    # lint, type-check, test
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow.

## 📄 License

MIT — see [LICENSE](LICENSE).
