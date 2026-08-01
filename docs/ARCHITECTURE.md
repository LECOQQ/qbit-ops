# 🏗️ Architecture

qbit-ops has one application core and two presentation layers:

```text
CLI (Typer + Rich) ─┐
                    ├── features ── shared ── qbit ── qbittorrent-api
TUI (Textual) ──────┘
```

## 📦 Package layout

```text
qbit_ops/
├── cli/        # Typer commands, Rich rendering, exit handling
├── tui/        # Textual app, widgets, modals, TUI state
├── features/   # user-facing use cases shared by CLI and TUI
├── shared/     # reusable selection, execution and state primitives
├── qbit/       # qBittorrent client boundary and payload handling
├── data/       # packaged compatibility evidence
├── config.py
├── errors.py
└── app_services.py
```

## 🔗 Dependency rules

- ⬇️ `cli/` and `tui/` may call `features/`.
- ⬇️ `features/` may use `shared/` and `qbit/`.
- 🚫 `shared/` does not depend on features or presentation code.
- 🚫 `qbit/` does not depend on CLI or TUI code.
- 🐢 Textual is imported only when `qbit-ops tui` is invoked.

These boundaries are checked by architecture tests.

## 🛡️ Safety-critical flows

### ✍️ Mutations

```text
select → build frozen plan → preview → confirm → apply → refresh
```

The apply step consumes the frozen plan; it does not rescan and silently change the target set.

### 🧵 TUI workers

Blocking qBittorrent calls run outside Textual's event loop. Refreshes and mutations share one coordination boundary to avoid overlapping remote operations and stale UI updates.

### 🧪 Compatibility evidence

Exact qBittorrent test evidence is packaged in:

```text
qbit_ops/data/qbittorrent-matrix.toml
```

Both the Docker matrix and `qbit-ops doctor` read the same source.

## 📚 More detail

- [Compatibility](COMPATIBILITY.md)
- [Errors and exit codes](ERRORS_AND_EXIT_CODES.md)
- [Contributing](../CONTRIBUTING.md)
