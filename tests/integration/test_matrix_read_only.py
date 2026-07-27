"""Read-only compatibility scenarios against real, disposable qBittorrent.

Every command here is invoked exactly as an end user would, through
`qbit_ops.main.app` via `CliRunner`, with the hermetic environment from
`conftest.hermetic_env` -- never by calling internal functions directly.
"""

from __future__ import annotations

import json
import re

from typer.testing import CliRunner

from qbit_ops.main import app

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()


def _invoke(env: dict[str, str], args: list[str]):
    return runner.invoke(app, args, env=env)


def test_status_json_is_valid_and_reports_the_container(
    hermetic_env, qbit_container
):
    result = _invoke(hermetic_env, ["status", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["connection"]["connected"] is True
    assert (
        payload["connection"]["qbittorrent_version"]
        == qbit_container.observed_version
    )
    assert (
        payload["connection"]["api_version"]
        == qbit_container.observed_web_api_version
    )
    assert not _ANSI_ESCAPE.search(result.stdout)


def test_doctor_passes_against_a_healthy_disposable_instance(
    hermetic_env, qbit_container
):
    result = _invoke(hermetic_env, ["doctor", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert isinstance(payload["checks"], list)
    assert payload["checks"], "doctor must run at least one check"


def test_torrents_list_json_schema_and_no_ansi(hermetic_env, seeded_corpus):
    result = _invoke(hermetic_env, ["torrents", "list", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    torrents = payload["torrents"] if isinstance(payload, dict) else payload
    hashes = {row["hash"] for row in torrents}
    assert seeded_corpus.complete.info_hash_hex in hashes
    assert seeded_corpus.incomplete.info_hash_hex in hashes
    assert not _ANSI_ESCAPE.search(result.stdout)
    assert result.stderr == "" or "Warning" not in result.stderr


def test_torrents_inspect_exact_hash(hermetic_env, seeded_corpus):
    result = _invoke(
        hermetic_env,
        [
            "torrents",
            "inspect",
            "--hash",
            seeded_corpus.complete.info_hash_hex,
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)["torrent"]
    assert payload["hash"] == seeded_corpus.complete.info_hash_hex
    assert payload["progress"] == 1.0


def test_trackers_list_contains_the_tracked_torrents_tracker(
    hermetic_env, seeded_corpus
):
    result = _invoke(hermetic_env, ["trackers", "list", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert any(
        "qbit-ops-tracker" in row["identity"] for row in payload["trackers"]
    )


def test_trackers_status_json_schema(hermetic_env, seeded_corpus):
    result = _invoke(hermetic_env, ["trackers", "status", "--format", "json"])

    # Exit code reflects tracker health, not command success -- our
    # disposable in-network tracker legitimately reports "warning"
    # (no real peer exchange), so a non-zero exit here is correct.
    assert result.exit_code in (0, 1), result.output
    payload = json.loads(result.stdout)
    assert "trackers" in payload


def test_explain_torrent_for_the_completed_torrent(hermetic_env, seeded_corpus):
    result = _invoke(
        hermetic_env,
        [
            "explain",
            "torrent",
            "--hash",
            seeded_corpus.complete.info_hash_hex,
            "--format",
            "json",
        ],
    )

    assert result.exit_code in (
        0,
        1,
        2,
    )  # explanation exit codes are severity-based, not just 0
    payload = json.loads(result.stdout)
    # A found torrent renders the full dict, not {"explanation": null}.
    assert payload


def test_explain_tracker_for_the_tracked_torrents_tracker(
    hermetic_env, seeded_corpus
):
    result = _invoke(
        hermetic_env,
        [
            "explain",
            "tracker",
            "--tracker",
            "qbit-ops-tracker:6969",
            "--format",
            "json",
        ],
    )

    assert result.exit_code in (0, 1, 2), result.output
    json.loads(result.stdout)  # must be valid JSON regardless of severity


def test_backup_export_json_against_the_isolated_instance_only(
    hermetic_env, seeded_corpus
):
    result = _invoke(hermetic_env, ["backup", "export", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"]["torrents"] >= 3
    # No real host/credential leakage into the exported payload.
    rendered = json.dumps(payload)
    assert "127.0.0.1" not in rendered or "password" not in rendered.lower()


def test_no_unexpected_endpoint_failure_across_the_read_only_suite(
    hermetic_env, seeded_corpus
):
    """A representative sweep: every read-only command exits cleanly.

    Any endpoint absent/changed for this qBittorrent version must show
    up as a non-zero, classified exit code (never a raw traceback) --
    `_catch_internal_errors` already guarantees this in production; this
    test proves it holds against a real instance, not just fakes.
    """
    commands = [
        (["status"], (0,)),
        (["doctor"], (0,)),
        (["torrents", "list"], (0,)),
        (["torrents", "categories"], (0,)),
        (["trackers", "list"], (0,)),
        # Health-reflecting exit code, not a command failure -- our
        # disposable in-network tracker legitimately reports "warning".
        (["trackers", "status"], (0, 1)),
    ]
    for command, acceptable_exit_codes in commands:
        result = _invoke(hermetic_env, [*command, "--format", "json"])
        assert (
            result.exit_code in acceptable_exit_codes
        ), f"{command}: {result.output}"
        if result.exception is not None:
            assert isinstance(
                result.exception, SystemExit
            ), f"{command}: unhandled exception {result.exception!r}"
        json.loads(result.stdout)
