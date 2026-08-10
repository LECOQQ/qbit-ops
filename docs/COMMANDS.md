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
│   ├── diff
│   └── restore
├── torrents
│   ├── list
│   ├── categories
│   ├── inspect
│   ├── pause
│   ├── resume
│   ├── start
│   ├── reannounce
│   ├── delete
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
qbit-ops trackers add-if-present --source old.example --target new.example --category sonarr
qbit-ops torrents pause --category sonarr
qbit-ops torrents pause --category sonarr --no-dry-run
qbit-ops torrents import ubuntu.torrent
qbit-ops torrents import ./torrents/ --recursive
qbit-ops torrents import archive.zip --category movies --save-path /downloads
qbit-ops torrents import archive.zip --no-dry-run --yes --start
qbit-ops backup export --format json > export.json
qbit-ops backup restore export.json --no-dry-run --yes
qbit-ops torrents delete --category radarr-old --no-dry-run --yes
qbit-ops torrents delete --hash abc123 --with-data --no-dry-run --yes
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
| `backup restore` | ✅ | ✅ | — | — |
| `explain torrent` | ✅ | ✅ | ✅ | — |
| `explain tracker` | ✅ | ✅ | ✅ | — |

## 🎯 Selecting torrents

Every command that acts on torrents answers the same question the same
way: **which torrents, and what do we know about them?**

```text
SELECT ──► INSPECT ──► PLAN ──► APPLY
```

- **SELECT** picks torrents from one listing call, using the filters
  below. Cheap.
- **INSPECT** looks up each selected torrent's trackers. One call per
  torrent, so it only ever runs on what SELECT already narrowed down.
- **PLAN** works out what would change. Read-only.
- **APPLY** executes exactly that plan -- it never re-scans, so the
  preview you confirmed is what runs.

### Filters

**Organisation**

| Filter | Meaning |
| --- | --- |
| `--category NAME` | Repeatable. Use `uncategorized` for torrents with no category. |
| `--tag NAME` | Repeatable. Has **any** of these tags. Ignores case. |
| `--tag-all NAME` | Repeatable. Has **every** one of these tags. |
| `--save-path PATH` | Repeatable. Saved at this path or below it. Case-sensitive. |
| `--name-contains TEXT` | Repeatable. Name contains this text. Ignores case. |
| `--name-regex PATTERN` | Name matches this regular expression (searched, not anchored; case-sensitive — use `(?i)`). |

**State**

| Filter | Meaning |
| --- | --- |
| `--state GROUP` | Repeatable. `downloading`, `seeding`, `checking`, `stalled`, `errored`, `unknown`. |
| `--completed` / `--incomplete` | Download finished or not. |
| `--active` / `--inactive` | **Not stopped** / stopped. Note: `--active` is about run state, not traffic — a stalled seed at 0 B/s is active. |
| `--stalled`, `--errored` | Shorthands for the matching state group. |
| `--private` / `--public` | Private or public torrent. Needs qBittorrent 5.0+; on older versions nothing matches. |

**Measures** — each pair is an inclusive range.

| Filter | Value |
| --- | --- |
| `--size-min` / `--size-max` | `1024`, `500MB`, `1.5TiB` |
| `--ratio-min` / `--ratio-max` | `1`, `2.5` |
| `--progress-min` / `--progress-max` | `95%` or a `0`–`1` fraction like `0.95` |
| `--uploaded-min` / `--uploaded-max` | same size syntax |
| `--seeded-for` | `30d` — seeded for **at least** this long |
| `--older-than` / `--newer-than` | `90d`, `12h` — based on when the torrent was added |

**Trackers**

| Filter | Meaning |
| --- | --- |
| `--tracker HOST` | Repeatable. Announces to one of these trackers, matched on `host[:port]`. |
| `--no-tracker` | Has no active tracker. |

**Exclusions** — every family has one: `--exclude-category`,
`--exclude-tag`, `--exclude-state`, `--exclude-save-path`,
`--exclude-name`, `--exclude-tracker`. All repeatable.

### Combining

- Repeating one filter means **OR**.
- Different filters mean **AND**.
- Exclusions are applied last.

So `--category a --category b --stalled --exclude-tag keep` reads as
*(a or b) and stalled, except anything tagged keep*.

