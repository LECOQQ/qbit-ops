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
- 🔁 **`status --watch`** — live-refreshing status in place (table) or as a
  flushed JSONL stream, on the same snapshot model, until `Ctrl+C`.
- 🖥️ **`tui`** *(optional `tui` extra)* — a read-only interactive terminal
  UI, Overview-first: it opens on an Overview workspace (connection,
  transfer rates, torrent counts, and grounded health findings, all
  from the same periodic refresh — no extra API call), with a second
  Torrents workspace (`t`/`2`) for browsing, filtering, and safe
  inspection (the same shared `TorrentFilter` vocabulary as `torrents
  list`, read-only name/hash search, structural tracker identities).
  Switching workspaces, filtering, and search are all local — zero
  qBittorrent calls. No mutation is reachable from the TUI. See
  [docs/COMMANDS.md](docs/COMMANDS.md#tui) for controls and
  [docs/TUI_ARCHITECTURE_REVIEW.md](docs/TUI_ARCHITECTURE_REVIEW.md) for
  the architecture and roadmap.
- 🩺 **`doctor`** — a bounded, read-only diagnostic report (configuration,
  connectivity, compatibility, runtime) with stable pass/warning/failure
  exit codes, in table/JSON/JSONL/CSV. Independent checks keep running
  after an unrelated one fails; failures never leak secrets and are never
  written to stderr — the report itself is the answer.
- 🔍 **Audit** torrents, categories, trackers and connectivity — one shared
  `--format table|json|jsonl|csv` across every read-only command.
- 🧠 **`explain torrent`/`explain tracker`** — a deterministic,
  evidence-based explanation of why a torrent or tracker looks the way
  it does: what's observed, why it's classified that way, the evidence
  behind it, known limitations, and safe commands to consider next.
  Read-only, no confidence scores, no generated prose beyond the fixed
  rule catalogue.
- 🧭 **Bulk torrent control** — pause, resume, start, reannounce, targeted
  by hash (complete or unique prefix), category, tracker, completed or all.
- 🔗 **Bulk tracker management** — add conditionally, remove, replace, or
  swap a tracker's passkey across every matching torrent.
- 💾 **Backup & diff** — export full instance state and compare two exports.
- 🧩 **Dynamic tracker matching** — ignore volatile query parameters (`sig`,
  `announce_ts`, ...) when comparing trackers.
- 🎨 **Rich terminal output** — colored tables and summaries, plus shell tab
  completion.
- ⏳ **Transient progress feedback** — a spinner or progress bar on
  interactive commands with a real scan or collection phase, fully
  cleared before the final result. Machine-readable output
  (`--format json|jsonl|csv`), non-interactive stderr, and `--quiet` stay
  completely silent.

## 🛡️ Safety Model

- `--dry-run` is **on by default** for every modifying command.
- Real changes require an explicit `--no-dry-run`.
- Every mutation is classified **low / medium / high** risk. Low-risk bulk
  torrent actions (`pause`, `resume`, `start`, `reannounce`) never prompt.
  Medium/high-risk tracker mutations (`add-if-present`, `remove`,
  `replace`, `replace-passkey`) prompt for confirmation on real,
  interactive execution — pre-approve unattended runs with `--yes`.
  `--yes` never implies `--no-dry-run`. Declining a prompt performs no
  mutation and exits `0`. See
  [docs/COMMANDS.md](docs/COMMANDS.md#mutation-risk--confirmation-policy)
  for the full policy.
- Confirmation prompts and `--verbose` output never show tracker query
  strings or passkeys — tracker identities are reduced to scheme + host,
  and `replace-passkey` never renders the old or new passkey anywhere.
- Bulk torrent actions require `--hash`, `--all`, or one or more shared
  filters (`--category`, `--state`, `--tracker`, `--completed`/
  `--incomplete`, `--active`/`--inactive`, `--stalled`, `--errored`) —
  never nothing at all, so a bulk mutation can never silently mean the
  whole seedbox. `--hash` and `--all` are always used alone. See
  [docs/COMMANDS.md](docs/COMMANDS.md#torrent-filters) for the full filter
  vocabulary, shared by `torrents list` and every bulk mutation command.
- **The infohash is the primary identifier for mutations.** `--hash` accepts
  a complete hash or an unambiguous prefix; an ambiguous prefix is rejected
  with the candidate list instead of guessing. Fuzzy name matching is
  read-only (`torrents inspect --name`) and is no longer accepted by
  `pause`, `resume`, `start`, or `reannounce` — see the migration note in
  [docs/COMMANDS.md](docs/COMMANDS.md#torrents).
- **`--tracker` on `torrents list`/bulk mutations matches by host[:port]
  only** — never the full announce URL, so a passkey embedded in a
  tracker's path or query string is never required or rendered by a
  filter.
- **No ordinary command displays a complete tracker announce URL or
  credential by default.** `trackers list`, `trackers status`, `trackers
  inspect`, and `trackers export` report only normalized `host[:port]`
  identities and structural detail (scheme, path *shape*, query
  parameter *names*) — never a passkey, query value, or userinfo — in
  table, JSON, JSONL, or CSV. Only the four bulk tracker mutation
  commands (`add-if-present`/`remove`/`replace`/`replace-passkey`) take
  and act on a raw tracker URL (`--match exact|without-query`), because
  qBittorrent's API requires the literal stored URL — and even they
  never echo it back in a prompt, preview, or summary. The one
  intentionally sensitive export is `backup export` (needed for a
  restorable backup); treat its output as a secret, and use `backup diff
  --reveal-sensitive` only when you explicitly need to see raw values.
  See [docs/COMMANDS.md](docs/COMMANDS.md#trackers) and
  [docs/PHILOSOPHY.md](docs/PHILOSOPHY.md) for the full contract.
- Credentials never live in `.env`-less environments or CLI arguments — only
  `.env` / environment variables.
- **Reporting a bug?** Replace any real tracker announce URL, passkey, or
  credential with a placeholder (`https://tracker.example/announce/
  REDACTED`) before pasting output into an issue, chat, or log — even
  though ordinary command output is safe by default, a raw `backup
  export` file or a copy-pasted `.env` is not.

## 📦 Install

Requires Python 3.12+, [Poetry](https://python-poetry.org), and a
qBittorrent instance with the Web UI/API enabled. Run `make doctor` to
check local tooling (Python/Poetry) — unrelated to `qbit-ops doctor`,
which diagnoses the qBittorrent connection itself once installed.

**Development:**

```bash
git clone https://github.com/LECOQQ/qbit-ops.git
cd qbit-ops
make install
poetry run qbit-ops --help
```

`make install` (and `make check`) install the optional `tui` extra by
default (`poetry install --extras tui`), since the test suite exercises
it. To install only the core CLI dependencies for development, without
Textual: `poetry install` (no `--extras`).

**As a regular command, with [pipx](https://pipx.pypa.io):**

```bash
git clone https://github.com/LECOQQ/qbit-ops.git
cd qbit-ops
pipx install .
qbit-ops --help
```

Update later with `pipx reinstall qbit-ops` (run from the repo).

**With the optional TUI** (`qbit-ops tui`, see
[docs/COMMANDS.md](docs/COMMANDS.md#tui)):

```bash
# local development (run from the cloned repo)
poetry install -E tui
poetry run qbit-ops tui

# a fresh pipx install (run from the cloned repo, like `pipx install .` above)
pipx install '.[tui]'

# an editable checkout, tracking the git clone instead of copying it
pipx install --editable '.[tui]'
```

**`qbit-ops` is not published on PyPI.** `pipx install "qbit-ops[tui]"`
(a bare package name, no path) always fails with "No matching
distribution found" — `pipx`/`pip` look that up on PyPI, where this
project does not exist. Every install form above is a **path**
(`.`/`'.[tui]'`), run from inside the cloned repository; there is no
package-name-only install for this project.

`qbit-ops tui` fails with an actionable message (no traceback) if run
without this extra installed — every other command works identically
either way, and none of them ever import Textual.

**Upgrading an install that did not originally include the extra does
not add it.** `pipx upgrade`/`pipx reinstall` reuse the package spec
recorded at install time, so a plain `pipx install .` install stays
without Textual even after upgrading. To add the TUI to an existing
install, uninstall and reinstall with the extra (from the cloned repo):

```bash
git clone https://github.com/LECOQQ/qbit-ops.git && cd qbit-ops
pipx uninstall qbit-ops
pipx install '.[tui]'
```

Quote `'.[tui]'` — an unquoted `[tui]` is glob-expanded by `zsh` and
some other shells.

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

qbit-ops status --watch                       # refresh in place every 5s until Ctrl+C
qbit-ops status --watch --interval 10

qbit-ops tui                                  # interactive: Overview dashboard, then Torrents (t/2) to browse/filter/search/inspect (requires the `tui` extra)
qbit-ops status --watch --format jsonl | jq .  # one flushed JSON object per line

qbit-ops connection check
qbit-ops doctor
qbit-ops doctor --format json; echo $?   # 0 pass, 1 warning, 2 failure

qbit-ops torrents list
qbit-ops torrents list --category sonarr --category radarr --state stalled

qbit-ops trackers list
qbit-ops trackers status; echo $?   # 0 healthy, 1 warning, 2 critical, 3 unavailable

qbit-ops torrents pause --category sonarr --dry-run --verbose

qbit-ops torrents inspect --name "debian"        # discover a hash
qbit-ops torrents reannounce --hash abc123 --dry-run   # act on it

qbit-ops explain torrent --hash abc123           # why does it look stalled/errored/...?
qbit-ops explain tracker --tracker tracker.example
```

👉 Full command reference, use cases and output formats:
**[docs/COMMANDS.md](docs/COMMANDS.md)**

👉 Error categories, local validation rules and exit-code contracts for
scripting: **[docs/ERRORS_AND_EXIT_CODES.md](docs/ERRORS_AND_EXIT_CODES.md)**

## 🧪 Development

```bash
make format   # format and autofix
make check    # lint, type-check, test
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow.

## 📄 License

MIT — see [LICENSE](LICENSE).
