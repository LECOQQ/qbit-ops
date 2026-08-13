"""Test the root `version` command and the eager `--version` option.

Covers the two distinct contracts: `--version` is a purely local,
offline identity line, while `version` also reports the instance's
versions and degrades to `unavailable`/`null` -- never to an error --
when the instance cannot be reached.
"""

import json
import platform

import pytest
from typer.testing import CliRunner

from qbit_core.errors import QbitAuthenticationError, QbitConnectionError
from qbit_ops import __version__
from qbit_ops.cli.app import app
from qbit_ops.cli.exit_codes import ExitCode
from qbit_ops.config import ConfigError
from tests.support import FakeQbitClient, make_torrent

VERSION_KEYS = {"qbit_ops", "python", "qbittorrent", "web_api"}

# Every failure mode the spec calls "expected unavailability": the local
# versions must survive all three, on stdout, with exit code 0.
EXPECTED_UNAVAILABILITY = {
    "configuration": {"config_error": ConfigError("boom")},
    "connection": {"client_error": QbitConnectionError("unreachable")},
    "authentication": {"client_error": QbitAuthenticationError("rejected")},
}


def _make_client() -> FakeQbitClient:
    return FakeQbitClient(
        torrents=[make_torrent()],
        qbittorrent_version="5.1.2",
        api_version="2.11.4",
    )


class _RaisingVersionClient(FakeQbitClient):
    """A client whose `app_version()` fails unexpectedly (not a
    connection/authentication failure)."""

    def app_version(self) -> str:
        raise RuntimeError("boom")


# --- `qbit-ops --version`: local, offline, deterministic --------------------


def test_version_option_prints_the_installed_version(
    runner: CliRunner,
) -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == ExitCode.SUCCESS
    assert result.stdout == f"qbit-ops {__version__}\n"


def test_version_option_matches_the_bare_invocation_identity(
    runner: CliRunner,
) -> None:
    assert runner.invoke(app, ["--version"]).stdout == runner.invoke(app).stdout


def test_version_option_never_creates_a_client(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    calls = configure_qbit_backend(client=_make_client())

    result = runner.invoke(app, ["--version"])

    assert result.exit_code == ExitCode.SUCCESS
    assert calls == []


def test_version_option_never_loads_the_configuration(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loads: list[str] = []

    def _record_load() -> None:
        loads.append("called")

    monkeypatch.setattr(
        "qbit_ops.cli.error_boundary.load_qbit_config", _record_load
    )
    monkeypatch.setattr("qbit_ops.app_services.load_qbit_config", _record_load)

    result = runner.invoke(app, ["--version"])

    assert result.exit_code == ExitCode.SUCCESS
    assert loads == []


# --- `qbit-ops version`: the four versions ----------------------------------


def test_version_command_reports_the_four_versions(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    configure_qbit_backend(client=_make_client())

    result = runner.invoke(app, ["version"])

    assert result.exit_code == ExitCode.SUCCESS
    for expected in ("qbit-ops", __version__, "Python", "qBittorrent", "Web"):
        assert expected in result.stdout
    assert "5.1.2" in result.stdout
    assert "2.11.4" in result.stdout


@pytest.mark.parametrize("output_format", ["json", "jsonl"])
def test_version_command_machine_readable_exposes_the_four_keys(
    runner: CliRunner,
    configure_qbit_backend,
    output_format: str,
) -> None:
    configure_qbit_backend(client=_make_client())

    result = runner.invoke(app, ["version", "--format", output_format])

    assert result.exit_code == ExitCode.SUCCESS
    payload = json.loads(result.stdout)
    assert set(payload) == VERSION_KEYS
    assert payload["qbit_ops"] == __version__
    assert payload["qbittorrent"] == "5.1.2"
    assert payload["web_api"] == "2.11.4"
    assert payload["python"].count(".") == 2
    assert result.stderr == ""


def test_version_command_reports_the_running_interpreter_version(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """`3.12.8`, never the multi-line `sys.version` banner."""
    configure_qbit_backend(client=_make_client())

    result = runner.invoke(app, ["version", "--format", "json"])

    assert json.loads(result.stdout)["python"] == platform.python_version()


def test_version_command_calls_each_remote_version_once(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """The spec asks for versions only: no torrent listing, no per-torrent
    tracker lookup, and no repeated version call."""
    client = _make_client()
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["version"])

    assert result.exit_code == ExitCode.SUCCESS
    assert client.app_version_calls == 1
    assert client.app_web_api_version_calls == 1
    assert client.torrents_info_calls == 0
    assert client.torrents_trackers_calls == 0


def test_version_command_rejects_csv_before_creating_a_client(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    calls = configure_qbit_backend(client=_make_client())

    result = runner.invoke(app, ["version", "--format", "csv"])

    assert result.exit_code == ExitCode.ERROR
    assert "csv" in result.stderr.lower()
    assert calls == []


# --- Unreachable instance: report, never an error ---------------------------


@pytest.mark.parametrize("failure", sorted(EXPECTED_UNAVAILABILITY))
def test_unreachable_instance_still_reports_local_versions(
    runner: CliRunner,
    configure_qbit_backend,
    failure: str,
) -> None:
    configure_qbit_backend(**EXPECTED_UNAVAILABILITY[failure])

    result = runner.invoke(app, ["version"])

    assert result.exit_code == ExitCode.SUCCESS
    assert __version__ in result.stdout
    assert result.stdout.count("unavailable") == 2
    assert result.stderr == ""


@pytest.mark.parametrize("failure", sorted(EXPECTED_UNAVAILABILITY))
def test_unreachable_instance_reports_null_remote_versions_in_json(
    runner: CliRunner,
    configure_qbit_backend,
    failure: str,
) -> None:
    configure_qbit_backend(**EXPECTED_UNAVAILABILITY[failure])

    result = runner.invoke(app, ["version", "--format", "json"])

    assert result.exit_code == ExitCode.SUCCESS
    payload = json.loads(result.stdout)
    assert payload["qbit_ops"] == __version__
    assert payload["qbittorrent"] is None
    assert payload["web_api"] is None
    assert result.stderr == ""


def test_unexpected_client_error_is_an_internal_error_not_unavailable(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    configure_qbit_backend(client_error=RuntimeError("boom"))

    result = runner.invoke(app, ["version"])

    assert result.exit_code == ExitCode.INTERNAL
    assert "unavailable" not in result.stdout
    assert "Internal error" in result.stderr


def test_unexpected_collection_error_is_not_degraded_to_unavailable(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """A failing `app_version()` that is not a connection/authentication
    failure must surface, not silently become `null`."""
    configure_qbit_backend(client=_RaisingVersionClient())

    result = runner.invoke(app, ["version", "--format", "json"])

    assert result.exit_code == ExitCode.INTERNAL
    assert result.stdout.strip() == ""
    assert "Internal error" in result.stderr
