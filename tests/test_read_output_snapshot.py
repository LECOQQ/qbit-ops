"""Characterize machine-readable output contracts (T2).

Complements `tests/test_output_format_cli.py` (which already proves
every read command's `--format json`/`jsonl`/`csv` is valid and
ANSI-free) with the deeper characterization
`docs/audits/2026-07-package-refactor-plan.md` §5 (T2) asks for before
any package move: top-level schema/keys, representative nested values,
stdout/stderr separation, and one non-success/structured-error path --
all against `tests.support.FakeQbitClient`, never a real qBittorrent
instance.

Timestamps (`generated_at`) are read but never asserted on a fixed
value: `FakeQbitClient` does not inject a deterministic clock, so
freezing that field here would be characterizing an artifact of "when
the test ran," not a real contract.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from app.main import ExitCode, StatusExitCode, app
from tests.support import FakeQbitClient, make_config, make_torrent

runner = CliRunner()

TORRENT_HASH = "abc123def456000000000000000000000000000a"
PASSKEY = "sup3r-secret-passkey-should-never-appear"
TRACKER_URL_WITH_PASSKEY = f"https://tracker.example/announce?passkey={PASSKEY}"


# --- status --format json: schema + nested values ---------------------------


def test_status_json_has_the_documented_top_level_schema(
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_HASH, state="uploading")],
        download_speed=1024,
        upload_speed=2048,
    )
    configure_qbit_backend(
        client=client, config=make_config(host="http://localhost:8080")
    )

    result = runner.invoke(app, ["status", "--format", "json"])

    assert result.exit_code == StatusExitCode.HEALTHY
    payload = json.loads(result.stdout)

    assert set(payload) == {
        "schema_version",
        "generated_at",
        "health",
        "connection",
        "transfers",
        "rates",
        "alerts",
    }
    assert payload["health"] == "healthy"
    assert payload["connection"]["connected"] is True
    assert payload["transfers"]["total"] == 1
    assert payload["transfers"]["seeding"] == 1
    assert payload["rates"]["download_bytes_per_second"] == 1024
    assert payload["rates"]["upload_bytes_per_second"] == 2048
    assert payload["alerts"] == []


def test_status_json_stdout_and_stderr_are_separated(
    configure_qbit_backend,
) -> None:
    """stdout carries only the JSON document; diagnostics stay on stderr."""
    configure_qbit_backend(
        client=FakeQbitClient(torrents=[make_torrent()]),
        config=make_config(host="http://localhost:8080"),
    )

    result = runner.invoke(app, ["status", "--format", "json"])

    assert result.exit_code == StatusExitCode.HEALTHY
    json.loads(result.stdout)  # raises if anything but pure JSON leaked in
    assert "Loading status" not in result.stdout
    # The transient spinner ("Loading status...") writes to stderr, not
    # stdout, whenever it fires -- but CliRunner's non-terminal stderr
    # already makes it a no-op (see `configure_qbit_backend`'s
    # docstring), so stderr is expected to be empty here too.
    assert result.stderr == ""


def test_status_json_has_no_rich_decoration_or_ansi_escapes(
    configure_qbit_backend,
) -> None:
    configure_qbit_backend(
        client=FakeQbitClient(torrents=[make_torrent()]),
        config=make_config(host="http://localhost:8080"),
    )

    result = runner.invoke(app, ["status", "--format", "json"])

    assert "\x1b[" not in result.stdout
    assert "┃" not in result.stdout  # Rich's box-drawing vertical bar


# --- status --format json: structured non-success path ----------------------


def test_status_json_reports_unavailable_health_on_connection_failure(
    configure_qbit_backend,
) -> None:
    """The project's existing structured error path for a read command.

    `status` never raises on a connection failure -- it degrades to a
    valid, schema-conformant snapshot with `health: "unavailable"` and a
    dedicated exit code (see `app.status.EXIT_CODE_BY_HEALTH`), which is
    qbit-ops' contract for "unavailable" rather than a fatal CLI error.
    """
    from app.errors import QbitConnectionError

    configure_qbit_backend(
        client_error=QbitConnectionError("Unable to connect to qBittorrent.")
    )

    result = runner.invoke(app, ["status", "--format", "json"])

    assert result.exit_code == StatusExitCode.UNAVAILABLE
    payload = json.loads(result.stdout)
    assert payload["health"] == "unavailable"
    assert payload["connection"]["connected"] is False


def test_fatal_cli_error_json_path_emits_no_stdout(
    configure_qbit_backend,
) -> None:
    """The other structured failure shape: a fatal error, not a degraded
    snapshot.

    Some commands (e.g. `torrents list`) have no "unavailable" snapshot
    of their own -- a connection failure there is a fatal CLI error:
    non-zero exit, no partial/malformed JSON on stdout. Both shapes are
    legitimate, existing contracts; this locks the second one.
    """
    from app.errors import QbitConnectionError

    configure_qbit_backend(
        client_error=QbitConnectionError("Unable to connect.")
    )

    result = runner.invoke(app, ["torrents", "list", "--format", "json"])

    assert result.exit_code == ExitCode.ERROR
    assert result.stdout.strip() == ""


# --- torrents list --format json: schema + nested values --------------------


def test_torrents_list_json_has_representative_nested_values(
    configure_qbit_backend,
) -> None:
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[
                make_torrent(
                    hash=TORRENT_HASH,
                    name="Debian ISO",
                    category="linux",
                    state="uploading",
                )
            ]
        )
    )

    result = runner.invoke(app, ["torrents", "list", "--format", "json"])

    assert result.exit_code == ExitCode.SUCCESS
    payload = json.loads(result.stdout)
    assert set(payload) == {"filters", "summary", "torrents"}
    assert len(payload["torrents"]) == 1
    torrent = payload["torrents"][0]
    assert torrent["hash"] == TORRENT_HASH
    assert torrent["name"] == "Debian ISO"
    assert torrent["category"] == "linux"


# --- trackers status --format json: no raw tracker secret leakage -----------


def test_trackers_status_json_never_leaks_a_raw_passkey(
    configure_qbit_backend,
) -> None:
    """Ensure a tracker URL's passkey never reaches ordinary JSON output.

    `trackers status` reports tracker *identities*
    (`describe_tracker_url`'s `host[:port]`), never the raw announce
    URL -- see `docs/PHILOSOPHY.md` §15 and
    `tests/test_tracker_security.py`. This test locks that guarantee
    specifically for `--format json`, which T2 requires characterized
    independently of the existing table-rendering tests.
    """
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[make_torrent(hash=TORRENT_HASH)],
            trackers_by_hash={
                TORRENT_HASH: [{"url": TRACKER_URL_WITH_PASSKEY, "status": "2"}]
            },
        )
    )

    result = runner.invoke(app, ["trackers", "status", "--format", "json"])

    payload = json.loads(result.stdout)
    assert "trackers" in payload
    assert PASSKEY not in result.stdout
    assert "tracker.example" in json.dumps(payload)
