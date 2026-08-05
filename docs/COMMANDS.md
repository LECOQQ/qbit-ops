# 🧰 Commands

Use the built-in help as the authoritative command reference:

```bash
qbit-ops --help
qbit-ops <group> --help
qbit-ops <group> <command> --help
```

## 🗂️ Overview

```text
qbit-ops
├── status [--watch]
├── doctor
├── tui
├── connection check
├── backup
│   ├── export
│   └── diff
├── torrents
│   ├── list
│   ├── categories
│   ├── inspect
│   ├── pause
│   ├── resume
│   ├── start
│   ├── reannounce
│   └── import
├── trackers
│   ├── list
│   ├── status
│   ├── inspect
│   ├── export
│   ├── add-if-present
│   ├── remove
│   ├── replace
│   └── replace-passkey
└── explain
    ├── torrent
    └── tracker
```

## 📜 Common recipes

```bash
qbit-ops status
qbit-ops doctor
qbit-ops torrents list --state stalled
qbit-ops torrents list --category sonarr --format json
qbit-ops explain torrent --hash abc123
qbit-ops trackers status
qbit-ops torrents pause --category sonarr
qbit-ops torrents pause --category sonarr --no-dry-run
qbit-ops torrents import ubuntu.torrent
qbit-ops torrents import ./torrents/ --recursive
qbit-ops torrents import archive.zip --category movies --save-path /downloads
qbit-ops torrents import archive.zip --no-dry-run --yes --start
```

## 📤 Output

Read commands support `table`, `json`, `jsonl`, and sometimes `csv`. Unsupported formats fail before contacting qBittorrent.

Machine-readable output contains only serialized data on stdout and no ANSI decoration.

## Format Support Matrix

| Command | table | json | jsonl | csv |
|---|---|---|---|---|
| `status` | ✅ | ✅ | ✅ | ✅ |
| `connection check` | ✅ | ✅ | ✅ | ✅ |
| `doctor` | ✅ | ✅ | ✅ | ✅ |
| `torrents list` | ✅ | ✅ | ✅ | ✅ |
| `torrents categories` | ✅ | ✅ | ✅ | ✅ |
| `torrents inspect` | ✅ | ✅ | ✅ | — |
| `torrents import` | ✅ | ✅ | — | — |
| `trackers list` | ✅ | ✅ | ✅ | ✅ |
| `trackers status` | ✅ | ✅ | ✅ | ✅ |
| `trackers inspect` | ✅ | ✅ | ✅ | ✅ |
| `trackers export` | ✅ | ✅ | ✅ | — |
| `backup export` | ✅ | ✅ | ✅ | — |
| `backup diff` | ✅ | ✅ | ✅ | — |
| `explain torrent` | ✅ | ✅ | ✅ | — |
| `explain tracker` | ✅ | ✅ | ✅ | — |

## ⚠️ Mutation rules

- 🧪 Mutations default to dry-run.
- ▶️ `--no-dry-run` requests real execution.
- ❓ Low-risk mutations apply without a prompt; medium/high-risk tracker mutations prompt in an interactive terminal.
- ⏭️ `--yes` skips that prompt but never enables real execution by itself.
- 🚫 Empty selections never mean “all”.

## 📥 Importing `.torrent` files

`torrents import SOURCE` adds `.torrent` files via the WebUI API only --
it never reads or writes qBittorrent's own filesystem. `SOURCE` is a
`.torrent` file, a directory of `.torrent` files, or a `.zip` archive of
`.torrent` files.

```bash
qbit-ops torrents import ubuntu.torrent
qbit-ops torrents import ./torrents/                # top-level files only
qbit-ops torrents import ./torrents/ --recursive     # descend into subdirectories
qbit-ops torrents import archive.zip
qbit-ops torrents import archive.zip --category movies --save-path /downloads --tag imported
qbit-ops torrents import archive.zip --no-dry-run --yes   # actually import
```

- 🧪 Dry-run by default: computes infohashes, classifies every entry, and
  prints a plan without contacting the add endpoint. Real import needs
  both `--no-dry-run` and (non-interactively) `--yes`, matching every
  other medium-risk mutation.
- ⏸️ Added **paused** by default; `--start` starts them immediately.
- 🔁 A torrent already present in qBittorrent, or a repeat infohash
  within the same source, is never re-sent -- `--skip-existing` only
  silences the "already present" notice, it does not change what gets
  imported.
- 🛡️ `.zip` sources are read directly from the archive, never extracted
  to disk. An archive is rejected outright (zero torrents imported) if
  it is encrypted, contains a path-traversal or symlink/special entry,
  or exceeds the entry-count, decompressed-size, compression-ratio, or
  path-depth limits (`qbit_core.features.torrent_import`). A malformed
  individual `.torrent` inside an otherwise-safe archive only
  invalidates that one entry. Only `.zip` is supported -- `tar`,
  `tar.gz`, RAR, and 7z are not.
- 🔑 A `.torrent` file's announce URL can carry a tracker passkey. Its
  torrent name may be shown; the announce URL itself is never printed
  by this command, in any format.
- Exit code is non-zero if nothing is importable, an API call fails, or
  any invalid file was found -- even in dry-run, so a broken input is
  visible before `--yes` is ever used.
