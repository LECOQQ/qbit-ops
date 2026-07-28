"""Test `trackers status` at the CLI level.

Covers filters (reusing the shared torrent-filter vocabulary), output
formats, exit codes, the `trackers health` removal/migration, generated
help, and secret-freedom. `tests/test_tracker_status.py` covers the same
semantics at the domain level; this file exercises them through the real
CLI wiring.
"""

import csv
import io
import json

from typer.testing import CliRunner

from qbit_ops.cli.app import app
from qbit_ops.cli.exit_codes import TrackerStatusExitCode
from tests.support import FakeQbitClient, make_torrent

TORRENT_A = "a" * 40
TORRENT_B = "b" * 40
PASSKEY_TRACKER_URL = "https://tracker.example/announce/SUPER-SECRET-PASSKEY"


def _client_with_mixed_health() -> FakeQbitClient:
    return FakeQbitClient(
        torrents=[
            make_torrent(
                hash=TORRENT_A, name="A", category="movies", state="uploading"
            ),
            make_torrent(
                hash=TORRENT_B, name="B", category="series", state="stalledUP"
            ),
        ],
        trackers_by_hash={
            TORRENT_A: [
                {"url": "https://tracker.example/announce", "status": 2}
            ],
            TORRENT_B: [{"url": "https://other.example/announce", "status": 4}],
        },
    )


# --- Filters -----------------------------------------------------------------


def test_status_unfiltered_reports_every_tracker(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure an unfiltered run reports every tracker identity."""
    configure_qbit_backend(client=_client_with_mixed_health())

    result = runner.invoke(app, ["trackers", "status", "--format", "json"])

    payload = json.loads(result.stdout)
    identities = {t["identity"] for t in payload["trackers"]}
    assert identities == {"tracker.example", "other.example"}


def test_status_category_filter_narrows_the_scan(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure `--category` restricts which torrents get scanned."""
    client = _client_with_mixed_health()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["trackers", "status", "--category", "movies", "--format", "json"]
    )

    payload = json.loads(result.stdout)
    assert [t["identity"] for t in payload["trackers"]] == ["tracker.example"]
    assert client.torrents_trackers_calls == 1


def test_status_state_filter(runner: CliRunner, configure_qbit_backend) -> None:
    """Ensure `--state` restricts the selection."""
    configure_qbit_backend(client=_client_with_mixed_health())

    result = runner.invoke(
        app, ["trackers", "status", "--state", "stalled", "--format", "json"]
    )

    payload = json.loads(result.stdout)
    assert [t["identity"] for t in payload["trackers"]] == ["other.example"]


