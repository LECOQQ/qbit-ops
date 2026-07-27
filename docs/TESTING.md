---
title: "Testing policy"
description: "Which test tier to run when, and why -- with measured durations"
status: stable
---

# 🧪 Testing policy

> This is the tracked, normative testing-tier policy the 2026-07-27
> independent review (`docs/audits/2026-07-27-qbittorrent-compatibility-independent-review.md`,
> constat F-4/§16) found missing. It replaces guesswork with the
> durations actually measured that day, on the maintainer's machine --
> treat every number below as **an observation from 2026-07-27, not a
> permanent guarantee**. Your machine, Docker image cache state, and
> qBittorrent's own release cadence will all shift these numbers.

---

## 1. Focused edit-loop tests (no dedicated Make target)

While iterating on one module, run only the tests that exercise it.
Examples:

```sh
poetry run pytest tests/test_qbit_fields.py
poetry run pytest tests/test_doctor.py -k compatibility
poetry run pytest tests/test_tui_app.py -k preview
```

No generic "run what changed" target is provided -- pick the test file
that matches what you're editing. This is the fastest possible loop
(well under a second for most single files) and needs no explanation
beyond "run the file you'd expect to catch a regression".

## 2. `make check-fast` -- fast local checkpoint

```sh
make check-fast
```

Runs Ruff, `black --check`, Pyright, the version-sync check, and every
**hermetic, non-Docker, non-TUI** test: synthetic compatibility
fixtures, captured-fixture contract tests, architecture/security
tests, and the rest of the plain unit/CLI suite. Explicitly excludes
the TUI suite (`-m "not tui"`) and anything under `tests/integration/`
(`-m "not docker"`, which also covers `capture`-marked tests). No
network access.

**Measured 2026-07-27**: ~13s of pytest (1070 tests), ~27s total
including lint/format/type-check. The independent review measured the
same non-TUI slice at 13.71s independently.

> ⚠️ **`make check-fast` does not replace `make check`.** The TUI suite
> covers real guarantees (concurrent mutation lifecycle, race-condition
> regressions, the security/layering boundary) that `check-fast`
> intentionally skips for speed. Use `check-fast` as an intermediate
> checkpoint while iterating, never as the final gate before a push or
> PR.

## 3. `make test-tui` -- complete TUI suite

```sh
make test-tui
```

Runs every TUI-related test file: `test_tui_app.py` (Pilot-based
interface tests, mutation lifecycle, concurrency),
`test_tui_bulk_mutation_audit.py` (adversarial bulk-mutation audit
regressions), `test_tui_cli.py`, `test_tui_security.py` (the
import-boundary/layering guard), and `test_tui_state.py`. Never
contacts qBittorrent or Docker.

**Measured 2026-07-27**: ~283s (`test_tui_app.py` +
`test_tui_bulk_mutation_audit.py` alone account for effectively all of
it -- the other three files run in ~1s combined). This is why only
those two files carry the `tui` pytest marker (see §9): the marker
identifies what's actually slow, not everything with "tui" in its
filename.

## 4. `make check` -- complete validation, the push/PR gate

```sh
make check
```

Runs formatting, lint, type-checking, version synchronization, every
hermetic non-Docker test, and the complete TUI suite. Never runs the
Docker matrix and never rewrites captured fixtures.

**Measured 2026-07-27**: ~306s of pytest (1234 passed, 84 skipped --
the skipped ones are `tests/integration/`, which self-skip without the
Docker opt-in), ~319s total.

This remains **the required gate before push**, and is exactly what
`.github/workflows/ci.yml` (`make ci` → `make check` +
`ci-entrypoint`) runs on every push and pull request.

## 5. Latest-stable Docker validation

```sh
make test-qbit-version QBIT_MATRIX_ID=<id>
```

