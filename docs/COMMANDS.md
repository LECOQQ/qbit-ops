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
├── version
├── tui
├── connection check
├── backup
│   ├── export
│   ├── diff
│   └── restore
├── torrents
│   ├── list
│   ├── stats
│   ├── categories
│   ├── inspect
│   ├── search
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
qbit-ops version
qbit-ops torrents list --state stalled
qbit-ops torrents list --category sonarr --format json
qbit-ops torrents stats
qbit-ops torrents stats --category sonarr --format json
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
| `version` | ✅ | ✅ | ✅ | -- |
| `torrents list` | ✅ | ✅ | ✅ | ✅ |
| `torrents stats` | ✅ | ✅ | ✅ | ✅ |
| `torrents categories` | ✅ | ✅ | ✅ | ✅ |
| `torrents inspect` | ✅ | ✅ | ✅ | -- |
| `torrents search` | ✅ | ✅ | ✅ | ✅ |
| `torrents import` | ✅ | ✅ | -- | -- |
| `trackers list` | ✅ | ✅ | ✅ | ✅ |
| `trackers status` | ✅ | ✅ | ✅ | ✅ |
| `trackers inspect` | ✅ | ✅ | ✅ | ✅ |
| `trackers export` | ✅ | ✅ | ✅ | -- |
| `backup export` | ✅ | ✅ | ✅ | -- |
| `backup diff` | ✅ | ✅ | ✅ | -- |
| `backup restore` | ✅ | ✅ | -- | -- |
| `explain torrent` | ✅ | ✅ | ✅ | -- |
| `explain tracker` | ✅ | ✅ | ✅ | -- |

## 🏷️ Versions

Two deliberately separate uses.

```bash
qbit-ops --version              # qbit-ops 0.4.0
qbit-ops version                # the four useful versions
qbit-ops version --format json
```

- 🔌 `--version` is purely local: no configuration is loaded, no
  connection is made, and it never depends on qBittorrent.
- 🧾 `version` adds the instance's versions -- Python, qBittorrent and
  Web API -- at the cost of one call per remote version. Versions are
  reported exactly as the instance returns them.
- 📡 An unreachable instance (invalid configuration, connection or
  authentication failure) is not an error here: local versions stay
  reported, remote ones become `unavailable` (table) or `null`
  (json/jsonl), stderr stays empty, and the exit code stays `0`. Use
  `doctor` to diagnose the instance itself.
- 💥 An unexpected error stays visible: it exits `70`, never disguised
  as `unavailable`.

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
| `--name-regex PATTERN` | Name matches this regular expression (searched, not anchored; case-sensitive -- use `(?i)`). |

**State**

| Filter | Meaning |
| --- | --- |
| `--state GROUP` | Repeatable. `downloading`, `seeding`, `checking`, `stalled`, `errored`, `unknown`. |
| `--completed` / `--incomplete` | Download finished or not. |
| `--active` / `--inactive` | **Not stopped** / stopped. Note: `--active` is about run state, not traffic -- a stalled seed at 0 B/s is active. |
| `--stalled`, `--errored` | Shorthands for the matching state group. |
| `--private` / `--public` | Private or public torrent. Needs qBittorrent 5.0+; on older versions nothing matches. |

**Measures** -- each pair is an inclusive range.

| Filter | Value |
| --- | --- |
| `--size-min` / `--size-max` | `1024`, `500MB`, `1.5TiB` |
| `--ratio-min` / `--ratio-max` | `1`, `2.5` |
| `--progress-min` / `--progress-max` | `95%` or a `0`–`1` fraction like `0.95` |
| `--uploaded-min` / `--uploaded-max` | same size syntax |
| `--seeded-for` | `30d` -- seeded for **at least** this long |
| `--older-than` / `--newer-than` | `90d`, `12h` -- based on when the torrent was added |
| `--inactive-for` / `--active-within` | `90d`, `24h` -- based on the last byte transferred |
| `--completed-before` / `--completed-within` | `90d`, `7d` -- based on when the download finished |

**Trackers**

