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
4. Run the full check suite before opening a PR:

   ```bash
   make format
   make check
   ```

   `make check` runs `ruff`, `black --check`, `pyright`, and `pytest`.

5. Commit using [Conventional Commits](https://www.conventionalcommits.org)
   (`feat:`, `fix:`, `docs:`, `chore:`, ...) — enforced by the `commit-msg`
   hook installed via `make install`.
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
