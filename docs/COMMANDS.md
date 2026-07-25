# Command Reference

Full command reference for `qbit-ops`. See the [README](../README.md) for
installation, configuration and the safety model.

The examples below use `poetry run`. If `qbit-ops` is installed with `pipx`,
drop the `poetry run` prefix.

## Table of Contents

- [Status](#status)
- [Status Watch Mode](#status-watch-mode)
- [Doctor](#doctor)
- [Connection](#connection)
- [Torrents](#torrents)
  - [Torrent Filters](#torrent-filters)
  - [Bulk Torrent Actions](#bulk-torrent-actions)
- [Trackers](#trackers)
- [Backup](#backup)
- [Use Cases](#use-cases)
- [Matching Modes](#matching-modes)
- [Format Support Matrix](#format-support-matrix)
- [Machine-Readable Silence Contract](#machine-readable-silence-contract)
- [Progress & Spinner Behavior](#progress--spinner-behavior)
- [Mutation Risk & Confirmation Policy](#mutation-risk--confirmation-policy)
- [Tracker Status](#tracker-status)
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

## Status Watch Mode

```bash
poetry run qbit-ops status --watch
poetry run qbit-ops status --watch --interval 10
poetry run qbit-ops status --watch --format jsonl
```

`--watch` repeatedly refreshes the same `status` snapshot until
interrupted (`Ctrl+C`). It reuses `collect_status_snapshot()` and the
existing health calculation unchanged, every refresh — there is no
second status model and no duplicated health/alert logic; watch mode
only adds a collect → render/serialize → wait loop around the exact
same snapshot.

**`--interval FLOAT`** — seconds between the *start* of one refresh and
the start of the next (a monotonic clock is used, so a slow collection
does not accumulate drift; a collection slower than the interval starts
its next attempt immediately, never sleeping a negative amount).
Defaults to `5.0`. Must be strictly positive and finite: `0`, negative,
`nan`, and `inf`/`-inf` are all rejected before any qBittorrent API
call. Only valid together with `--watch`; `--interval` without `--watch`
is rejected as invalid usage.

**Supported formats: `table` and `jsonl` only.** `--watch --format json`
and `--watch --format csv` are rejected before connecting to
qBittorrent — a repeated stream of snapshots cannot honestly masquerade
as one JSON document or one CSV table, unlike one-shot `status`, which
supports all four formats.

**`--quiet` is rejected with `--watch`.** `--quiet` exists for one-shot
healthchecks where only the exit code matters; a watch that runs
indefinitely and prints nothing has no output contract left to have.
One-shot `status --quiet` is unaffected.

### Table watch

Uses a persistent Rich `Live` display (`screen=False`: no full-screen
alternate-buffer mode) that redraws the same view in place on every
refresh, instead of appending a new table to scrollback each time. This
is deliberately **not** the transient spinner/progress bar used
elsewhere (`docs/COMMANDS.md#progress--spinner-behavior`) — that helper
is for "working, briefly, then gone"; this display is intentionally
persistent for the whole life of the watch loop. On top of the existing
status view it shows the refresh interval, an iteration counter, and
the snapshot's own `generated_at` timestamp as "last refresh":

```text
qbit-ops · healthy
watching · refresh every 5s · iteration 12 · last refresh 2026-07-24 22:42:44 UTC

  qBittorrent
Version   5.0.1
...
```

The first version simply replaces the displayed snapshot on each
refresh — no diffing, no history, no charts or sparklines, no alert
acknowledgement.

### JSONL watch

Each refresh writes exactly one compact JSON object (the same schema as
one-shot `--format jsonl`, `schema_version: "1"`) followed by one
newline, and the stream is flushed immediately after — safe to pipe into
`jq`, `tail -f`, or a log collector. A successful iteration never writes
anything to stderr and never emits ANSI/Rich decoration, identical to
the machine-readable silence contract for one-shot commands (see
[Machine-Readable Silence Contract](#machine-readable-silence-contract)).

### Temporary failures and recovery

A watch survives temporary qBittorrent trouble: connection loss,
authentication failure, or API unavailability discovered *during* the
loop produce an `unavailable` snapshot (same `build_unavailable_snapshot`
used by one-shot `status`) and the loop keeps retrying at the configured
interval — it never stops on the first blip, and later successful
collections recover to a normal snapshot automatically. Local invalid
configuration (a missing or malformed `.env`) is checked once, before
the loop starts, and terminates immediately instead of retrying forever
against a failure that cannot self-heal. An unexpected error that is
**not** a recognized connection/authentication/API failure (i.e. a real
bug, not a temporary remote condition) is never silently swallowed as
"unavailable" forever: it stops the watch with a clear message.

### Exit codes differ from one-shot `status`

One-shot `status` exit codes report **health** (see [Exit
Codes](#exit-codes)). A running watch cannot continuously update the
process's exit code, and a warning/critical/unavailable snapshot must
never stop the loop — so `--watch` uses an unrelated, small set of
**process** exit codes instead:

| Exit code | Meaning |
| --- | --- |
| `0` | Clean, user-requested stop (`Ctrl+C`). |
| `4` | Invalid CLI usage, or local configuration invalid before the loop started. |
| `1` | Unexpected fatal error (not a temporary remote failure). |

`Ctrl+C` stops the loop, closes the `Live` display (table mode) or ends
after the last complete JSONL line, restores the terminal, and never
prints a traceback.

## Doctor

```bash
poetry run qbit-ops doctor
poetry run qbit-ops doctor --format json
poetry run qbit-ops doctor --format jsonl
poetry run qbit-ops doctor --format csv
```

`doctor` is a root-level, read-only diagnostic command answering "is
qbit-ops correctly configured, able to communicate with qBittorrent, and
operating against a supported and coherent runtime environment?". It
replaces the earlier `config doctor` entirely (removed, no alias — see
`docs/DECISIONS.md`): pre-1.0, keeping two overlapping doctor commands
had no justification once the root command covers everything `config
doctor` did and more. `qbit-ops doctor` is unrelated to `make doctor`
(the `Makefile` target that checks local Python/Poetry tooling before
`make install`) — same word, two different things at two different
layers.

Like `status`, `doctor` performs a bounded, documented number of remote
calls regardless of torrent count: **at most one authenticated login**
(`auth_log_in()`) **plus up to four read calls** — `app_version()`,
`app_web_api_version()`, `transfer_info()`, `torrents_info()` — never a
per-torrent call (no `torrents_trackers()` scan, no filesystem, Docker,
hardlink, or disk-space check — none of those are backed by any
configuration this repository currently has, so none are invented here).

### Check catalogue

Every check has a stable code (safe for scripts and monitoring to key
off), a section, one of four statuses, a human message, and optional
`detail`/`remediation`. Checks always run and render in this fixed
order:

| Code | Section | Checks |
| --- | --- | --- |
| `CFG001` | configuration | Configuration (`.env`/environment) loaded successfully. |
| `CFG002` | configuration | `QBIT_HOST` is a well-formed `http`/`https` URL. |
| `CFG003` | configuration | `QBIT_HOST` does not embed `user:pass@` credentials (qbit-ops ignores them in favor of `QBIT_USER`/`QBIT_PASSWORD`, so this is a warning, not an error). |
| `CONN001` | connectivity | qBittorrent host is reachable (a login attempt received a response — this also passes when authentication itself fails, since that proves reachability). |
| `CONN002` | connectivity | Authentication succeeded. |
| `CONN003` | connectivity | qBittorrent application version is readable (`app_version()`). |
| `CONN004` | connectivity | Web API version is readable (`app_web_api_version()`). |
| `COMPAT001` | compatibility | The qBittorrent version string is parsable. |
| `COMPAT002` | compatibility | The qBittorrent major version (4 or 5) is one qbit-ops has been validated against. |
| `RUNTIME001` | runtime | Torrent listing succeeds (`torrents_info()`). |
| `RUNTIME002` | runtime | Global transfer info succeeds (`transfer_info()`). |
| `RUNTIME003` | runtime | Every torrent's state is recognized (reuses `app.status.classify_torrent_state`, the exact same vocabulary `status`/`status --watch` use). |

### Status vocabulary and skip semantics

```text
pass      the check succeeded
warning   the check found something worth attention, but not incorrect
fail      the check failed
skipped   a prerequisite check already failed, so this one could not run
```

Checks are independent wherever the underlying data is: one API call
failing does not erase unrelated diagnostics (e.g. a broken
`torrents_info()` still lets `transfer_info()`, `app_version()`, and
every configuration check report normally). A check is only ever
`skipped` because a specific, identifiable prerequisite already failed
— invalid configuration skips every connectivity/compatibility/runtime
check; a failed connection skips authentication onward; a failed
authentication skips the two version reads and everything depending on
them; an unreadable version skips the supported-version-range check
(never inventing a compatibility verdict for a version it never saw —
an unparsable or unrecognized version string is a `warning`, never a
`fail`, for the same reason). **`skipped` never independently
influences the overall status** — it exists only downstream of a `fail`
that already does.

### Overall status and exit codes

The report's `overall_status` is the most severe status among its
checks (`fail` > `warning` > `pass`; `skipped` is never the most severe
by construction):

| Exit code | Meaning |
| --- | --- |
| `0` | All checks `pass`. |
| `1` | One or more `warning`s, no `fail`. |
| `2` | One or more `fail`s. |
| `4` | Invalid CLI invocation preventing doctor from starting. Reserved, currently unreachable: `doctor` takes no option besides `--format`, and an invalid `--format` value is rejected by Click before the command body runs (Click's own usage-error exit code, not this one) — kept for a future doctor-specific flag rather than removed. |

Unlike every other command, a configuration, connection, or
authentication failure is not something `doctor` reports on stderr and
aborts on: it is the diagnostic payload itself (a `fail` check in the
report). **`doctor` never writes to stderr**, in any `--format`,
regardless of how many checks fail — the non-zero exit code and the
rendered report body carry the outcome. This is a deliberate difference
from the [Machine-Readable Silence
Contract](#machine-readable-silence-contract), extended here to table
output and to failure, not just success.

### Output formats

* `table` — one grouped table per section (`Configuration`,
  `Connectivity`, `Compatibility`, `Runtime`), each row showing code,
  status, message, and remediation (blank when none applies), preceded
  by an overall-status header line.
* `json` — one document: `schema_version`, `generated_at`,
  `overall_status`, and `checks` (a list of
  `code`/`section`/`status`/`message`/`detail`/`remediation` objects).
* `jsonl` — exactly **one** compact JSON document per invocation, the
  same payload and schema as `--format json`, on one line. This
  deliberately follows the project-wide jsonl contract (one document per
  invocation, established for `status`/`status --watch` and every
  collection-returning command in Phase 2.1 — see `docs/DECISIONS.md`)
  rather than emitting one line per check, even though the latter is
  also a reasonable contract for a checks collection.
* `csv` — stable columns `section,code,status,message,detail,remediation`,
  one row per check (12 rows plus a header for the current catalogue).

### Security

Every check's `message`/`detail`/`remediation` is redacted before
reaching any renderer: passwords, cookies, authorization headers, and
credentials embedded in a URL (`user:pass@host`) are never shown, in
`table`, `json`, `jsonl`, `csv`, or (since doctor writes nothing there)
stderr. `CFG003` reports that `QBIT_HOST` embeds credentials without
ever printing the credentials themselves. A connection/authentication
error's underlying exception text — which can otherwise carry the
configured host verbatim — is passed through one redaction funnel
(`app.doctor._redact`) that strips URL userinfo and the exact configured
password before it becomes a check's `detail`.

### Out of scope

`doctor` does not perform any filesystem check (existing/readable/
writable roots, hardlink support, free disk space), Docker/path-mapping
check, tracker-quality check, or category-policy check: none of those
are backed by configuration this repository currently reads, and
inventing one would violate `docs/PHILOSOPHY.md` §9 ("a lack of
certainty should produce a warning or refusal, not a guess"). `doctor`
never modifies anything, never offers `--fix`, and never filters checks
with `--only`/`--skip` (not needed at today's catalogue size; a future
phase can add them without changing any existing check's code).

## Connection

```bash
poetry run qbit-ops connection check
poetry run qbit-ops connection check --format json
```

`connection check` is a minimal reachability/authentication probe (one
login attempt, one message, `ok`/error) — use `doctor` for a full,
structured diagnostic report; `connection check` remains for a fast
yes/no answer with no report to parse.

## Torrents

```bash
poetry run qbit-ops torrents list
poetry run qbit-ops torrents list --format json

poetry run qbit-ops torrents categories
poetry run qbit-ops torrents categories --format json

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
instead of guessing. `torrents categories` aggregates over the whole
instance and does not take a filter.

### Torrent Filters

One structured, Typer/Rich-free filter model (`app.torrents.TorrentFilter`)
and one filtering pipeline (`app.torrents.select_torrents`) back `torrents
list` and all four bulk mutation commands (`pause`/`resume`/`start`/
`reannounce`) — filter semantics can never drift between listing and
mutating.

```text
--category VALUE   (repeatable)
--state VALUE       (repeatable)
--tracker VALUE
--completed / --incomplete
--active / --inactive
--stalled
--errored
```

**Combination semantics**: repeated values of the same filter combine with
**OR**; different filter types combine with **AND**.

```bash
poetry run qbit-ops torrents list \
  --category movies --category series \
  --state stalled \
  --tracker tracker.example
# (category == movies OR category == series) AND stalled AND tracker == tracker.example
```

A combination that is not *locally provable* as contradictory (e.g.
`--state downloading --stalled`) is accepted and simply matches nothing —
qbit-ops does not try to build a full contradiction matrix across `--state`
and the activity flags. Only the two combinations that are always
impossible are rejected before any qBittorrent API call:

```text
--completed --incomplete
--active --inactive
```

#### API-call behavior

The filtering pipeline (`app.torrents.select_torrents`) always loads
torrents with exactly one `torrents_info()` call, then applies every
cheap, torrent-info-only filter first:

```text
--category / --state / --completed / --incomplete
--active / --inactive / --stalled / --errored
  → torrents_info() only, no per-torrent call

--tracker
  → torrents_info(), then at most one torrents_trackers() call
    per torrent that survived every cheaper filter above
```

A `--tracker` filter never scans torrents already excluded by a cheaper
filter — e.g. `--category sonarr --tracker tracker.example` on a 1105-
torrent instance where 50 are in `sonarr` calls `torrents_trackers()` at
most 50 times, never 1105. A selection with no `--tracker` filter never
calls `torrents_trackers()` at all — including plain `torrents list`,
which no longer does so by default (see the migration note below).
On `torrents list`, progress reflects this directly: no `--tracker` filter
shows a spinner (one bulk `torrents_info()` call, nothing to count per
item), and a `--tracker` filter shows a real progress bar over exactly the
narrowed candidate set. The four bulk mutation commands always show a
progress bar (see [Progress & Spinner Behavior](#progress--spinner-behavior)),
which completes in a single step when there is no per-item remote work to
report.

**Migration note** — before this phase, plain `torrents list` (and
`torrents list --category`) always called `torrents_trackers()` once per
torrent to populate a `Trackers` count column, regardless of whether any
tracker filter was requested. That per-torrent scan is gone, and so is the
column whenever it wasn't run: `table` output **omits the `Trackers`
column entirely** when tracker data was not collected for the selection,
rather than filling it with a placeholder that could read as "zero
trackers". `json`/`jsonl` render `tracker_count: null` (explicitly absent,
not measured); `csv` renders an empty cell in the still-present
`tracker_count` column (a stable header is a firmer contract than a table
column, which is presentation only). In every format, `null`/omitted is
distinct from `0`, a torrent whose trackers were actually scanned and
found empty. Populate it by adding `--tracker`.

#### `--category`

Repeatable; OR'd together. The public token for torrents without a
category is the bare word `uncategorized`; the display label
`(uncategorized)` (what `torrents list` renders) is also accepted, so
copy-pasting a category shown by qbit-ops always works as a filter value
too:

```bash
poetry run qbit-ops torrents list --category sonarr
poetry run qbit-ops torrents list --category uncategorized
poetry run qbit-ops torrents list --category "(uncategorized)"
```

#### `--state`

A stable public vocabulary, not raw qBittorrent state strings — reuses the
same classification `status`/`status --watch`/`doctor` already use
(`app.torrent_states.classify_torrent_state`), so a torrent's group can
never disagree between commands:

```text
downloading  seeding  checking  stalled  errored  unknown
```

`unknown` covers any remote state qBittorrent reports that qbit-ops does
not yet recognize — states are never silently discarded, and `--state
unknown` is how to find them. `completed`/`active`/`inactive` are
deliberately **not** part of this vocabulary: they have their own
dedicated flags below instead of a second, overlapping spelling.

```bash
poetry run qbit-ops torrents list --state stalled --state errored
```

#### `--completed` / `--incomplete` / `--active` / `--inactive` / `--stalled` / `--errored`

Independent boolean filters. `--completed`/`--incomplete` restrict by
progress (`>= 100%`); `--active`/`--inactive` restrict by whether the
torrent is currently stopped (`paused*`/`stopped*`, covering both
qBittorrent 4 and 5); `--stalled`/`--errored` restrict to those two state
groups directly, as a convenient alternative to `--state stalled`/`--state
errored`.

```bash
poetry run qbit-ops torrents list --completed
poetry run qbit-ops torrents pause --active   # dry-run preview only
```

#### `--tracker`

Matches by **host, or host:port** — never the full announce URL. A private
tracker's URL commonly embeds a passkey in its path or query string, and a
public filter must never require or display one. Pass either a bare host
or a full announce URL; only the host and port are ever used or rendered:

```bash
poetry run qbit-ops torrents list --tracker tracker.example
poetry run qbit-ops torrents list --tracker tracker.example:6969
poetry run qbit-ops torrents list \
  --tracker "https://tracker.example:6969/announce/PASSKEY"   # host:port extracted, PASSKEY discarded
```

**Migration note** — before this phase, `torrents list --tracker` matched
the **full** tracker URL (`--match exact|without-query`, like the
`trackers` command group still does) and rendered every matching tracker
URL verbatim, including any embedded passkey. Both `--match` and that
rendering are gone from `torrents list` and the four bulk mutation
commands (pre-1.0 breaking change; see `docs/DECISIONS.md`) — hostname
matching is strictly safer and does not require knowing the exact
normalized URL qBittorrent stores. `trackers inspect`/`trackers export`/
etc. are unchanged: those commands are explicit, read-only tracker
inspection where showing the full URL is exactly what was requested.
`trackers status` (see [Tracker Status](#tracker-status)) is also
hostname-only by construction — like `torrents list --tracker`, it never
renders a full announce URL or passkey.

### Bulk torrent actions

`pause`, `resume`, `start`, and `reannounce` act on torrents targeted by
`--hash`, `--all`, or one or more of the filters above. Selection safety
rules:

- **`--hash` is always used alone** — a full infohash or a unique leading
  prefix (case-insensitive), resolving to exactly one torrent. It cannot
  combine with `--all` or any filter.
- **`--all` is always used alone** — an explicit acknowledgement of
  whole-instance scope. It cannot combine with any filter either: there is
  no meaningful "confirm scope with a narrower filter" reading.
- **One or more filters may define a bulk selection on their own, without
  `--all`.** Combined filters use the same AND/OR rules as `torrents list`.
- **No selector at all is rejected before any qBittorrent API call** — a
  bulk mutation can never silently mean the whole seedbox by omission;
  `--all` is the only way to say that.

```bash
poetry run qbit-ops torrents inspect --name "debian"      # 1. discover
poetry run qbit-ops torrents reannounce --hash abc123 --dry-run   # 2. act
poetry run qbit-ops torrents reannounce --hash abc123 --no-dry-run

poetry run qbit-ops torrents pause --category sonarr --dry-run --verbose
poetry run qbit-ops torrents resume --category sonarr --no-dry-run
poetry run qbit-ops torrents resume --all --no-dry-run
poetry run qbit-ops torrents resume --tracker tracker.example --no-dry-run

poetry run qbit-ops torrents pause \
  --category sonarr --state stalled --no-dry-run

poetry run qbit-ops torrents start --completed --dry-run --verbose
poetry run qbit-ops torrents start --completed --no-dry-run
poetry run qbit-ops torrents start --completed --all --no-dry-run  # Web UI "Start All"
```

Hash resolution rules (identical in dry-run and real execution):

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
commands in an earlier phase (pre-1.0 breaking change; see
`docs/DECISIONS.md`) and remains removed: it never guaranteed a single
target and could silently affect several torrents that happened to share
part of their name. It is not reintroduced by this phase's shared filter
model either.

```text
Before:
qbit-ops torrents reannounce --name "debian"

After:
qbit-ops torrents inspect --name "debian"
qbit-ops torrents reannounce --hash abc123
```

`pause`, `resume` and `start` are idempotent:

- `pause` skips torrents already stopped (`paused*` or `stopped*` states).
- `resume`/`start` skip torrents that are not stopped; active torrents are
  never restarted.
- `start --completed` (with or without `--all`) only targets torrents with
  `progress=100%`. **Migration note**: `--completed` was previously a
  `start`-only flag with bespoke selection logic; it is now the same
  general `TorrentFilter.completed` filter available on all four bulk
  commands, combinable with any other filter — `pause --completed`,
  `resume --completed`, and `reannounce --completed` are now valid too.

## Trackers

```bash
poetry run qbit-ops trackers list
poetry run qbit-ops trackers list --match without-query
poetry run qbit-ops trackers list --format json

poetry run qbit-ops trackers status
poetry run qbit-ops trackers status --format json
poetry run qbit-ops trackers status --tracker tracker.example
poetry run qbit-ops trackers status --category films --state stalled

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
  --source "http://tracker-port.example:8080/passkey/announce" \
  --target "https://tracker-b.example/announce" \
  --match without-query --dry-run --verbose

poetry run qbit-ops trackers remove \
  --tracker "http://tracker-port.example:8080/passkey/announce" \
  --match without-query --dry-run --verbose

poetry run qbit-ops trackers replace \
  --source "http://tracker-port.example:8080/passkey/announce" \
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
poetry run qbit-ops doctor
poetry run qbit-ops torrents list
poetry run qbit-ops torrents categories
poetry run qbit-ops trackers list
poetry run qbit-ops trackers status
poetry run qbit-ops trackers export --format json
poetry run qbit-ops backup export --format json
```

## Matching Modes

`--match exact|without-query` is a **`trackers` command group** concept
only (`trackers list`/`inspect`/`export`/`add-if-present`/`remove`/
`replace`/`replace-passkey`), where the exact, raw tracker URL matters
for the API calls those commands make:

- `exact` (default): compares the full normalized tracker URL.
- `without-query`: ignores query parameters when comparing trackers.

Both modes preserve the raw qBittorrent URLs for API calls — this matters
for `remove`, since qBittorrent expects the original tracker URL.
`trackers status` does not have `--match`: its tracker identities are
always `host[:port]` (`app.trackers.normalize_tracker_host`, the same
function `torrents list --tracker` uses), never a raw URL, so there is no
query-string comparison mode to pick — see [Tracker
Status](#tracker-status).

`torrents list` and the four bulk mutation commands do **not** have
`--match`: their `--tracker` filter matches by host[:port] instead (see
[Torrent Filters](#torrent-filters)) and never needs a raw-URL comparison
mode.

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
| `doctor` | ✅ | ✅ | ✅ | ✅ | `section,code,status,message,detail,remediation` rows |
| `torrents list` (any filter combination) | ✅ | ✅ | ✅ | ✅ | one row per torrent; JSON/JSONL also include a normalized `filters` object |
| `torrents categories` | ✅ | ✅ | ✅ | ✅ | `category,torrents` rows |
| `torrents inspect` (`--hash` or `--name`) | ✅ | ✅ | ✅ | ❌ | no stable tabular shape across both modes (nested tracker details for `--hash`) |
| `trackers list` | ✅ | ✅ | ✅ | ✅ | `tracker,torrents` rows |
| `trackers status` (any filter combination) | ✅ | ✅ | ✅ | ✅ | one row per tracker identity; `tracker,health,torrent_count,endpoint_count,healthy_count,warning_count,critical_count,disabled_count,unknown_count` (CSV omits `representative_message`) |
| `trackers inspect` | ✅ | ✅ | ✅ | ✅ | one row per torrent, matching tracker URLs joined with `; ` |
| `trackers export` | ✅ | ✅ | ✅ | ❌ | nested per-torrent tracker lists |
| `backup export` | ✅ | ✅ | ✅ | ❌ | nested per-torrent tracker lists |
| `backup diff` | ✅ | ✅ | ✅ | ❌ | heterogeneous nested sections (added/removed/changed, tracker usage) |

Requesting an unsupported format fails fast, before any qBittorrent API
call:

```console
$ qbit-ops trackers export --format csv
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
**`doctor` is the one deliberate exception** — see [Doctor](#doctor):
those same failures are the diagnostic payload it exists to report, so
they appear as `fail` checks in the rendered report instead of on
stderr, in every `--format` including `table`.

### `--quiet` scope

`--quiet` exists only on `status`. It was deliberately not added to the
other read-only commands, including `doctor`: `status` is the only
command whose exit code alone must stay silent to be useful as a
healthchecker (`--quiet` suppresses the report and leaves only
`healthy`/`warning`/`critical`/`unavailable` for a monitoring system to
read). `doctor` also has a multi-value exit code
(pass/warning/failure — see [Doctor](#doctor)), but its whole purpose is
the human/machine-readable report itself; a silent `doctor` would defeat
the command, so `--quiet` was not added to it. The remaining read-only
commands only distinguish success, no-match, and error, which is already
conveyed without needing a silent mode. Adding `--quiet` to e.g.
`torrents list` would mean "emit nothing and exit 0" with no further
contract to build on — see `docs/DECISIONS.md` (2026-07-24).

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
| `status` | Spinner | Single bounded snapshot collection (4 fixed API calls). `status --watch` uses a persistent `Live` display instead — see [Status Watch Mode](#status-watch-mode); the two are never combined. |
| `connection check` | Spinner | One connection attempt. |
| `doctor` | Spinner | One login attempt plus up to four bounded read calls. |
| `torrents list` (no `--tracker`) | Spinner | One bulk `torrents_info()` call; every other filter is applied in memory. |
| `torrents list --tracker ...` | Progress bar | One `torrents_trackers()` call per candidate surviving cheaper filters. |
| `torrents categories` | Spinner | One bulk `torrents_info()` call, in-memory grouping only. |
| `torrents inspect` (`--hash` or `--name`) | Spinner | Single-torrent lookup or one in-memory name search. |
| `trackers list` | Progress bar | One `torrents_trackers()` call per torrent. |
| `trackers status` | Progress bar | Cheap filters applied first via `torrents_info()`, then one `torrents_trackers()` call per torrent that survived them — same shape as `torrents list --tracker`, except a `--tracker` filter here still scans every survivor (see [Tracker Status](#tracker-status)). |
| `trackers inspect` | Progress bar | One `torrents_trackers()` call per torrent. |
| `trackers export` | Progress bar | One `torrents_trackers()` call per torrent. |
| `backup export` | Spinner | Composite of two per-torrent scans; a single spinner is simpler and more honest than two sequential fabricated-total bars. |
| `backup diff` | None (deliberate) | Local JSON file reads plus an in-memory diff — effectively instantaneous, no network call. |
| Bulk torrent actions (`pause`/`resume`/`start`/`reannounce`) | Progress bar | A `--tracker` filter advances once per candidate surviving cheaper filters; every other selector (`--hash`, `--all`, or any combination of the other filters) has no per-item remote work and completes the bar in a single step. |
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

## Tracker Status

`trackers status` aggregates per-torrent tracker observations into
stable, redacted tracker identities and answers: which trackers are in
use, how many torrents/endpoints depend on each, and whether each is
healthy, degraded, failing, disabled, or unknown. It replaced `trackers
health` (removed, not kept alongside this command — see **Migration
note** below): `trackers health` never classified qBittorrent's real
per-endpoint status codes (it only ever distinguished "disabled" from
"everything else"), and its query-variant-detection output rendered raw,
unredacted announce URLs — the same passkey-exposure class already fixed
for `torrents list --tracker`.

```bash
poetry run qbit-ops trackers status
poetry run qbit-ops trackers status --tracker tracker.example
poetry run qbit-ops trackers status --category films --state stalled
poetry run qbit-ops trackers status --format json
poetry run qbit-ops trackers status --verbose
```

**Migration note**: `trackers health`'s query-variant-detection (grouping
announce URLs that differ only by a dynamic query string) has no direct
replacement — it is dropped, not renamed, since it doubled as this
command's secret-exposure surface and `trackers status`'s `host[:port]`
identity model cannot express it without reintroducing raw URLs. Use
`trackers list --match without-query` for a redacted-adjacent view of the
same grouping (it counts torrents per without-query URL, but does not
enumerate the differing variants). `trackers list` itself still renders
full tracker URLs — a known, pre-existing gap, out of scope for this
phase.

| Before | After |
| --- | --- |
| `qbit-ops trackers health` | `qbit-ops trackers status` |
| `qbit-ops trackers health --format json` | `qbit-ops trackers status --format json` |

### Filters

`trackers status` accepts the same shared torrent filters as `torrents
list` (see [Torrent Filters](#torrent-filters)): `--category`, `--state`,
`--completed`/`--incomplete`, `--active`/`--inactive`, `--stalled`,
`--errored`. `--tracker` additionally restricts the *report* to one
normalized identity: unlike `torrents list --tracker`, it does not
narrow which torrents get scanned (the identity a torrent's trackers
normalize to is not known until they are read) — every torrent surviving
the cheaper filters is still scanned, and the resulting aggregates are
filtered down to the one requested identity afterward. Combination
semantics (repeated values OR, different filter types AND, the two
locally-provable contradictions rejected before any API call) are
identical to `torrents list`.

### Tracker identity

Every tracker is identified as `host` or `host:port`
(`app.trackers.normalize_tracker_host`) — never a full announce URL. This
is the exact same function `torrents list --tracker` and the bulk
mutation commands use, so "the same tracker" always means the same thing
across every command. Scheme, path, query string, and userinfo (where a
passkey commonly lives) are always discarded before an identity is
computed, rendered, or compared. Port numbers are preserved verbatim, not
collapsed to a scheme's default (`https://host:443` and `https://host`
are two distinct identities, `host:443` and `host`) — this keeps
`normalize_tracker_host`'s behavior identical everywhere it is used
rather than inventing a second, scheme-aware notion of "the same host".
DHT/PeX/LSD pseudo-trackers (qBittorrent represents these as sentinel
strings like `"** [DHT] **"`, not URLs) are excluded from the report
entirely: they have no host to normalize and are not operationally
meaningful trackers to report on.

### Raw status mapping

Each observed tracker endpoint's raw qBittorrent `status` (the real
`qbittorrentapi.definitions.TrackerStatus` int enum) maps to one
`TrackerHealth`:

| Raw status | Meaning | `TrackerHealth` |
| --- | --- | --- |
| `0` (`DISABLED`) | Intentionally disabled | `disabled` |
| `1` (`NOT_CONTACTED`) | No announce attempted yet | `warning` |
| `2` (`WORKING`) | Last announce succeeded | `healthy` |
| `3` (`UPDATING`) | Announce in flight | `healthy` |
| `4` (`NOT_WORKING`) | Last announce failed | `critical` |
| `5` (`TRACKER_ERROR`) | Tracker returned an error | `critical` |
| `6` (`UNREACHABLE`) | Tracker could not be reached | `critical` |
| anything else / unparsable | Not classifiable | `unknown` |

`UPDATING` maps to `healthy`, not `warning`: it means an announce is
actively in flight, the most transient state qBittorrent reports, and a
normal working instance can be observed mid-announce at any moment —
scoring it `warning` would make a healthy instance flap between exit
codes `0` and `1` for no operational reason. Unknown or unrecognized
values map to `unknown`, are counted, and are always visible — qbit-ops
never invents a severity it cannot justify.

### Aggregate health

Each tracker identity's health is computed from its **enabled**
endpoints only — disabled endpoints are excluded from every comparison
below, so an intentionally-disabled tracker can never push a working
tracker toward `warning`/`critical`:

1. No enabled endpoints at all → `disabled` (every observation was an
   intentional disable).
2. Every enabled endpoint is `critical` → `critical`.
3. Every enabled endpoint is `healthy` → `healthy`.
4. Every enabled endpoint is `unknown` → `unknown`.
5. Otherwise (a genuine mix) → `warning`.

A tracker with some healthy and some failing endpoints is `warning`, not
`critical`: one failing endpoint never drowns out others that are
working. The report's `overall_health` is the most severe of: `critical`
if any tracker is `critical`; `warning` if any tracker is `warning` or
`unknown`, or if any (but not all) torrents failed tracker collection;
`unavailable` if tracker collection failed for **every** matched torrent
(and at least one torrent matched); `healthy` otherwise, including an
empty selection (no torrents matched, or `--tracker` matched no observed
identity) — a narrow filter matching nothing is not itself a finding.

### Partial collection failures

A single torrent's `torrents_trackers()` call failing does not discard
already-collected observations from other torrents: `trackers status`
continues past it, counts it in `collection_errors` (always present in
every output format), and `overall_health` can never read fully
`healthy` while `collection_errors > 0` — see the precedence above.

### API-call behavior

One `torrents_info()` call, then at most one `torrents_trackers()` call
per torrent surviving the cheap filters (`--category`/`--state`/
`--completed`/`--incomplete`/`--active`/`--inactive`/`--stalled`/
`--errored`) — identical shape to `torrents list --tracker`'s budget,
except `--tracker` here does not reduce the candidate set (see
**Filters** above). Torrents excluded by a cheap filter are never
scanned. Progress reporting never changes this call count (see
[Progress & Spinner Behavior](#progress--spinner-behavior)).

### Exit codes

`trackers status` reports operational health, not success/failure, using
its own codes (`app.tracker_status.tracker_status_exit_code`, mirrored by
`app.main.TrackerStatusExitCode`):

- `0`: `healthy` — includes an empty selection and a report where every
  finding is `disabled`.
- `1`: `warning` — at least one tracker is `warning`/`unknown`, or
  tracker collection partially failed, and nothing is `critical`.
- `2`: `critical` — at least one tracker is `critical`.
- `3`: `unavailable` — tracker collection failed for every matched
  torrent.
- `4`: invalid CLI usage (e.g. `--completed --incomplete`), rejected
  before any qBittorrent API call.

Deliberately its own scheme, not `ExitCode.NO_MATCH` (`2`): here `2`
means `critical`, so reusing the shared no-match code would collide with
a real severity meaning. An empty selection is therefore `0`
(`healthy`), diverging intentionally from `trackers inspect`'s no-match
convention.

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
- filter: hash|all|filter
- value: ...    (the resolved full hash, "*" for --all, or a concise
                 description of every combined filter, e.g.
                 "category=sonarr, state=stalled")
- scanned: X
- matched: X
- modified: X
- skipped: X
- status: PREVIEW|APPLIED|CANCELLED|NO_MATCH|NO_CHANGES
```

**Migration note** — `filter`/`value` used to name exactly one selector
(`category`, `tracker`, `completed`, …) with a matching raw value, plus a
`match: exact|without-query` row when `--tracker` was used. Since bulk
mutations can now combine multiple filters, `filter` collapses to three
cases (`hash`/`all`/`filter`) and `value` renders every active filter via
the same concise, secret-free description used by `torrents list`'s
`Filter:` line (`app.torrents.describe_torrent_filter`) — never a raw
tracker URL. `match` is gone entirely: `--tracker` is hostname-matched now,
so there is no comparison mode left to report.

Pass `--verbose` on any bulk modification command to print impacted
torrents after the summary.

See [Mutation status vocabulary](#mutation-status-vocabulary) for exactly
what each `status` value means and when it appears.

## Exit Codes

Every command except `status` and `doctor` uses:

- `0`: success.
- `1`: configuration, connection, authentication or API error — **also used
  when a `--hash` prefix is ambiguous** (matches several torrents). No new
  exit code was introduced for ambiguity; it is treated as a validation
  error, distinct from "no match".
- `2`: the command completed but matched no torrent — including an
  unresolvable `--hash` (or, for `backup diff`, the two exports differ).

Commands using exit code `2` on no match: `torrents inspect`, `torrents
list` with any filter applied (plain, unfiltered `torrents list` never
uses `2` for an empty instance — see [Torrent
Filters](#torrent-filters)), `torrents pause`, `torrents resume`, `torrents
start`, `torrents reannounce`, `trackers inspect`, `trackers
add-if-present`, `trackers remove`, `trackers replace`, `trackers
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

This health-based mapping applies to **one-shot** `status` only.
`status --watch` uses a separate, small set of process exit codes — see
["Exit codes differ from one-shot `status`"](#exit-codes-differ-from-one-shot-status)
in [Status Watch Mode](#status-watch-mode).

### `doctor` exit codes

`doctor` also reports a severity, not success/failure, using its own
codes (`app.doctor.doctor_exit_code`, mirrored by `app.main.DoctorExitCode`
for readability at call sites — see [Doctor](#doctor) for the full check
catalogue and [Exit codes](#overall-status-and-exit-codes) there for
detail):

- `0`: all checks `pass`.
- `1`: one or more `warning`s, no `fail`.
- `2`: one or more `fail`s.
- `4`: invalid CLI invocation preventing doctor from starting (currently
  unreachable — see [Doctor](#doctor)).
in [Status Watch Mode](#status-watch-mode).