| Filter | Meaning |
| --- | --- |
| `--tracker HOST` | Repeatable. Announces to one of these trackers, matched on `host[:port]`. |
| `--tracker-health VERDICT` | Repeatable. This torrent's **own** endpoints aggregate to that verdict: `healthy`, `warning`, `critical`, `disabled` or `unknown`. |
| `--no-tracker` | Has **no configured tracker** -- read from the bulk listing's tracker count, so DHT/PeX/LSD never count as one. |

`--tracker-health` asks about *this torrent's* trackers, not about a
tracker in general. A torrent on three trackers of which one is dead is
`warning`, not `critical` -- it still seeds. `unavailable` is refused:
it describes a whole `trackers status` report whose collection failed,
never a single torrent.

A torrent qbit-ops knows nothing about -- no endpoint reported, or a
tracker lookup that failed -- matches **no** health value at all. Not
even `unknown`, which means "qBittorrent reported a status we do not
recognize", a different fact. A non-answer must never be enough to
pause something.

The verdict comes from the same computation `explain torrent` reports,
over the same endpoints, so the two can never disagree:

```bash
qbit-ops torrents list --tracker-health critical
qbit-ops explain torrent --hash <one of them>   # same verdict, with evidence
```

`--no-tracker` and `--tracker-health` together are refused before any
API call: a torrent with no tracker has no tracker health.

**Exclusions** -- every family has one: `--exclude-category`,
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
  `KB/MB/GB/TB` are 1000-based. `500M` is refused -- it belongs to
  neither.
- **Durations**: `s`, `m`, `h`, `d`, `w`. One unit per value (`1d12h` is
  refused). No months or years: they have no fixed length, so write
  `365d`.
- **Percentages**: `95%`, or a bare fraction between `0` and `1`. A bare
  `95` is refused, since it could mean either.

### Three different clocks

- `--older-than` -- how long ago the torrent was **added**.
- `--inactive-for` -- how long since it last **moved data**.
- `--completed-before` -- how long ago the **download finished**.

They disagree often, which is why all three exist: a torrent added two
years ago and still seeding is old but neither inactive nor recently
completed.

Two edge cases worth knowing: a torrent that never transferred anything
counts as inactive from the day it was added, and a torrent that never
finished downloading is never selected by a completion bound.

### Two selectors that stand apart

Neither combines with a filter, or with each other:

- `--hash` targets one torrent by full infohash or an unambiguous
  prefix. An ambiguous prefix is refused and lists the candidates.
- `--all` acts on the whole instance, and has to be typed out.

### Cost

One listing call, then the cheap filters, and only then -- if you asked
for `--tracker`, `--exclude-tracker` or `--tracker-health` -- one tracker
lookup per surviving torrent. Filtering first is what keeps that count
proportional to your selection instead of your whole instance.
`--no-tracker` needs no lookup at all.

Combining them costs nothing extra: `--tracker` and `--tracker-health`
are both answered from that single pass, never from two.

### Where the filters work

Everywhere a command acts on a set of torrents: `torrents list`,
`torrents stats`, `torrents inspect`, `torrents search`,
`trackers list`, `trackers status`, the five bulk mutations (`pause`,
`resume`, `start`, `reannounce`, `delete`), and the four tracker
operations (`add-if-present`, `remove`, `replace`, `replace-passkey`).

`--tracker-health` is the one exception to "everywhere": it is offered
on `torrents list`, `torrents stats`, `trackers list` and the five bulk
mutations. The four tracker operations do not take it -- selecting
torrents by the health of their trackers in order to act on those same
trackers would be circular. `torrents inspect` does not offer it either:
the filter is honoured if something sets it, but no flag exposes it
there yet. `torrents search` is a third, different case: `--tracker`,
`--exclude-tracker` and `--tracker-health` are all three declared
options there, but every one of them is **refused** before any
qBittorrent call rather than applied -- see "Finding a torrent by
name" above.

The same filters always select the same torrents, whichever command
consumes them -- listing, inspecting or mutating.

