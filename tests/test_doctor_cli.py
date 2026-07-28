"""Test the root `doctor` command at the CLI level.

Covers what `tests/test_doctor.py` cannot: exit codes, output-format
wiring, the machine-readable silence contract, secret redaction across
every rendered format and stderr, transient progress behavior, bounded
API calls via a real Typer invocation, and that `doctor` fully replaces
the removed `config doctor` command.
"""

import csv
import io
import json
import re

import pytest
from typer.testing import CliRunner

import qbit_ops.cli.rendering as m
from qbit_ops.cli.app import app
from qbit_ops.cli.exit_codes import DoctorExitCode
from tests.support import FakeQbitClient, make_config, make_torrent

ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

CLEAN_CONFIG = make_config(host="http://localhost:8080")
EMBEDDED_CREDENTIALS_CONFIG = make_config(
    host="http://admin:secret-password@localhost:8080",
    password="secret-password",
)


def _healthy_client() -> FakeQbitClient:
    return FakeQbitClient(
        torrents=[make_torrent(state="uploading")],
        qbittorrent_version="5.0.1",
        api_version="2.9.3",
    )


def _make_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m, "is_interactive_terminal", lambda: True)


# --- Command surface / migration --------------------------------------------


def test_doctor_is_a_root_command(runner: CliRunner) -> None:
    """Ensure `doctor` is a root-level command, not nested under a group."""
    result = runner.invoke(app, ["doctor", "--help"])

    assert result.exit_code == DoctorExitCode.PASS
    assert "--format" in result.stdout


def test_config_doctor_no_longer_exists(runner: CliRunner) -> None:
    """Ensure the removed `config doctor` command fails clearly instead of
    silently doing something else."""
    result = runner.invoke(app, ["config", "doctor"])

    assert result.exit_code != DoctorExitCode.PASS
    assert "no such command" in (result.stdout + result.stderr).lower()


def test_root_help_lists_doctor_and_not_config(runner: CliRunner) -> None:
    """Ensure the root help reflects the migration: `doctor` is listed,
    the `config` group is gone."""
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == DoctorExitCode.PASS
    assert "doctor" in result.stdout
    assert " config " not in result.stdout


# --- Exit codes ---------------------------------------------------------


