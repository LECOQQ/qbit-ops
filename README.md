# qbit-ops

[![CI](https://github.com/LECOQQ/qbit-ops/actions/workflows/ci.yml/badge.svg)](https://github.com/LECOQQ/qbit-ops/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](pyproject.toml)
[![Version](https://img.shields.io/badge/version-0.0.1-blue.svg)](CHANGELOG.md)

`qbit-ops` is a small qBittorrent operations CLI for homelab usage.

It helps run bulk qBittorrent operations with safe defaults, explicit dry-runs
and readable summaries.

## Features

- Check qBittorrent connectivity from `.env`.
- Diagnose qbit-ops configuration and qBittorrent API access.
- List torrents with basic audit fields.
- List torrent categories and filter torrents by category.
- Inspect a torrent by hash with tracker details.
- Export a full instance backup with torrents, trackers and metadata.
- Compare two export files before applying bulk changes.
- List trackers used by a qBittorrent instance.
- Analyze tracker health, disabled trackers and dynamic query variants.
- Inspect torrents using a specific tracker.
- Export the active tracker state as JSON.
- Add a tracker only when another tracker is already present.
- Remove a tracker in bulk.
- Replace a tracker in bulk without creating duplicate target trackers.
- Pause, resume, start or reannounce torrents in bulk by category, tracker, name, completed or all.
- Match trackers exactly or without query parameters.
- Ignore disabled qBittorrent pseudo-trackers such as DHT, PeX and LSD.
- Use `--output json` on all audit commands for scripting.
- Keep `--dry-run` enabled by default for modifying commands.

## Safety Model

`qbit-ops` is designed to avoid accidental destructive operations.

- `--dry-run` is enabled by default for bulk modifications.
- Real changes require `--no-dry-run`.
- Bulk torrent actions require exactly one filter: `--category`, `--tracker`,
  `--name`, `--all` or `--completed` (on `start` only).
- Tracker URLs are normalized before comparison.
- `--match exact` is the default matching mode.
- `--match without-query` must be requested explicitly.
- Raw qBittorrent tracker URLs are preserved for API operations.

## Requirements

- Linux
- Git
- Make
- Python 3.12
- Poetry
- qBittorrent Web UI/API enabled

Check local tooling:

```bash
make doctor
```

## Installation

### Development install

```bash
git clone https://github.com/LECOQQ/qbit-ops.git
cd qbit-ops
make install
```

This installs Poetry dependencies and local Git hooks.

Run the CLI from the repository with:

```bash
poetry run qbit-ops --help
```

### Application install with pipx

Use `pipx` when you want `qbit-ops` available as a regular command outside the
repository.

Install `pipx` on Ubuntu/Debian:

```bash
sudo apt update
sudo apt install pipx
pipx ensurepath
```

Restart your shell if `pipx ensurepath` asks you to.

Install `qbit-ops` from the repository:

```bash
git clone https://github.com/LECOQQ/qbit-ops.git
cd qbit-ops
pipx install .
```

Then run:

```bash
qbit-ops --help
```

Update an existing `pipx` installation from the repository:

```bash
cd qbit-ops
pipx reinstall qbit-ops
```

## Configuration

Create a local `.env` file:

```bash
cp .env.example .env
```

Then configure your qBittorrent connection:

```env
QBIT_HOST=http://localhost:8080
QBIT_USER=admin
QBIT_PASSWORD=change-me
```

Never commit `.env`; it contains local secrets and is ignored by Git.

When `qbit-ops` is installed as an application, for example with `pipx`, prefer
a user-level configuration file:

```bash
mkdir -p ~/.config/qbit-ops
cp .env.example ~/.config/qbit-ops/.env
```

Then edit:

```bash
nano ~/.config/qbit-ops/.env
```

Configuration lookup order:

- existing environment variables;
- file pointed to by `QBIT_OPS_ENV_FILE`, when set;
- `.env` in the current working directory;
- `~/.config/qbit-ops/.env`.

## Quickstart

Check connectivity:

```bash
qbit-ops connection check
```

Run configuration diagnostics:

```bash
qbit-ops config doctor
```

List torrents:

```bash
qbit-ops torrents list
```

List categories:

```bash
qbit-ops torrents categories
```

List torrents in a category:

```bash
qbit-ops torrents list --category sonarr
```

List torrents using a tracker:

```bash
qbit-ops torrents list --tracker "https://tracker-a.example/announce"
```

Inspect a torrent by hash:

```bash
qbit-ops torrents inspect --hash "TORRENT_HASH"
```

Search torrents by name:

```bash
qbit-ops torrents inspect --name "L.amour.est.dans.le.pre.S20E02"
```

List trackers:

```bash
qbit-ops trackers list
```

Inspect torrents using a tracker:

```bash
qbit-ops trackers inspect --tracker "https://tracker-a.example/announce"
```

Export the active tracker state:

```bash
qbit-ops trackers export
```

Group dynamic tracker URLs without query parameters:

```bash
qbit-ops trackers list --match without-query
```

Analyze tracker health:

```bash
qbit-ops trackers health
```

Export a full backup with torrents, trackers and metadata:

```bash
qbit-ops backup export --output json
```

Compare two export files:

```bash
qbit-ops backup diff backup-before.json backup-after.json
```

Pause torrents in a category:

```bash
qbit-ops torrents pause --category sonarr --dry-run
```

When working from a Poetry development environment, prefix commands with
`poetry run`, for example:

```bash
poetry run qbit-ops connection check
```

## Commands

The examples below use `poetry run` for development. If `qbit-ops` is installed
with `pipx`, remove the `poetry run` prefix.

Show the CLI help:

```bash
poetry run qbit-ops --help
```

Check connection settings:

```bash
poetry run qbit-ops connection check
```

Check connection settings as JSON:

```bash
poetry run qbit-ops connection check --output json
```

Run configuration diagnostics:

```bash
poetry run qbit-ops config doctor
```

Run configuration diagnostics as JSON:

```bash
poetry run qbit-ops config doctor --output json
```

List torrents:

```bash
poetry run qbit-ops torrents list
```

List torrents as JSON:

```bash
poetry run qbit-ops torrents list --output json
```

List categories:

```bash
poetry run qbit-ops torrents categories
```

List categories as JSON:

```bash
poetry run qbit-ops torrents categories --output json
```

List torrents in a category:

```bash
poetry run qbit-ops torrents list --category sonarr
```

List uncategorized torrents:

```bash
poetry run qbit-ops torrents list --category "(uncategorized)"
```

List torrents using a tracker:

```bash
poetry run qbit-ops torrents list \
  --tracker "https://tracker-a.example/announce"
```

List torrents using a tracker without query parameters:

```bash
poetry run qbit-ops torrents list \
  --tracker "http://connect.maxp2p.org:8080/passkey/announce" \
  --match without-query
```

Inspect a torrent by hash:

```bash
poetry run qbit-ops torrents inspect --hash "TORRENT_HASH"
```

Search torrents by name:

```bash
poetry run qbit-ops torrents inspect --name "L.amour.est.dans.le.pre"
```

Search torrents by name as JSON:

```bash
poetry run qbit-ops torrents inspect \
  --name "L.amour.est.dans.le.pre" \
  --output json
```

Inspect a torrent as JSON:

```bash
poetry run qbit-ops torrents inspect \
  --hash "TORRENT_HASH" \
  --output json
```

List trackers with exact matching:

```bash
poetry run qbit-ops trackers list
```

List trackers grouped without query parameters:

```bash
poetry run qbit-ops trackers list --match without-query
```

List trackers as JSON:

```bash
poetry run qbit-ops trackers list --output json
```

Analyze tracker health:

```bash
poetry run qbit-ops trackers health
```

Analyze tracker health as JSON:

```bash
poetry run qbit-ops trackers health --output json
```

Inspect torrents using a tracker:

```bash
poetry run qbit-ops trackers inspect \
  --tracker "https://tracker-a.example/announce"
```

Inspect torrents using a tracker as JSON:

```bash
poetry run qbit-ops trackers inspect \
  --tracker "https://tracker-a.example/announce" \
  --output json
```

Export the active tracker state as JSON:

```bash
poetry run qbit-ops trackers export --output json
```

Export a full backup with torrents, trackers and metadata:

```bash
poetry run qbit-ops backup export --output json
```

Compare two export files:

```bash
poetry run qbit-ops backup diff backup-before.json backup-after.json
```

Compare two export files as JSON:

```bash
poetry run qbit-ops backup diff backup-before.json backup-after.json \
  --output json
```

Add a tracker if another tracker is already present:

```bash
poetry run qbit-ops trackers add-if-present \
  --source "https://tracker-a.example/announce" \
  --target "https://tracker-b.example/announce"
```

Apply the add operation:

```bash
poetry run qbit-ops trackers add-if-present \
  --source "https://tracker-a.example/announce" \
  --target "https://tracker-b.example/announce" \
  --no-dry-run
```

Remove a tracker in bulk:

```bash
poetry run qbit-ops trackers remove \
  --tracker "https://tracker-a.example/announce"
```

Replace a tracker in bulk:

```bash
poetry run qbit-ops trackers replace \
  --source "https://tracker-a.example/announce" \
  --target "https://tracker-b.example/announce"
```

Apply the remove operation:

```bash
poetry run qbit-ops trackers remove \
  --tracker "https://tracker-a.example/announce" \
  --no-dry-run
```

Apply the replace operation:

```bash
poetry run qbit-ops trackers replace \
  --source "https://tracker-a.example/announce" \
  --target "https://tracker-b.example/announce" \
  --no-dry-run
```

Pause torrents in a category:

```bash
poetry run qbit-ops torrents pause --category sonarr --dry-run
```

Resume torrents in a category:

```bash
poetry run qbit-ops torrents resume --category sonarr --no-dry-run
```

Reannounce torrents using a tracker:

```bash
poetry run qbit-ops torrents reannounce \
  --tracker "https://tracker-a.example/announce" \
  --dry-run \
  --verbose
```

## Use Cases

### Audit trackers

Use these commands to inspect the qBittorrent instance before changing it:

```bash
poetry run qbit-ops connection check
poetry run qbit-ops config doctor
poetry run qbit-ops torrents list
poetry run qbit-ops torrents categories
poetry run qbit-ops torrents list --category sonarr
poetry run qbit-ops torrents inspect --hash "TORRENT_HASH"
poetry run qbit-ops trackers list
poetry run qbit-ops trackers list --match without-query
poetry run qbit-ops trackers health
poetry run qbit-ops trackers inspect \
  --tracker "https://tracker-a.example/announce"
poetry run qbit-ops trackers export --output json
poetry run qbit-ops backup export --output json
poetry run qbit-ops backup diff backup-before.json backup-after.json
```

### Add a tracker conditionally

Use this when you want to add a target tracker only to torrents that already use
a known source tracker.

Dry-run:

```bash
poetry run qbit-ops trackers add-if-present \
  --source "https://tracker-a.example/announce" \
  --target "https://tracker-b.example/announce" \
  --dry-run \
  --verbose
```

Apply:

```bash
poetry run qbit-ops trackers add-if-present \
  --source "https://tracker-a.example/announce" \
  --target "https://tracker-b.example/announce" \
  --no-dry-run
```

### Handle dynamic tracker URLs

Some trackers include dynamic query parameters such as `sig` or `announce_ts`.
Use `--match without-query` to compare only the stable tracker URL identity.

List grouped variants:

```bash
poetry run qbit-ops trackers list --match without-query
```

Add conditionally with query parameters ignored:

```bash
poetry run qbit-ops trackers add-if-present \
  --source "http://connect.maxp2p.org:8080/passkey/announce" \
  --target "https://tracker-b.example/announce" \
  --match without-query \
  --dry-run \
  --verbose
```

Remove dynamic variants with query parameters ignored:

```bash
poetry run qbit-ops trackers remove \
  --tracker "http://connect.maxp2p.org:8080/passkey/announce" \
  --match without-query \
  --dry-run \
  --verbose
```

Replace dynamic variants with query parameters ignored:

```bash
poetry run qbit-ops trackers replace \
  --source "http://connect.maxp2p.org:8080/passkey/announce" \
  --target "https://tracker-b.example/announce" \
  --match without-query \
  --dry-run \
  --verbose
```

### Remove a tracker in bulk

Use this when a tracker should be removed from every torrent using it.

Dry-run:

```bash
poetry run qbit-ops trackers remove \
  --tracker "https://tracker-a.example/announce" \
  --dry-run \
  --verbose
```

Apply:

```bash
poetry run qbit-ops trackers remove \
  --tracker "https://tracker-a.example/announce" \
  --no-dry-run
```

### Replace a tracker in bulk

Use this when a tracker should be migrated to another tracker.

If the target tracker is already present on a torrent, `qbit-ops` removes the
source tracker instead of adding a duplicate target.

Dry-run:

```bash
poetry run qbit-ops trackers replace \
  --source "https://tracker-a.example/announce" \
  --target "https://tracker-b.example/announce" \
  --dry-run \
  --verbose
```

Apply:

```bash
poetry run qbit-ops trackers replace \
  --source "https://tracker-a.example/announce" \
  --target "https://tracker-b.example/announce" \
  --no-dry-run
```

### Pause, resume, start or reannounce torrents in bulk

Use these commands to act on torrents filtered by category, tracker, name,
completed or all. Exactly one filter is required, except `--completed` which
can also be combined with `--category`, `--tracker` or `--name`.

Dry-run pause by category:

```bash
poetry run qbit-ops torrents pause --category sonarr --dry-run --verbose
```

Start all stopped completed torrents (qBittorrent Web UI equivalent):

```bash
poetry run qbit-ops torrents start --completed --dry-run --verbose
poetry run qbit-ops torrents start --completed --no-dry-run
```

Apply resume to all stopped torrents:

```bash
poetry run qbit-ops torrents resume --all --no-dry-run
```

Apply resume by tracker:

```bash
poetry run qbit-ops torrents resume \
  --tracker "https://tracker-a.example/announce" \
  --no-dry-run
```

Reannounce torrents matching a name:

```bash
poetry run qbit-ops torrents reannounce \
  --name "L.amour.est.dans.le.pre" \
  --dry-run
```

`pause`, `resume` and `start` are idempotent:

- `pause` skips torrents already stopped (`paused*` or `stopped*` states).
- `resume` and `start` skip torrents that are not stopped; active torrents are
  never restarted.
- `start --completed` only targets torrents with `progress=100%`.

## Matching Modes

- `exact`: compares the full normalized tracker URL. This is the default.
- `without-query`: ignores query parameters when comparing trackers.

Both modes preserve the raw qBittorrent URLs for API operations. This matters
for remove operations, because qBittorrent expects the original tracker URLs.

## Audit Output

All audit commands accept `--output text|json`:

- `connection check`
- `config doctor`
- `torrents list`
- `torrents categories`
- `torrents inspect`
- `trackers list`
- `trackers health`
- `trackers inspect`
- `trackers export`
- `backup export`
- `backup diff`

Text output is the default and prints a human-readable summary. JSON output is
intended for scripting and backups.

`backup export --output json` produces a full payload with:

- export metadata (`exported_at`, qBittorrent versions, configured host);
- torrent metadata and tracker details for every torrent;
- normalized tracker identities;
- aggregated tracker usage counts.

Example:

```bash
qbit-ops backup export --output json > backup.json
```

`torrents inspect --name` ranks matches by relevance:

- exact name match;
- name starts with the query;
- name contains the query;
- fuzzy similarity fallback.

Use `--hash` when you already know the torrent hash and need full tracker
details. Use `--name` to find candidate torrents first, then inspect the hash
you selected.

`backup diff` compares two export files produced by `backup export` or
`trackers export`. It reports:

- torrents added, removed or changed;
- normalized tracker additions and removals per torrent;
- tracker usage additions, removals and count changes.

Example:

```bash
qbit-ops backup diff backup-before.json backup-after.json
```

## Tracker Health

`trackers health` reports:

- scanned torrents;
- active tracker occurrences;
- disabled tracker occurrences;
- unique exact tracker URLs;
- unique logical tracker URLs without query parameters;
- query variant groups;
- disabled tracker URLs such as DHT, PeX or LSD.

Use JSON output when the report should be consumed by another tool:

```bash
qbit-ops trackers health --output json
```

## Output

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

Tracker replacement uses a dedicated summary:

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

When `--verbose` is passed to a bulk modification command, impacted torrents are
printed after the summary.

## Exit Codes

- `0`: command completed successfully.
- `1`: configuration, connection, authentication or API error.
- `2`: targeted command completed but matched no torrent.

Exit code `2` is used when a command completes successfully but reports a
non-error outcome that may still require attention:

- `torrents inspect`, `torrents list --tracker`, `torrents list --category`,
  `torrents pause`, `torrents resume`, `torrents start`, `torrents reannounce`, `trackers inspect`,
  `trackers add-if-present`, `trackers remove`, `trackers replace`: no torrent
  matched the requested criteria;
- `backup diff`: the two exports differ.

## Development

Format and validate the project:

```bash
make format
make check
```

## Contributing

Issues and pull requests are welcome.

Before opening a pull request:

```bash
make format
make check
```

Please keep changes small, explicit and aligned with the safety-first behavior
of the CLI.

## License

`qbit-ops` is licensed under the MIT License. See `LICENSE`.
