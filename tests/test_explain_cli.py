"""Test `explain torrent`/`explain tracker` at the CLI level.

Covers hash/tracker resolution, output formats, exit codes, generated
help, and secret-freedom. `tests/test_explain.py` covers the same
semantics at the domain level; this file exercises them through the
real CLI wiring.
"""

import json

from typer.testing import CliRunner

from qbit_ops.cli.app import app
from qbit_ops.cli.exit_codes import ExplainExitCode
from tests.support import FakeQbitClient, make_torrent

TORRENT_A = "a" * 40
TORRENT_B = "b" * 40
SECRET_PASSKEY = "SUPER_SECRET_PASSKEY_918273"
PASSKEY_URL = f"https://tracker.example/announce/{SECRET_PASSKEY}"


def _client_with_stalled_upload() -> FakeQbitClient:
    return FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A, name="Debian ISO", state="stalledUP")
        ],
        trackers_by_hash={
            TORRENT_A: [
                {"url": "https://tracker.example/announce", "status": 2}
            ]
        },
    )


# --- Torrent explanation ------------------------------------------------------


def test_explain_torrent_by_full_hash(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client_with_stalled_upload())

    result = runner.invoke(
        app, ["explain", "torrent", "--hash", TORRENT_A, "--format", "json"]
    )

    assert result.exit_code == ExplainExitCode.WARNING
    payload = json.loads(result.stdout)
    assert payload["target_identity"] == TORRENT_A
    assert payload["findings"][0]["code"] == "TORRENT_STALLED_UP"


def test_explain_torrent_by_unique_prefix(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client_with_stalled_upload())

    result = runner.invoke(
        app,
        ["explain", "torrent", "--hash", TORRENT_A[:8], "--format", "json"],
    )

    assert result.exit_code == ExplainExitCode.WARNING
    payload = json.loads(result.stdout)
    assert payload["target_identity"] == TORRENT_A


def test_explain_torrent_unknown_hash_is_target_unavailable(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(
        client=FakeQbitClient(torrents=[make_torrent(hash=TORRENT_A, name="A")])
    )

    result = runner.invoke(
        app, ["explain", "torrent", "--hash", "deadbeef", "--format", "json"]
    )

    assert result.exit_code == ExplainExitCode.TARGET_UNAVAILABLE
    payload = json.loads(result.stdout)
    assert payload["explanation"] is None


def test_explain_torrent_ambiguous_hash_fails_clearly(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[
                make_torrent(hash="abc123def456", name="Debian ISO"),
                make_torrent(hash="abc987fed654", name="Debian live image"),
            ]
        )
    )

    result = runner.invoke(app, ["explain", "torrent", "--hash", "abc"])

    from qbit_ops.cli.exit_codes import ExitCode

    assert result.exit_code == ExitCode.ERROR
    assert "matches" in result.stderr
    assert "Debian ISO" in result.stderr


def test_explain_torrent_table_output(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client_with_stalled_upload())

    result = runner.invoke(app, ["explain", "torrent", "--hash", TORRENT_A])

    assert result.exit_code == ExplainExitCode.WARNING
    assert "Stalled upload" in result.stdout
    assert "warning" in result.stdout.lower()


def test_explain_torrent_jsonl_matches_json(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client_with_stalled_upload())

    json_result = runner.invoke(
        app, ["explain", "torrent", "--hash", TORRENT_A, "--format", "json"]
    )
    jsonl_result = runner.invoke(
        app, ["explain", "torrent", "--hash", TORRENT_A, "--format", "jsonl"]
    )

    lines = jsonl_result.stdout.splitlines()
    assert len(lines) == 1
    jsonl_payload = json.loads(lines[0])
    json_payload = json.loads(json_result.stdout)
    jsonl_payload.pop("generated_at")
    json_payload.pop("generated_at")
    assert jsonl_payload == json_payload


def test_explain_torrent_csv_is_rejected_before_any_api_call(
    runner: CliRunner,
) -> None:
    result = runner.invoke(
        app, ["explain", "torrent", "--hash", TORRENT_A, "--format", "csv"]
    )

    assert result.exit_code != ExplainExitCode.INFO
    assert "csv" in result.stderr.lower()
    assert "not supported" in result.stderr.lower()


