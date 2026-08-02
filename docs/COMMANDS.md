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
│   └── reannounce
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
