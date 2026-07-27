# Compatibility payload fixtures

This directory contains **payload fixtures** — JSON snapshots of the
shapes qBittorrent's Web API returns (torrents, trackers, transfer
info, application/version strings) — plus contract tests that exercise
qbit-ops's real production boundary functions
(`qbit_ops.qbit.fields`, `qbit_ops.torrent_states`,
`qbit_ops.trackers`, `qbit_ops.doctor`) against them.

The goal is **contract testing against payload shapes**, not a
qBittorrent version compatibility matrix. No fixture here proves that
qbit-ops works against a real running qBittorrent instance of any
particular version — that is the job of a future Docker-based version
matrix (see "What this does not prove" below).

## Trust levels

Every fixture's `_meta.trust` field is one of:

- **`synthetic`** — hand-constructed to match the *documented* shape of
  a Web API response (field names/types as described in
  `qbittorrentapi`'s own source and the qBittorrent WebUI API wiki), or
  to exercise an edge case (missing/`null`/extra fields, an unusual but
  real state string). Not observed from any running qBittorrent
  instance. Most fixtures in this directory are `synthetic`.
- **`official-example`** — matches a documented convention (e.g. a
  version-string format) drawn from public qBittorrent/qbittorrent-api
  documentation, without being an actual capture of a specific running
  instance.
- **`captured-container`** — captured from a real qBittorrent instance
  running in a disposable, hermetic Docker container, via the (not yet
  implemented) capture mechanism described below. **No fixture in this
  directory currently has this trust level.**
- **`captured-instance`** — captured from a real qBittorrent instance
  outside of the disposable-container harness. **qbit-ops never
  captures fixtures from a user's homelab instance; this trust level is
  reserved and, as of this phase, unused.**

A fixture's trust level is a claim about *how the payload was
obtained*, not about its correctness. `synthetic` and
`official-example` fixtures may still be wrong if the documented shape
they're based on is wrong or has drifted — they are a best-effort
approximation, not evidence.

**Provenance rule, enforced by `test_fixture_provenance.py`:** only
`captured-container`/`captured-instance` fixtures may declare a
specific `qbittorrent_version`, `web_api_version`, or
`qbittorrent_api_version` as if it were observed. A `synthetic` or
`official-example` fixture asserting a specific captured version would
misrepresent a fabricated payload as evidence — exactly what
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
| `limitations`             | What this fixture does **not** prove (required, non-empty) — e.g. "not a capture", "does not prove Docker compatibility". |

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

## Version-shaped fixture directories (qBittorrent 4.6.x / 5.0.x / 5.1.x)

This phase does **not** create per-version fixture directories (e.g.
`fixtures/4.6.x/`, `fixtures/5.0.x/`, `fixtures/5.1.x/`). Doing so
without real captures would either be empty directories that falsely
imply version support work has started, or would require inventing
version-specific payload differences that have not actually been
observed — both explicitly out of scope for this phase.

**Current honest state: authentic version fixture pending Docker
capture.** The `torrents`/`trackers`/`transfer`/`application` fixtures
in this directory use vocabulary known to differ between 4.x and 5.x
where it matters (e.g. `paused_state_4x.json` vs. `stopped_state_5x.json`
for the pause/resume → stop/start state-name rename), documented from
public qBittorrent release notes and `qbittorrentapi` source, not from
a captured instance. When the future Docker version matrix captures
real per-version payloads, they should land in version-labelled
subdirectories with `trust: "captured-container"` and an actual
`qbittorrent_version`/`web_api_version`, and the fixtures here should be
compared against them rather than assumed correct.

## Fixture capture mechanism (designed, not implemented)

No capture script exists yet. This phase only designs the constraints
a future one-off capture tool must satisfy before it may run against a
disposable, hermetic qBittorrent container as part of the eventual
Docker version matrix work:

- Runs with an explicit temporary `HOME`/`XDG_CONFIG_HOME` and a
  controlled temporary working directory — never the real user
  environment, never discovering a real `.env` (see `AGENTS.md`'s
  smoke-test isolation rule).
- Targets only an explicit, hardcoded `localhost`/container-network URL
  for a disposable container the tool itself provisions — never a URL
  read from configuration, never the user's homelab instance.
- Uses explicit, dedicated test credentials created for that disposable
  container, never real credentials.
- Adds only synthetic torrent data (freely distributable test content,
  e.g. a well-known Linux ISO's public torrent) to the container before
  capturing — never real user torrents.
- Calls **only read-only endpoints** (`torrents/info`,
  `torrents/trackers`, `transfer/info`, `app/version`,
  `app/webapiVersion`) against an explicit allowlist. It must refuse to
  call any mutation endpoint unless it has independently verified the
  target is the isolated disposable container it provisioned itself —
  never the user's real instance.
- Applies the same deterministic sanitization/placeholder rules as
  this README's security policy before writing anything to disk, and
  refuses to write a fixture file containing an unredacted secret.
- Writes each captured payload with a `_meta` block using
  `trust: "captured-container"` and the real observed
  `qbittorrent_version`/`web_api_version`/`qbittorrent_api_version`,
  into a version-labelled subdirectory.
- Writes to an explicit, dedicated output directory — never overwrites
  existing hand-authored `synthetic`/`official-example` fixtures in
  place.

Building and running this tool is out of scope for this phase; it is
a prerequisite for the future Docker version matrix phase, not part of
the payload-fixture compatibility phase.

## What this does prove

- qbit-ops's field-reading, state-classification, tracker-health, and
  version-parsing functions handle every payload shape exercised here
  (ordinary values, missing optional fields, explicit `null`s, unknown
  states/statuses, extra future fields, both 4.x and 5.x state
  vocabularies, both v1 and v2/hybrid infohash lengths) without raising
  or silently producing wrong output.
- Tracker messages containing embedded secret-shaped URLs are fully
  sanitized before they could reach rendered output.

## What this does not prove

- That qbit-ops works against any specific real, running qBittorrent
  version. That requires the future Docker version matrix, using real
  captured-container fixtures, not the synthetic/official-example
  fixtures here.
- That the documented Web API shapes these fixtures encode have not
  drifted from a given qBittorrent release's actual behavior.
