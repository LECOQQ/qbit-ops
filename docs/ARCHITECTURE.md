# 🏗️ Architecture

qbit-ops ships two packages from one distribution: a reusable core
(`qbit_core`) with no UI dependency, and a thin CLI/TUI presentation
layer (`qbit_ops`) built on top of it.

```text
CLI (Typer + Rich) ─┐
                    ├── qbit_core: features ── shared ── qbit ── qbittorrent-api
TUI (Textual) ──────┘
```

`qbit_core` never imports Typer, Rich, Textual, or `qbit_ops`; never
prints, prompts, or calls `sys.exit`. A future non-CLI Python consumer
(e.g. Waitarr) can depend on `qbit_core` alone. See the module
docstring of `qbit_core/__init__.py` for a minimal usage example.

## 📦 Package layout

```text
qbit_core/
├── features/   # user-facing use cases shared by CLI and TUI
├── shared/     # SELECT/INSPECT stages, execution policy, torrent model
├── qbit/       # qBittorrent client boundary and payload handling
├── data/       # packaged compatibility evidence
├── config.py   # QbitConfig -- connection settings, no env/dotenv logic
└── errors.py   # QbitCoreError hierarchy

qbit_ops/
├── cli/        # Typer commands, Rich rendering, exit handling
├── tui/        # Textual app, widgets, modals, TUI state
├── config.py   # .env/environment loading -> qbit_core.config.QbitConfig
└── app_services.py  # create_qbit_client() env wrapper + TUI refresh glue
```

## 🔗 Dependency rules

- ⬇️ `qbit_ops/cli/` and `qbit_ops/tui/` may call `qbit_core.features`.
- ⬇️ `qbit_core.features` may use `qbit_core.shared` and `qbit_core.qbit`.
- 🚫 `qbit_core.shared` does not depend on features or presentation code.
- 🚫 `qbit_core.qbit` does not depend on CLI or TUI code.
- 🚫 No module under `qbit_core/` imports `qbit_ops`, Typer, Rich, or
  Textual, at any nesting.
- 🐢 Textual is imported only when `qbit-ops tui` is invoked.

These boundaries are checked by architecture tests
(`tests/test_layering.py`, `tests/test_package_layout.py`,
`tests/test_qbit_architecture.py`, `tests/test_qbit_boundary.py`).

## 🧱 One representation per measure

`shared/torrent_states.py`'s `TorrentSnapshot` is the single
representation of a torrent's individual measures -- size, ratio,
transferred bytes, seeding time, added/completed/last-activity dates.
Readers aggregate it (`features/stats.py`) instead of re-reading raw API
objects, so two commands can never disagree about the same torrent.

The "value unknown" markers qBittorrent uses (`-1`, `-2`, and `0` on a
timestamp) are defined once, in `qbit/fields.py`, and imported by both
the filtering layer and the model. A measure a bounded filter treats as
never reported is therefore also absent from the model -- never `0`,
never 1970. `tests/test_selection_predicates.py` asserts that agreement
on the same raw payload.

## 🛡️ Safety-critical flows

### ✍️ Mutations

Every torrent-facing operation runs the same four stages:

```text
SELECT → INSPECT → PLAN → APPLY
```

| Stage | Owner | Cost |
| --- | --- | --- |
| SELECT | `shared/selection.py` | one `torrents_info()` |
| INSPECT | `shared/inspection.py` | one `torrents_trackers()` per selected torrent |
| PLAN | the operation's own `features/` module | none -- pure |
| APPLY | same module | the mutation calls only |

Two properties this ordering buys:

- **Bounded cost.** INSPECT only ever runs on what SELECT already
  narrowed down, so a filtered command never pays one tracker lookup
  per torrent in the instance.
- **No preview/execute drift.** PLAN is read-only and APPLY consumes
  the frozen plan; it never rescans and never silently changes the
  target set.

Plans and their change types stay typed per operation. Only SELECT and
INSPECT are shared -- a generic change type would defeat the
per-operation redaction rules that keep tracker passkeys out of any
preview, log, or summary.

Around this, `shared/execution.py` decides whether a plan previews,
prompts, applies, or is refused (dry-run by default), and the TUI adds
a refresh after APPLY.

### 🧵 TUI workers

Blocking qBittorrent calls run outside Textual's event loop. Refreshes and mutations share one coordination boundary to avoid overlapping remote operations and stale UI updates.

### 🧪 Compatibility evidence

Exact qBittorrent test evidence is packaged in:

```text
qbit_core/data/qbittorrent-matrix.toml
```

Both the Docker matrix and `qbit-ops doctor` read the same source.

## 📚 More detail

- [Compatibility](COMPATIBILITY.md)
- [Errors and exit codes](ERRORS_AND_EXIT_CODES.md)
- [Contributing](../CONTRIBUTING.md)
