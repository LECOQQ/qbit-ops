"""Tracker-mutation scenarios against real, disposable qBittorrent instances.

Only the disposable in-network tracker identity is ever used as a
source/target value -- never a real URL or passkey. Source/target
matching uses full announce URLs (verified against the real CLI: the
default `--match exact` mode compares the full tracker URL, not a bare
`host:port` -- see the Docker matrix implementation note).
"""

from __future__ import annotations

import json
import time

import qbittorrentapi
from typer.testing import CliRunner

from qbit_ops.main import app
from tests.integration._instrumentation import record_http_requests

runner = CliRunner()

FAKE_PLACEHOLDER_PASSKEY = "FAKE_PLACEHOLDER_PASSKEY_0000000000000000"
TRACKER_URL = "http://qbit-ops-tracker:6969/announce"
TRACKER_IDENTITY = "qbit-ops-tracker:6969"
# A distinct *port* (not just path) so its normalized identity is
# distinguishable from TRACKER_URL's in qbit-ops's own redacted,
# host:port-only tracker views -- it never needs to be reachable, only
# distinct.
MIRROR_TRACKER_URL = "http://qbit-ops-tracker:6970/announce"
MIRROR_TRACKER_IDENTITY = "qbit-ops-tracker:6970"
THIRD_TRACKER_URL = "http://qbit-ops-tracker:6971/announce"
THIRD_TRACKER_IDENTITY = "qbit-ops-tracker:6971"


def _invoke(env, args):
    return runner.invoke(app, args, env=env)


