# Compatibility payload fixtures

This directory contains **payload fixtures** -- JSON snapshots of the
shapes qBittorrent's Web API returns (torrents, trackers, transfer
info, application/version strings) -- plus contract tests that exercise
qbit-ops's real production boundary functions
(`qbit_core.qbit.fields`, `qbit_core.shared.torrent_states`,
`qbit_core.features.trackers`, `qbit_core.features.doctor`) against
them.

The goal is **contract testing against payload shapes**. Most fixtures
here are `synthetic`/`official-example` and prove nothing about a real
running instance on their own -- but `fixtures/captured-container/`
(added by the Docker version matrix phase, 2026-07-27) now contains
authentic payloads captured from real, disposable
`linuxserver/qbittorrent` containers for the 4.6.x/5.0.x/5.1.x/5.2.x
release lines. See
`tests/compatibility/test_captured_container_payloads.py` for the
contract tests that exercise them, and `docs/COMPATIBILITY.md` for
exactly what that does and does not justify claiming.

## Trust levels

Every fixture's `_meta.trust` field is one of:

- **`synthetic`** -- hand-constructed to match the *documented* shape of
  a Web API response (field names/types as described in
  `qbittorrentapi`'s own source and the qBittorrent WebUI API wiki), or
  to exercise an edge case (missing/`null`/extra fields, an unusual but
  real state string). Not observed from any running qBittorrent
  instance. Most fixtures in this directory are `synthetic`.
- **`official-example`** -- matches a documented convention (e.g. a
  version-string format) drawn from public qBittorrent/qbittorrent-api
  documentation, without being an actual capture of a specific running
  instance.
- **`captured-container`** -- captured from a real qBittorrent instance
  running in a disposable container on a dedicated Docker network, with
  hermetic *configuration* (temporary `HOME`/`.env` discovery, generated
  credentials -- see `tests/integration/README.md` "Hermeticity, in one
  paragraph"). That configuration hermeticity does **not** extend to the
  network: the container's public egress is not technically blocked.
  Captured via the capture
  mechanism described below (`tests/integration/_capture.py`,
  `make capture-qbit-fixtures QBIT_MATRIX_ID=<id>`). Now used by
  `fixtures/captured-container/<matrix-id>/*.json` for each matrix
  entry in `qbit_core/data/qbittorrent-matrix.toml`.
- **`captured-instance`** -- captured from a real qBittorrent instance
  outside of the disposable-container harness. **qbit-ops never
  captures fixtures from a user's homelab instance; this trust level is
  reserved and, as of this phase, unused.**

A fixture's trust level is a claim about *how the payload was
obtained*, not about its correctness. `synthetic` and
`official-example` fixtures may still be wrong if the documented shape
they're based on is wrong or has drifted -- they are a best-effort
approximation, not evidence.

**Provenance rule, enforced by `test_fixture_provenance.py`:** only
`captured-container`/`captured-instance` fixtures may declare a
specific `qbittorrent_version`, `web_api_version`, or
`qbittorrent_api_version` as if it were observed. A `synthetic` or
`official-example` fixture asserting a specific captured version would
misrepresent a fabricated payload as evidence -- exactly what
`docs/COMPATIBILITY.md` forbids for the project as a whole.

## Fixture file shape

Every fixture is a JSON file with two top-level keys:

```json
{
  "_meta": {
    "trust": "synthetic",
    "description": "...",
    "qbittorrent_version": null,
    "web_api_version": null,
    "qbittorrent_api_version": null,
    "sanitization": "...",
    "fields_removed": [],
    "limitations": "..."
  },
  "payload": { "...": "..." }
}
```

`_meta` fields:

| Field                     | Meaning                                                                                   |
| ------------------------- | ------------------------------------------------------------------------------------------ |
| `trust`                   | One of the four trust levels above.                                                       |
| `description`             | What this fixture is and why it exists (required, non-empty).                             |
| `qbittorrent_version`     | The qBittorrent application version, only if actually captured/known; else `null`.        |
| `web_api_version`         | The Web API version, only if actually captured/known; else `null`.                        |
| `qbittorrent_api_version` | The `qbittorrent-api` Python client version in use, when relevant to how the value was produced; else `null`. |
| `sanitization`            | What was changed/removed/replaced to remove real-world identifying data, or `"n/a"`.       |
| `fields_removed`          | List of field names intentionally dropped from a real or documented shape (if any).        |
| `limitations`             | What this fixture does **not** prove (required, non-empty) -- e.g. "not a capture", "does not prove Docker compatibility". |

`load_fixture()`/`load_all_fixtures()` in `_fixture_loader.py` are the
only intended way to read these files; they validate `trust` against
the known set and raise if it's not recognized.

## Security policy

**No fixture may contain:**

- a real announce URL or tracker hostname
- a real passkey, API token, username, or password
- a real qBittorrent/torrent host or IP
- a local filesystem path identifying the user (e.g. a real `$HOME`)
- a real torrent name or infohash
- copied `.env` content
- raw backup exports

Use explicit placeholders: `tracker.example`, `other.example`,
`REDACTED`, repeated hex like
`aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`, and names like
`sample-linux-image.iso`.

A raw tracker URL with embedded credential-shaped material may exist
in a fixture **only** when the fixture's purpose is to test
sanitization itself, and only with a fake placeholder value:
`message_with_secret_like_material.json` is the one sanctioned
exception, using the placeholder passkey
`FAKE_PLACEHOLDER_PASSKEY_0000000000000000`.

`test_payload_security.py` scans every fixture file's **raw text**
(not just the decoded payload, so a leak hidden in `_meta` prose is
still caught) for:

- userinfo embedded in a URL (`scheme://user:pass@host`)
- passkey/token/secret/key query parameters with a non-placeholder
  value
- hostnames outside an explicit allowlist (`tracker.example`,
  `other.example`, `example.com`/`.org`/`.net`, `localhost`)
- home-directory-shaped paths (`/home/...`, `/Users/...`,
  `C:\Users\...`)
- `.env`-shaped fragments (`QBIT_HOST=`, `QBIT_USER=`,
  `QBIT_PASSWORD=`)
- malformed JSON

Any new fixture must pass all of these scans.

## Version-shaped fixture directories (qBittorrent 4.6.x / 5.0.x / 5.1.x / 5.2.x)

Per-version fixture directories now exist, but only where a real image
was pulled, started, version-verified, and captured (2026-07-27) -- see
`qbit_core/data/qbittorrent-matrix.toml` (loaded via
`qbit_core.qbit.compatibility.load_compatibility_evidence()`) for the
exact pinned image references and digests:

| Directory | Release line | Observed | Web API |
|---|---|---|---|
| `fixtures/captured-container/qbit-4.6.7/` | 4.6.x (last maintained tag) | `v4.6.7` | `2.9.3` |
| `fixtures/captured-container/qbit-5.0.0/` | 5.0.x (first tag) | `v5.0.0` | `2.11.2` |
| `fixtures/captured-container/qbit-5.1.4/` | 5.1.x (last maintained tag) | `v5.1.4` | `2.11.4` |
| `fixtures/captured-container/qbit-5.2.3/` | 5.2.x (current stable at capture time) | `v5.2.3` | `2.15.1` |

`qbit-5.2.3` was added as the then-current stable release, verified
against the official `qbittorrent/qBittorrent` GitHub releases API (not
memory) -- it does not replace any of the three historical entries.

No empty directory was ever created for a version that had not yet
been captured, and no version-specific payload difference was invented
ahead of an actual observation.

## Fixture capture mechanism

`tests/integration/_capture.py` (`capture_matrix_fixtures()`) implements
the capture tool, run via `make capture-qbit-fixtures QBIT_MATRIX_ID=<id>`
(also exercised by `tests/integration/test_matrix_capture.py`):

- Runs with an explicit temporary `HOME`/`XDG_CONFIG_HOME` and a
  controlled temporary working directory, via
  `tests/integration/_harness.HermeticEnv` -- never the real user
  environment, never discovering a real `.env`.
- Targets only the loopback-published port of the disposable container
  the harness itself started and version-verified
  (`tests/integration/_harness.start_matrix_container` fails closed if
  the observed `app_version()` does not match the matrix manifest) --
  never a URL read from configuration.
- Uses a fixed, per-run WebUI credential the harness itself generates
  and pre-seeds into the container's config before first boot (see
  `_qbit_conf_template.py`) -- never a real credential.
- Adds only the deterministic synthetic torrent corpus
  (`tests/integration/_torrent_corpus.py`) to the container before
  capturing -- never real user torrents.
- Calls only `torrents_info()`, `torrents_trackers()`, `transfer_info()`,
  `app_version()`, `app_web_api_version()`, and `app_build_info()`
  (where the endpoint exists) -- no mutation endpoint.
- Substitutes the disposable in-network tracker's hostname
  (`qbit-ops-tracker`) with the allowlisted placeholder
  `tracker.example` before writing, and re-scans the final serialized
  JSON text with the exact same `tests/compatibility/_security_scan.py`
  rules the committed-fixture test suite enforces
  (`test_no_captured_fixture_leaks_a_real_secret`) -- a violation
  raises `CaptureSecurityError` and nothing is written.
- Writes each captured payload with a `_meta` block using
  `trust: "captured-container"`, the real observed
  `qbittorrent_version`/`web_api_version`/`qbittorrent_api_version`,
  and the exact pinned `image_reference`/`image_digest`, into
  `fixtures/captured-container/<matrix-id>/`.
- Never overwrites existing hand-authored `synthetic`/`official-example`
  fixtures -- a fully separate directory tree.

## What this does prove

- qbit-ops's field-reading, state-classification, tracker-health, and
  version-parsing functions handle every payload shape exercised here
  (ordinary values, missing optional fields, explicit `null`s, unknown
  states/statuses, extra future fields, both 4.x and 5.x state
  vocabularies, both v1 and v2/hybrid infohash lengths) without raising
  or silently producing wrong output -- against both hand-constructed
  `synthetic` payloads and real `captured-container` payloads.
- Tracker messages containing embedded secret-shaped URLs are fully
  sanitized before they could reach rendered output.
- For each captured matrix entry: qbit-ops's field-reading
  functions handle a *real* `torrents_info()`/`torrents_trackers()`/
  `transfer_info()`/`app_version()` payload from that exact pinned
  image, not an approximation of one.

## What this does not prove

- That qbit-ops's *mutation* commands work end to end against a real
  instance from bare payload fixtures alone -- that requires the live
  container tests under `tests/integration/` (`make test-qbit-matrix`),
  which exercise real HTTP calls, not just captured snapshots.
- Broad version-range support (e.g. "4.6-5.2 supported"). Each captured
  entry is one specific, pinned image digest at one point in time --
  `payload fixture tested` and `container integration tested` are both
  narrower claims than `supported`, and `docs/COMPATIBILITY.md` never
  makes the broader one.
- That the documented Web API shapes these fixtures encode have not
  drifted from a given qBittorrent release's actual behavior beyond
  the pinned digests actually captured.
