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

    # F-12: the deterministic corpus's "complete" torrent is
    # `stoppedUP`/progress 1.0 with no unhealthy trackers attached --
    # `build_torrent_explanation` classifies this as `TORRENT_STOPPED`
    # at INFO severity (exit 0), verified against a real container.
    # Any other code would mean the corpus or the explanation rules
    # changed underneath this test.
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["overall_severity"] == "info"
    assert payload["findings"][0]["code"] == "TORRENT_STOPPED"


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

    # F-12: the disposable in-network tracker (real, but only a minimal
    # bencode responder -- see _tracker_service.py) mixes with the
    # always-present DHT/PeX/LSD pseudo-trackers, which
    # `trackers_status` reports as disabled -- `explain tracker`
    # classifies this exact mix as `TRACKER_MIXED_ENDPOINT_HEALTH` at
    # WARNING severity (exit 1), verified against a real container.
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["overall_severity"] == "warning"
    assert payload["findings"][0]["code"] == "TRACKER_MIXED_ENDPOINT_HEALTH"


def test_backup_export_json_against_the_isolated_instance_only(
    hermetic_env, seeded_corpus
):
    result = _invoke(hermetic_env, ["backup", "export", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"]["torrents"] >= 3
    # F-11: each secret class is checked independently -- an `or`
    # between them would pass as long as just one class were absent,
    # proving nothing about the other. `backup export`'s whole purpose
    # is to preserve real tracker URLs (so a backup is restorable), so
    # the disposable tracker's hostname legitimately appears here --
    # that is not a leak. What must never appear is the WebUI
    # password, or a passkey-shaped credential embedded in a tracker
    # URL (this corpus's tracker URL carries no passkey, so none
    # should appear either).
    rendered = json.dumps(payload)
    assert "password" not in rendered.lower()
    assert hermetic_env["QBIT_PASSWORD"] not in rendered
    assert "passkey" not in rendered.lower()


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


def test_correct_password_succeeds_and_wrong_password_is_rejected(
    qbit_container,
):
    """F-16: prove the sealed `Password_PBKDF2` is what actually gates
    access, not a same-host/localhost authentication bypass --
    `WebUI\\LocalHostAuth=false` is set explicitly in the pre-sealed
    conf precisely so this stays true even though the harness always
    connects via 127.0.0.1."""
    import qbittorrentapi

    good_client = qbittorrentapi.Client(
        host=qbit_container.host,
        username=qbit_container.username,
        password=qbit_container.password,
    )
    good_client.auth_log_in()  # must not raise
    assert str(good_client.app_version()) == qbit_container.observed_version

    bad_client = qbittorrentapi.Client(
        host=qbit_container.host,
        username=qbit_container.username,
        password="definitely-the-wrong-password",
    )
    try:
        bad_client.auth_log_in()
        raise AssertionError(
            "an incorrect WebUI password was accepted -- LocalHostAuth "
            "bypass or a broken sealed password"
        )
    except qbittorrentapi.LoginFailed:
        pass  # expected