def test_all_pass_exits_zero(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure a fully healthy instance exits 0."""
    configure_qbit_backend(client=_healthy_client(), config=CLEAN_CONFIG)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == DoctorExitCode.PASS


def test_warning_only_exits_one(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure a warning-only report (embedded host credentials) exits 1."""
    configure_qbit_backend(
        client=_healthy_client(), config=EMBEDDED_CREDENTIALS_CONFIG
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == DoctorExitCode.WARNING


def test_local_configuration_failure_exits_two(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure invalid local configuration is reported as a failure, not a
    crash."""
    from qbit_ops.config import ConfigError

    configure_qbit_backend(config_error=ConfigError("Missing QBIT_HOST"))

    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert result.exit_code == DoctorExitCode.FAILURE
    payload = json.loads(result.stdout)
    assert payload["overall_status"] == "fail"
    assert payload["checks"][0]["code"] == "CFG001"
    assert payload["checks"][0]["status"] == "fail"


def test_unreachable_qbittorrent_exits_two(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure an unreachable qBittorrent instance fails CONN001 and exits
    2, without qbit-ops crashing."""
    configure_qbit_backend(
        config=CLEAN_CONFIG, client_error=RuntimeError("connection refused")
    )

    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert result.exit_code == DoctorExitCode.FAILURE
    payload = json.loads(result.stdout)
    checks = {c["code"]: c for c in payload["checks"]}
    assert checks["CONN001"]["status"] == "fail"
    assert checks["CONN002"]["status"] == "skipped"


def test_authentication_failure_exits_two(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure a rejected login fails CONN002 specifically, while CONN001
    (reachability) still passes."""
    from qbit_ops.errors import QbitAuthenticationError

    configure_qbit_backend(
        config=CLEAN_CONFIG,
        client_error=QbitAuthenticationError("bad credentials"),
    )

    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert result.exit_code == DoctorExitCode.FAILURE
    payload = json.loads(result.stdout)
    checks = {c["code"]: c for c in payload["checks"]}
    assert checks["CONN001"]["status"] == "pass"
    assert checks["CONN002"]["status"] == "fail"


def test_unsupported_version_warns(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure an unvalidated qBittorrent major version warns, not fails."""
    client = FakeQbitClient(
        torrents=[make_torrent(state="uploading")],
        qbittorrent_version="3.3.16",
    )
    configure_qbit_backend(client=client, config=CLEAN_CONFIG)

    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert result.exit_code == DoctorExitCode.WARNING
    payload = json.loads(result.stdout)
    checks = {c["code"]: c for c in payload["checks"]}
    assert checks["COMPAT002"]["status"] == "warning"


def test_unknown_torrent_state_warns(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure an unrecognized torrent state warns via RUNTIME003."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(state="uploading"),
            make_torrent(hash="b" * 40, state="somethingBrandNew"),
        ]
    )
    configure_qbit_backend(client=client, config=CLEAN_CONFIG)

    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert result.exit_code == DoctorExitCode.WARNING
    payload = json.loads(result.stdout)
    checks = {c["code"]: c for c in payload["checks"]}
    assert checks["RUNTIME003"]["status"] == "warning"


# --- Output formats ----------------------------------------------------


def test_table_format_shows_overall_status_and_sections(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure table output groups checks by section and shows the overall
    status."""
    configure_qbit_backend(client=_healthy_client(), config=CLEAN_CONFIG)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == DoctorExitCode.PASS
    assert "pass" in result.stdout
    assert "Configuration" in result.stdout
    assert "Connectivity" in result.stdout
    assert "Compatibility" in result.stdout
    assert "Runtime" in result.stdout


def test_json_is_valid_and_ansi_free(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure --format json is valid JSON with the documented top-level
    shape and no ANSI decoration."""
    configure_qbit_backend(client=_healthy_client(), config=CLEAN_CONFIG)

    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert result.exit_code == DoctorExitCode.PASS
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "1"
    assert payload["overall_status"] == "pass"
    assert ANSI_ESCAPE_PATTERN.search(result.stdout) is None


def test_jsonl_is_one_document_per_invocation(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure --format jsonl emits exactly one document, matching the
    shared jsonl contract used by every other command (see
    docs/DECISIONS.md: doctor deliberately does not do one-line-per-check)."""
    configure_qbit_backend(client=_healthy_client(), config=CLEAN_CONFIG)

    result = runner.invoke(app, ["doctor", "--format", "jsonl"])

    assert result.exit_code == DoctorExitCode.PASS
    lines = result.stdout.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["overall_status"] == "pass"
    assert ANSI_ESCAPE_PATTERN.search(result.stdout) is None


def test_csv_has_the_documented_columns(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure --format csv uses the documented stable column set."""
    configure_qbit_backend(client=_healthy_client(), config=CLEAN_CONFIG)

    result = runner.invoke(app, ["doctor", "--format", "csv"])

    assert result.exit_code == DoctorExitCode.PASS
    rows = list(csv.reader(io.StringIO(result.stdout)))
    assert rows[0] == [
        "section",
        "code",
        "status",
        "message",
        "detail",
        "remediation",
    ]
    assert len(rows) == 14  # header + 13 checks
    assert ANSI_ESCAPE_PATTERN.search(result.stdout) is None


# --- Machine-readable silence & stderr -------------------------------------


@pytest.mark.parametrize("output_format", ["json", "jsonl", "csv"])
def test_machine_readable_success_is_silent_on_stderr(
    runner: CliRunner,
    configure_qbit_backend,
    output_format: str,
) -> None:
    """Ensure a successful machine-readable run prints nothing on stderr."""
    configure_qbit_backend(client=_healthy_client(), config=CLEAN_CONFIG)

    result = runner.invoke(app, ["doctor", "--format", output_format])

    assert result.exit_code == DoctorExitCode.PASS
    assert result.stderr == ""


@pytest.mark.parametrize("output_format", ["table", "json", "jsonl", "csv"])
def test_doctor_failure_is_silent_on_stderr(
    runner: CliRunner,
    configure_qbit_backend,
    output_format: str,
) -> None:
    """Ensure doctor never writes to stderr, even when the report itself
    describes a failure: the failure is the payload, not a CLI error."""
    from qbit_ops.config import ConfigError

    configure_qbit_backend(config_error=ConfigError("Missing QBIT_HOST"))

    result = runner.invoke(app, ["doctor", "--format", output_format])

    assert result.exit_code == DoctorExitCode.FAILURE
    assert result.stderr == ""


# --- Secret redaction ----------------------------------------------------


@pytest.mark.parametrize("output_format", ["table", "json", "jsonl", "csv"])
def test_no_secret_leak_in_any_format(
    runner: CliRunner,
    configure_qbit_backend,
    output_format: str,
) -> None:
    """Ensure the configured password never reaches any rendered format,
    even when it is embedded in QBIT_HOST and echoed back inside a
    connection error message."""
    configure_qbit_backend(
        config=EMBEDDED_CREDENTIALS_CONFIG,
        client_error=RuntimeError(
            "Unable to connect to qBittorrent at "
            "http://admin:secret-password@localhost:8080."
        ),
    )

    result = runner.invoke(app, ["doctor", "--format", output_format])

    assert "secret-password" not in result.stdout
    assert "secret-password" not in result.stderr


def test_no_secret_leak_on_unhandled_exception_message(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure an exception message that itself contains the password is
    redacted, not just the pre-built connection-error message."""

    class _LeakyClient(FakeQbitClient):
        def app_version(self) -> str:
            raise RuntimeError(
                "auth header rejected: Basic YWRtaW46c2VjcmV0LXBhc3N3b3Jk "
                "for user secret-password"
            )

    configure_qbit_backend(
        client=_LeakyClient(qbittorrent_version="5.0.1"),
        config=EMBEDDED_CREDENTIALS_CONFIG,
    )

    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert "secret-password" not in result.stdout


# --- Progress ------------------------------------------------------------


def test_spinner_used_in_interactive_table_mode(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure doctor drives a transient spinner when interactive and
    rendering a table."""
    _make_interactive(monkeypatch)
    calls: list[tuple[str, bool]] = []
    real_transient_spinner = m.transient_spinner

    def _spying_transient_spinner(message: str, *, enabled: bool):
        calls.append((message, enabled))
        return real_transient_spinner(message, enabled=enabled)

    monkeypatch.setattr(m, "transient_spinner", _spying_transient_spinner)
    configure_qbit_backend(client=_healthy_client(), config=CLEAN_CONFIG)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == DoctorExitCode.PASS
    assert calls == [("Running diagnostics...", True)]


@pytest.mark.parametrize("output_format", ["json", "jsonl", "csv"])
def test_spinner_disabled_for_machine_readable_formats(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
    output_format: str,
) -> None:
    """Ensure machine-readable formats disable progress even when
    interactive."""
    _make_interactive(monkeypatch)
    calls: list[tuple[str, bool]] = []
    real_transient_spinner = m.transient_spinner

    def _spying_transient_spinner(message: str, *, enabled: bool):
        calls.append((message, enabled))
        return real_transient_spinner(message, enabled=enabled)

    monkeypatch.setattr(m, "transient_spinner", _spying_transient_spinner)
    configure_qbit_backend(client=_healthy_client(), config=CLEAN_CONFIG)

    runner.invoke(app, ["doctor", "--format", output_format])

    assert calls == [("Running diagnostics...", False)]


def test_spinner_disabled_when_not_interactive(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure a non-interactive stderr disables progress for table output
    too, without needing --quiet (CliRunner's stderr is never a TTY, so
    this does not need `_make_interactive`)."""
    calls: list[tuple[str, bool]] = []
    real_transient_spinner = m.transient_spinner

    def _spying_transient_spinner(message: str, *, enabled: bool):
        calls.append((message, enabled))
        return real_transient_spinner(message, enabled=enabled)

    monkeypatch.setattr(m, "transient_spinner", _spying_transient_spinner)
    configure_qbit_backend(client=_healthy_client(), config=CLEAN_CONFIG)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == DoctorExitCode.PASS
    assert calls == [("Running diagnostics...", False)]


# --- Bounded API calls / client creation -----------------------------------


def test_creates_client_exactly_once(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure doctor creates exactly one qBittorrent client per
    invocation."""
    calls = configure_qbit_backend(
        client=_healthy_client(), config=CLEAN_CONFIG
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == DoctorExitCode.PASS
    assert calls == [{}]


def test_bounded_api_calls_via_cli(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure a full CLI invocation never exceeds the documented bounded
    budget: at most one call each to app_version, app_web_api_version,
    transfer_info, and torrents_info, and never a per-torrent call."""
    client = FakeQbitClient(
        torrents=[make_torrent(state="uploading") for _ in range(10)],
    )
    configure_qbit_backend(client=client, config=CLEAN_CONFIG)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == DoctorExitCode.PASS
    assert client.app_version_calls == 1
    assert client.app_web_api_version_calls == 1
    assert client.transfer_info_calls == 1
    assert client.torrents_info_calls == 1
    assert client.torrents_trackers_calls == 0