The tracker operations accept every filter **except** the tracker
family (`--tracker`, `--exclude-tracker`, `--no-tracker`). On those
commands `--tracker` or `--source` already names the tracker being
acted on, so a second tracker notion on the same line would be
ambiguous.

```bash
# drop a tracker, but only within one category
qbit-ops trackers remove --tracker old.example --category sonarr
```

Their summaries read left to right: `scanned` is the whole instance,
`matched` what the filters kept, and `matched_tracker` /
`matched_source` what actually uses the tracker.

`trackers list` and `trackers status` are one more exception: they
inspect every selected torrent to aggregate their trackers, so
`--tracker` restricts the *report* rather than the selection, takes a
single value, and `--exclude-tracker` is not offered at all. A filter
that cannot be honoured is refused, never silently dropped. The two
commands accept exactly the same filters, with the same meanings.

### What counts as a tracker

qBittorrent lists DHT, PeX and LSD next to real trackers, as `** [DHT]
**` markers. They are peer-discovery mechanisms, not announce
endpoints -- so qbit-ops never counts them as trackers and never matches
them with `--tracker`. A torrent whose only entries are DHT/PeX/LSD has
zero trackers, and `--no-tracker` finds it.

They are still reported, separately: `torrents inspect` and
`backup export` carry a `peer_discovery` field listing each mechanism
and whether it is enabled, and the TUI details pane shows them on their
own line.

## 🔎 Finding a torrent by name

**A filter is an option; `torrents search` is a command.** `--name-contains`
and `--name-regex` are deterministic substring/regex predicates -- exact,
composable, safe to feed a mutation. `torrents search` is the opposite:
tolerant of case, accents, punctuation, word order and (in the default
mode) typos, and **never** a selector. It returns a ranked list and stops
there -- there is no `--search` option anywhere, and no way to turn a
search result into a target. Copy the hash you want and pass it to the
command that acts on it:

```bash
qbit-ops torrents search "amour est dnas le pre"   # discover -> a hash
qbit-ops torrents pause --hash 3f2a1b               # target -> mutate
```

That hand-off is a deliberate human checkpoint, not friction to remove.

```bash
qbit-ops torrents search ubuntu
qbit-ops torrents search "amour est dnas le pre"          # typo-tolerant
qbit-ops torrents search debian --mode contains --limit 5
qbit-ops torrents search sonarr --category tv --format json
qbit-ops torrents search dead --verbose                    # show similarity
```

### Modes

One ordinal dial, each step a superset of the last -- raising the mode
only ever **adds** results, it never removes or reorders one:

| `--mode` | Adds | Use it for |
| --- | --- | --- |
| `exact` | the literal name (punctuation-insensitive) | you know the exact title |
| `contains` | + substring | the tolerant equivalent of `--name-contains` |
| `tokens` | + words in any order, partial words | most searches |
| `fuzzy` (default) | + typo tolerance | "I know roughly how it's spelled" |

Every match reports which tier found it (`hash`, `exact`, `prefix`,
`substring`, `all_tokens`, `token_prefix`, `fuzzy`) in `json`/`jsonl`/`csv`
as `match` -- a stable name, never a numeric score. The one numeric
signal, `similarity` (`fuzzy` matches only), is table-only and only
under `--verbose`: a `0.83` invites a hard-coded threshold downstream,
a tier name doesn't.

### Filters narrow the corpus; they never widen it

`torrents search` accepts the same composable filters as `torrents list`
(see "Selecting torrents" above), applied *before* ranking -- searching
a smaller corpus, never re-ranking a smaller one. `--tracker`,
`--exclude-tracker` and `--tracker-health` are the one exception: they
need a per-torrent tracker lookup, and `search` is bounded to exactly
one `torrents_info()` call regardless of library size, so they are
refused with a message pointing at `torrents list`.

### What "tolerant" does not mean

Some structure is deliberately **not** understood, so an unexpected match
never surprises you silently:

- `S01E02` is one token, not a season/episode pair -- `1x02` does not match it.
- Years, resolutions (`1080p`) and release-group tags are ordinary tokens
  with no special weight.
