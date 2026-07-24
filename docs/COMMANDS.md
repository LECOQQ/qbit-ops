# Command Reference

Full command reference for `qbit-ops`. See the [README](../README.md) for
installation, configuration and the safety model.

The examples below use `poetry run`. If `qbit-ops` is installed with `pipx`,
drop the `poetry run` prefix.

## Table of Contents

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

## Connection & Config

```bash
poetry run qbit-ops connection check
poetry run qbit-ops connection check --output json

poetry run qbit-ops config doctor
poetry run qbit-ops config doctor --output json
```

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
match, substring match, then fuzzy similarity. Use `--hash` once you know the
torrent; use `--name` to find candidates first.

### Bulk torrent actions

`pause`, `resume`, `start` and `reannounce` act on torrents filtered by
`--category`, `--tracker`, `--name`, `--all`, or `--completed` (`start`
only). Exactly one filter is required, except `--completed`, which can also
combine with `--category`, `--tracker` or `--name`.

```bash
poetry run qbit-ops torrents pause --category sonarr --dry-run --verbose
poetry run qbit-ops torrents resume --category sonarr --no-dry-run
poetry run qbit-ops torrents resume --all --no-dry-run
poetry run qbit-ops torrents resume \
  --tracker "https://tracker-a.example/announce" \
  --no-dry-run

poetry run qbit-ops torrents start --completed --dry-run --verbose
poetry run qbit-ops torrents start --completed --no-dry-run

poetry run qbit-ops torrents reannounce \
  --name "L.amour.est.dans.le.pre" \
  --dry-run
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

All audit commands accept `--output text|json`: `connection check`, `config
doctor`, `torrents list`, `torrents categories`, `torrents inspect`,
`trackers list`, `trackers health`, `trackers inspect`, `trackers export`,
`backup export`, `backup diff`.

Text output is the default, human-readable summary. JSON output is for
scripting and backups.

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
- filter: category|tracker|name|completed|all
- value: ...
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

- `0`: success.
- `1`: configuration, connection, authentication or API error.
- `2`: the command completed but matched no torrent (or, for `backup diff`,
  the two exports differ).

Commands using exit code `2` on no match: `torrents inspect`, `torrents
list --tracker`, `torrents list --category`, `torrents pause`, `torrents
resume`, `torrents start`, `torrents reannounce`, `trackers inspect`,
`trackers add-if-present`, `trackers remove`, `trackers replace`, `trackers
replace-passkey`.