def test_status_combined_filters_use_and(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure combining --category and --state applies both (AND)."""
    configure_qbit_backend(client=_client_with_mixed_health())

    result = runner.invoke(
        app,
        [
            "trackers",
            "status",
            "--category",
            "movies",
            "--state",
            "stalled",
            "--format",
            "json",
        ],
    )

    payload = json.loads(result.stdout)
    assert payload["matched_torrents"] == 0
    assert payload["trackers"] == []


def test_status_tracker_filter_normalizes_a_full_url(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure `--tracker` accepts a full URL and normalizes it to host."""
    configure_qbit_backend(client=_client_with_mixed_health())

    result = runner.invoke(
        app,
        [
            "trackers",
            "status",
            "--tracker",
            "https://tracker.example/announce/PASSKEY",
            "--format",
            "json",
        ],
    )

    payload = json.loads(result.stdout)
    assert [t["identity"] for t in payload["trackers"]] == ["tracker.example"]


def test_status_rejects_completed_and_incomplete_together(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure a locally-provable contradiction fails before any API call."""
    client = _client_with_mixed_health()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["trackers", "status", "--completed", "--incomplete"]
    )

    assert result.exit_code == TrackerStatusExitCode.INVALID_USAGE
    assert client.torrents_info_calls == 0


# --- Output formats -----------------------------------------------------------


def test_status_table_output(runner: CliRunner, configure_qbit_backend) -> None:
    """Ensure table output renders the documented columns and both trackers.

    Nine columns wrap the `Tracker` identity across lines at CliRunner's
    narrow default width, interleaving other cells' text between the
    wrapped fragments -- a plain substring check on the raw table would
    be unreliable, so this checks for the (unambiguous, non-wrapping)
    per-tracker health words instead; `test_status_json_...` already
    pins down the exact identity strings.
    """
    configure_qbit_backend(client=_client_with_mixed_health())

    result = runner.invoke(app, ["trackers", "status"])

    assert "Health" in result.stdout
    assert "healthy" in result.stdout
    assert "critical" in result.stdout


def test_status_json_is_valid_and_matches_documented_schema(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure --format json is valid and has every documented field."""
    configure_qbit_backend(client=_client_with_mixed_health())

    result = runner.invoke(app, ["trackers", "status", "--format", "json"])

    payload = json.loads(result.stdout)
    assert set(payload) == {
        "schema_version",
        "generated_at",
        "filters",
        "scanned_torrents",
        "matched_torrents",
        "collection_errors",
        "overall_health",
        "trackers",
    }
    for tracker in payload["trackers"]:
        assert set(tracker) == {
            "identity",
            "health",
            "torrent_count",
            "endpoint_count",
            "healthy_count",
            "warning_count",
            "critical_count",
            "disabled_count",
            "unknown_count",
            "representative_message",
        }


def test_status_jsonl_is_one_line_with_the_same_schema_as_json(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure jsonl is exactly one line, identical schema to json."""
    configure_qbit_backend(client=_client_with_mixed_health())

    json_result = runner.invoke(app, ["trackers", "status", "--format", "json"])
    jsonl_result = runner.invoke(
        app, ["trackers", "status", "--format", "jsonl"]
    )

    lines = jsonl_result.stdout.splitlines()
    assert len(lines) == 1

    json_payload = json.loads(json_result.stdout)
    jsonl_payload = json.loads(lines[0])
    # Each invocation collects its own report, so `generated_at` legitimately
    # differs between the two separate runs -- compare everything else.
    del json_payload["generated_at"]
    del jsonl_payload["generated_at"]
    assert json_payload == jsonl_payload


def test_status_csv_has_the_documented_columns(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure --format csv has the exact documented column set, no message."""
    configure_qbit_backend(client=_client_with_mixed_health())

    result = runner.invoke(app, ["trackers", "status", "--format", "csv"])

    rows = list(csv.reader(io.StringIO(result.stdout)))
    assert rows[0] == [
        "tracker",
        "health",
        "torrent_count",
        "endpoint_count",
        "healthy_count",
        "warning_count",
        "critical_count",
        "disabled_count",
        "unknown_count",
    ]
    assert len(rows) == 3  # header + two trackers


def test_status_machine_readable_success_is_silent_on_stderr(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure a successful json/jsonl/csv run prints nothing on stderr."""
    configure_qbit_backend(client=_client_with_mixed_health())

    for fmt in ("json", "jsonl", "csv"):
        result = runner.invoke(app, ["trackers", "status", "--format", fmt])
        assert result.stderr == ""


# --- Exit codes ---------------------------------------------------------------


def test_status_exit_code_healthy(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure an all-healthy report exits 0."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, name="A")],
        trackers_by_hash={
            TORRENT_A: [
                {"url": "https://tracker.example/announce", "status": 2}
            ]
        },
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["trackers", "status"])

    assert result.exit_code == TrackerStatusExitCode.HEALTHY


def test_status_exit_code_warning(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure a mixed healthy/failing report exits 1."""
    client = FakeQbitClient(
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
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["trackers", "status"])

    assert result.exit_code == TrackerStatusExitCode.WARNING


def test_status_exit_code_critical(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure an all-failing tracker exits 2."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, name="A")],
        trackers_by_hash={
            TORRENT_A: [
                {"url": "https://tracker.example/announce", "status": 4}
            ]
        },
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["trackers", "status"])

    assert result.exit_code == TrackerStatusExitCode.CRITICAL


def test_status_exit_code_unavailable(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure total tracker-collection failure exits 3."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, name="A")],
        tracker_error_hashes={TORRENT_A},
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["trackers", "status"])

    assert result.exit_code == TrackerStatusExitCode.UNAVAILABLE


def test_status_empty_selection_exits_healthy_not_no_match(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure a filter matching nothing exits 0 (healthy), not 2."""
    configure_qbit_backend(client=FakeQbitClient(torrents=[]))

    result = runner.invoke(
        app, ["trackers", "status", "--category", "nonexistent"]
    )

    assert result.exit_code == TrackerStatusExitCode.HEALTHY


# --- `trackers health` removal / migration ------------------------------------


def test_trackers_health_no_longer_exists(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure `trackers health` was removed, not kept alongside `status`."""
    configure_qbit_backend(client=_client_with_mixed_health())

    result = runner.invoke(app, ["trackers", "health"])

    assert result.exit_code != 0
    assert "no such command" in result.output.lower()


def test_trackers_status_appears_in_generated_help(runner: CliRunner) -> None:
    """Ensure generated --help lists `status` as a subcommand, not `health`."""
    result = runner.invoke(app, ["trackers", "--help"])

    command_names = {
        line.strip().split()[1]
        for line in result.output.splitlines()
        if line.strip().startswith("│") and len(line.strip().split()) > 1
    }
    assert "status" in command_names
    assert "health" not in command_names


# --- Secret-freedom -----------------------------------------------------------


def test_status_never_renders_a_full_tracker_url_or_passkey(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure no output format ever renders a full announce URL or passkey."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, name="A")],
        trackers_by_hash={
            TORRENT_A: [
                {
                    "url": PASSKEY_TRACKER_URL,
                    "status": 4,
                    "msg": f"could not reach {PASSKEY_TRACKER_URL}",
                }
            ]
        },
    )

    for fmt in ("table", "json", "jsonl", "csv"):
        configure_qbit_backend(client=client)
        result = runner.invoke(app, ["trackers", "status", "--format", fmt])
        assert "SUPER-SECRET-PASSKEY" not in result.stdout
        assert "SUPER-SECRET-PASSKEY" not in result.stderr