def _tracker_identities(env, torrent_hash: str) -> list[str]:
    """Return qbit-ops's own redacted, host:port-only tracker view.

    `trackers list` has no `--hash` filter (instance-wide inventory);
    read the torrent's own tracker list instead. Intentionally *not*
    the raw announce URL -- this exercises the same redaction path
    qbit-ops uses everywhere else.
    """
    result = _invoke(
        env, ["torrents", "inspect", "--hash", torrent_hash, "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    trackers = json.loads(result.stdout)["torrent"]["trackers"]
    return [entry["tracker"] for entry in trackers if entry.get("tracker")]


def _client(env) -> qbittorrentapi.Client:
    client = qbittorrentapi.Client(
        host=env["QBIT_HOST"],
        username=env["QBIT_USER"],
        password=env["QBIT_PASSWORD"],
    )
    client.auth_log_in()
    return client


def _raw_tracker_urls(env, torrent_hash: str) -> list[str]:
    """Return the real, un-redacted announce URLs, read directly via
    qbittorrentapi -- the ground truth this test harness checks
    against, never how qbit-ops itself renders trackers."""
    trackers = _client(env).torrents_trackers(torrent_hash=torrent_hash)
    return [tracker.url for tracker in trackers]


def _wait_until(predicate, timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.5)
    raise AssertionError("condition was never met before timeout")


def test_dry_run_tracker_remove_sends_no_mutation_request(
    hermetic_env, seeded_corpus
):
    torrent_hash = seeded_corpus.tracked.info_hash_hex

    with record_http_requests() as rec:
        result = _invoke(
            hermetic_env, ["trackers", "remove", "--tracker", TRACKER_URL]
        )

    assert result.exit_code == 0, result.output
    assert rec.count("torrents/removeTrackers") == 0
    assert TRACKER_IDENTITY in _tracker_identities(hermetic_env, torrent_hash)


def test_real_tracker_remove_requires_yes_and_reaches_the_endpoint(
    hermetic_env, seeded_corpus
):
    torrent_hash = seeded_corpus.tracked.info_hash_hex

    # Real execution without --yes must refuse (no TTY to confirm in a
    # CliRunner invocation) rather than silently proceeding.
    refused = _invoke(
        hermetic_env,
        ["trackers", "remove", "--tracker", TRACKER_URL, "--no-dry-run"],
    )
    assert refused.exit_code != 0
    assert TRACKER_IDENTITY in _tracker_identities(hermetic_env, torrent_hash)

    with record_http_requests() as rec:
        result = _invoke(
            hermetic_env,
            [
                "trackers",
                "remove",
                "--tracker",
                TRACKER_URL,
                "--no-dry-run",
                "--yes",
            ],
        )
    assert result.exit_code == 0, result.output
    assert rec.count("torrents/removeTrackers") == 1, rec.endpoints()

    _wait_until(
        lambda: TRACKER_IDENTITY
        not in _tracker_identities(hermetic_env, torrent_hash)
    )

    # Restore for any later test: re-add the tracker directly (a
    # harness-side seeding action, not qbit-ops -- qbit-ops has no
    # "add tracker from scratch" command, only add-if-present).
    _client(hermetic_env).torrents_add_trackers(
        torrent_hash=torrent_hash, urls=TRACKER_URL
    )
    _wait_until(
        lambda: TRACKER_IDENTITY
        in _tracker_identities(hermetic_env, torrent_hash)
    )


def test_add_if_present_and_replace_round_trip(hermetic_env, seeded_corpus):
    torrent_hash = seeded_corpus.tracked.info_hash_hex

    with record_http_requests() as rec:
        result = _invoke(
            hermetic_env,
            [
                "trackers",
                "add-if-present",
                "--source",
                TRACKER_URL,
                "--target",
                MIRROR_TRACKER_URL,
                "--no-dry-run",
                "--yes",
            ],
        )
    assert result.exit_code == 0, result.output
    assert rec.count("torrents/addTrackers") == 1, rec.endpoints()
    _wait_until(
        lambda: MIRROR_TRACKER_IDENTITY
        in _tracker_identities(hermetic_env, torrent_hash)
    )

    # Replace onto a target that does not already exist on the torrent,
    # so `apply_tracker_replacement` takes its `torrents_edit_tracker`
    # branch (verified: replacing onto an *already-present* target
    # instead takes its `torrents_remove_trackers`-only branch, since
    # editTracker onto a duplicate URL is unnecessary -- see
    # `qbit_ops.trackers.apply_tracker_replacement`).
    with record_http_requests() as rec:
        result = _invoke(
            hermetic_env,
            [
                "trackers",
                "replace",
                "--source",
                MIRROR_TRACKER_URL,
                "--target",
                THIRD_TRACKER_URL,
                "--no-dry-run",
                "--yes",
            ],
        )
    assert result.exit_code == 0, result.output
    assert rec.count("torrents/editTracker") == 1, rec.endpoints()
    _wait_until(
        lambda: MIRROR_TRACKER_IDENTITY
        not in _tracker_identities(hermetic_env, torrent_hash)
    )
    assert THIRD_TRACKER_IDENTITY in _tracker_identities(
        hermetic_env, torrent_hash
    )
    assert TRACKER_IDENTITY in _tracker_identities(hermetic_env, torrent_hash)

    # Restore: remove the third tracker directly (harness-side, not
    # qbit-ops), leaving only the original TRACKER_URL for later tests.
    _client(hermetic_env).torrents_remove_trackers(
        torrent_hash=torrent_hash, urls=[THIRD_TRACKER_URL]
    )
    _wait_until(
        lambda: THIRD_TRACKER_IDENTITY
        not in _tracker_identities(hermetic_env, torrent_hash)
    )


def test_replace_passkey_never_logs_the_real_passkey_value(
    hermetic_env, seeded_corpus
):
    # A distinct path from TRACKER_URL's bare "/announce": passkey
    # matching keys on (scheme, netloc, path) only, ignoring the query
    # string -- reusing "/announce" here would make the already-present
    # bare TRACKER_URL match the template too and collide with the
    # rewritten URL (verified: qBittorrent rejected the second edit with
    # "New tracker URL already exists").
    torrent_hash = seeded_corpus.tracked.info_hash_hex
    old_url = f"http://qbit-ops-tracker:6969/announce-passkey?passkey=old-{FAKE_PLACEHOLDER_PASSKEY}"
    new_url = f"http://qbit-ops-tracker:6969/announce-passkey?passkey=new-{FAKE_PLACEHOLDER_PASSKEY}"

    client = _client(hermetic_env)
    client.torrents_add_trackers(torrent_hash=torrent_hash, urls=old_url)
    _wait_until(
        lambda: old_url in _raw_tracker_urls(hermetic_env, torrent_hash)
    )

    with record_http_requests() as rec:
        result = _invoke(
            hermetic_env,
            [
                "trackers",
                "replace-passkey",
                "--tracker",
                "http://qbit-ops-tracker:6969/announce-passkey?passkey={passkey}",
                "--new-passkey",
                f"new-{FAKE_PLACEHOLDER_PASSKEY}",
                "--no-dry-run",
                "--yes",
            ],
        )

    assert result.exit_code == 0, result.output
    assert rec.count("torrents/editTracker") >= 1, rec.endpoints()
    assert f"new-{FAKE_PLACEHOLDER_PASSKEY}" not in result.output
    assert f"old-{FAKE_PLACEHOLDER_PASSKEY}" not in result.output

    _wait_until(
        lambda: new_url in _raw_tracker_urls(hermetic_env, torrent_hash)
    )
    client.torrents_remove_trackers(torrent_hash=torrent_hash, urls=[new_url])


def test_unsupported_tracker_operation_records_observed_behavior_not_a_guess(
    hermetic_env, seeded_corpus
):
    """Removing a tracker identity that is not present must report a
    clean no-op, never a guessed version threshold."""
    result = _invoke(
        hermetic_env,
        [
            "trackers",
            "remove",
            "--tracker",
            "http://no-such-tracker.invalid/announce",
            "--no-dry-run",
            "--yes",
        ],
    )
    assert result.exit_code != 0  # no targeted matches