```bash
# every torrent over 20 GiB added more than 6 months ago
qbit-ops torrents list --size-min 20GiB --older-than 180d

# well-seeded and idle, except what you marked to keep
qbit-ops torrents list --ratio-min 2 --seeded-for 90d --exclude-tag keep

# almost done
qbit-ops torrents list --incomplete --progress-min 99%
```

### Units, without ambiguity

- **Sizes**: the suffix decides. `KiB/MiB/GiB/TiB` are 1024-based,
  `KB/MB/GB/TB` are 1000-based. `500M` is refused — it belongs to
  neither.
- **Durations**: `s`, `m`, `h`, `d`, `w`. One unit per value (`1d12h` is
  refused). No months or years: they have no fixed length, so write
  `365d`.
- **Percentages**: `95%`, or a bare fraction between `0` and `1`. A bare
  `95` is refused, since it could mean either.

### Two selectors that stand apart

Neither combines with a filter, or with each other:

- `--hash` targets one torrent by full infohash or an unambiguous
  prefix. An ambiguous prefix is refused and lists the candidates.
- `--all` acts on the whole instance, and has to be typed out.

### Cost

One listing call, then the cheap filters, and only then — if you asked
for `--tracker` or `--exclude-tracker` — one tracker lookup per
surviving torrent. Filtering first is what keeps that count
proportional to your selection instead of your whole instance.
`--no-tracker` needs no lookup at all.

`trackers add-if-present` accepts the same filters to scope its scan.
It has no `--tracker` filter: `--source` already names the tracker it
looks for.

## ⚠️ Mutation rules

- 🧪 Mutations default to dry-run.
- ▶️ `--no-dry-run` requests real execution.
- ❓ Low-risk mutations apply without a prompt; medium/high-risk mutations (tracker changes, `torrents delete`) prompt in an interactive terminal.
- ⏭️ `--yes` skips that prompt but never enables real execution by itself.
- 🚫 Empty selections never mean “all”.

## 🗑️ Deleting torrents

`torrents delete` is HIGH risk and irreversible: unlike
`pause`/`resume`/`start`/`reannounce`, every matched torrent is always
a change -- there is no "already satisfied" state to skip.

```bash
qbit-ops torrents delete --category radarr-old            # dry-run
qbit-ops torrents delete --category radarr-old --no-dry-run --yes
qbit-ops torrents delete --hash abc123 --with-data --no-dry-run --yes
```

- 🧪 Dry-run by default. Real execution needs `--no-dry-run` and
  (non-interactively) `--yes`.
- 💾 **Downloaded data is kept by default.** `--with-data` also deletes
  the files from disk -- without it, only the torrent entry is removed
  from qBittorrent.
- ❓ Interactive confirmation states plainly whether data will be kept
  or deleted, so the two outcomes are never confused at the prompt.
- Selection follows the same `--hash`/filters/`--all` rules as every
  other bulk torrent command -- no selector ever silently means "all".

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

## 📦 Restoring from a `backup export`

`backup restore EXPORT_FILE` additively replays category/tags/trackers
from a previous `backup export` onto torrents **already present** in
the target instance, matched by hash. It never adds a torrent absent
locally -- use `torrents import` for that.

```bash
qbit-ops backup export --format json > export.json
qbit-ops backup restore export.json                        # dry-run
qbit-ops backup restore export.json --no-dry-run --yes
```

- 🧪 Dry-run by default; real execution needs `--no-dry-run` and
  (non-interactively) `--yes`, like every other medium-risk mutation.
- ➕ **Additive only**: an existing category is never overwritten (only
  filled in when currently blank), and an existing tag or tracker is
  never removed -- only missing ones are added.
- 🕳️ A field absent from an older export (e.g. `tags`, added after
  `backup export` first shipped) means "not captured", never "clear
  this field" -- it produces zero changes, not a deletion.
- 🚫 Never creates a torrent: an export entry with no matching hash
  locally is reported as `unmatched`, informational only.
- 🔑 Restored trackers can carry a passkey; only a count is ever shown
  in `--verbose` output or any format, never the raw URL.
- Exit code is non-zero if no export entry matches a local torrent, or
  any restore action (category creation, category/tag/tracker
  assignment) fails via the API.