def test_explain_torrent_healthy_exits_zero(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[
                make_torrent(hash=TORRENT_A, name="A", state="uploading")
            ],
            trackers_by_hash={
                TORRENT_A: [
                    {"url": "https://tracker.example/announce", "status": 2}
                ]
            },
        )
    )

    result = runner.invoke(
        app, ["explain", "torrent", "--hash", TORRENT_A, "--format", "json"]
    )

    assert result.exit_code == ExplainExitCode.INFO


def test_explain_torrent_table_does_not_repeat_the_summary(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """`explain torrent` always builds exactly one finding, whose
    `explanation` *is* the report summary -- so printing both showed the
    same sentence twice on every single invocation. The TUI already
    skipped it (`qbit_ops.tui.formatting`); the CLI did not.
    """
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[
                make_torrent(hash=TORRENT_A, name="A", state="uploading")
            ],
            trackers_by_hash={
                TORRENT_A: [
                    {"url": "https://tracker.example/announce", "status": 2}
                ]
            },
        )
    )

    result = runner.invoke(app, ["explain", "torrent", "--hash", TORRENT_A])

    assert result.exit_code == ExplainExitCode.INFO
    # The sentence still reaches the reader -- once, in the finding.
    assert "actively seeding" in result.stdout
    assert "Summary" not in result.stdout


def test_explain_torrent_critical_exits_two(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[make_torrent(hash=TORRENT_A, name="A", state="error")],
            trackers_by_hash={TORRENT_A: []},
        )
    )

    result = runner.invoke(
        app, ["explain", "torrent", "--hash", TORRENT_A, "--format", "json"]
    )

    assert result.exit_code == ExplainExitCode.CRITICAL


def test_explain_torrent_help_documents_hash_and_format(
    runner: CliRunner,
) -> None:
    result = runner.invoke(app, ["explain", "torrent", "--help"])

    assert result.exit_code == 0
    assert "--hash" in result.stdout
    assert "--format" in result.stdout


# --- Tracker explanation ------------------------------------------------------


def _client_with_mixed_tracker() -> FakeQbitClient:
    return FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A, name="A"),
            make_torrent(hash=TORRENT_B, name="B"),
        ],
        trackers_by_hash={
            TORRENT_A: [
                {"url": "https://tracker.example/announce", "status": 2}
            ],
            TORRENT_B: [
                {"url": "https://tracker.example/announce", "status": 4}
            ],
        },
    )


