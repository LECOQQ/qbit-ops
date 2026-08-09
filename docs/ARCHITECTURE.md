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
