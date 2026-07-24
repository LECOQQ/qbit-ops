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
- [Audit Output](#audit-output)
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

`status` uses `--format table|json|jsonl|csv` (see
[Output Summaries](#output-summaries) below for how this differs from the
`--output text|json` used by audit commands). `status --watch` is not
implemented yet.

## Connection & Config

```bash
poetry run qbit-ops connection check
poetry run qbit-ops connection check --format json

poetry run qbit-ops config doctor
poetry run qbit-ops config doctor --output json
```

`connection check` uses the same `--format table|json|jsonl|csv` option as
`status` (migrated in the same phase to prove the option is reusable).
`config doctor` still uses `--output text|json`, like the rest of the
audit commands below.

## Torrents

```bash
poetry run qbit-ops torrents list
poetry run qbit-ops torrents list --output json

poetry run qbit-ops torrents categories
poetry run qbit-ops torrents categories --output json

poetry run qbit-ops torrents list --category sonarr
poetry run qbit-ops torrents list --category "(uncategorized)"

poetry run qbit-ops torrents list \
  --tracker "https://tracker-a.example/announce"

poetry run qbit-ops torrents list \
  --tracker "http://connect.maxp2p.org:8080/passkey/announce" \
  --match without-query

poetry run qbit-ops torrents inspect --hash "TORRENT_HASH"
poetry run qbit-ops torrents inspect --hash "TORRENT_HASH" --output json

poetry run qbit-ops torrents inspect --name "L.amour.est.dans.le.pre"
poetry run qbit-ops torrents inspect \
  --name "L.amour.est.dans.le.pre" \
  --output json
```

`torrents inspect --name` ranks matches by relevance: exact match, prefix
match, substring match, then fuzzy similarity. It is **read-only discovery
only** — a way to find a hash, not a way to target a mutation. `torrents
inspect --hash` now also accepts a complete hash or an unambiguous prefix
(not only a full hash), matched case-insensitively; an ambiguous prefix
fails with the candidate list instead of guessing.

### Bulk torrent actions

`pause`, `resume`, `start` and `reannounce` act on torrents targeted by
`--hash`, `--category`, `--tracker`, `--all`, or `--completed` (`start`
only). Exactly one targeting mode is required. `--hash` is always used
alone: it resolves to a single torrent, so it cannot combine with
`--category`, `--tracker`, `--all`, or `--completed`. `--completed` is the
only mode that can still combine with `--category` or `--tracker`.

**`--hash` is the safe, canonical way to target one torrent** — a complete
infohash or an unambiguous prefix, resolved case-insensitively:

```bash
poetry run qbit-ops torrents inspect --name "debian"      # 1. discover
poetry run qbit-ops torrents reannounce --hash abc123 --dry-run   # 2. act
poetry run qbit-ops torrents reannounce --hash abc123 --no-dry-run
```

Resolution rules:

1. No torrent matches the hash or prefix → the command matches nothing and
   exits with the existing no-match exit code (`2`); no mutation is
   attempted.
2. Exactly one torrent matches → it resolves to the complete hash and the
   action proceeds normally. The resolved full hash is shown in the
   command's summary output (`value` row), even in dry-run.
3. Several torrents share the prefix → the command fails with the
   candidate hashes and names, mutates nothing, and exits `1`:

   ```text
   ✗ ERROR Hash prefix 'abc' matches 2 torrents:

     abc123def456…  Debian ISO
     abc987fed654…  Debian live image

   Use a longer hash prefix.
   ```

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
poetry run qbit-ops trackers list --output json

poetry run qbit-ops trackers health
poetry run qbit-ops trackers health --output json

poetry run qbit-ops trackers inspect \
  --tracker "https://tracker-a.example/announce"
poetry run qbit-ops trackers inspect \
  --tracker "https://tracker-a.example/announce" \
  --output json

poetry run qbit-ops trackers export --output json
```

### Add a tracker if another tracker is present

Adds a target tracker only to torrents that already use a known source
tracker.

```bash
poetry run qbit-ops trackers add-if-present \
  --source "https://tracker-a.example/announce" \
  --target "https://tracker-b.example/announce" \
  --dry-run --verbose

poetry run qbit-ops trackers add-if-present \
  --source "https://tracker-a.example/announce" \
  --target "https://tracker-b.example/announce" \
  --no-dry-run
```

### Remove a tracker in bulk

```bash
poetry run qbit-ops trackers remove \
  --tracker "https://tracker-a.example/announce" \
  --dry-run --verbose

poetry run qbit-ops trackers remove \
  --tracker "https://tracker-a.example/announce" \
  --no-dry-run
```

### Replace a tracker in bulk

Migrates torrents from one tracker to another. If the target tracker is
already present, `qbit-ops` removes the source instead of adding a
duplicate.

```bash
poetry run qbit-ops trackers replace \
  --source "https://tracker-a.example/announce" \
  --target "https://tracker-b.example/announce" \
  --dry-run --verbose

poetry run qbit-ops trackers replace \
  --source "https://tracker-a.example/announce" \
  --target "https://tracker-b.example/announce" \
  --no-dry-run
```

### Replace a tracker's passkey in bulk

Keeps the tracker URL otherwise unchanged. Mark the passkey's position with
a literal `{passkey}` placeholder, either as a query parameter value or as a
full path segment — the current passkey does not need to be known.

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
  --no-dry-run
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
poetry run qbit-ops backup export --output json > backup.json
poetry run qbit-ops backup diff backup-before.json backup-after.json
poetry run qbit-ops backup diff backup-before.json backup-after.json \
  --output json
```

`backup export --output json` produces:

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
poetry run qbit-ops trackers export --output json
poetry run qbit-ops backup export --output json
```

## Matching Modes

- `exact` (default): compares the full normalized tracker URL.
- `without-query`: ignores query parameters when comparing trackers.

Both modes preserve the raw qBittorrent URLs for API calls — this matters
for `remove`, since qBittorrent expects the original tracker URL.

## Audit Output

Most audit commands accept `--output text|json`: `config doctor`, `torrents
list`, `torrents categories`, `torrents inspect`, `trackers list`, `trackers
health`, `trackers inspect`, `trackers export`, `backup export`, `backup
diff`.

Text output is the default, human-readable summary. JSON output is for
scripting and backups.

`status` and `connection check` are the exception: they use `--format
table|json|jsonl|csv` instead (`table` replaces `text`; `jsonl` and `csv` are
new). This is an intentional pre-1.0 break, not yet applied to the other
audit commands — see `docs/DECISIONS.md` (2026-07-24).

## Tracker Health

`trackers health` reports: scanned torrents, active/disabled tracker
occurrences, unique exact and logical tracker URLs, query variant groups,
and disabled pseudo-trackers (DHT, PeX, LSD).

```bash
poetry run qbit-ops trackers health --output json
```

## Output Summaries

Modifying commands print a final summary:

```text
Summary:
- scanned: X
- matched_source: X
- already_had_target: X
- modified: X
- dry_run: true/false
```

Tracker removal uses a dedicated summary:

```text
Summary:
- scanned: X
- matched_tracker: X
- modified: X
- removed_urls: X
- dry_run: true/false
```

Tracker replacement (and passkey replacement) uses a dedicated summary:

```text
Summary:
- scanned: X
- matched_source: X
- already_had_target: X
- modified: X
- replaced_urls: X
- removed_urls: X
- dry_run: true/false
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
- dry_run: true/false
```

Pass `--verbose` on any bulk modification command to print impacted
torrents after the summary.

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
