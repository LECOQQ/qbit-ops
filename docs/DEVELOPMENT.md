---
title: "Contributor workflow"
description: "Development setup, test tiers, and release validation -- the operational reference for contributors"
status: stable
---

# 🛠️ Contributor workflow

## Setup

```bash
git clone https://github.com/LECOQQ/qbit-ops.git
cd qbit-ops
make install
```

Installs dependencies and the Conventional Commits Git hook.

## Test tiers

### 1. Focused edit-loop tests

While iterating on one module, run only the tests that exercise it:

```bash
poetry run pytest tests/test_doctor.py -k compatibility
```

**Isolation rule (mandatory)**: a test or smoke script must never be
able to discover a real `.env` or `~/.config/qbit-ops/.env` (resolved
by `qbit_ops.config._get_default_env_files()`). Run with a temporary
`HOME`/`XDG_CONFIG_HOME` and an isolated working directory, or set fake
`QBIT_HOST`/`QBIT_USER`/`QBIT_PASSWORD` values first -- never assume an
absent `.env` in the cwd is enough. This isn't theoretical: a real
homelab qBittorrent instance was once contacted this way.

### 2. `make check-fast` -- fast local checkpoint

```bash
make check-fast
```

Ruff, `black --check`, Pyright, version-sync, and every hermetic
non-Docker, non-TUI test. No network access, ~30s. Use while iterating
-- **not a substitute for `make check`** before a push.

### 3. `make test-tui` -- complete TUI suite

```bash
make test-tui
```

The Pilot-based interface tests, mutation lifecycle, concurrency, and
the security/layering guard. Never contacts qBittorrent or Docker.
Slower (~5 min) -- covers concurrent-mutation and race-condition
regressions `check-fast` skips for speed.

### 4. `make check` -- the required push/PR gate

```bash
make check
```

Formatting, lint, type-checking, version sync, every hermetic test,
and the complete TUI suite. Never touches Docker. This is exactly what
CI (`make ci`) runs on every push and pull request -- run it before
you push.

### 5. Docker-based qBittorrent compatibility tests

Opt-in, local or scheduled-CI only -- never part of `make check` or PR
CI:

```bash
make test-qbit-version QBIT_MATRIX_ID=<id>  # one exact version
make test-qbit-matrix                        # the full historical matrix
```

Requires Docker; starts a disposable container per entry (dedicated
network, loopback-only published port) and runs read-only, mutation,
and tracker-mutation scenarios against it. None of them read your real
`.env` or contact a real qBittorrent instance.

### 6. Fixture capture

```bash
make capture-qbit-fixtures QBIT_MATRIX_ID=<id>
```

The only command that writes to
`tests/compatibility/fixtures/captured-container/<id>/`. Review the
diff before committing.

### 7. `make clean`

Removes locally generated, reproducible artifacts. Safe and idempotent
-- never touches `.venv`, `.git`, `.env`, or committed fixtures.

## Release validation

Versioning is fully automated by [Release Please](https://github.com/googleapis/release-please)
from [Conventional Commits](https://www.conventionalcommits.org)
(`feat:` → minor, `fix:` → patch, `feat!:`/`BREAKING CHANGE:` → minor
while pre-1.0). `qbit_ops.__version__` resolves at runtime via
`importlib.metadata.version("qbit-ops")`, never by reading
`pyproject.toml`.

Before merging a deliberate release PR:

```bash
make check-version                              # pyproject.toml <-> manifest agreement
make test-qbit-matrix                            # confirm all matrix entries pass
python3 scripts/check_qbit_matrix_freshness.py   # flag a STALE matrix (informational only)
```

Neither compatibility check auto-publishes or auto-merges -- both are
advisory input to a human decision.

## Guidelines

- Keep `--dry-run` as the default for any new modifying command.
- Preserve raw qBittorrent tracker URLs for API calls; only normalize
  for comparison and display.
- Never read secrets from CLI arguments -- use `.env`/environment
  variables, consistent with `src/qbit_ops/config.py`.
- Document new commands in [docs/COMMANDS.md](COMMANDS.md) and add a
  one-line feature bullet to the README if user-facing.

## Pytest markers

| Marker | Meaning |
|---|---|
| `tui` | Slow (Pilot-based) TUI tests -- `test_tui_app.py`, `test_tui_bulk_mutation_audit.py` |
| `docker` | Requires a real Docker daemon + `QBIT_OPS_DOCKER_MATRIX=1` -- every test under `tests/integration/` |
| `capture` | Writes committed fixtures -- `test_matrix_capture.py` only (always also `docker`) |

Enforced with `--strict-markers`: an unregistered marker is a
collection error, not a silent no-op.