- Roman numerals never match arabic digits: `Part II` != `Part 2`.
- No stemming, lemmatization, synonyms, or stop words: `Movies` != `Movie`.
- No transliteration beyond Unicode NFKD decomposition: `Война` != `Voyna`,
  though accents and diacritics (`café` == `cafe`) are folded.
- No language or release-group table: `VF` != `FRENCH`.

### A hash prefix needs 8 characters here

A query that looks like a hex hash prefix only triggers a hash match at 8
characters or more in the CLI (an anti-noise floor -- `1080` must never
match torrents by hash). The TUI's live `/` search uses a 1-character
floor instead, since it never turns a match into a mutation target.

### Machine output

```json
{
  "query": "amour est dnas le pre",
  "normalized_query": "amour est dnas le pre",
  "mode": "fuzzy",
  "summary": {"scanned": 1105, "matched": 3, "returned": 3,
              "limit": 20, "truncated": false},
  "matches": [
    {"hash": "3f2a1b...", "name": "...", "match": "fuzzy",
     "state": "stalledUP", "category": "sonarr", "size": 2254857830,
     "progress": 1.0, "ratio": 3.42}
  ]
}
```

`matched` counts every match **before** `--limit` truncates the list;
`returned` is how many are actually in `matches`; `truncated` is
`matched > returned`. `csv` carries only the `matches` rows, like every
other `csv` in the repo -- `summary` exists in `json`/`jsonl` and in the
table's footer.

## 🛰️ What each tracker weighs

`trackers list` answers *which trackers am I on, and what do they carry?*
It is read-only, and accepts exactly the filters `trackers status` does,
with the same meanings.

```bash
qbit-ops trackers list
qbit-ops trackers list --category sonarr
qbit-ops trackers list --state seeding --format json
qbit-ops trackers list --tracker-health critical
qbit-ops trackers list --verbose
```

The table is a fixed, five-column set by default -- it never adapts to
the detected terminal width, so the same command renders the same
columns whether the terminal is narrow or wide:

| Column | Meaning |
| --- | --- |
| `Tracker` | Normalized identity, `host[:port]` -- never an announce URL |
| `Torrents` | Retained torrents announcing to this tracker |
| `Size` | Sum of their sizes |
| `Uploaded` | Sum of the known uploaded bytes |
| `Ratio` | Total uploaded ÷ total downloaded |

`--verbose` renders all nine columns instead, adding:

| Column | Meaning |
| --- | --- |
| `Excl` | Among the tracker's torrents, those carrying **no other** tracker identity |
| `Endpoints` | Endpoints observed for this identity |
| `Downloaded` | Sum of the known downloaded bytes |
| `Seed Time` | Sum of the known seeding times |

`json`, `jsonl` and `csv` always carry all nine measures, regardless of
`--verbose` -- they are the way to read the full detail from a script.

### The columns do not add up, on purpose

> A torrent announcing to three trackers counts **entirely** in each of
> the three aggregates.

So **summing a column over every tracker exceeds your library total**,
sometimes by a lot. That is documented rather than corrected: splitting
bytes between trackers would need an invented sharing key, and qbit-ops
does not invent values.

`Excl` is the honest answer to the question behind the numbers -- *what
would I lose by leaving this tracker?* A torrent counts there when this
identity is the **only** one it carries.

An identity carried by a **disabled** endpoint still counts as another
identity: you disabled it, you did not remove it, and you can turn it
back on. A torrent on an active X and a disabled Y is therefore
exclusive to neither.

### Reading the numbers

The measures are summed exactly the way `torrents stats` sums them,
from the same code -- two commands adding up the same bytes must not
answer differently:

- 🧮 The ratio is total uploaded ÷ total downloaded, never the average
  of the per-torrent ratios. `null` when nothing was downloaded.
- ❔ A measure qBittorrent never reported is left **out** of its
  aggregate, never counted as `0`.
- ⏱️ `Seed Time` is a cumulative total nobody retypes, so it reads in
  the conventional units `torrents stats` uses for its own total (`1y`
  is 365 days, `1mo` is 30 days) rather than the filter vocabulary.
  Machine output stays in seconds.
