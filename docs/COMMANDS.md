# Command Reference

Full command reference for `qbit-ops`. See the [README](../README.md) for
installation, configuration and the safety model.

The examples below use `poetry run`. If `qbit-ops` is installed with `pipx`,
drop the `poetry run` prefix.

## Table of Contents

- [Status](#status)
- [Connection & Config](#connection--config)
- [Torrents](#torrents)
- [Trackers](#trackers)
- [Backup](#backup)
- [Use Cases](#use-cases)
- [Matching Modes](#matching-modes)
- [Format Support Matrix](#format-support-matrix)
- [Machine-Readable Silence Contract](#machine-readable-silence-contract)
- [Progress & Spinner Behavior](#progress--spinner-behavior)
- [Mutation Risk & Confirmation Policy](#mutation-risk--confirmation-policy)
- [Tracker Health](#tracker-health)
- [Output Summaries](#output-summaries)
- [Exit Codes](#exit-codes)

## Status

```bash
poetry run qbit-ops status
poetry run qbit-ops status --format json
poetry run qbit-ops status --format jsonl
poetry run qbit-ops status --format csv
poetry run qbit-ops status --quiet   # only the exit code matters
```

`status` is a root-level, read-only command answering "is this instance
reachable, and what is its current operational state?". It performs a
bounded, fixed number of qBittorrent API calls (`app_version`,
`app_web_api_version`, `transfer_info`, `torrents_info` — never a
per-torrent tracker scan), so it stays cheap regardless of torrent count.

It reports:

- **connection**: reachable, authenticated, qBittorrent version, Web API
  version, redacted instance host;
- **transfers**: total, downloading, seeding, completed, stalled, checking,
  errored, unknown torrent counts, plus global download/upload speed;
- **alerts**: structured findings such as `torrents_errored`,
  `torrents_stalled`, `torrents_unknown_state`, `qbittorrent_unavailable`,
  `authentication_failed`.

Health is one of `healthy`, `warning`, `critical`, `unavailable`, with a
dedicated exit code for each — see [Exit Codes](#exit-codes). `--quiet`
suppresses all normal output (no table, no JSON) and is meant for
healthchecks: only the exit code carries information. Combining `--quiet`
with an explicit non-default `--format` is a validation error (exit `4`).
`--quiet` is only available on `status`: it is the only read-only command
whose exit code carries information beyond success/failure — see
[`--quiet` scope](#quiet-scope) below.

## Connection & Config

```bash
poetry run qbit-ops connection check
poetry run qbit-ops connection check --format json

poetry run qbit-ops config doctor
poetry run qbit-ops config doctor --format json
```

## Torrents

```bash
poetry run qbit-ops torrents list
poetry run qbit-ops torrents list --format json

poetry run qbit-ops torrents categories
poetry run qbit-ops torrents categories --format json

poetry run qbit-ops torrents list --category sonarr
poetry run qbit-ops torrents list --category "(uncategorized)"

poetry run qbit-ops torrents list \
  --tracker "https://tracker-a.example/announce"

poetry run qbit-ops torrents list \
  --tracker "http://connect.maxp2p.org:8080/passkey/announce" \
  --match without-query

poetry run qbit-ops torrents inspect --hash "TORRENT_HASH_OR_PREFIX"
poetry run qbit-ops torrents inspect --hash "abc123" --format json

poetry run qbit-ops torrents inspect --name "L.amour.est.dans.le.pre"
poetry run qbit-ops torrents inspect \
  --name "L.amour.est.dans.le.pre" \
  --format json
```

`torrents inspect --name` ranks matches by relevance: exact match, prefix
match, substring match, then fuzzy similarity. It is **read-only discovery
only** — a way to find a hash, not a way to target a mutation. `torrents
inspect --hash` accepts a full infohash or a unique leading prefix (matched
case-insensitively); an ambiguous prefix fails with the candidate list
instead of guessing.

### Bulk torrent actions

`pause`, `resume`, `start` and `reannounce` act on torrents targeted by
`--hash`, `--category`, `--tracker`, `--all`, or `--completed` (`start`
only). Exactly one targeting mode is required. `--hash` is always used
alone: it resolves to a single torrent, so it cannot combine with
`--category`, `--tracker`, `--all`, or `--completed`. `--completed` is the
only mode that can still combine with `--category` or `--tracker`.

**`--hash` is the safe, canonical way to target one torrent** — a full
infohash or a unique leading prefix, resolved case-insensitively. A unique
prefix is the shortest leading sequence of hex characters that currently
matches exactly one torrent on the connected instance; there is no fixed
required length, and the same prefix can stop being unique as torrents are
added or removed.

```bash
poetry run qbit-ops torrents inspect --name "debian"      # 1. discover
poetry run qbit-ops torrents reannounce --hash abc123 --dry-run   # 2. act
poetry run qbit-ops torrents reannounce --hash abc123 --no-dry-run
```

Resolution rules (identical in dry-run and real execution):

1. **No torrent matches the hash or prefix** → the command matches nothing
   and exits with the existing no-match exit code (`2`); no mutation is
   attempted. `torrents inspect --hash` reports this as:

   ```text
   No torrent found for hash prefix: <value>
   ```

2. **Exactly one torrent matches** → it resolves to the complete hash and
   the action proceeds normally. The resolved full hash is shown in the
   command's summary output (`value` row), even in dry-run.
3. **Several torrents share the prefix** → the command fails, mutates
   nothing, and exits `1`:

   ```text
   ✗ ERROR Hash prefix 'abc' matches multiple torrents.

     abc123def456…  Debian ISO
     abc987fed654…  Debian live image

   Use a longer prefix.
   ```

   The candidate list is sorted deterministically (by hash, then name) and
   capped at 10 entries, with a count of any omitted candidates.

**Migration note** — fuzzy `--name` targeting was removed from mutating
commands (pre-1.0 breaking change; see `docs/DECISIONS.md`). It never
guaranteed a single target and could silently affect several torrents that
happened to share part of their name:

```text
Before:
qbit-ops torrents reannounce --name "debian"

After:
qbit-ops torrents inspect --name "debian"
qbit-ops torrents reannounce --hash abc123
```

Other examples:

```bash
poetry run qbit-ops torrents pause --category sonarr --dry-run --verbose
poetry run qbit-ops torrents resume --category sonarr --no-dry-run
poetry run qbit-ops torrents resume --all --no-dry-run
poetry run qbit-ops torrents resume \
  --tracker "https://tracker-a.example/announce" \
  --no-dry-run

poetry run qbit-ops torrents start --completed --dry-run --verbose
poetry run qbit-ops torrents start --completed --no-dry-run
```

`pause`, `resume` and `start` are idempotent:

- `pause` skips torrents already stopped (`paused*` or `stopped*` states).
- `resume`/`start` skip torrents that are not stopped; active torrents are
  never restarted.
- `start --completed` only targets torrents with `progress=100%`.

## Trackers

```bash
poetry run qbit-ops trackers list
poetry run qbit-ops trackers list --match without-query
poetry run qbit-ops trackers list --format json

poetry run qbit-ops trackers health
poetry run qbit-ops trackers health --format json

poetry run qbit-ops trackers inspect \
  --tracker "https://tracker-a.example/announce"
poetry run qbit-ops trackers inspect \
  --tracker "https://tracker-a.example/announce" \
  --format json

poetry run qbit-ops trackers export --format json
```

### Add a tracker if another tracker is present

Adds a target tracker only to torrents that already use a known source
tracker. Medium risk: real execution prompts for confirmation unless
`--yes` is given (see
[Mutation Risk & Confirmation Policy](#mutation-risk--confirmation-policy)).

```bash
poetry run qbit-ops trackers add-if-present \
  --source "https://tracker-a.example/announce" \
  --target "https://tracker-b.example/announce" \
  --dry-run --verbose

poetry run qbit-ops trackers add-if-present \
  --source "https://tracker-a.example/announce" \
  --target "https://tracker-b.example/announce" \
  --no-dry-run --yes
```

### Remove a tracker in bulk

High risk: real execution prompts for confirmation unless `--yes` is given.

```bash
poetry run qbit-ops trackers remove \
  --tracker "https://tracker-a.example/announce" \
  --dry-run --verbose

poetry run qbit-ops trackers remove \
  --tracker "https://tracker-a.example/announce" \
  --no-dry-run --yes
```

### Replace a tracker in bulk

Migrates torrents from one tracker to another. If the target tracker is
already present, `qbit-ops` removes the source instead of adding a
duplicate. High risk: real execution prompts for confirmation unless
`--yes` is given.

```bash
poetry run qbit-ops trackers replace \
  --source "https://tracker-a.example/announce" \
  --target "https://tracker-b.example/announce" \
  --dry-run --verbose

poetry run qbit-ops trackers replace \
  --source "https://tracker-a.example/announce" \
  --target "https://tracker-b.example/announce" \
  --no-dry-run --yes
```

### Replace a tracker's passkey in bulk

Keeps the tracker URL otherwise unchanged. Mark the passkey's position with
a literal `{passkey}` placeholder, either as a query parameter value or as a
full path segment — the current passkey does not need to be known. High
risk: real execution prompts for confirmation unless `--yes` is given. The
old and new passkey are never shown in the prompt, preview, or
`--verbose` output.

```bash
# passkey in the query string
poetry run qbit-ops trackers replace-passkey \
  --tracker "https://tracker-a.example/announce?passkey={passkey}" \
  --new-passkey "NEW_PASSKEY" \
  --dry-run --verbose

# passkey as a path segment
poetry run qbit-ops trackers replace-passkey \
  --tracker "https://tracker-a.example/announce/{passkey}" \
  --new-passkey "NEW_PASSKEY" \
  --no-dry-run --yes
```

### Handle dynamic tracker URLs

Some trackers include dynamic query parameters such as `sig` or
`announce_ts`. Use `--match without-query` to compare only the stable
tracker identity.

```bash
poetry run qbit-ops trackers list --match without-query

poetry run qbit-ops trackers add-if-present \
  --source "http://connect.maxp2p.org:8080/passkey/announce" \
  --target "https://tracker-b.example/announce" \
  --match without-query --dry-run --verbose

poetry run qbit-ops trackers remove \
  --tracker "http://connect.maxp2p.org:8080/passkey/announce" \
  --match without-query --dry-run --verbose

poetry run qbit-ops trackers replace \
  --source "http://connect.maxp2p.org:8080/passkey/announce" \
  --target "https://tracker-b.example/announce" \
  --match without-query --dry-run --verbose
```

## Backup

```bash
poetry run qbit-ops backup export --format json > backup.json
poetry run qbit-ops backup diff backup-before.json backup-after.json
poetry run qbit-ops backup diff backup-before.json backup-after.json \
  --format json
```

`backup export --format json` produces:

- export metadata (`exported_at`, qBittorrent versions, configured host);
- torrent metadata and tracker details for every torrent;
- normalized tracker identities;
- aggregated tracker usage counts.

`backup diff` compares two exports from `backup export` or `trackers
export` and reports torrents added/removed/changed and tracker usage
changes.

## Use Cases

### Audit trackers before changing anything

```bash
poetry run qbit-ops connection check
poetry run qbit-ops config doctor
poetry run qbit-ops torrents list
poetry run qbit-ops torrents categories
poetry run qbit-ops trackers list
poetry run qbit-ops trackers health
poetry run qbit-ops trackers export --format json
poetry run qbit-ops backup export --format json
```

## Matching Modes

- `exact` (default): compares the full normalized tracker URL.
- `without-query`: ignores query parameters when comparing trackers.

Both modes preserve the raw qBittorrent URLs for API calls — this matters
for `remove`, since qBittorrent expects the original tracker URL.

## Format Support Matrix

Every read-only command shares one `--format` option and one
`app.ui.OutputFormat` enum (`table | json | jsonl | csv`). `--output` no
longer exists anywhere in the CLI — this was an intentional pre-1.0 break,
not an alias (see `docs/DECISIONS.md`, 2026-07-24).

`table` is the default and, together with `json`, is supported by every
read-only command. `jsonl` emits exactly **one** compact JSON document per
invocation (the same payload as `--format json`, on one line) — this
applies uniformly whether the command's result is a single snapshot or a
collection, so a script never has to guess how many lines to expect.
`csv` is offered only where the result has a stable, non-artificial
tabular shape; where it does not, the command rejects `--format csv` with
a clear error instead of producing an ad hoc or lossy serialization.

| Command | table | json | jsonl | csv | Notes |
| --- | --- | --- | --- | --- | --- |
| `status` | ✅ | ✅ | ✅ | ✅ | `section,key,value` rows |
| `connection check` | ✅ | ✅ | ✅ | ✅ | `key,value` rows |
| `config doctor` | ✅ | ✅ | ✅ | ✅ | `key,value` rows |
| `torrents list` (incl. `--category`/`--tracker`) | ✅ | ✅ | ✅ | ✅ | one row per torrent |
| `torrents categories` | ✅ | ✅ | ✅ | ✅ | `category,torrents` rows |
| `torrents inspect` (`--hash` or `--name`) | ✅ | ✅ | ✅ | ❌ | no stable tabular shape across both modes (nested tracker details for `--hash`) |
| `trackers list` | ✅ | ✅ | ✅ | ✅ | `tracker,torrents` rows |
| `trackers health` | ✅ | ✅ | ✅ | ❌ | heterogeneous nested sections (variant groups, disabled trackers) |
| `trackers inspect` | ✅ | ✅ | ✅ | ✅ | one row per torrent, matching tracker URLs joined with `; ` |
| `trackers export` | ✅ | ✅ | ✅ | ❌ | nested per-torrent tracker lists |
| `backup export` | ✅ | ✅ | ✅ | ❌ | nested per-torrent tracker lists |
| `backup diff` | ✅ | ✅ | ✅ | ❌ | heterogeneous nested sections (added/removed/changed, tracker usage) |

Requesting an unsupported format fails fast, before any qBittorrent API
call:

```console
$ qbit-ops trackers health --format csv
✗ ERROR --format csv is not supported here (no stable representation).
Supported formats: json, jsonl, table.
```

Mutation commands (`pause`, `resume`, `start`, `reannounce`,
`add-if-present`, `remove`, `replace`, `replace-passkey`) do not expose
`--format`; they keep their existing Rich text summaries only. See
[Mutation Risk & Confirmation Policy](#mutation-risk--confirmation-policy)
for their dry-run/confirmation behavior.

## Machine-Readable Silence Contract

For every read-only command, a successful `--format json`, `--format
jsonl`, or `--format csv` invocation prints **only** the requested
serialized data on stdout: no connection banner, no spinner, no Rich or
ANSI decoration, no informational prose. This also applies to `table`
output: the connecting spinner's `✔ Connected to qBittorrent` banner has
been removed from every read-only command entirely (not just
machine-readable formats), since the rendered result already communicates
that the connection succeeded.

Genuine errors are never silenced by this contract: configuration,
connection, authentication, and validation failures are always printed to
stderr and always produce a non-zero exit code, regardless of `--format`.

### `--quiet` scope

`--quiet` exists only on `status`. It was deliberately not added to the
other read-only commands: `status` is the only one whose exit code alone
carries operational information (`healthy`/`warning`/`critical`/
`unavailable`); the rest only distinguish success, no-match, and error,
which is already conveyed without needing a silent mode. Adding `--quiet`
to e.g. `torrents list` would mean "emit nothing and exit 0" with no
further contract to build on — see `docs/DECISIONS.md` (2026-07-24).

## Progress & Spinner Behavior

Commands that may take noticeable time show **transient** progress
feedback on stderr — a spinner or a progress bar, never both — when
**all** of the following hold:

* the command is human-readable (table for read-only commands; every
  mutation command, which has no `--format` at all);
* stderr is an interactive terminal;
* `--quiet` is not active (`status` only);
* the operation has a real, non-trivial collection or scan phase.

Progress is disabled — silently, with no fallback text — whenever any of
those does not hold: `--format json|jsonl|csv`, a non-interactive stderr
(piped, redirected, CI, cron), or `--quiet`. This is decided once per
command by `app.ui.progress_enabled()` and never re-implemented inline.

Progress is always **transient**: it never survives in the final
scrollback. Before a table, a mutation preview, a confirmation prompt, a
cancellation message, an applied summary, or an error is shown, any
active spinner or progress bar has already been fully torn down — not
just visually cleared but stopped, so a confirmation prompt is never
shown while a Rich live display is still active. This holds on normal
completion, on a raised exception, and on `Ctrl+C`.

### Spinner vs. progress bar

* **Spinner** (`app.ui.transient_spinner`) — one pending remote request,
  or a bounded collection fetched with a single call, where there is
  nothing meaningful to count per item (`Loading torrents…`,
  `Checking connection…`).
* **Progress bar** (`app.ui.transient_progress`) — a collection has
  already been fetched and is then processed item by item with a real,
  known total, typically one extra API call per item
  (`Scanning torrent trackers… 642/1105`).

A progress bar is never shown with a fabricated or unknown total (no
`0/?`) — that case uses a spinner instead.

### Per-command decision

| Command | Feedback | Why |
| --- | --- | --- |
| `status` | Spinner | Single bounded snapshot collection (4 fixed API calls). |
| `connection check` | Spinner | One connection attempt. |
| `config doctor` | Spinner | One connection attempt plus two version reads. |
| `torrents list` (incl. `--category`/`--tracker`) | Progress bar | One `torrents_trackers()` call per torrent. |
| `torrents categories` | Spinner | One bulk `torrents_info()` call, in-memory grouping only. |
| `torrents inspect` (`--hash` or `--name`) | Spinner | Single-torrent lookup or one in-memory name search. |
| `trackers list` | Progress bar | One `torrents_trackers()` call per torrent. |
| `trackers health` | Progress bar | One `torrents_trackers()` call per torrent. |
| `trackers inspect` | Progress bar | One `torrents_trackers()` call per torrent. |
| `trackers export` | Progress bar | One `torrents_trackers()` call per torrent. |
| `backup export` | Spinner | Composite of two per-torrent scans; a single spinner is simpler and more honest than two sequential fabricated-total bars. |
| `backup diff` | None (deliberate) | Local JSON file reads plus an in-memory diff — effectively instantaneous, no network call. |
| Bulk torrent actions (`pause`/`resume`/`start`/`reannounce`) | Progress bar | Real, known total from the already-fetched torrent list; a filtered `--tracker` scan does one `torrents_trackers()` call per torrent. |
| `trackers add-if-present`/`remove`/`replace`/`replace-passkey` (planning) | Progress bar | One `torrents_trackers()` call per torrent while building the plan. |

Every mutation command's progress only ever wraps **planning** (building
the structured plan). There is deliberately no second, separate progress
indicator around *applying* a plan: applying is either one bulk API call
(nothing to show incremental progress for) or a handful of calls for the
already-confirmed, already-known change set, where a second spinner
would be pure decoration rather than real feedback.

Progress is presentation-only: it never changes `matched`, `has_changes`,
`modified`, `MutationStatus`, confirmation behavior, which mutation calls
are made, or exit codes. The plan built while progress is showing is the
exact same plan used for the preview and for the real application — see
[Mutation Risk & Confirmation Policy](#mutation-risk--confirmation-policy).

## Mutation Risk & Confirmation Policy

Every mutating command (`torrents pause/resume/start/reannounce`, `trackers
add-if-present/remove/replace/replace-passkey`) is classified into exactly
one risk tier, defined once in `app.execution.MUTATION_RISK` so it cannot
drift between the CLI, its tests, and this table:

| Risk | Commands | Confirmation | `--yes` |
| --- | --- | --- | --- |
| **low** | `torrents pause`, `torrents resume`, `torrents start`, `torrents reannounce` | never prompted | not exposed (no behavioral purpose) |
| **medium** | `trackers add-if-present` | prompted on real, interactive execution | skips the prompt |
| **high** | `trackers remove`, `trackers replace`, `trackers replace-passkey` | prompted on real, interactive execution | skips the prompt |

Execution contract, identical across every mutating command:

- No flag → **preview only**. The plan is built (source data is scanned
  once) and shown as a summary; nothing is sent to qBittorrent.
- `--no-dry-run` → requests real execution.
- `--no-dry-run` alone on a **medium/high** command in a non-interactive
  context (no TTY on stderr — CI, cron, a pipe) **fails immediately** with a
  clear error naming the command and suggesting `--yes`. It never hangs
  waiting for input that will never arrive.
- `--no-dry-run` alone on a **medium/high** command in an interactive
  terminal shows the preview, then asks `Continue? [y/N]` (default **No**).
- `--no-dry-run --yes` → applies immediately, no prompt, on any risk tier.
- `--yes` **never** implies `--no-dry-run`: `trackers remove --tracker ...
  --yes` (without `--no-dry-run`) still only previews.
- **Declining the confirmation prompt is not an error.** No mutation
  happens, `qbit-ops` prints `Operation cancelled.`, and exits `0`.
- A plan that matched nothing, or matched but had nothing left to change,
  never prompts, on any risk tier — there is nothing to confirm. Neither is
  ever reported as `APPLIED`, even with `--no-dry-run --yes` (see
  [Mutation status vocabulary](#mutation-status-vocabulary) below).
- Preview and real execution always share the **same plan**: torrents are
  scanned once; confirming re-uses that already-built plan instead of
  scanning again, so what you confirmed is exactly what gets applied.

### Mutation status vocabulary

Every mutation command's summary ends with a `status` row using exactly
one of these five values (`app.execution.MutationStatus`):

| Status | Meaning | Exit code |
| --- | --- | --- |
| `PREVIEW` | Dry-run (the default): changes were planned but nothing was sent to qBittorrent. | `0`, or `2` if `matched` is `0` |
| `APPLIED` | One or more changes were successfully sent to qBittorrent. | `0` |
| `CANCELLED` | The user declined the confirmation prompt; no mutation occurred. Shown as `Operation cancelled.` after the preview, not as a second summary. | `0` |
| `NO_MATCH` | The requested selector (`--hash`, `--tracker`, `--category`, ...) matched nothing at all. | `2` |
| `NO_CHANGES` | The selector matched one or more targets, but every one already satisfied the requested state (e.g. every torrent already paused, a passkey already current). | `0` |

`NO_MATCH` and `NO_CHANGES` are resolved **before** any risk/confirmation
decision is made — they never prompt, never call a mutation API, and are
never shown as `APPLIED`, regardless of `--no-dry-run` or `--yes`:

```console
$ qbit-ops trackers remove --tracker "https://unmatched.example/announce" \
  --no-dry-run
         Summary
scanned         12
matched_tracker 0
modified        0
removed_urls    0
status          NO_MATCH
```

```console
$ qbit-ops torrents resume --all --no-dry-run
     Summary
action   resume
filter   all
value    *
scanned  8
matched  3
modified 0
skipped  3
status   NO_CHANGES
```

`trackers remove` and `trackers replace` always turn a match into at least
one change (removing a duplicate or stale tracker URL counts as a change),
so those two commands can report `NO_MATCH` but never `NO_CHANGES`.
`trackers add-if-present` and `trackers replace-passkey` can report either,
depending on whether matched torrents already have the target tracker or
passkey. Bulk torrent actions (`pause`/`resume`/`start`/`reannounce`) are
idempotent, so `NO_CHANGES` is common there too (e.g. `resume --all` when
every torrent is already running).

```bash
# low risk: applies immediately, no prompt, even unattended
qbit-ops torrents pause --all --no-dry-run

# medium/high risk, interactive: preview, then a y/N prompt
qbit-ops trackers remove --tracker "https://tracker.example/announce" \
  --no-dry-run

# medium/high risk, unattended (cron/CI): pre-approve with --yes
qbit-ops trackers remove --tracker "https://tracker.example/announce" \
  --no-dry-run --yes
```

### Confirmation prompt content

The confirmation prompt states the operation's impact in plain terms —
matching torrent counts, and for `remove`/`replace`, an explicit warning
that a private torrent relying solely on the affected tracker will lose its
announce. `replace-passkey`'s prompt warns that an incorrect passkey will
break every affected torrent's announce until corrected.

**Tracker identities shown in a prompt, preview, or `--verbose` detail
table are always reduced to scheme + host** (`redact_tracker_identity`):
private trackers commonly embed a passkey or other per-user secret in the
path or query string, and guessing which part is safe to show is
unreliable, so the full URL is never rendered in a mutation context — only
in read-only inspection output, which the user explicitly requested. The
old and new passkey values for `replace-passkey` are **never** rendered
anywhere (prompt, preview, `--verbose`, stdout, stderr) — only counts (how
many torrents, how many tracker URLs) are shown.

## Tracker Health

`trackers health` reports: scanned torrents, active/disabled tracker
occurrences, unique exact and logical tracker URLs, query variant groups,
and disabled pseudo-trackers (DHT, PeX, LSD).

```bash
poetry run qbit-ops trackers health --format json
```

## Output Summaries

Modifying commands print a final summary:

```text
Summary:
- scanned: X
- matched_source: X
- already_had_target: X
- modified: X
- status: PREVIEW|APPLIED|CANCELLED|NO_MATCH|NO_CHANGES
```

Tracker removal uses a dedicated summary:

```text
Summary:
- scanned: X
- matched_tracker: X
- modified: X
- removed_urls: X
- status: PREVIEW|APPLIED|CANCELLED|NO_MATCH|NO_CHANGES
```

Tracker replacement uses a dedicated summary:

```text
Summary:
- scanned: X
- matched_source: X
- already_had_target: X
- modified: X
- replaced_urls: X
- removed_urls: X
- status: PREVIEW|APPLIED|CANCELLED|NO_MATCH|NO_CHANGES
```

Passkey replacement uses its own summary (no tracker URL ever appears in
it, only counts):

```text
Summary:
- scanned: X
- matched_source: X
- already_up_to_date: X
- modified: X
- replaced_urls: X
- status: PREVIEW|APPLIED|CANCELLED|NO_MATCH|NO_CHANGES
```

Bulk torrent actions use a dedicated summary:

```text
Summary:
- action: pause|resume|start|reannounce
- filter: hash|category|tracker|completed|all
- value: ...    (the resolved full hash when filter is "hash")
- match: exact|without-query
- scanned: X
- matched: X
- modified: X
- skipped: X
- status: PREVIEW|APPLIED|CANCELLED|NO_MATCH|NO_CHANGES
```

Pass `--verbose` on any bulk modification command to print impacted
torrents after the summary.

See [Mutation status vocabulary](#mutation-status-vocabulary) for exactly
what each `status` value means and when it appears.

## Exit Codes

Every command except `status` uses:

- `0`: success.
- `1`: configuration, connection, authentication or API error — **also used
  when a `--hash` prefix is ambiguous** (matches several torrents). No new
  exit code was introduced for ambiguity; it is treated as a validation
  error, distinct from "no match".
- `2`: the command completed but matched no torrent — including an
  unresolvable `--hash` (or, for `backup diff`, the two exports differ).

Commands using exit code `2` on no match: `torrents inspect`, `torrents
list --tracker`, `torrents list --category`, `torrents pause`, `torrents
resume`, `torrents start`, `torrents reannounce`, `trackers inspect`,
`trackers add-if-present`, `trackers remove`, `trackers replace`, `trackers
replace-passkey`.

`torrents inspect --hash`, `torrents pause --hash`, `torrents resume
--hash`, `torrents start --hash`, and `torrents reannounce --hash` use exit
code `1` when the prefix is ambiguous, and exit code `2` when it matches no
torrent.

### `status` exit codes

`status` reports operational health rather than success/failure, so it uses
its own codes (documented in `docs/DECISIONS.md`, 2026-07-24):

- `0`: `healthy`.
- `1`: `warning` (stalled torrents and/or unrecognized torrent states).
- `2`: `critical` (one or more torrents in an error state).
- `3`: `unavailable` (configuration, connection, or authentication failure).
- `4`: invalid local configuration or invalid CLI usage (e.g. `--quiet`
  combined with an explicit non-default `--format`).

If both a warning and a critical condition are present, `status` reports
`critical` (the most severe).
