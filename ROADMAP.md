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
> concluded `REJECT` rather than building to tick a line.

## 🚧 WIP - v0.5.0: 📚 Know Your Library - Part 2

> Find anything. Understand what is happening.

- Deterministic search ✅
- Shared filters and search across CLI and TUI ✅
- Trackers inventory ✅
- Library statistics ✅
- Per-tracker statistics ✅
- Tracker health inventory and per-torrent tracker verdicts ✅
- Stall, error and inactivity filters, composable across CLI and TUI ✅
- A canonical vocabulary for "no longer seeding". The notion is
  expressible today but not named, and three non-equivalent filter
  combinations answer it differently.
- Duration-range filters (`--seeded-for`) on the TUI as well as the CLI
- Health that answers "must I act": a torrent completed from local data
  no longer raises a warning ✅

## 🔭 Later

### v0.6.0 - 🏭 Operate at Scale

> Select precisely. Operate on thousands of torrents safely.

- Bulk category management ✅
- Bulk tag management ✅
- Bulk throttling
- Tracker URL migration
- Composable filtering across mutating operations ✅
- Machine-readable results for every mutation ✅
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

> Expliciting what must never happens.

- Protected torrents
- Retention policies
- Tracker-specific protections
- Ratio constraints
- Potential HnR detection
- Warnings and hard stops before unsafe mutation
- Cleanup planner
- Policy-aware delete, move and pause
- Stale configuration and torrents detection
- Seed time
- Absolute uploaded

### v0.9.0 - ⚙️ Automation Toolkit

> Becoming a scriptable operational substrate.

- JSONL output ✅
- Machine-readable selectors
- Automation-friendly commands
- Reporting
- Config-driven operations
- Plan export/import

### v1.0.0 - 🦖 Safe qBittorrent Operations

> Stabilize. Harden. Evolve.

## 🗃️ Shipped

Completed milestones are archived in [docs/ROADMAP_ARCHIVE.md](docs/ROADMAP_ARCHIVE.md).
