"""Test the `qbit-ops status` command end to end via the CLI."""

import csv
import io
import json
import re

from typer.testing import CliRunner

from qbit_core.errors import QbitAuthenticationError
from qbit_ops.cli.app import app
from qbit_ops.cli.exit_codes import StatusExitCode
from tests.support import FakeQbitClient, make_torrent

ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def test_status_exits_healthy_for_a_clean_instance(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a", state="uploading", progress=1.0)],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == StatusExitCode.HEALTHY


def test_status_table_shows_aggregate_counts(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a", state="downloading", progress=0.2),
            make_torrent(hash="b", state="uploading", progress=1.0),
            make_torrent(hash="c", state="uploading", progress=1.0),
        ],
        download_speed=1024,
        upload_speed=2048,
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == StatusExitCode.HEALTHY
    assert "healthy" in result.stdout
    assert "Total" in result.stdout and "3" in result.stdout
    assert "Downloading" in result.stdout
    assert "Seeding" in result.stdout
    assert "1.0 KiB/s" in result.stdout
    assert "2.0 KiB/s" in result.stdout


def test_status_reports_warning_for_stalled_torrents(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    torrents = [make_torrent(hash="a", state="stalledDL")]
    configure_qbit_backend(client=FakeQbitClient(torrents=torrents))

    result = runner.invoke(app, ["status"])

    assert result.exit_code == StatusExitCode.WARNING
    assert "warning" in result.stdout


def test_status_reports_critical_for_errored_torrents(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(torrents=[make_torrent(hash="a", state="error")])
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == StatusExitCode.CRITICAL
    assert "critical" in result.stdout


def test_status_reports_unavailable_on_authentication_failure(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    configure_qbit_backend(
        client_error=QbitAuthenticationError(
            "Authentication to qBittorrent failed."
        ),
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == StatusExitCode.UNAVAILABLE
    assert "unavailable" in result.stdout
    assert "Authentication to qBittorrent failed." in result.stderr


def test_status_quiet_emits_no_stdout_when_healthy(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    configure_qbit_backend(client=FakeQbitClient(torrents=[]))

    result = runner.invoke(app, ["status", "--quiet"])

    assert result.exit_code == StatusExitCode.HEALTHY
    assert result.stdout == ""


def test_status_quiet_emits_no_stdout_when_warning(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    torrents = [make_torrent(hash="a", state="stalledDL")]
    configure_qbit_backend(client=FakeQbitClient(torrents=torrents))

    result = runner.invoke(app, ["status", "--quiet"])

    assert result.exit_code == StatusExitCode.WARNING
    assert result.stdout == ""


def test_status_quiet_emits_no_stdout_when_critical(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    configure_qbit_backend(
        client=FakeQbitClient(torrents=[make_torrent(hash="a", state="error")]),
    )

    result = runner.invoke(app, ["status", "--quiet"])

    assert result.exit_code == StatusExitCode.CRITICAL
    assert result.stdout == ""


def test_status_quiet_creates_client_exactly_once(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """`_create_qbit_client()` never shows a connection spinner or banner,
    so there is no `quiet` flag left to assert on; this instead pins
    down the call count itself.
    """
    calls = configure_qbit_backend(client=FakeQbitClient(torrents=[]))

    result = runner.invoke(app, ["status", "--quiet"])

    assert result.exit_code == StatusExitCode.HEALTHY
    assert calls == [{}]


def test_status_errors_stay_visible_on_stderr_even_when_quiet(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """A connection/configuration error must still emit a concise message
    on stderr even when `--quiet` is set.
    """
    configure_qbit_backend(
        client_error=QbitAuthenticationError("Authentication failed."),
    )

    result = runner.invoke(app, ["status", "--quiet"])

    assert result.exit_code == StatusExitCode.UNAVAILABLE
    assert result.stdout == ""
    assert "Authentication failed." in result.stderr


def test_status_quiet_rejects_an_explicit_non_default_format(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    configure_qbit_backend(client=FakeQbitClient(torrents=[]))

    result = runner.invoke(app, ["status", "--quiet", "--format", "json"])

    assert result.exit_code == StatusExitCode.INVALID_USAGE
    assert result.stdout == ""


def test_status_json_is_valid_and_matches_the_schema(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    torrents = [make_torrent(hash="a", state="stalledDL")]
    configure_qbit_backend(client=FakeQbitClient(torrents=torrents))

    result = runner.invoke(app, ["status", "--format", "json"])

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "1"
    assert payload["health"] == "warning"
    assert "connection" in payload
    assert "transfers" in payload
    assert "rates" in payload
    assert "alerts" in payload


def test_status_json_output_contains_no_ansi_escape_sequences(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    configure_qbit_backend(client=FakeQbitClient(torrents=[]))

    result = runner.invoke(app, ["status", "--format", "json"])

    assert ANSI_ESCAPE_PATTERN.search(result.stdout) is None


def test_status_jsonl_emits_exactly_one_json_object(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    configure_qbit_backend(client=FakeQbitClient(torrents=[]))

    result = runner.invoke(app, ["status", "--format", "jsonl"])

    lines = result.stdout.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["schema_version"] == "1"


def test_status_csv_has_a_stable_header_and_numeric_values(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[make_torrent(hash="a", state="downloading")],
            download_speed=13_002_342,
        ),
    )

    result = runner.invoke(app, ["status", "--format", "csv"])

    reader = csv.reader(io.StringIO(result.stdout))
    rows = list(reader)
    assert rows[0] == ["section", "key", "value"]
    assert ["transfers", "total", "1"] in rows
    assert ["rates", "download_bytes_per_second", "13002342"] in rows


def test_status_counts_unknown_torrent_states_instead_of_dropping_them(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[make_torrent(hash="a", state="somethingBrandNew")],
        ),
    )

    result = runner.invoke(app, ["status", "--format", "json"])

    payload = json.loads(result.stdout)
    assert payload["transfers"]["unknown"] == 1
    assert payload["health"] == "warning"


def test_status_never_leaks_configured_credentials(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    configure_qbit_backend(client=FakeQbitClient(torrents=[]))

    for output_format in ("table", "json", "jsonl", "csv"):
        result = runner.invoke(app, ["status", "--format", output_format])
        assert "secret-password" not in result.stdout
        assert "secret-password" not in result.stderr
