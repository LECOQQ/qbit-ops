"""Test the shared `--format` option across every read-only command.

Phase 2.1 consistency pass: `--output` was removed everywhere in favor of
one `--format table|json|jsonl|csv` option. `FORMAT_SUPPORT` in
`app/main.py` is the single source of truth for which formats each
command actually supports; these tests parametrize over that same table
so the implementation, its validation, and its tests cannot drift apart.
"""

import csv
import io
import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.config import ConfigError
from app.main import FORMAT_SUPPORT, ExitCode, app
from app.ui import OutputFormat
from tests.support import FakeQbitClient, make_torrent

COMMANDS_DOC = Path(__file__).resolve().parent.parent / "docs" / "COMMANDS.md"
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
TORRENT_HASH = "abc123def456000000000000000000000000000a"
TRACKER_URL = "https://tracker.example/announce"

# Full argv (including any required options) for every read-only command
# except `backup diff`, which needs on-disk export files and is covered
# by its own dedicated tests below.
COMMAND_ARGV: dict[str, list[str]] = {
    "status": ["status"],
    "connection_check": ["connection", "check"],
    "config_doctor": ["config", "doctor"],
    "torrents_list": ["torrents", "list"],
    "torrents_categories": ["torrents", "categories"],
    "torrents_inspect": ["torrents", "inspect", "--hash", TORRENT_HASH[:8]],
    "trackers_list": ["trackers", "list"],
    "trackers_health": ["trackers", "health"],
    "trackers_inspect": ["trackers", "inspect", "--tracker", TRACKER_URL],
    "trackers_export": ["trackers", "export"],
    "backup_export": ["backup", "export"],
}

# Command path only (no options), valid for --help regardless of any
# required option, since --help is resolved eagerly by Click/Typer.
COMMAND_PATH: dict[str, list[str]] = {
    "status": ["status"],
    "connection_check": ["connection", "check"],
    "config_doctor": ["config", "doctor"],
    "torrents_list": ["torrents", "list"],
    "torrents_categories": ["torrents", "categories"],
    "torrents_inspect": ["torrents", "inspect"],
    "trackers_list": ["trackers", "list"],
    "trackers_health": ["trackers", "health"],
    "trackers_inspect": ["trackers", "inspect"],
    "trackers_export": ["trackers", "export"],
    "backup_export": ["backup", "export"],
    "backup_diff": ["backup", "diff"],
}

assert set(COMMAND_ARGV) == set(FORMAT_SUPPORT) - {"backup_diff"}
assert set(COMMAND_PATH) == set(FORMAT_SUPPORT)

CSV_SUPPORTED = sorted(
    command_id
    for command_id in COMMAND_ARGV
    if OutputFormat.csv in FORMAT_SUPPORT[command_id]
)
CSV_UNSUPPORTED = sorted(
    command_id
    for command_id in COMMAND_ARGV
    if OutputFormat.csv not in FORMAT_SUPPORT[command_id]
)


def _make_client() -> FakeQbitClient:
    """Build a minimal client that every command in the table can use."""
    return FakeQbitClient(
        torrents=[
            make_torrent(
                hash=TORRENT_HASH,
                name="Debian ISO",
                category="linux",
                state="uploading",
            )
        ],
        trackers_by_hash={
            TORRENT_HASH: [{"url": TRACKER_URL, "status": "2"}],
        },
    )


