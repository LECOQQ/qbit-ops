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
> migration was removed on 2026-08-23 from the milestone now numbered
> v0.7.0, for the same reason:
> `trackers replace`, `replace-passkey`, `add-if-present` and `remove`
> already ship it.

## 🚧 WIP - v0.6.0: 🚪 Come On In

> Easy to arrive at, and cheaper to keep running.

A release about reaching the library rather than about what can be done
to it. It was not planned: `Operate at Scale` was next, and none of its
lines shipped -- they move to v0.7.0 intact. This one grew out of
getting the tool ready to be handed to people.

- One-line install on a machine with no Python at all
- Shell completion for closed sets, and for your own categories, tags
  and tracker hosts, from a cache filled by ordinary use
- `--sort` on `torrents list` and `trackers list`
- Reconfiguring a running TUI, without restarting it
- A rate graph that keeps sampling while you work elsewhere
- Forty percent fewer API calls per refresh, and half the bytes

## 🔭 Later

### v0.7.0 - 🏭 Operate at Scale

> Select precisely. Operate on thousands of torrents safely.

- A canonical name for "no longer seeding"
- A trackers page in the TUI, read-only, with copyable commands
- `qbit_core` as a versioned library, with its own public surface
- Saved selectors
- Batch-aware reannounce and recheck
- State snapshots before bulk mutations

### v0.8.0 - 🐉 Move Without Fear

> Move freely across disks, servers or qBittorrent instances without losing anything.

- Export migration state
- Migration validation and helpers
- Mass moving and renaming
- Recheck planner
- Path validation
- Orphan detection
- Storage-aware operations
- Duplicate-content detection

### v0.9.0 - 🛡️ Guardrails

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

### v0.10.0 - ⚙️ Automation Toolkit

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
