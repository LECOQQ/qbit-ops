# qBittorrent Docker version matrix

Real, disposable qBittorrent containers — not fakes, not fixtures.
Skipped by default; every test here requires `QBIT_OPS_DOCKER_MATRIX=1`
(the `_require_opt_in` autouse fixture in `conftest.py` enforces this),
so plain `pytest`/`make check` never touches Docker.

## Running it

```sh
make test-qbit-matrix                          # all matrix entries
make test-qbit-version QBIT_MATRIX_ID=qbit-5.1.4  # one entry
make capture-qbit-fixtures QBIT_MATRIX_ID=qbit-5.1.4  # (re)capture fixtures
```

Requires Docker (checked by `make docker-matrix-doctor`, a dependency of
all three targets). Each run pulls (if not already cached) and starts
one to four `linuxserver/qbittorrent` containers, pinned by exact
image digest in the packaged compatibility manifest — expect a few
hundred MB of image storage and roughly 10-30 seconds per matrix entry.

## What's here

| File | Purpose |
|---|---|
| `../../src/qbit_ops/data/qbittorrent-matrix.toml` | The single source of truth: image references, digests, expected versions, capabilities, per matrix entry. Packaged with `qbit-ops` and loaded via `qbit_ops.qbit.compatibility.load_compatibility_evidence()` (`importlib.resources`), never a repository-relative path. |
| `_matrix.py` | Test-side adapter over the package loader (`qbit_ops.qbit.compatibility`) -- re-exports `QbitMatrixEntry` as `MatrixEntry` and the loader convenience wrappers Docker harness code uses; does not re-parse the manifest itself. |
| `_harness.py` | Docker network/container lifecycle, hermetic environment construction, the disposable-host and no-ambient-config guards, version-mismatch fail-closed check, teardown + leak detection. |
| `_qbit_conf_template.py` | Builds a pre-seeded `qBittorrent.conf` (DHT/PeX/LSD/UPnP/GeoIP-update disabled from first boot, fixed WebUI credential) — see the module docstring for how the password hash format was reverse-engineered. |
| `_bencode.py` | Minimal BEP 0003 bencode encoder (no dependency added). |
| `_torrent_corpus.py` | The deterministic 3-torrent synthetic corpus (complete/incomplete/tracked). |
| `_tracker_service.py` | The disposable in-network tracker (stdlib `http.server`); also runnable standalone inside its own container. |
| `_instrumentation.py` | Records real HTTP requests qbittorrentapi sends to a real container, without faking any of them. |
| `_seed.py` | Adds the synthetic corpus to a running container. |
| `_capture.py` | Captures authentic `captured-container` payload fixtures into `tests/compatibility/fixtures/captured-container/<matrix-id>/`. |
| `conftest.py` | pytest wiring: opt-in guard, `matrix_entry`/`qbit_container`/`tracker_service`/`seeded_corpus`/`hermetic_env` fixtures. |
| `test_matrix_read_only.py` | Read-only compatibility scenarios (status, doctor, torrents, trackers, explain, backup export). |
| `test_matrix_mutations.py` | LOW-risk torrent mutations (pause/resume/reannounce), dry-run vs. real, exact-hash targeting. |
| `test_matrix_tracker_mutations.py` | Tracker mutations (add-if-present/remove/replace/replace-passkey). |
| `test_matrix_capture.py` | Runs the capture mechanism and re-scans its own output for security. |

Pure guard-logic and corpus-generation unit tests (no Docker) live at
`tests/test_integration_harness_units.py` instead, so they always run
in the ordinary suite.

## Hermeticity, in one paragraph

**"Hermetic" here means configuration/test-target hermeticity, not
network isolation** (see `docs/COMPATIBILITY.md` §5.2's reserve F-1 for
the full detail) — every container is disposable, on a per-run
**dedicated** Docker network, with a uniquely named container and
loopback-only published ports. `HermeticEnv` fixes `QBIT_OPS_ENV_FILE`
to a guaranteed-absent path (which alone short-circuits qbit-ops's
`.env` discovery), plus `HOME`/`XDG_CONFIG_HOME` pointed at a temp
directory as defense in depth, plus explicit
`QBIT_HOST`/`QBIT_USER`/`QBIT_PASSWORD`.
`assert_no_ambient_qbit_ops_config` and `assert_target_is_disposable`
fail closed if any of this is not true — see
`tests/test_integration_harness_units.py` for the sabotage proof.
`start_matrix_container` refuses to proceed if the container's
*observed* `app_version()` does not match the manifest's
`expected_version`. DHT, PeX, LSD, UPnP/NAT-PMP, and public trackers
are all disabled at the qBittorrent application level, pre-sealed into
`qBittorrent.conf` before first boot (`_qbit_conf_template.py`).

**What this does not do**: the dedicated Docker network is not
`--internal`, and public network egress from the container is **not
technically blocked** — `docker network inspect` reports
`Internal=false`, and outbound HTTPS to the public Internet from inside
the container has been verified to succeed. `--internal` was tested and
rejected: it does block egress, but it also makes the loopback-published
WebUI port unreachable, which the harness depends on entirely. Never
describe this network itself as "hermetic" or "isolated" — only the
**configuration** (the paragraph above) is.
