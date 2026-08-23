# 🗺️ Roadmap

`qbit-ops` aims at becoming a safe operations toolkit for qBittorrent power users, seedboxes and large torrent libraries.

> [!NOTE]
> This roadmap describes direction, not commitments. Features may move between releases as `qbit-ops` evolves.

> [!NOTE]
> A line describes a capability, never an implementation owed. When a
> capability turns out to already exist, the line is corrected against
> the code rather than a feature built to satisfy its wording.
> Enriching `doctor` and `explain` was removed on 2026-08-16: two
> discovery runs found the capabilities already served elsewhere, and
> concluded `REJECT` rather than building to tick a line. Tracker URL
> migration was removed from v0.6.0 on 2026-08-23 for the same reason:
> `trackers replace`, `replace-passkey`, `add-if-present` and `remove`
> already ship it.

## 🚧 WIP - v0.5.0: 📚 Know Your Library - Part 2

> Find anything. Understand what is happening.

- Deterministic search ✅
- Shared filters and search across CLI and TUI ✅
- Trackers inventory ✅
- Library statistics ✅
- Per-tracker statistics ✅
- Tracker health inventory and per-torrent tracker verdicts ✅
- Stall, error and inactivity filters, composable across CLI and TUI ✅
- Health that answers "must I act" ✅
- Bulk category and tag management ✅
- Bulk rate limiting, per torrent or library-wide ✅
- Composable filtering across mutating operations ✅
- Machine-readable results for every mutation ✅
- JSONL output ✅
- Experimental MCP server, read-only and bounded ✅
- Guided connection setup, from the CLI or the TUI ✅
- Duration-range filters on the TUI ✅
- A canonical name for "no longer seeding"

## 🔭 Later

### v0.6.0 - 🏭 Operate at Scale

> Select precisely. Operate on thousands of torrents safely.

- A trackers page in the TUI, read-only, with copyable commands
- `qbit_core` as a versioned library, with its own public surface
- Saved selectors
- Batch-aware reannounce and recheck
- State snapshots before bulk mutations

### v0.7.0 - 🐉 Move Without Fear

> Move freely across disks, servers or qBittorrent instances without losing anything.

- Export migration state
- Migration validation and helpers
- Mass moving and renaming
- Recheck planner
- Path validation
- Orphan detection
- Storage-aware operations
- Duplicate-content detection

### v0.8.0 - 🛡️ Guardrails

> Making explicit what must never happen.

- Protected torrents
- Retention policies
- Tracker-specific protections
- Ratio, seed-time and absolute-upload constraints
- Potential HnR detection
- Warnings and hard stops before unsafe mutation
- Cleanup planner
- Policy-aware delete, move and pause
- Stale configuration and torrents detection

### v0.9.0 - ⚙️ Automation Toolkit

> Becoming a scriptable operational substrate.

- Machine-readable selectors
- Automation-friendly commands
- Reporting
- Config-driven operations
- Plan export/import

### v1.0.0 - 🦖 Safe qBittorrent Operations

> Stabilize. Harden. Evolve.

## 🗃️ Shipped

Completed milestones are archived in [docs/ROADMAP_ARCHIVE.md](docs/ROADMAP_ARCHIVE.md).
