# qbit-ops

`qbit-ops` is a small qBittorrent operations CLI for homelab usage.

It helps run bulk tracker operations with safe defaults, explicit dry-runs and
readable summaries.

## Features

- Check qBittorrent connectivity from `.env`.
- List trackers used by a qBittorrent instance.
- Add a tracker only when another tracker is already present.
- Remove a tracker in bulk.
- Match trackers exactly or without query parameters.
- Ignore disabled qBittorrent pseudo-trackers such as DHT, PeX and LSD.
- Keep `--dry-run` enabled by default for modifying commands.

## Safety Model

`qbit-ops` is designed to avoid accidental destructive operations.

- `--dry-run` is enabled by default for bulk modifications.
- Real changes require `--no-dry-run`.
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

```bash
git clone https://github.com/LECOQQ/qbit-ops.git
cd qbit-ops
make install
```

This installs Poetry dependencies and local Git hooks.

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

## Quickstart

Check connectivity:

```bash
poetry run qbit-ops connection check
```

List trackers:

```bash
poetry run qbit-ops trackers list
```

Group dynamic tracker URLs without query parameters:

```bash
poetry run qbit-ops trackers list --match without-query
```

## Commands

Show the CLI help:

```bash
poetry run qbit-ops --help
```

Check connection settings:

```bash
poetry run qbit-ops connection check
```

List trackers with exact matching:

```bash
poetry run qbit-ops trackers list
```

List trackers grouped without query parameters:

```bash
poetry run qbit-ops trackers list --match without-query
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

Apply the remove operation:

```bash
poetry run qbit-ops trackers remove \
  --tracker "https://tracker-a.example/announce" \
  --no-dry-run
```

## Use Cases

### Audit trackers

Use these commands to inspect the qBittorrent instance before changing it:

```bash
poetry run qbit-ops connection check
poetry run qbit-ops trackers list
poetry run qbit-ops trackers list --match without-query
```

### Add a tracker conditionally

Use this when you want to add a target tracker only to torrents that already use
a known source tracker.

Dry-run:

```bash
poetry run qbit-ops trackers add-if-present \
  --source "https://tracker-a.example/announce" \
  --target "https://tracker-b.example/announce" \
  --dry-run
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
  --dry-run
```

Remove dynamic variants with query parameters ignored:

```bash
poetry run qbit-ops trackers remove \
  --tracker "http://connect.maxp2p.org:8080/passkey/announce" \
  --match without-query \
  --dry-run
```

### Remove a tracker in bulk

Use this when a tracker should be removed from every torrent using it.

Dry-run:

```bash
poetry run qbit-ops trackers remove \
  --tracker "https://tracker-a.example/announce" \
  --dry-run
```

Apply:

```bash
poetry run qbit-ops trackers remove \
  --tracker "https://tracker-a.example/announce" \
  --no-dry-run
```

## Matching Modes

- `exact`: compares the full normalized tracker URL. This is the default.
- `without-query`: ignores query parameters when comparing trackers.

Both modes preserve the raw qBittorrent URLs for API operations. This matters
for remove operations, because qBittorrent expects the original tracker URLs.

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

## Versioning

The current project version is stored in `VERSION` and `app/__init__.py`.

## License

`qbit-ops` is licensed under the MIT License. See `LICENSE`.