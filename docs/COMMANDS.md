# Command Reference

Full command reference for `qbit-ops`. See the [README](../README.md) for
installation, configuration and the safety model.

The examples below use `poetry run`. If `qbit-ops` is installed with `pipx`,
drop the `poetry run` prefix.

## Table of Contents

- [Status](#status)
- [Status Watch Mode](#status-watch-mode)
- [Doctor](#doctor)
- [TUI](#tui)
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
- [Explain](#explain)
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
| `COMPAT002` | compatibility | Matrix-aware compatibility evidence: classifies the observed exact application version against the packaged compatibility manifest (`qbit_ops.qbit.compatibility`) -- see [Compatibility evidence (COMPAT002/COMPAT003)](#compatibility-evidence-compat002compat003) below. |
| `COMPAT003` | compatibility | Required Web API capabilities: whether the observed Web API version exposes the capabilities qbit-ops's mutation commands actually rely on (reannounce, tracker edit/remove) -- independent of COMPAT002, since a version absent from the matrix is not itself a capability problem. |
| `RUNTIME001` | runtime | Torrent listing succeeds (`torrents_info()`). |
| `RUNTIME002` | runtime | Global transfer info succeeds (`transfer_info()`). |
| `RUNTIME003` | runtime | Every torrent's state is recognized (reuses `qbit_ops.status.classify_torrent_state`, the exact same vocabulary `status`/`status --watch` use). |

### Compatibility evidence (COMPAT002/COMPAT003)

`COMPAT002` reads the packaged compatibility evidence manifest
(`qbit_ops.qbit.compatibility.load_compatibility_evidence()`, backed by
`qbit_ops/data/qbittorrent-matrix.toml` -- see `docs/COMPATIBILITY.md`
§10) and compares the *exact* observed application version against it.
There is no second, duplicated list of versions in `doctor.py` --
`COMPAT002` always reads the same packaged manifest every other
consumer does. Five outcomes, none of which ever says
`supported`/`unsupported`/`compatible`/`incompatible`:

| Case | Status | Wording |
| --- | --- | --- |
| Exact application version **and** exact Web API version both match a tested entry | `pass` | "Container integration tested against this exact qBittorrent \<version\> and Web API \<version\> version, on \<architecture\>." (never claims other architectures are incompatible) |
| Exact application version matches, but the observed Web API differs from that entry's recorded evidence | `warning` | "...matches an exact container-integration-tested release, but the observed Web API version (...) differs from tested evidence (...)." |
| Version strictly between the oldest and newest tested entries, itself untested | `pass` | "...is exact version not container-integration tested (between tested evidence \<oldest\> and \<newest\>); no incompatibility is known." (never infers support for the intervening range) |
| Version newer than the newest tested entry | `warning` | "...is newer than the latest container-tested evidence (...)." |
| Version older than the oldest tested entry | `warning` | "...is older than the oldest container-tested evidence (...)." |

An unparsable/unavailable version leaves `COMPAT002` `skipped` (via
`COMPAT001`'s existing contract, unchanged). If the packaged manifest
itself cannot be read, `COMPAT002` is `warning`, never a crash.

`COMPAT003` is a **separate concern**: given only the observed Web API
version, it checks whether the two capabilities qbit-ops's mutation
commands actually depend on -- `torrents/reannounce` (Web API ≥
`2.0.2`) and `torrents/editTracker`/`torrents/removeTrackers` (Web API
≥ `2.2.0`), both declared floors from `qbittorrent-api` itself, see
`docs/COMPATIBILITY.md` §3 -- are available. A version absent from the
compatibility matrix is not itself a capability failure, and a real
capability floor is not itself compatibility evidence: an untested-but-
plausible version (`COMPAT002` `pass`) can still be missing a real,
version-gated capability (`COMPAT003` `warning`), and the two checks
are free to disagree. No endpoint threshold beyond those two documented
floors is invented here.

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
them; an unreadable version skips `COMPAT002`'s evidence classification
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
(`qbit_ops.doctor._redact`) that strips URL userinfo and the exact configured
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

## TUI

```bash
qbit-ops tui
qbit-ops tui --interval 10
```

`qbit-ops tui` is a **read-only**, Overview-first interactive terminal
UI (see [docs/TUI_ARCHITECTURE_REVIEW.md](TUI_ARCHITECTURE_REVIEW.md)):
it opens on an **Overview** workspace explaining the current condition
of the qBittorrent instance, with a second **Torrents** workspace for
browsing, filtering, searching, and inspecting individual torrents. It
adds interactive navigation and inspection that `status --watch` does
not provide — `status --watch` is deliberately status-only, the TUI is
not.

**No mutation is reachable from the TUI.** It shares the exact same
`TorrentFilter` vocabulary and safe torrent/tracker data as the rest of
the CLI (`qbit_ops.torrents`, `qbit_ops.status`, `qbit_ops.app_services`) but never
imports any `plan_*`/`apply_*` mutation function, `qbit_ops.cli`, or any
raw-tracker-URL-producing helper — enforced by a static (AST) test,
not just a runtime one (`tests/test_tui_security.py`).

### Optional dependency

The TUI requires the optional `tui` extra. **`qbit-ops` is not
published on PyPI** — every install form below is a *path*
(`.`/`'.[tui]'`), run from inside a cloned repository checkout, never a
bare package name. `pipx install "qbit-ops[tui]"` (no path) always
fails with "No matching distribution found", since that syntax tells
`pip`/`pipx` to look the package up on PyPI, where it does not exist.

```bash
git clone https://github.com/LECOQQ/qbit-ops.git && cd qbit-ops

# local development (short form of --extras)
poetry install -E tui
poetry run qbit-ops tui

# a fresh pipx install
pipx install '.[tui]'

# an editable checkout, tracking the git clone instead of copying it
pipx install --editable '.[tui]'
```

**Upgrading an existing `pipx` install does not add the extra
automatically.** If `qbit-ops` was originally installed with plain
`pipx install .` (no `[tui]`), `pipx reinstall`/`pipx upgrade` will
*not* retroactively pull in Textual — `pipx` remembers the package spec
used at install time. Uninstall and reinstall with the extra instead
(from the cloned repo):

```bash
pipx uninstall qbit-ops
pipx install '.[tui]'
```

Quoting `'.[tui]'` is required in most shells (`zsh` in particular)
since an unquoted `[tui]` is glob-expanded.

Every other command works identically whether or not this extra is
installed, and never imports Textual — `qbit-ops tui` imports it
lazily, inside the command body, only when actually invoked. Running
`qbit-ops tui` without the extra installed fails immediately, before
any qBittorrent client is created, with one actionable message and no
traceback:

```console
$ qbit-ops tui
✗ ERROR The TUI requires the optional 'tui' extra, which is not installed.

qbit-ops is not published on PyPI, so "pipx install qbit-ops[tui]" alone
will not work -- install from a repository checkout instead (see
README.md, section "Install", for the clone URL):
  cd <your qbit-ops checkout>
  pipx uninstall qbit-ops  # only if already installed without the extra
  pipx install ".[tui]"

or, for local development:
  poetry install --extras tui
```

(This message deliberately never prints a literal URL — every message
routed through `print_error()` is passed through a tracker-secret
redaction filter that would silently replace a real URL with
`<redacted-url>`. It points at `README.md` instead.)

### Workspaces

```text
1, g   Overview
2, t   Torrents
```

**Overview** (the default, landing workspace) explains the current
condition of the instance, built entirely from the same periodic
refresh already used everywhere else in the TUI — no extra API call,
no tracker-wide scan. It is grouped into distinct cards, deliberately
kept separate rather than merged into one block of counters that could
read like a single mutually exclusive partition (a torrent can be
completed, seeding, *and* stopped, all at once — those are three
different dimensions, not three slices of one total):

* **Connection** — connected / reconnecting / unavailable /
  authentication failed, the last successful refresh time (local time,
  timezone labeled), a `STALE` flag when showing last-good data after a
  failed refresh, and the qBittorrent/Web API version where available.
* **Transfer** — current download/upload rates, compact human-readable
  formatting.
* **Activity** — total, downloading, seeding, stopped, checking — a
  torrent's current transfer state, reusing the existing
  `qbit_ops.torrent_states` classifier (no second one).
* **Completion** — completed / incomplete, by progress — independent of
  Activity: a completed torrent can be seeding *or* stopped.
* **Attention** — stalled, errored, unknown — conditions worth an
  operator's attention, given stronger visual weight (a distinct card
  border color), independent of Activity/Completion.
* **Health and alerts** — the overall health plus every grounded reason
  behind it (e.g. "3 stalled torrent(s)", "1 torrent(s) reporting an
  error"), the same `StatusAlert` data `status` reports — never a bare
  "warning" with no explanation, never an invented recommendation or
  confidence score. When nothing is wrong: "Healthy · 0 finding(s)".
* A visible `Enter` / `t` hint to jump to Torrents.

At wide terminal sizes the cards lay out in a compact two-column grid;
below 100 columns they stack vertically — the same content either way.

**Torrents** is the detailed browsing workspace: the table, search,
filters, and focused-torrent details/copy/explain described below.
Switching workspaces is purely local (zero qBittorrent calls) and
preserves search/filter state and the last-focused torrent — it never
leaves a widget from the now-hidden workspace focused, and is blocked
while any modal (Filters/Details/Help/Explain) is open.

### Controls

```text
1, g           Overview
2, t           Torrents
up/down, j/k   navigate torrents (Torrents workspace) -- moves focus
/              open a search box (Enter to apply, Esc to close)
f              open filters (a modal, at every terminal width)
enter          Overview: browse torrents · Torrents: open focused details
c              copy the focused torrent's full hash
e              explain the focused torrent's current state
r              refresh the focused torrent's tracker details
space          toggle the focused torrent's selection
ctrl+a         select every currently visible torrent
ctrl+d         deselect every torrent (the explicit counterpart to ctrl+a)
a              open Actions for the current selection
esc            close a modal/help/search box, clear a non-empty
               selection, or return focus to the list
?              open help (a real modal, listing only working bindings)
q              quit
```

Inside any modal that has more than one field or button (Filters,
Actions, Preview), `Tab`/`Shift+Tab` **and** `↑`/`↓` both move between
them, and `Enter` presses whichever button currently has focus — the
same as clicking it.

**Focus, selection, and visibility are three distinct concepts:**
*focused* is the single highlighted row (keyboard navigation moves it);
*selected* is any number of torrents explicitly marked with `space`/
`ctrl+a` for a bulk action; *visible* is whatever the current filter/
search leaves in the table. Copy, Explain, and Refresh details always
act on the **focused** torrent only, never the selection — moving focus
never selects anything by itself.

The footer only ever advertises what is actually reachable right now:
Overview shows just Torrents/Help/Quit; Torrents without a focused
torrent adds Search/Filters/Overview; focusing a torrent additionally
shows Copy/Explain/Refresh; a non-empty selection additionally shows
Actions. This is enforced by Textual's own `check_action` mechanism,
not cosmetic — the underlying key press is genuinely inert outside its
context, not merely unlisted. Help (`?`) is one concise, scrollable
table grouped as "Global" / "Torrents workspace" rather than repeating
per-line context notes.

`q` quits from anywhere the App's own bindings are reachable — the
torrent list, the details panel, any Checkbox — but while a text box
(search, or a filter's category/state field) has focus, typing `q`
inserts the literal character instead, exactly like any other letter (a
category can legitimately be named "queue"); press `Esc` first to
return to the torrent list, then `q`. This is standard text-editing
behavior, not a bug — Textual's `Input` widget consumes every printable
character itself, before any single-key binding (this project's or
Textual's own) can intercept it. `c`/`e`/`r`/`q` are also silently
inert while a modal is on top *and hasn't explicitly re-declared them*
(a distinct Textual behavior from the `Input`-consumes-characters case
above — see docs/MEMORY.md); Copy hash is the one action re-declared on
the Details modal specifically, since it is a documented Details-view
action.

The torrent table shows, in this order: **Name** (gets the remaining
width), **State**, **Progress** always; **Down**/**Up** once the
terminal is 100 columns or wider; **Ratio**/**Category** once it is 130
columns or wider. Row identity (which torrent a row refers to) is
always the full hash internally regardless of which columns are
visible — narrower terminals never lose the ability to select, focus,
copy, or explain a torrent, only the number of visible columns changes.

Filters open as a modal at **every** terminal width (there is no
permanently visible sidebar) and use the exact same fields, tokens, and
AND/OR combination rules as [Torrent Filters](#torrent-filters)
(`--category`, `--state`, `--completed`/`--incomplete`,
`--active`/`--inactive`, `--stalled`, `--errored`). Category and state
are comma-separated text inputs; Completion (Any/Completed/Incomplete)
and Activity (Any/Active/Inactive) are each an exclusive choice (a
`RadioSet`), so a contradictory pair (both Completed and Incomplete) is
structurally impossible through the UI — `torrents list`'s own
`--completed --incomplete` rejection remains as defense in depth, never
actually reachable from here; Stalled/Errored remain independent
checkboxes since they are not opposites of anything. Filter edits apply
live, but Apply/Cancel/Clear are three distinct, deterministic actions,
each reachable both by a binding and by a visible button in the modal:

```text
enter / Apply button    apply (already in effect) and close the modal
esc / Cancel button     cancel: revert to the filter that was active
                        when the modal opened, then close
ctrl+r / Clear button   clear: reset to no filter at all; the modal
                        stays open
```

**What the draft affects, and what it does not.** Filter fields apply
*live* as you type: the torrent list behind the modal updates on every
keystroke, so you can see what a filter will match before committing
it. Because category matching is exact, a partially typed "films"
transiently matches nothing — that is expected, and the list fills back
in as you finish typing.

What is deliberately **deferred** is only the reconciliation of your
*selection* against the filter. Typing a draft never drops selected
torrents, and neither does a periodic refresh landing mid-edit.
Reconciliation happens exactly once, at a commit point:

```text
Apply    commit the draft, then reconcile the selection
Cancel   restore the filter that was active when the modal opened;
         the selection is left exactly as it was
Clear    reset the draft to no filter; the modal stays open
```

Any selection dropped for becoming invisible at a commit point is
reported (`N hidden selection(s) cleared.`). Cancel leaves both the
active filter and the selection exactly as they were, no matter how
many refreshes landed while the modal was open.

Search is a separate, UI-only, read-only match, live as you type: a
case-insensitive substring match on torrent **name**, OR a
case-insensitive leading-prefix match on torrent **hash** (covers both
a full hash and a shortened prefix) — no fuzzy scoring, no ambiguity
error (this is a list filter, not `torrents inspect --name`'s
single-target resolution), and never a qBittorrent API call. Neither
filters nor search ever call `torrents_trackers()`.

The active filter and search are always shown as one concise line above
the torrent table:

```text
146 shown / 1,105
stalled · category: films · search: ubuntu
```

or, with a non-empty selection (shown right after the shown/total
count):

```text
24 shown / 1,106 · 7 selected · stalled · category: films
```

Details are always reachable, at every terminal width: an inline side
panel when the terminal is wide enough, a dedicated modal (opened by
`Enter`) otherwise — narrow terminals never simply lose access to safe
torrent details. Details are grouped into three sections:

* **Identity** — name, a *shortened* hash (e.g.
  `8ac34f89…f95704b8`), category. The full hash is never wrapped or
  dumped inline; use `c` (Copy hash) to get it.
* **Transfer** — state, progress, ratio, download/upload rate, live
  from the current periodic torrent snapshot.
* **Trackers** — one line per endpoint, identity and health/status as
  clearly separate columns, a sanitized message only when one exists
  (never a bare status word with nothing identifying which tracker it
  belongs to, and never a duplicated "disabled disabled"), plus the
  fetch timestamp (local time) — never a raw announce URL, path
  segment, query value, userinfo, or passkey.

### Multi-selection and bulk actions

```text
space      toggle the focused torrent's selection
ctrl+a     select every currently visible torrent
ctrl+d     deselect every torrent (works unconditionally)
a          open Actions for the current selection
esc        clear the selection (when non-empty and no modal is open)
```

Selection is explicit and separate from focus: navigating the table
never selects anything, and `ctrl+a` only ever selects the torrents
**currently visible** under the active filter/search — never a hidden
torrent. Note the honest consequence: with no filter and no search
active, every torrent is visible, so `ctrl+a` does then select the
whole instance. The guarantee is "never beyond what is displayed", not
"never many" — filter or search first if you want a bounded scope.
Separately, an empty selection is always empty and never implicitly
means "all". If a filter or search change hides an
already-selected torrent, it is dropped from the selection the moment
that change is applied (Filters' Apply/Clear/Cancel, or as you type in
Search), with a concise notification for Filters (`N hidden
selection(s) cleared.`) — Search reconciles silently on every
keystroke, since narrating every character typed would be noise, not
signal. A periodic refresh that removes a torrent entirely also drops
it from the selection.

With a non-empty selection, `a` opens **Actions**:

```text
Actions · 7 selected

Pause
Resume
Reannounce

Cancel
```

Choosing an action builds a **frozen plan** from exactly the selected
hashes at that moment (`tuple(sorted(selected_hashes))`) and opens a
**Preview** — no qBittorrent call is needed for this, since the plan is
built entirely from the torrent data the TUI already has from its last
periodic refresh:

```text
Reannounce · Preview

Selected             12
Will reannounce      10
Skipped               2
Snapshot             14:32:05 CEST

Affected torrents
✓ Ubuntu ISO             8ac34f89…f95704b8
✓ Debian ISO             1a2b3c4d…5e6f7089
…

Cancel                                    Apply
```

The plan shown is frozen the moment Preview opens: it does not change
even if the selection, filters, search, or focus change in the
background while the modal is open.

**The enumeration is truncated, the plan is not.** Preview lists at
most 50 affected torrents and 20 skipped ones by name, followed by
`… and N more`. Truncation is purely visual: the counts above the list
and the frozen plan itself always cover every selected canonical hash,
and Apply acts on the complete plan — not merely on the sample shown. Only **Apply** — an explicit
button press, never automatic — mutates anything; Escape or Cancel
close the modal with zero API calls and leave the selection untouched
for reconsideration. Because these are the same LOW-risk operations
`torrents pause`/`resume`/`reannounce` already perform without a
confirmation prompt, Apply does not ask for a second yes/no
confirmation — the Preview itself is the confirmation step. While
Apply is running, both buttons are disabled and the button reads
"Applying…", so double-pressing (or pressing Enter twice) can never
dispatch a second mutation; Cancel/Escape are also refused until it
finishes, since cancelling out from under an in-flight mutation would
leave nothing to observe its result.

**Serialized remote access.** Every blocking qBittorrent operation the
TUI can reach — the periodic refresh, focused/manual tracker-detail
collection, and Apply — is serialized through one coordinator, so at
most one of them ever uses the client at a time, in *either* direction.
An Apply requested while a refresh or detail fetch is still in flight
waits behind it rather than sharing the HTTP session; a periodic tick
arriving during a mutation is skipped (coalesced, never queued). One
refresh is triggered immediately once a mutation completes. The
interface stays responsive throughout: all of this happens on worker
threads, never on the event loop.

**Stale previews cannot Apply.** A preview is grounded in exactly one
snapshot generation. If the connection leaves `connected` — or a
refresh fails and the data becomes stale — the preview stays fully
readable but Apply is withdrawn:

```text
Snapshot stale -- qBittorrent is currently unreachable; this preview
uses last-known data.
Apply disabled — rebuild the preview after reconnection.
```

Staleness is **sticky**: reconnecting deliberately does *not* re-enable
an old preview, because its plan was computed against torrent states
that are no longer known to be accurate. Close it and build a new one.
The refusal is real, not cosmetic — the keyboard cannot bypass what the
disabled button forbids, and no mutation call is made while stale,
reconnecting, unavailable, authentication-failed, or
configuration-failed.

The result is always reported truthfully, never inferred merely from
"Apply was pressed":

```text
Submitted

Action submitted for 10 torrent(s).
A refresh will show the latest observable state.

2 already satisfied 'reannounce'.
```

```text
No changes

2 selected torrent(s) already satisfied 'pause'.
```

```text
Nothing to do

No selected torrents were found in the current snapshot.
3 selected torrent(s) had disappeared before the plan was built.
```

Note the deliberate distinction: **"already satisfied" and "not found"
are never conflated.** A torrent that vanished between selection and
planning is reported as missing, not as already being in the requested
state.

Note also the deliberate wording of a success: **"submitted", not
"applied to each torrent".** qBittorrent's bulk endpoints confirm that
the request was accepted for exactly the planned hashes; they do not
report a per-hash state transition. qbit-ops does not fabricate that
information — it tells you the request went out and that a refresh will
show the observable state.

Failures are classified into distinct, grounded categories, always by
looking at the raised error itself first (and only then at its
underlying cause as supporting context):

```text
Configuration invalid   local .env problem -- not a software defect
Authentication failed   check QBIT_USER / QBIT_PASSWORD
Unavailable             qBittorrent unreachable; nothing confirmed sent
Internal error          a genuine qbit-ops defect
```

A recoverable connection failure is therefore never reported as an
internal defect merely because it carries an opaque cause, and an
invalid local configuration is never blamed on qbit-ops. No mutation is
ever retried automatically after any failure. Note that an
authentication or configuration failure *during Apply* is reported in
the result modal and does **not** put the TUI into its blocking
connection state (unlike the same failure during a periodic refresh) —
the frozen plan stays inspectable, and a fresh preview is required
before retrying.

**A submitted mutation always leaves a visible outcome.** If its
Preview is still the active screen, the Result modal is shown. If it is
not — because another modal was opened over it — an unrelated modal is
never closed or replaced to make room for the result, and a closed
modal is never reopened.

Either way, the latest outcome is recorded on a compact persistent line
in the Torrents workspace:

```text
Last action · Pause submitted for 3 torrent(s) · 14:32:18 CEST
```

That line is the durable record: it stays until the next bulk action
replaces it, survives its originating Preview disappearing, and remains
after any transient toast has expired (toasts are supplemental only,
and disappear after a few seconds). It shows exactly one result — the
most recent — and is hidden entirely until you have run a bulk action.
qbit-ops keeps no mutation history beyond it. The line never dispatches
anything and never changes your selection.

It distinguishes every outcome the TUI can produce: submitted, no
change needed, nothing found, cancelled before dispatch, and each
failure category (configuration, authentication, unavailable,
internal).

**Cancelled before dispatch.** Because remote access is serialized, an
Apply can be queued behind an in-flight read. If you quit qbit-ops
while it is still queued, the queued operation is abandoned before the
qBittorrent call is made. Concretely:

* no mutation request is sent — qBittorrent receives nothing;
* no submitted-selection policy is applied, so your selection is left
  exactly as it was;
* no post-mutation refresh is scheduled;
* no success is reported.

Note what this deliberately does **not** promise: since the only thing
that revokes an Apply's authority today is quitting the application,
there is no running interface left to show you the outcome, and none is
expected. The guarantee here is material, not visual — the action does
not happen. qbit-ops still carries an internal *cancelled before
dispatch* representation, kept distinct from a remote failure (nothing
failed) and from a submitted request (nothing was sent); it is
defence in depth for any future authority-loss path that does not end
the process, and it keeps the failure taxonomy honest.

Dismissing a result never re-applies anything. It only applies the
selection policy:

```text
Submitted     drop exactly the hashes the plan submitted
No changes    drop the plan's hashes (found, already satisfied)
Nothing to do drop only the hashes proven absent from the snapshot
any failure   keep the selection so you can retry deliberately
```

Whatever remains is then reconciled against currently visible torrents,
preserving the "selection ⊆ visible" rule.

Reannounce never exposes a tracker URL anywhere in Actions/Preview/
Result, and never repeats or schedules itself automatically. No
torrent/file deletion, tracker add/remove/replace, category editing,
priority change, or whole-instance (`--all`-equivalent) selector is
reachable from the TUI — those remain CLI-only, at their existing risk
tiers.

### Copy hash

```text
c   Copy hash
```

Copies the focused torrent's **full, canonical** hash (never the
shortened display value, never tracker data) to the clipboard, from
either the Torrents table or the Details view (inline or modal), and
shows a concise confirmation: `Copied hash 8ac34f89…f95704b8`. A safe,
non-crashing notification ("No torrent focused.") when nothing is
focused. Performs no qBittorrent API call.

Uses Textual's own `App.copy_to_clipboard` (an OSC 52 terminal escape
sequence). **Some terminal emulators do not support OSC 52** — notably
macOS's built-in Terminal.app — and will silently not receive the
copied value. This is a terminal capability limitation qbit-ops cannot
detect or work around from inside the TUI; if `c` shows a confirmation
but pasting elsewhere does not produce the hash, the terminal emulator
in use is the likely cause, not a qbit-ops defect.

### Explain

```text
e   Explain
```

Opens a modal, evidence-based explanation of the focused torrent's
current state — the exact same rule catalogue, finding codes,
severities, and evidence/limitation semantics `explain torrent` uses on
the CLI (`qbit_ops.explain.build_torrent_explanation`, shared by both
interfaces; there is no second, TUI-only explanation catalogue). Only
meaningful in the Torrents workspace; a safe notification ("No torrent
focused.") when nothing is focused, never a crash.

**API-call budget**: if the focused torrent's tracker details are
already loaded (the common case — focusing a torrent already triggers
one background fetch), `e` performs **zero** additional API calls. If
they are not yet loaded (still fetching, or an earlier fetch failed),
`e` reuses the already in-flight fetch if there is one, or starts
**at most one** `torrents_trackers()` call otherwise — never a second,
redundant call, and never `torrents_info()`. There is no tracker-wide
scan.

The modal shows a header (torrent name, overall severity), when the
torrent snapshot was refreshed and tracker details were fetched (both
local time), a summary, each finding's severity/title/explanation/
evidence/limitations, and safe CLI commands to consider (display-only
text — `e` never executes anything, and suggested commands never
include `--no-dry-run`/`--yes`). If the TUI's own data is currently
stale (qBittorrent unreachable), the modal says so explicitly rather
than presenting a stale explanation as current. Closes with `Esc`,
scrolls at every tested terminal size, and blocks workspace switching
while open, exactly like Filters/Details/Help.

**Race safety**: if focus moves to a different torrent while a fetch
Explain triggered is still in flight, the stale result is discarded —
it never populates a modal for the wrong torrent. If the modal is
closed before the result arrives, it is never reopened automatically.
If the focused torrent disappears before the explanation can be built,
a not-found notification is shown instead of an empty or broken modal.

Textual's built-in command palette (`Ctrl+P`) is disabled — it has no
qbit-ops commands yet and only added a confusing `^p palette` hint to
the footer. Workspace navigation is always the explicit `1`/`g`/`2`/`t`
bindings above, never the palette.

### Narrow-terminal layout

Below 100 columns, the Filters and Details panels are not shown inline
(there is no room), but every read-only capability they provide stays
reachable:

* `f` opens Filters in a modal dialog instead of an inline panel —
  the same fields, same live application, same Apply/Cancel/Clear.
* `enter` opens the currently focused torrent's Details in a modal
  dialog; `c` (Copy hash) works there too.
* `e` (Explain) opens the same modal regardless of width.
* `Esc` closes any modal and returns to the torrent list.

Resizing between wide and narrow (and back) never loses your place: the
torrent list, its scroll position, and the focused torrent are
unaffected, no qBittorrent API call is triggered by a resize alone, and
a widget that becomes hidden by the layout change is never left
focused-but-invisible — focus moves back to the torrent list instead.

### Refresh model

`--interval SECONDS` (default `5.0`, same default as `status --watch`)
sets how often the TUI refreshes the Overview and torrent table. Each
refresh performs exactly four qBittorrent API calls —
`app_version()`, `app_web_api_version()`, `transfer_info()`, and one
`torrents_info()` shared by both the Overview's counters and the
torrent table (never a second, redundant `torrents_info()` call) — the
same bounded budget `status` uses. Filter and search changes are
applied entirely in memory against the last fetched torrent list: zero
API calls.

Focusing a torrent (via keyboard navigation) fetches that torrent's
tracker details with **at most one** `torrents_trackers()` call — never
a scan of every torrent, and never called again automatically on the
next periodic tick. The Details panel (inline or modal) shows when
tracker details were last fetched; press `r` to refresh them manually
without waiting for a new focus change, or `e` to explain using
whatever is currently available. `r`/`e`/`c` are silently ignored (with
a notification for `e`/`c`) when no torrent is focused (e.g. an empty
filter result) — none of them ever fabricates a request.

### Stale data and failure states

A temporary qBittorrent outage discovered during a periodic refresh
does not clear the screen: the last successfully collected status and
torrent list stay visible, marked stale, under a reconnecting banner,
while the TUI keeps retrying at the configured interval. A later
successful refresh clears the banner and the stale marker automatically
— there is no manual "retry" action to take.

Authentication failure and invalid local configuration (a missing or
malformed `.env`) are **not** treated as temporary: the TUI shows a
blocking screen and stops retrying, since neither can self-heal without
fixing the underlying `.env`/credentials and restarting. An unexpected
internal error (a real programming defect, not a remote/temporary
failure) is never silently presented as "qBittorrent is temporarily
unavailable": the TUI stops refreshing and shows a distinct fatal
notice instead.

### Security

Every value the TUI renders is a safe, structured domain output — the
same `StatusSnapshot`/`SelectedTorrent`/`inspect_torrent`/
`ExplanationReport`-shaped data the rest of the CLI already uses.
Tracker identities and messages are always the same structural,
secret-free fields `trackers inspect` renders
(`qbit_ops.torrents.get_safe_tracker_details`): a normalized `host[:port]`
identity, health, scheme, path *shape*, and query parameter *names* —
never a raw announce URL, path value, query value, userinfo, or
unsanitized tracker message. Explain's evidence and suggested commands
are built from that same safe data (and already-classified torrent
state) — a suggested command is always a display-only, dry-run-safe
string, never `--no-dry-run`/`--yes`, and is never executed by the TUI.
Copy hash copies only the full canonical hash string, never a details
block or tracker data. The only mutation surface reachable from the TUI
is exactly two functions — `qbit_ops.torrents.build_bulk_action_plan_from_snapshot`
(pure, builds a plan from an already-fetched snapshot) and
`apply_bulk_torrent_action` (mutates exactly a frozen plan's hashes) —
covering only Pause/Resume/Reannounce; every tracker mutation function,
`qbit_ops.torrents.plan_bulk_torrent_action` (always rescans, accepts
`--all`), and any deletion function remain fully out of reach,
enforced by a static (AST) test, not just a runtime one
(`tests/test_tui_security.py`). See
[docs/PHILOSOPHY.md](PHILOSOPHY.md) §15 and
[docs/TUI_ARCHITECTURE_REVIEW.md](TUI_ARCHITECTURE_REVIEW.md) §10 for
the full invariant and the tests that enforce it.

### Scope

Interactive, but deliberately narrow: the TUI supports explicit
multi-selection and LOW-risk bulk actions (Pause/Resume/Reannounce)
only. It does not include torrent/file deletion, tracker add/remove/
replace, passkey replacement, category editing, priorities, automatic
actions, a whole-instance (`--all`-equivalent) selector, MEDIUM/HIGH-risk
operations, undo, background queues, tracker-wide status/health views,
or a doctor workspace — see
[docs/TUI_ARCHITECTURE_REVIEW.md §12](TUI_ARCHITECTURE_REVIEW.md#12-revised-roadmap)
for the full phased roadmap.

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

One structured, Typer/Rich-free filter model (`qbit_ops.torrents.TorrentFilter`)
and one filtering pipeline (`qbit_ops.torrents.select_torrents`) back `torrents
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

The filtering pipeline (`qbit_ops.torrents.select_torrents`) always loads
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
(`qbit_ops.torrent_states.classify_torrent_state`), so a torrent's group can
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
the **full** tracker URL (`--match exact|without-query`) and rendered
every matching tracker URL verbatim, including any embedded passkey.
Both `--match` and that rendering are gone from `torrents list` and the
four bulk mutation commands' filters (pre-1.0 breaking change; see
`docs/DECISIONS.md`) — hostname matching is strictly safer and does not
require knowing the exact normalized URL qBittorrent stores. A later
tracker-security hardening phase (see `docs/DECISIONS.md`) extended the
same principle to the whole `trackers` command group: `trackers list`,
`trackers inspect`, and `trackers export` no longer have `--match`
either and no longer render a full announce URL — only the four
mutation commands (`add-if-present`/`remove`/`replace`/
`replace-passkey`) still take and act on a raw URL, because qBittorrent's
API requires it. `trackers status` (see [Tracker
Status](#tracker-status)) has always been hostname-only by construction
— like `torrents list --tracker`, it never renders a full announce URL
or passkey.

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

**Safe by default**: `trackers list`, `trackers inspect`, `trackers
status`, and `trackers export` never render a complete tracker announce
URL, passkey, or query value, in any format (`table`/`json`/`jsonl`/
`csv`). All four report the normalized `host[:port]` identity
(`qbit_ops.trackers.normalize_tracker_host`) plus, for `inspect`/`status`, a
structural breakdown (scheme, path *shape*, query parameter *names* —
never values). Only the four bulk mutation commands
(`add-if-present`/`remove`/`replace`/`replace-passkey`) take a raw
`--source`/`--target`/`--tracker` URL as input, because qBittorrent's API
requires the literal stored URL to act on it — see [Matching
Modes](#matching-modes) and the security invariant in
`docs/PHILOSOPHY.md`.

```bash
poetry run qbit-ops trackers list
poetry run qbit-ops trackers list --format json

poetry run qbit-ops trackers status
poetry run qbit-ops trackers status --format json
poetry run qbit-ops trackers status --tracker tracker.example
poetry run qbit-ops trackers status --category films --state stalled

poetry run qbit-ops trackers inspect --tracker tracker.example
poetry run qbit-ops trackers inspect --tracker tracker.example \
  --format json

poetry run qbit-ops trackers export --format json
```

`trackers list` is a lightweight identity inventory — `Tracker /
Torrents / Endpoints` — always exits `0`, regardless of tracker health.
`trackers status` covers the same identities plus health classification
and exits based on the worst observed health (see [Tracker
Status](#tracker-status)). The two commands are kept distinct
deliberately, not merged: `list` is the cheap "what trackers exist"
answer with no health semantics to reason about, `status` is the
health-aware answer — see `docs/DECISIONS.md`.

`trackers export` produces normalized identities only
(`normalized_trackers`, `host[:port]` strings) — it has no raw-URL mode
and no `--include-sensitive` escape hatch. The one place raw tracker
URLs are ever exported is `backup export`, which exists specifically to
produce a restorable backup — see [Backup](#backup).

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
`announce_ts`. Use `--match without-query` (mutation commands only — see
[Matching Modes](#matching-modes)) to compare only the stable tracker
identity.

```bash
poetry run qbit-ops trackers add-if-present \
  --source "http://tracker.example:8080/passkey/announce" \
  --target "https://tracker-b.example/announce" \
  --match without-query --dry-run --verbose

poetry run qbit-ops trackers remove \
  --tracker "http://tracker.example:8080/passkey/announce" \
  --match without-query --dry-run --verbose

poetry run qbit-ops trackers replace \
  --source "http://tracker.example:8080/passkey/announce" \
  --target "https://tracker-b.example/announce" \
  --match without-query --dry-run --verbose
```

## Backup

`backup export` is the project's one deliberately sensitive export: a
restorable backup needs qBittorrent's literal, raw tracker URLs (passkey
included), so — unlike every other command — its `trackers` field is not
redacted. Treat a backup file as a secret: do not paste its contents into
a bug report, chat, or public issue; give it file permissions that match
any other credential file on the host (e.g. `chmod 600`).

`metadata.qbit_host` is the one field inside `backup export` that *is*
redacted (scheme + `host[:port]` only, via `redact_tracker_identity`) —
it is metadata about the qbit-ops connection, not part of the restorable
torrent/tracker data, so it never needs to carry `QBIT_HOST`'s userinfo.

```bash
poetry run qbit-ops backup export --format json > backup.json
poetry run qbit-ops backup diff backup-before.json backup-after.json
poetry run qbit-ops backup diff backup-before.json backup-after.json \
  --format json
poetry run qbit-ops backup diff backup-before.json backup-after.json \
  --reveal-sensitive
```

`backup export --format json` produces:

- export metadata (`exported_at`, qBittorrent versions,
  `qbit_host` — redacted to `scheme://host[:port]`);
- torrent metadata and raw tracker details for every torrent (sensitive);
- normalized tracker identities (`host[:port]`, safe);
- aggregated tracker usage counts.

`backup diff` compares two exports from `backup export` or `trackers
export` and reports torrents added/removed/changed and tracker usage
changes. Its default output is **redacted**: tracker identities in the
diff are shown as `host[:port]`, never a raw URL, even though the input
files may contain raw URLs. Pass `--reveal-sensitive` to see the diff
computed against the raw values instead — an explicit, named opt-in
(never a bare `--yes`), consistent with the rest of the project's
mutation-confirmation naming.

**Redaction limitation**: because redaction happens after the diff is
computed on raw values, two raw URLs on the same host that differ only
by passkey (e.g. after a passkey rotation) can appear in the *redacted*
output as the same identity `added` **and** `removed` — visually a
no-op, even though the diff's exit code still reports a real change. If
a diff `changed`/`added`/`removed` reads like a no-op, re-run with
`--reveal-sensitive` to see the actual (raw) difference before assuming
it's a bug.

`backup diff` does not refuse to print to an interactive terminal the
way a sensitive-export mode might: `--reveal-sensitive` only affects
*this project's own backup artifacts* (already opted into raw storage by
running `backup export`), not a fresh credential exposure, so no
additional refusal was added on top of the explicit flag.

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

### Understand why a torrent or tracker looks off

```bash
poetry run qbit-ops torrents list --stalled
poetry run qbit-ops explain torrent --hash abc123
poetry run qbit-ops trackers status
poetry run qbit-ops explain tracker --tracker tracker.example
```

## Matching Modes

`--match exact|without-query` is a **mutation-only** concept
(`add-if-present`/`remove`/`replace`/`replace-passkey`), where the exact,
raw tracker URL matters for the API calls those commands make:

- `exact` (default): compares the full normalized tracker URL.
- `without-query`: ignores query parameters when comparing trackers.

Both modes preserve the raw qBittorrent URLs for API calls — this matters
for `remove`, since qBittorrent expects the original tracker URL. A
mutation cannot avoid taking a raw `--source`/`--target`/`--tracker` URL
as input for this reason, but its confirmation prompts, previews, and
summaries never echo it back — see [Mutation Risk & Confirmation
Policy](#mutation-risk--confirmation-policy).

Every **read-only** tracker command — `trackers list`, `trackers
status`, `trackers inspect`, `trackers export`, and `torrents list
--tracker` — has no `--match` and no raw-URL comparison mode at all:
none of them need qBittorrent's literal stored URL to act on, so they
always match by the normalized `host[:port]` identity
(`qbit_ops.trackers.normalize_tracker_host`), the same one everywhere. This
is also why they can be safe by default: a command that never needs the
raw URL never has one to accidentally render.

## Format Support Matrix

Every read-only command shares one `--format` option and one
`qbit_ops.cli.rendering.OutputFormat` enum (`table | json | jsonl | csv`). `--output` no
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
| `torrents inspect` (`--hash` or `--name`) | ✅ | ✅ | ✅ | ❌ | no stable tabular shape across both modes (nested, sanitized tracker details for `--hash` — never a raw announce URL) |
| `trackers list` | ✅ | ✅ | ✅ | ✅ | `tracker,torrents,endpoints` rows; normalized identities only, never a raw URL |
| `trackers status` (any filter combination) | ✅ | ✅ | ✅ | ✅ | one row per tracker identity; `tracker,health,torrent_count,endpoint_count,healthy_count,warning_count,critical_count,disabled_count,unknown_count` (CSV omits `representative_message`) |
| `trackers inspect` | ✅ | ✅ | ✅ | ✅ | one row per torrent, matching endpoints reduced to structural fields (health/scheme/path shape/query key names) joined with `; ` — never a raw URL |
| `trackers export` | ✅ | ✅ | ✅ | ❌ | nested per-torrent tracker lists; normalized identities only, never a raw URL |
| `backup export` | ✅ | ✅ | ✅ | ❌ | nested per-torrent tracker lists — **the one export with raw tracker URLs**, see [Backup](#backup) |
| `backup diff` | ✅ | ✅ | ✅ | ❌ | heterogeneous nested sections (added/removed/changed, tracker usage); redacted by default, `--reveal-sensitive` shows raw values |
| `explain torrent` | ✅ | ✅ | ✅ | ❌ | narrative report (summary + findings with evidence/limitations/next commands); no stable flattened row shape |
| `explain tracker` | ✅ | ✅ | ✅ | ❌ | same narrative report shape as `explain torrent` |

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
command by `qbit_ops.cli.rendering.progress_enabled()` and never re-implemented inline.

Progress is always **transient**: it never survives in the final
scrollback. Before a table, a mutation preview, a confirmation prompt, a
cancellation message, an applied summary, or an error is shown, any
active spinner or progress bar has already been fully torn down — not
just visually cleared but stopped, so a confirmation prompt is never
shown while a Rich live display is still active. This holds on normal
completion, on a raised exception, and on `Ctrl+C`.

### Spinner vs. progress bar

* **Spinner** (`qbit_ops.cli.rendering.transient_spinner`) — one pending remote request,
  or a bounded collection fetched with a single call, where there is
  nothing meaningful to count per item (`Loading torrents…`,
  `Checking connection…`).
* **Progress bar** (`qbit_ops.cli.rendering.transient_progress`) — a collection has
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
one risk tier, defined once in `qbit_ops.execution.MUTATION_RISK` so it cannot
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
one of these five values (`qbit_ops.execution.MutationStatus`):

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
identity model cannot express it without reintroducing raw URLs.
`trackers list` no longer has `--match` and, as of the tracker-security
hardening phase, never renders a raw tracker URL either — it reports the
same `host[:port]` identity model as `trackers status`, just without
health classification.

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
(`qbit_ops.trackers.normalize_tracker_host`) — never a full announce URL. This
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
its own codes (`qbit_ops.tracker_status.tracker_status_exit_code`, mirrored by
`qbit_ops.cli.exit_codes.TrackerStatusExitCode`):

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

## Explain

```bash
poetry run qbit-ops explain torrent --hash abc123
poetry run qbit-ops explain torrent --hash abc123 --format json

poetry run qbit-ops explain tracker --tracker tracker.example
poetry run qbit-ops explain tracker --tracker tracker.example --format json
```

`explain torrent`/`explain tracker` answer four questions from
deterministic, already-collected evidence — never a generated or
speculative explanation: what is currently observed, why qbit-ops
classifies it that way, what evidence supports that classification, and
what limitations or unknowns remain. Every finding also suggests safe,
existing next commands to consider. This is read-only: it never mutates
qBittorrent, never prompts, and a suggested mutation command is always
shown with `--dry-run`, never `--no-dry-run` or `--yes`.

There is no generic rule engine: a small, fixed catalogue of deterministic
rules is evaluated over data other commands already collect (torrent
state groups from `qbit_ops.torrent_states`, tracker health from
`qbit_ops.trackers`/`qbit_ops.tracker_status`). A finding never claims a cause its
evidence does not support — where qbit-ops cannot classify something
(an unrecognized torrent state or tracker status), the finding says so
explicitly (`unknown` severity) rather than guessing. No confidence
percentage is ever computed or shown (see `docs/PHILOSOPHY.md` §9).

### `explain torrent`

Resolves `--hash` with the same unique-prefix resolver every other
hash-driven command uses (see [Torrents](#torrents)). Exactly one
finding is produced per torrent, since `qbit_ops.torrent_states`' state
groups are mutually exclusive by construction:

| Code | Trigger | Severity |
| --- | --- | --- |
| `TORRENT_ERROR_STATE` | qBittorrent reports an error state | `critical` |
| `TORRENT_STALLED_DL` | Incomplete, no active download transfer | `warning` |
| `TORRENT_STALLED_UP` | Complete, no active upload transfer | `warning` |
| `TORRENT_CHECKING` | qBittorrent is validating torrent data | `info` |
| `TORRENT_STOPPED` | Torrent is paused/stopped | `info` |
| `TORRENT_HEALTHY_SEEDING` | Actively seeding, no issues observed | `info` |
| `TORRENT_HEALTHY_DOWNLOADING` | Actively downloading, no issues observed | `info` |
| `TORRENT_UNKNOWN_STATE` | Raw state not recognized by qbit-ops | `unknown` |

A stalled-download finding distinguishes, from the torrent's own tracker
endpoints only (never a wider scan): healthy trackers with no transfer,
all relevant trackers failing, and unclassified/unknown observations —
grounded in the same `TrackerHealth` model `trackers status` uses,
computed only from the endpoints this one torrent already reported.

### `explain tracker`

Resolves `--tracker` to a normalized `host[:port]` identity the same way
`trackers status --tracker` does, and reuses
`qbit_ops.tracker_status.collect_tracker_status` directly — no separate
collection pass. Because that collector scans every torrent surviving
the (here, absent) cheap filters, it never scopes API calls to the
requested tracker alone, only the *report*.

| Code | Trigger | Severity |
| --- | --- | --- |
| `TRACKER_ALL_ENDPOINTS_FAILING` | Every enabled endpoint is critical | `critical` |
| `TRACKER_MIXED_ENDPOINT_HEALTH` | A genuine mix of endpoint health | `warning` |
| `TRACKER_UNKNOWN_STATES` | Every enabled endpoint is unclassifiable | `unknown` |
| `TRACKER_DISABLED_ONLY` | No enabled endpoint observed | `info` |
| `TRACKER_HEALTHY` | Every enabled endpoint is healthy | `info` |
| `TRACKER_PARTIAL_COLLECTION` | Collection failed for some torrents scanned | `warning` (secondary finding) |

`TRACKER_PARTIAL_COLLECTION` is always a *secondary* finding, appended
alongside whichever health finding above applies, whenever
`collection_errors > 0` — mirrors `trackers status`'s own
partial-failure handling. Its limitation is explicit that the failed
torrents may or may not have used the requested tracker, so the reported
endpoint counts may be incomplete.

### Target unavailable

Both commands return nothing to explain — no torrent hash resolves, or
no observation exists at all for the requested tracker identity — the
same way `qbit_ops.torrents.inspect_torrent` signals "no match": internally,
`None`. The CLI renders this distinctly from every severity-based
finding:

```console
$ qbit-ops explain torrent --hash deadbeef
No torrent found for hash prefix: deadbeef

$ qbit-ops explain tracker --tracker unrelated.example
No observations found for tracker: unrelated.example
```

`--format json`/`jsonl` render `{"explanation": null, "hash": "..."}` or
`{"explanation": null, "tracker": "..."}` (the tracker value is always
the normalized `host[:port]` identity, never the raw `--tracker`
argument, even when a full announce URL with a passkey was passed). See
[Exit codes](#explain-exit-codes) for why this uses its own exit code
rather than a severity value or `ExitCode.NO_MATCH`.

An ambiguous hash prefix does not reach this path at all: it is rejected
the same way every other hash-driven command rejects it (candidate list
on stderr, `ExitCode.ERROR`), before an explanation is ever computed.

### `explain` exit codes

`explain torrent`/`explain tracker` share one scheme
(`qbit_ops.explain.explanation_exit_code`, mirrored by
`qbit_ops.cli.exit_codes.ExplainExitCode`):

- `0`: `info` — no warning, critical, or unknown finding.
- `1`: `warning` or `unknown` — at least one such finding, nothing
  `critical`.
- `2`: `critical` — at least one critical finding.
- `3`: `TARGET_UNAVAILABLE` — nothing to explain at all (unresolved
  torrent hash, or a tracker identity with zero observations).
- `4`: invalid CLI usage. Currently unreachable in practice — neither
  command has a combination of options that is locally contradictory
  (see `DoctorExitCode` for the same pattern).

`3` is deliberately its own value, not a reuse of `2`: this scheme's `2`
already means `critical`, so a "nothing found" condition reusing it
would be indistinguishable from a real critical finding — the same
collision `TrackerStatusExitCode` avoids for an empty selection (see
[Tracker Status](#tracker-status)). It also deliberately diverges from
`ExitCode.NO_MATCH`/`trackers inspect`'s no-match convention (both `2`),
for the same reason.

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
`Filter:` line (`qbit_ops.torrents.describe_torrent_filter`) — never a raw
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
codes (`qbit_ops.doctor.doctor_exit_code`, mirrored by
`qbit_ops.cli.exit_codes.DoctorExitCode`
for readability at call sites — see [Doctor](#doctor) for the full check
catalogue and [Exit codes](#overall-status-and-exit-codes) there for
detail):

- `0`: all checks `pass`.
- `1`: one or more `warning`s, no `fail`.
- `2`: one or more `fail`s.
- `4`: invalid CLI invocation preventing doctor from starting (currently
  unreachable — see [Doctor](#doctor)).

### `trackers status` and `explain` exit codes

Both also report a severity rather than success/failure, each using its
own scheme — see [Exit codes](#exit-codes) under [Tracker
Status](#tracker-status) and [`explain` exit
codes](#explain-exit-codes) under [Explain](#explain) for the full
mapping and the reasoning behind each one's non-obvious choices.

### Internal error: exit code `70`

Every command shares one additional code regardless of the scheme
above: `70` reports an unexpected internal defect (a programming bug,
not a remote/configuration failure or an operational finding) and is
never reused for anything else. See
[docs/ERRORS_AND_EXIT_CODES.md](ERRORS_AND_EXIT_CODES.md) for the full
error-category model, local validation rules, and the stdout/stderr
contract for fatal errors.
