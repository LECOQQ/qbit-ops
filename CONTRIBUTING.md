# Contributing to qbit-ops

Thanks for taking the time to contribute. This project favors small,
explicit, safety-first changes over broad refactors — see the Safety Model
section in the [README](README.md) before touching any bulk operation.

## Setup

```bash
git clone https://github.com/LECOQQ/qbit-ops.git
cd qbit-ops
make install
```

This installs dependencies and the Conventional Commits Git hook.

## Workflow

1. Create a branch from `main`.
2. Make your change, keeping it scoped to one concern.
3. Add or update tests for any behavior change — bulk/tracker logic in
   particular should be covered under `tests/`.
4. While iterating, prefer a focused test file over the full suite --
   e.g. `poetry run pytest tests/test_doctor.py` -- or the fast
   checkpoint below. Run the full check suite before opening a PR:

   ```bash
   make format
   make check
   ```

   `make check` runs `ruff`, `black --check`, `pyright`, and `pytest`
   (including the full TUI suite). This is the same command CI runs on
   every push and pull request, and is the required gate before you
   push -- see [docs/TESTING.md](docs/TESTING.md) for the full tier
   breakdown and measured durations.

   ```bash
   make check-fast   # lint/types/version + non-TUI, non-Docker tests -- an
                      # intermediate checkpoint, NOT a substitute for `make check`
   make test-tui      # the complete TUI suite on its own
   ```

   Docker-based qBittorrent compatibility tests are never part of
   ordinary PR CI or `make check`. They are opt-in, local or
   scheduled-CI only:

   ```bash
   make test-qbit-version QBIT_MATRIX_ID=<id>  # one exact version, e.g. qbit-5.2.3
   make test-qbit-matrix                        # the full historical matrix
   make capture-qbit-fixtures QBIT_MATRIX_ID=<id>  # (re)capture committed fixtures -- review the diff
   ```

   All three require Docker and print the disposable target they're
   using; none of them ever read your real `.env` or contact a real
   qBittorrent instance. See [docs/TESTING.md](docs/TESTING.md) for
   when each is expected to run, and
   [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) for the compatibility
   evidence and claims policy.

5. Commit using [Conventional Commits](https://www.conventionalcommits.org)
   (`feat:`, `fix:`, `docs:`, `chore:`, ...) — enforced by the `commit-msg`
   hook installed via `make install`. These commits drive the version
   Release Please proposes next — see
   [docs/RELEASE.md](docs/RELEASE.md).
6. Open a pull request describing **what** changed and **why**.

## Guidelines

- Keep `--dry-run` as the default for any new modifying command.
- Preserve raw qBittorrent tracker URLs for API calls; only normalize for
  comparison.
- Never read secrets from CLI arguments — use `.env` / environment
  variables, consistent with `src/qbit_ops/config.py`.
- Document new commands in [docs/COMMANDS.md](docs/COMMANDS.md) and add a
  one-line feature bullet to the README if user-facing.

## Reporting issues

Open a GitHub issue with steps to reproduce, expected vs. actual behavior,
and your qBittorrent version (4.x vs 5.x tracker state naming differs — see
`src/qbit_ops/torrents.py`).
