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
absent `.env` in the cwd is enough.

### 2. `make check-fast` -- fast local checkpoint

```bash
make check-fast
```

Ruff, `black --check`, Pyright, version-sync, documentation-link
consistency, AI-hygiene rules, and every hermetic non-Docker, non-TUI
test. No network access. Use while iterating -- **not a substitute for
`make check`** before a push.

### 3. `make test-tui` -- complete TUI suite

```bash
make test-tui
```

The Pilot-based interface tests, mutation lifecycle, concurrency, and
the security/layering guard. Never contacts qBittorrent or Docker.
The slowest block -- covers concurrent-mutation and
race-condition regressions `check-fast` skips for speed.

### 4. `make check` -- the required push/PR gate

```bash
make check
```

Formatting, lint, type-checking, version sync, documentation-link
consistency, AI-hygiene rules, every hermetic test, and the complete TUI
suite. Never touches Docker. This is exactly what CI (`make ci`) runs on
every push and pull request -- run it before you push.

The hermetic suites run in parallel (`pytest -n auto`). The Docker
targets below deliberately do not: they drive one shared disposable
container, so concurrent workers would collide on its ports and
mutate its state underneath each other.

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

## Feature worktrees

An isolated branch, worktree and virtualenv for one feature:

```bash
make worktree-new FEATURE=search-regex
make worktree-clean FEATURE=search-regex
```

`worktree-new` symlinks any locally present, gitignored configuration
into the new worktree, but only after Git confirms each target is
ignored -- a tracked file is never replaced by a symlink. The worktree gets its own
`poetry install`: it must never share the main `.venv`, whose editable
install points at the main checkout's `src/`.

### Why the environment is verified, not assumed

Poetry honours an inherited `VIRTUAL_ENV` **ahead of** its own
`virtualenvs.in-project` setting. Run `poetry install` from a worktree
with any environment activated and it reinstalls the project into *that*
environment, pointed at the worktree's `src/` -- so both checkouts then
exercise the wrong code, with no import error and no failing test.

`worktree-new` strips those variables before calling Poetry and then
proves the result, so the failure cannot reach you silently. Check any
checkout at any time:

```bash
make env-attest
```

It reports the expected root, interpreter, virtualenv, the `qbit_core`
and `qbit_ops` files actually imported, and the branch and SHA; it exits
`1` when the virtualenv or either package resolves outside the expected
root. The interpreter is reported but never judged -- a virtualenv
symlinks its `python` to the system binary, so `sys.executable` points
outside the checkout on a perfectly isolated environment.

Running Poetry by hand from a worktree gets none of this. Prefix it:

```bash
env -u VIRTUAL_ENV poetry install --extras tui
```

Editors inherit the same variable. A language server started from a
shell with the main `.venv` activated type-checks a worktree's files
against the *main* checkout's modules and reports errors that do not
exist -- point it at the worktree's own `.venv`, or ignore diagnostics
under `.worktrees/`.

`worktree-clean` is deliberately conservative. It refuses when the
worktree holds uncommitted or untracked work, when the branch is not
fully merged, and when the directory is not a registered Git worktree.
It never runs `git worktree remove --force` or `git branch -D`. Use
`--keep-branch` (via `python3 scripts/worktree_clean.py`) to drop only
the worktree.

## Documentation consistency

```bash
make check-docs
```

Fails on a Markdown link whose local target does not resolve, and on a
backtick-quoted path anchored at a repository root directory
(`docs/...`, `src/...`, `tests/...`) that does not exist. Contextual
shorthand like `shared/selection.py` is deliberately not checked: it
names a real file without naming a path from the repository root. Mark
a deliberate exception with `<!-- doc-links: ignore-next-line -->` on
the preceding line.

## Secret scanning

```bash
make secrets                      # the working tree
make secrets GITLEAKS_SCOPE=git   # every commit in history
```