Starts one real, disposable qBittorrent container (dedicated Docker
network, loopback-only published port) and runs the complete read-only
+ mutation + tracker-mutation scenario set against it. Requires
Docker. Never writes to `tests/compatibility/fixtures/captured-container/`
(see §7). `<id>` must be an id from the packaged compatibility manifest
(`src/qbit_ops/data/qbittorrent-matrix.toml`, loaded via
`qbit_ops.qbit.compatibility.load_compatibility_evidence()` -- run
`make test-qbit-version` without `QBIT_MATRIX_ID` to print known ids).

**Measured 2026-07-27** (single-entry runs, images already pulled):
`qbit-5.2.3` ~108s, `qbit-4.6.7` ~112s, `qbit-5.0.0` ~168s, `qbit-5.1.4`
~206s. Expect noticeably longer on a cold image cache (a `docker pull`
per entry, ~194-202 MB each).

## 6. Full historical Docker matrix

```sh
make test-qbit-matrix
```

Runs every entry in the manifest. **Measured 2026-07-27**: ~594s
(~9m54s) with images cached. Not part of `make check`, not part of PR
CI -- see the CI cadence in `docs/COMPATIBILITY.md` §5.2/§15 and the
scheduled workflow (`.github/workflows/qbittorrent-matrix.yml`): weekly
runs only the current-stable entry (selected from the manifest, never
hardcoded); the complete historical matrix runs monthly, since the
historical entries pin immutable image digests and cannot drift
between weekly runs.

## 7. Fixture capture

```sh
make capture-qbit-fixtures QBIT_MATRIX_ID=<id>
```

The **only** command that writes to
`tests/compatibility/fixtures/captured-container/<id>/`. Selects only
the `capture`-marked test (`tests/integration/test_matrix_capture.py`)
via `-m capture` -- never a side effect of `test-qbit-matrix` or
`test-qbit-version` (independent-review finding F-7: an ordinary
matrix run used to rewrite committed fixtures on every invocation).
Review the diff before committing; known-volatile fields
(`added_on`, `completion_on`, `last_activity`, `seeding_time`) are
normalized to a fixed sentinel before writing, so an unchanged
instance produces a byte-identical file.

## 8. Release-preparation requirements

Before merging a deliberate release PR (not an ordinary feature PR):

1. Run `make test-qbit-matrix` and confirm all entries pass.
2. Run `scripts/check_qbit_matrix_freshness.py` (or wait for the
   scheduled workflow's `freshness-check` job) and review its result --
   a `STALE` result means the matrix should be extended with the
   current stable release (`docs/COMPATIBILITY.md` §10, rule 9) before
   the release, not that anything is broken.
3. Do not automatically publish or merge based on either result --
   both steps are advisory input to a human decision.

---

## 9. Pytest markers

Registered in `pyproject.toml` (`[tool.pytest.ini_options] markers`),
enforced with `--strict-markers` (an unregistered marker is a
collection error, not a silent no-op):

| Marker | Meaning | Applied to |
|---|---|---|
| `tui` | Slow (Pilot-based) TUI tests | `test_tui_app.py`, `test_tui_bulk_mutation_audit.py` (module-level `pytestmark`, not filename matching) |
| `docker` | Requires a real Docker daemon + `QBIT_OPS_DOCKER_MATRIX=1` | every test under `tests/integration/` (via a `pytest_collection_modifyitems` hook in `tests/integration/conftest.py`, scoped to that directory's own files -- see the hook's docstring for why an unscoped version would leak onto the whole suite) |
| `capture` | Writes committed fixtures | `tests/integration/test_matrix_capture.py` only (always also `docker`) |

`test_tui_cli.py`, `test_tui_security.py`, and `test_tui_state.py` are
**not** marked `tui`: measured at a combined ~1s, they run happily
inside `make check-fast` and gain nothing from exclusion. The Docker
opt-in/availability checks (`QBIT_OPS_DOCKER_MATRIX`,
`docker_is_available()`) are unchanged by the marker -- opting in
while Docker is unavailable still fails (`pytest.fail`), never skips.