- 🚦 Tracker health **never** drives this command's exit code. It is an
  inventory, not a diagnostic: a script asking "what trackers exist"
  must not break because one tracker is degraded. Ordinary errors -- an
  invalid filter, an unreachable instance -- still exit non-zero. Use
  `trackers status` when you want health to decide the exit code.
- 🈳 An empty selection is an answer, not a failure: no rows, exit `0`.
- 🛑 The summary carries `collection_errors`, so zero rows because the
  instance has no tracker never looks like zero rows because nothing
  could be read. It does not change the exit code.
- 💸 Cost: one listing call plus one tracker lookup per surviving
  torrent -- exactly what this command already spent before it reported
  any volume.

## 📊 Library statistics

`torrents stats` answers, in one command, *what does my library weigh,
what has it transferred, and for how long?* -- over exactly the torrents
you describe. It is read-only and never changes anything.

```bash
qbit-ops torrents stats
qbit-ops torrents stats --category sonarr
qbit-ops torrents stats --state stalled --ratio-max 1.0
qbit-ops torrents stats --tracker tracker.example --format json
```

It accepts every filter listed above, with the same semantics, plus
`--hash` and `--all`. A selection that matches nothing is an answer, not
a failure: counters read `0`, undefined aggregates read `null`, and the
command still exits `0`.

### Two blocks that must not be confused

| Block | What it measures |
| --- | --- |
| `library` | The torrents **currently present** and kept by your selection: count, total/average/largest size, downloaded and uploaded bytes, selection ratio, seeding time total and median, oldest and newest added date. |
| `instance` | qBittorrent's own **all-time** counters, the ones in the WebUI *Statistics* dialog: total downloaded, total uploaded, global share ratio. They include torrents you have since deleted, and qBittorrent exposes them for the whole instance only. |

> The `instance` block appears **if and only if** the invocation carries
> no selector -- no filter, no `--hash`, no `--all`.

`--all` counts as a selector: it does name the whole library, but it
asks about torrents that are *present*, not about counters that include
deleted ones. With any selector the block becomes `null` in JSON, its
lines disappear from the table, and its rows are absent from the CSV.

Showing a global total next to a filtered one would invite a comparison
between two things that do not measure the same population -- so the
rule removes the possibility instead of documenting the trap.

### Reading the numbers

- 🧮 The **selection ratio** is total uploaded ÷ total downloaded, not
  the average of the per-torrent ratios: a 50 MB torrent must not weigh
  as much as an 80 GB one. It is `null` when nothing was downloaded.
- 🏷️ The two ratios are always labelled apart -- `Selection ratio` and
  `Instance ratio` -- because they answer different questions.
- ❔ A measure qBittorrent never reported is left **out** of its
  aggregate, never counted as `0` or as a 1970 date. Its "unset" marker
  is negative, so counting it would take bytes *away* from a total.
- 📀 Size is the same size `--size-min`/`--size-max` filter on, so
  `torrents stats` and `torrents list` always agree.
- ⏱️ The two halves of **Seeding time** use different units on purpose.
  The **median** stays in `d`/`h`/`m`/`s`, the vocabulary
  `--seeded-for` accepts, so you can retype it straight into a filter.
  The **total** is retyped by nobody -- `156764d` names no torrent --
  so it reads in conventional units instead: `1y` is **365 days** and
  `1mo` is **30 days**, fixed lengths chosen for display only. Those
  units are deliberately *not* accepted by the filters, because a real
  month has no fixed length. Machine output is unaffected:
  `seeding_time_total_seconds` and `seeding_time_median_seconds` stay
  in seconds.
- 💸 Cost: one listing call, plus one all-time counters read when there
  is no selector, plus the tracker lookups a `--tracker` filter already
  implies. Selecting makes this command cheaper, never dearer.

`csv` uses the long `section,key,value` shape `status` already uses, so
an absent `instance` block is simply absent instead of leaving empty
columns; `jsonl` emits exactly one compact document.

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
