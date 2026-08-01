"""LOW-risk mutation scenarios against real, disposable qBittorrent instances.

Every mutation targets an exact synthetic infohash -- never `--all` --
and every test restores the torrent to a known state afterward so the
same container fixture stays usable for later tests in the same
session.
"""

from __future__ import annotations

import json
import time

from packaging.version import Version
from typer.testing import CliRunner

from qbit_ops.cli.app import app
from tests.integration._instrumentation import record_http_requests

runner = CliRunner()


def _is_at_or_above_stop_start_threshold(web_api_version: str) -> bool:
    """Return whether `web_api_version` is >= 2.11.0, numerically.

    A plain string comparison is wrong here: "2.9.3" >= "2.11.0" is
    `True` lexicographically (since "9" > "1"), even though 2.9.3 is
    numerically the *older* version -- exactly the qBittorrent 4.6.7
    Web API this matrix exercises. Always compare via `Version`.
    """
    return Version(web_api_version) >= Version("2.11.0")


def _invoke(env, args):
    return runner.invoke(app, args, env=env)


def _state_of(env, torrent_hash: str) -> str:
    result = _invoke(
        env, ["torrents", "inspect", "--hash", torrent_hash, "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)["torrent"]["state"]


def _wait_for_state_prefix(
    env, torrent_hash: str, prefix: str, timeout_seconds: float = 15.0
) -> str:
    """Poll until the torrent's state starts with `prefix` (e.g. "stalled").

    qBittorrent applies a start/stop internally with a short delay
    before `torrents_info()` reflects it -- polling avoids an
    assertion racing that delay.
    """
    deadline = time.monotonic() + timeout_seconds
    last_state = ""
    while time.monotonic() < deadline:
        last_state = _state_of(env, torrent_hash)
        if last_state.startswith(prefix):
            return last_state
        time.sleep(0.5)
    raise AssertionError(
        f"{torrent_hash} never reached state prefix {prefix!r}, "
        f"last: {last_state!r}"
    )


def test_dry_run_pause_sends_no_mutation_request(hermetic_env, seeded_corpus):
    before = _state_of(hermetic_env, seeded_corpus.complete.info_hash_hex)

    with record_http_requests() as rec:
        result = _invoke(
            hermetic_env,
            [
                "torrents",
                "pause",
                "--hash",
                seeded_corpus.complete.info_hash_hex,
            ],
        )

    assert result.exit_code == 0, result.output
    assert rec.count("torrents/stop") == 0
    assert rec.count("torrents/pause") == 0
    after = _state_of(hermetic_env, seeded_corpus.complete.info_hash_hex)
    assert after == before, "dry-run must never change torrent state"


def test_dry_run_resume_sends_no_mutation_request(
    hermetic_env, qbit_container, seeded_corpus
):
    target_hash = seeded_corpus.complete.info_hash_hex
    web_api_version = qbit_container.observed_web_api_version
    above_threshold = _is_at_or_above_stop_start_threshold(web_api_version)
    start_endpoint = "torrents/start" if above_threshold else "torrents/resume"

    before = _state_of(hermetic_env, target_hash)

    with record_http_requests() as rec:
        result = _invoke(
            hermetic_env, ["torrents", "resume", "--hash", target_hash]
        )

    assert result.exit_code == 0, result.output
    assert rec.count(start_endpoint) == 0
    assert rec.count("torrents/start") == 0
    assert rec.count("torrents/resume") == 0
    after = _state_of(hermetic_env, target_hash)
    assert after == before, "dry-run must never change torrent state"


def test_dry_run_reannounce_sends_no_mutation_request(
    hermetic_env, seeded_corpus
):
    target_hash = seeded_corpus.tracked.info_hash_hex
    control_hash = seeded_corpus.complete.info_hash_hex
    control_state_before = _state_of(hermetic_env, control_hash)

    with record_http_requests() as rec:
        result = _invoke(
            hermetic_env, ["torrents", "reannounce", "--hash", target_hash]
        )

    assert result.exit_code == 0, result.output
    assert rec.count("torrents/reannounce") == 0
    assert _state_of(hermetic_env, control_hash) == control_state_before, (
        "an unrelated exact-hash torrent must never be affected by a "
        "dry-run mutation (which should send no request at all)"
    )


def test_real_resume_then_pause_reaches_the_negotiated_endpoint_exactly_once(
    hermetic_env, qbit_container, seeded_corpus
):
    # The corpus is seeded already-stopped (see `_seed.py`), so `resume`
    # is the first mutation guaranteed to be a real state change --
    # `pause` on an already-stopped torrent is a legitimate no-op that
    # qbit-ops correctly skips without an API call.
    target_hash = seeded_corpus.complete.info_hash_hex
    control_hash = seeded_corpus.incomplete.info_hash_hex
    control_state_before = _state_of(hermetic_env, control_hash)

    web_api_version = qbit_container.observed_web_api_version
    above_threshold = _is_at_or_above_stop_start_threshold(web_api_version)
    start_endpoint = "torrents/start" if above_threshold else "torrents/resume"
    stop_endpoint = "torrents/stop" if above_threshold else "torrents/pause"

    with record_http_requests() as rec:
        result = _invoke(
            hermetic_env,
            ["torrents", "resume", "--hash", target_hash, "--no-dry-run"],
        )
    assert result.exit_code == 0, result.output
    assert rec.count(start_endpoint) == 1, rec.endpoints()
    _wait_for_state_prefix(
        hermetic_env, target_hash, "stalled"
    )  # no peers, so seeding stalls

    assert _state_of(hermetic_env, control_hash) == control_state_before, (
        "an unrelated exact-hash torrent must never be affected by "
        "another torrent's mutation"
    )

    with record_http_requests() as rec:
        result = _invoke(
            hermetic_env,
            ["torrents", "pause", "--hash", target_hash, "--no-dry-run"],
        )
    assert result.exit_code == 0, result.output
    assert rec.count(stop_endpoint) == 1, rec.endpoints()

    # qBittorrent 4.x reports "paused*", 5.x reports "stopped*" for the
    # same concept (docs/COMPATIBILITY.md #8) -- assert on whichever
    # vocabulary this matrix entry actually uses.
    stopped_prefix = "stopped" if above_threshold else "paused"
    restored_state = _wait_for_state_prefix(
        hermetic_env, target_hash, stopped_prefix
    )
    assert (
        restored_state == f"{stopped_prefix}UP"
    ), f"restore failed, torrent is {restored_state!r}"
    assert _state_of(hermetic_env, control_hash) == control_state_before


def test_reannounce_targets_the_exact_hash_and_reaches_the_endpoint(
    hermetic_env, seeded_corpus
):
    target_hash = seeded_corpus.tracked.info_hash_hex
    control_hash = seeded_corpus.complete.info_hash_hex
    control_state_before = _state_of(hermetic_env, control_hash)

    with record_http_requests() as rec:
        result = _invoke(
            hermetic_env,
            ["torrents", "reannounce", "--hash", target_hash, "--no-dry-run"],
        )

    assert result.exit_code == 0, result.output
    assert rec.count("torrents/reannounce") == 1, rec.endpoints()
    assert _state_of(hermetic_env, control_hash) == control_state_before


def test_command_result_is_truthful_about_matched_and_modified_counts(
    hermetic_env, seeded_corpus
):
    # `pause`/`resume`/`reannounce` have no `--format`; they print a
    # plain Rich summary table (verified against the real CLI, not
    # assumed) -- assert on that text directly.
    result = _invoke(
        hermetic_env,
        ["torrents", "pause", "--hash", seeded_corpus.tracked.info_hash_hex],
    )

    assert result.exit_code == 0, result.output
    assert "matched" in result.output and "1" in result.output
    # The tracked torrent was seeded already-stopped, so a dry-run
    # pause is correctly a no-op: matched, but not modified.
    assert "modified" in result.output
    assert (
        "NO_CHANGES" in result.output.upper().replace(" ", "_")
        or "no_changes" in result.output.lower()
    )


def test_selection_never_means_all_without_an_exact_hash_or_filter(
    hermetic_env, seeded_corpus
):
    result = _invoke(hermetic_env, ["torrents", "pause"])

    assert result.exit_code != 0
    assert "--hash" in result.output or "--all" in result.output