@pytest.mark.parametrize("command_id", sorted(COMMAND_PATH))
def test_command_help_exposes_format_not_output(
    runner: CliRunner,
    command_id: str,
) -> None:
    """Ensure every read-only command's help shows --format, not --output."""
    result = runner.invoke(app, [*COMMAND_PATH[command_id], "--help"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "--format" in result.stdout
    assert "--output" not in result.stdout


@pytest.mark.parametrize("command_id", sorted(COMMAND_ARGV))
def test_command_rejects_removed_output_option(
    runner: CliRunner,
    command_id: str,
) -> None:
    """Ensure --output fails clearly instead of being silently accepted."""
    result = runner.invoke(
        app,
        [*COMMAND_ARGV[command_id], "--output", "json"],
    )

    assert result.exit_code != ExitCode.SUCCESS
    combined = (result.stdout + result.stderr).lower()
    assert "no such option" in combined


@pytest.mark.parametrize("command_id", sorted(COMMAND_ARGV))
def test_command_json_is_valid_and_ansi_free(
    runner: CliRunner,
    configure_qbit_backend,
    command_id: str,
) -> None:
    """Ensure --format json is valid JSON with no ANSI escape sequences."""
    configure_qbit_backend(client=_make_client())

    result = runner.invoke(
        app,
        [*COMMAND_ARGV[command_id], "--format", "json"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    json.loads(result.stdout)
    assert ANSI_ESCAPE_PATTERN.search(result.stdout) is None


@pytest.mark.parametrize("command_id", sorted(COMMAND_ARGV))
def test_command_jsonl_is_one_valid_json_document(
    runner: CliRunner,
    configure_qbit_backend,
    command_id: str,
) -> None:
    """Ensure --format jsonl emits exactly one parseable line."""
    configure_qbit_backend(client=_make_client())

    result = runner.invoke(
        app,
        [*COMMAND_ARGV[command_id], "--format", "jsonl"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    lines = result.stdout.splitlines()
    assert len(lines) == 1
    json.loads(lines[0])
    assert ANSI_ESCAPE_PATTERN.search(result.stdout) is None


@pytest.mark.parametrize("command_id", CSV_SUPPORTED)
def test_command_csv_is_valid_where_supported(
    runner: CliRunner,
    configure_qbit_backend,
    command_id: str,
) -> None:
    """Ensure --format csv produces parseable, ANSI-free CSV data."""
    configure_qbit_backend(client=_make_client())

    result = runner.invoke(
        app,
        [*COMMAND_ARGV[command_id], "--format", "csv"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    rows = list(csv.reader(io.StringIO(result.stdout)))
    assert len(rows) >= 1
    assert ANSI_ESCAPE_PATTERN.search(result.stdout) is None


@pytest.mark.parametrize("command_id", CSV_UNSUPPORTED)
def test_command_csv_is_rejected_before_any_api_call(
    runner: CliRunner,
    command_id: str,
) -> None:
    """Ensure an unsupported --format fails fast, without a backend."""
    result = runner.invoke(
        app,
        [*COMMAND_ARGV[command_id], "--format", "csv"],
    )

    assert result.exit_code == ExitCode.ERROR
    assert "csv" in result.stderr.lower()
    assert "not supported" in result.stderr.lower()


@pytest.mark.parametrize("command_id", sorted(COMMAND_ARGV))
@pytest.mark.parametrize("output_format", ["json", "jsonl"])
def test_machine_readable_success_has_no_connection_message(
    runner: CliRunner,
    configure_qbit_backend,
    command_id: str,
    output_format: str,
) -> None:
    """Ensure a successful machine-readable run prints no connection banner."""
    configure_qbit_backend(client=_make_client())

    result = runner.invoke(
        app,
        [*COMMAND_ARGV[command_id], "--format", output_format],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "Connected to qBittorrent" not in result.stdout
    assert "Connected to qBittorrent" not in result.stderr


@pytest.mark.parametrize("command_id", sorted(COMMAND_ARGV))
def test_table_success_also_has_no_connection_message(
    runner: CliRunner,
    configure_qbit_backend,
    command_id: str,
) -> None:
    """Ensure the connection banner was removed from table output too."""
    configure_qbit_backend(client=_make_client())

    result = runner.invoke(app, COMMAND_ARGV[command_id])

    assert result.exit_code == ExitCode.SUCCESS
    assert "Connected to qBittorrent" not in result.stdout
    assert "Connected to qBittorrent" not in result.stderr


@pytest.mark.parametrize("command_id", sorted(COMMAND_ARGV))
def test_command_always_creates_client_quietly(
    runner: CliRunner,
    configure_qbit_backend,
    command_id: str,
) -> None:
    """Ensure every read-only command calls _create_qbit_client(quiet=True).

    Asserted at the call-path level, not just on rendered output: under
    CliRunner, `err_console.is_terminal` is already False, so
    `spinner()` no-ops regardless of `quiet` — output-only assertions
    cannot catch a regression here.
    """
    calls = configure_qbit_backend(client=_make_client())

    result = runner.invoke(app, COMMAND_ARGV[command_id])

    assert result.exit_code == ExitCode.SUCCESS
    assert calls == [{"quiet": True}]


@pytest.mark.parametrize("command_id", sorted(COMMAND_ARGV))
def test_command_error_remains_visible_on_stderr(
    runner: CliRunner,
    configure_qbit_backend,
    command_id: str,
) -> None:
    """Ensure genuine errors are never silenced by the machine-readable
    or table silence contract."""
    configure_qbit_backend(config_error=ConfigError("boom"))

    result = runner.invoke(
        app,
        [*COMMAND_ARGV[command_id], "--format", "json"],
    )

    assert result.exit_code != ExitCode.SUCCESS
    assert result.stderr.strip() != ""


def test_backup_diff_help_exposes_format(runner: CliRunner) -> None:
    """Ensure `backup diff --help` exposes --format, not --output."""
    result = runner.invoke(app, ["backup", "diff", "--help"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "--format" in result.stdout
    assert "--output" not in result.stdout


def test_backup_diff_json_is_valid_and_silent(
    runner: CliRunner,
    tmp_path,
) -> None:
    """Ensure `backup diff --format json` is valid and connection-silent."""
    export = {
        "torrents": [
            {
                "hash": TORRENT_HASH,
                "name": "Debian ISO",
                "normalized_trackers": [],
            }
        ],
        "tracker_usage": {},
    }
    baseline = tmp_path / "baseline.json"
    target = tmp_path / "target.json"
    baseline.write_text(json.dumps(export))
    target.write_text(json.dumps(export))

    result = runner.invoke(
        app,
        ["backup", "diff", str(baseline), str(target), "--format", "json"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    json.loads(result.stdout)
    assert "Connected to qBittorrent" not in result.stdout


def test_docs_commands_reference_has_no_leftover_output_examples() -> None:
    """Ensure no documented command example still uses the removed --output."""
    lines = COMMANDS_DOC.read_text(encoding="utf-8").splitlines()

    offending_lines = [
        line for line in lines if "qbit-ops" in line and "--output" in line
    ]

    assert offending_lines == []


@pytest.mark.parametrize("command_id", sorted(FORMAT_SUPPORT))
def test_docs_format_matrix_lists_every_command(command_id: str) -> None:
    """Ensure docs/COMMANDS.md's support matrix covers every command in
    `FORMAT_SUPPORT`, so the implementation and the documented matrix
    cannot silently diverge."""
    matrix_text = COMMANDS_DOC.read_text(encoding="utf-8")
    matrix_section = matrix_text.split("## Format Support Matrix", 1)[1]
    matrix_section = matrix_section.split("## Machine-Readable", 1)[0]

    command_name = command_id.replace("_", " ", 1)
    assert f"`{command_name}`" in matrix_section
