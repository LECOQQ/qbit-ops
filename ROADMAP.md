# 🗺️ Roadmap

`qbit-ops` aims at becoming a safe operations toolkit for qBittorrent power users, seedboxes and large torrent libraries.

> [!NOTE]
> This roadmap describes direction, not commitments. Features may move between releases as `qbit-ops` evolves.

## 🚧 WIP - v0.5.0: 📚 Know Your Library - Part 2

> Find anything. Understand what is happening.

- Deterministic search
- Shared filters and search across CLI and TUI
- Trackers inventory
- Library statistics
- Per-tracker statistics
- Detection of tracker errors and torrents that are no longer seeding
- Enriched `doctor` and `explain`

## 🔭 Later

### v0.6.0 - 🏭 Operate at Scale

> Select precisely. Operate on thousands of torrents safely.

- Bulk category management
- Bulk tag management
- Bulk throttling
- Tracker URL migration
- Composable filtering across mutating operations
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
- Duplicate-content detection in `doctor`

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

- JSONL output
- Machine-readable selectors
- Automation-friendly commands
- Reporting
- Config-driven operations
- Plan export/import

### v1.0.0 - 🦖 Safe qBittorrent Operations

> Stabilize. Harden. Evolve.

## 🗃️ Shipped

Completed milestones are archived in [docs/ROADMAP_ARCHIVE.md](docs/ROADMAP_ARCHIVE.md).