Runs [gitleaks](https://github.com/gitleaks/gitleaks) against
`.gitleaks.toml`, preferring a locally installed binary and falling
back to the official container image. With neither available it
**fails** rather than skipping: a scanner that quietly does nothing
reports "clean" for the wrong reason.

`GITLEAKS_SCOPE` is a qbit-ops word, not a gitleaks subcommand. The
target invokes `detect`, understood by every 8.x release -- the newer
`dir`/`git` subcommands do not exist before 8.18, and Ubuntu still
packages 8.16. Verified on 8.16 and 8.30 against this config, custom
rule included.

The config extends the upstream ruleset and adds one project rule for
tracker announce URLs carrying a passkey -- this codebase's real
credential shape, and one entropy alone gets wrong in both directions.

Fixtures are allowlisted by **marker**, not by path: a fake secret is
spelled `UNMISTAKABLE-...`, so the scanner stays fully live on the test
files that carry them. Excluding `tests/` would blind it to exactly
where a real credential gets pasted while debugging.

Deliberately **not** part of `make check`. A secret scanner is
heuristic, and `AGENTS.md` reserves the blocking gates for deterministic
rules -- this one reports, a human judges.

The scan covers the working tree, not only what Git tracks -- an
ignored file still sits in plaintext on disk, and `git log` will never
show it to you. Local logs that record shell commands verbatim are the
usual culprit: a passkey typed into one command lands there.

Nothing in the working tree is excluded on the grounds of being
untracked, and that is the point. Where a local log is generated by
tooling that redacts credentials as it writes, the scan is what proves
the redaction still works -- a redactor nothing verifies is a belief,
not a control.

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
make check-dist                                  # build, validate and smoke the artifacts
make test-qbit-matrix                            # confirm all matrix entries pass
python3 scripts/check_qbit_matrix_freshness.py   # flag a STALE matrix (informational only)
```

## Publishing to PyPI

Users install from PyPI (`uv tool install qbit-ops`), so every release
has to reach it. Publishing is automatic and hangs off the existing
Release Please flow -- there is no second versioning or release system:

```text
Conventional Commits on main
  -> Release Please opens a release PR
  -> merging it bumps the version, tags vX.Y.Z, creates the GitHub Release
  -> release-please.yml dispatches publish.yml with that tag
  -> publish.yml builds, validates, and uploads to PyPI
```

Two constraints shape that wiring, and neither is optional:

- **`publish.yml` must stay non-reusable**, with the publish job defined
  in it. PyPI matches the `job_workflow_ref` OIDC claim against the
  workflow filename declared in the Trusted Publisher, and [reusable
  workflows are explicitly
  unsupported](https://docs.pypi.org/trusted-publishers/troubleshooting/):
  calling it through `workflow_call` fails with `invalid-publisher`.
- **The trigger is `workflow_dispatch`, not `on: release`.** Release
  Please creates the release with the default `GITHUB_TOKEN`, and
  [events raised by that token do not start another workflow
  run](https://docs.github.com/en/actions/concepts/security/github_token)
  -- an `on: release` trigger would silently never fire.
  `workflow_dispatch` is a documented exception to that rule, so
  `gh workflow run` from the release job does start it (which is why
  that job needs `actions: write`).

Uploading uses [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
over OIDC: the job requests `id-token: write`, runs in the `pypi`
GitHub Environment, and exchanges a short-lived token for upload
credentials. **No PyPI API token is stored in the repository.**

Before uploading, the workflow cross-checks the release tag against the
declared version (`scripts/check_version_sync.py --tag`), runs
`twine check --strict`, and installs the built wheel into a throwaway
virtualenv to confirm the `qbit-ops` console script actually runs.

To reproduce that locally:

```bash
make build        # wheel + sdist into dist/
make check-dist   # twine check, then install the wheel and run the entrypoint
```

`make check-dist` needs network access to resolve dependencies, so its
install smoke test is marked `network` and stays out of `make check`
and `make check-fast`.

Recovery path: `publish.yml` also accepts `workflow_dispatch`, so a
release whose publish step failed can be retried without cutting a new
version.

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
| `tui` | Slow (Pilot-based) TUI tests -- `test_tui_app.py`, `test_tui_architecture.py`, `test_tui_bulk_mutation_audit.py`, `test_tui_table_performance.py` |
| `docker` | Requires a real Docker daemon + `QBIT_OPS_DOCKER_MATRIX=1` -- every test under `tests/integration/` |
| `capture` | Writes committed fixtures -- `test_matrix_capture.py` only (always also `docker`) |

Enforced with `--strict-markers`: an unregistered marker is a
collection error, not a silent no-op.