def test_explain_tracker_by_normalized_identity(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client_with_mixed_tracker())

    result = runner.invoke(
        app,
        [
            "explain",
            "tracker",
            "--tracker",
            "tracker.example",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == ExplainExitCode.WARNING
    payload = json.loads(result.stdout)
    assert payload["target_identity"] == "tracker.example"
    assert payload["findings"][0]["code"] == "TRACKER_MIXED_ENDPOINT_HEALTH"


def test_explain_tracker_accepts_a_full_url(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client_with_mixed_tracker())

    result = runner.invoke(
        app,
        [
            "explain",
            "tracker",
            "--tracker",
            "https://tracker.example/announce/PASSKEY",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == ExplainExitCode.WARNING
    payload = json.loads(result.stdout)
    assert payload["target_identity"] == "tracker.example"
    assert "PASSKEY" not in result.stdout


def test_explain_tracker_no_observations_is_target_unavailable(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[make_torrent(hash=TORRENT_A, name="A")],
            trackers_by_hash={
                TORRENT_A: [
                    {"url": "https://other.example/announce", "status": 2}
                ]
            },
        )
    )

    result = runner.invoke(
        app,
        [
            "explain",
            "tracker",
            "--tracker",
            "tracker.example",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == ExplainExitCode.TARGET_UNAVAILABLE
    payload = json.loads(result.stdout)
    assert payload["explanation"] is None
    assert payload["tracker"] == "tracker.example"


def test_explain_tracker_table_output(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client_with_mixed_tracker())

    result = runner.invoke(
        app, ["explain", "tracker", "--tracker", "tracker.example"]
    )

    assert result.exit_code == ExplainExitCode.WARNING
    assert "tracker.example" in result.stdout
    assert "Mixed endpoint health" in result.stdout


def test_explain_tracker_jsonl_matches_json(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client_with_mixed_tracker())

    json_result = runner.invoke(
        app,
        [
            "explain",
            "tracker",
            "--tracker",
            "tracker.example",
            "--format",
            "json",
        ],
    )
    jsonl_result = runner.invoke(
        app,
        [
            "explain",
            "tracker",
            "--tracker",
            "tracker.example",
            "--format",
            "jsonl",
        ],
    )

    lines = jsonl_result.stdout.splitlines()
    assert len(lines) == 1
    jsonl_payload = json.loads(lines[0])
    json_payload = json.loads(json_result.stdout)
    jsonl_payload.pop("generated_at")
    json_payload.pop("generated_at")
    assert jsonl_payload == json_payload


def test_explain_tracker_csv_is_rejected_before_any_api_call(
    runner: CliRunner,
) -> None:
    result = runner.invoke(
        app,
        [
            "explain",
            "tracker",
            "--tracker",
            "tracker.example",
            "--format",
            "csv",
        ],
    )

    assert result.exit_code != ExplainExitCode.INFO
    assert "csv" in result.stderr.lower()
    assert "not supported" in result.stderr.lower()


def test_explain_tracker_help_documents_tracker_and_format(
    runner: CliRunner,
) -> None:
    result = runner.invoke(app, ["explain", "tracker", "--help"])

    assert result.exit_code == 0
    assert "--tracker" in result.stdout
    assert "--format" in result.stdout


# --- Machine-readable silence -------------------------------------------------


def test_explain_torrent_machine_readable_success_is_silent_on_stderr(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client_with_stalled_upload())

    result = runner.invoke(
        app, ["explain", "torrent", "--hash", TORRENT_A, "--format", "json"]
    )

    assert result.stderr == ""


def test_explain_tracker_machine_readable_success_is_silent_on_stderr(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client_with_mixed_tracker())

    result = runner.invoke(
        app,
        [
            "explain",
            "tracker",
            "--tracker",
            "tracker.example",
            "--format",
            "json",
        ],
    )

    assert result.stderr == ""


# --- Secret sanitization ------------------------------------------------------


def test_explain_torrent_never_leaks_a_secret(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[make_torrent(hash=TORRENT_A, name="A", state="error")],
            trackers_by_hash={
                TORRENT_A: [
                    {
                        "url": PASSKEY_URL,
                        "status": 4,
                        "msg": f"failed to reach {PASSKEY_URL}",
                    }
                ]
            },
        )
    )

    for fmt in ("table", "json", "jsonl"):
        result = runner.invoke(
            app,
            ["explain", "torrent", "--hash", TORRENT_A, "--format", fmt],
        )
        assert SECRET_PASSKEY not in result.stdout
        assert SECRET_PASSKEY not in result.stderr


def test_explain_tracker_never_leaks_a_secret(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[make_torrent(hash=TORRENT_A, name="A")],
            trackers_by_hash={
                TORRENT_A: [
                    {
                        "url": PASSKEY_URL,
                        "status": 4,
                        "msg": f"failed to reach {PASSKEY_URL}",
                    }
                ]
            },
        )
    )

    for fmt in ("table", "json", "jsonl"):
        result = runner.invoke(
            app,
            [
                "explain",
                "tracker",
                "--tracker",
                "tracker.example",
                "--format",
                fmt,
            ],
        )
        assert SECRET_PASSKEY not in result.stdout
        assert SECRET_PASSKEY not in result.stderr


def test_explain_tracker_no_observations_path_never_leaks_the_raw_url(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[make_torrent(hash=TORRENT_A, name="A")],
        )
    )

    result = runner.invoke(
        app,
        ["explain", "tracker", "--tracker", PASSKEY_URL, "--format", "table"],
    )

    assert result.exit_code == ExplainExitCode.TARGET_UNAVAILABLE
    assert SECRET_PASSKEY not in result.stdout
    assert SECRET_PASSKEY not in result.stderr
    assert "tracker.example" in result.stdout


def test_report_tracker_help_never_promises_repetition() -> None:
    """`--tracker` is a single-value option on both report commands, but
    both advertised the composable filter's help text ("repeatable;
    combines with OR"). Typer keeps the last value silently, so an
    operator auditing two trackers got a report on one and no warning.
    """
    for command in (("explain", "tracker"), ("trackers", "inspect")):
        result = CliRunner().invoke(app, [*command, "--help"])
        rendered = " ".join(result.stdout.split())

        assert "repeatable" not in rendered, command
        assert "Restrict the report to one tracker" in rendered, command
