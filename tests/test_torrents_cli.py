"""Test hash-centric torrent selection at the CLI level."""

from typer.testing import CliRunner

from qbit_ops.cli.app import app
from qbit_ops.cli.exit_codes import ExitCode
from tests.support import FakeQbitClient, make_torrent

TORRENT_A_HASH = "abc123def456000000000000000000000000000a"
TORRENT_B_HASH = "abc987fed654000000000000000000000000000b"
UNIQUE_HASH = "zz00112233ff000000000000000000000000000c"


def _collapse_whitespace(text: str) -> str:
    """Undo Rich's terminal-width line wrapping for substring checks."""
    return "".join(text.split())


def test_inspect_resolves_a_unique_hash_prefix(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[make_torrent(hash=UNIQUE_HASH, name="Debian ISO")],
        ),
    )

    result = runner.invoke(
        app,
        ["torrents", "inspect", "--hash", "zz0011"],
        env={"COLUMNS": "200"},
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert UNIQUE_HASH in _collapse_whitespace(result.stdout)
    assert "Debian ISO" in result.stdout


def test_pause_with_unique_hash_prefix_selects_one_torrent(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A_HASH, name="A", state="uploading"),
            make_torrent(hash=UNIQUE_HASH, name="B", state="uploading"),
        ],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "pause", "--hash", "zz0011", "--no-dry-run"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.paused_hashes == [[UNIQUE_HASH]]


def test_pause_with_ambiguous_hash_performs_no_mutation(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A_HASH, name="Debian ISO"),
            make_torrent(hash=TORRENT_B_HASH, name="Debian live image"),
        ],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "pause", "--hash", "abc", "--no-dry-run"],
    )

    assert result.exit_code == ExitCode.ERROR
    assert client.paused_hashes == []
    assert "matches" in result.stderr
    assert "Debian ISO" in result.stderr
    assert "Debian live image" in result.stderr
    assert "longer prefix" in result.stderr


def test_pause_with_unknown_hash_performs_no_mutation(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A_HASH, name="A")],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "pause", "--hash", "doesnotexist", "--no-dry-run"],
    )

    assert result.exit_code == ExitCode.NO_MATCH
    assert client.paused_hashes == []


def test_pause_help_no_longer_mentions_name(
    runner: CliRunner,
) -> None:
    result = runner.invoke(app, ["torrents", "pause", "--help"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "--name" not in result.stdout
    assert "--hash" in result.stdout


def test_pause_rejects_the_removed_name_option(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    configure_qbit_backend(client=FakeQbitClient(torrents=[]))

    result = runner.invoke(
        app,
        ["torrents", "pause", "--name", "debian", "--no-dry-run"],
    )

    assert result.exit_code != ExitCode.SUCCESS
    assert "no such option" in result.stdout.lower() + result.stderr.lower()


def test_pause_hash_is_mutually_exclusive_with_category(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A_HASH, category="sonarr")],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "pause",
            "--hash",
            TORRENT_A_HASH,
            "--category",
            "sonarr",
            "--no-dry-run",
        ],
    )

    assert result.exit_code == ExitCode.ERROR
    assert client.paused_hashes == []


def test_pause_hash_dry_run_by_default_performs_no_mutation(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=UNIQUE_HASH, state="uploading")],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "pause", "--hash", "zz0011"],
        env={"COLUMNS": "200"},
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.paused_hashes == []
    assert UNIQUE_HASH in _collapse_whitespace(result.stdout)


def test_pause_hash_no_dry_run_mutates_only_the_resolved_target(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A_HASH, state="uploading"),
            make_torrent(hash=UNIQUE_HASH, state="uploading"),
        ],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "pause", "--hash", "zz0011", "--no-dry-run"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.paused_hashes == [[UNIQUE_HASH]]


def test_pause_by_category_still_works(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(
                hash=TORRENT_A_HASH,
                category="sonarr",
                state="uploading",
            ),
            make_torrent(
                hash=TORRENT_B_HASH,
                category="radarr",
                state="uploading",
            ),
        ],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "pause", "--category", "sonarr", "--no-dry-run"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.paused_hashes == [[TORRENT_A_HASH]]


def test_resume_by_all_still_works(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A_HASH, state="stoppedUP")],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "resume", "--all", "--no-dry-run"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.started_hashes == [[TORRENT_A_HASH]]


def test_start_completed_still_works(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A_HASH, state="stoppedUP", progress=1.0),
        ],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "start", "--completed", "--no-dry-run"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.started_hashes == [[TORRENT_A_HASH]]


def test_reannounce_by_tracker_still_works(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A_HASH, state="uploading")],
        trackers_by_hash={
            TORRENT_A_HASH: [
                {"url": "https://tracker.example/announce", "status": "2"},
            ],
        },
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "reannounce",
            "--tracker",
            "https://tracker.example/announce",
            "--no-dry-run",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.reannounced_hashes == [[TORRENT_A_HASH]]


def test_reannounce_help_exposes_hash(runner: CliRunner) -> None:
    result = runner.invoke(app, ["torrents", "reannounce", "--help"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "--hash" in result.stdout
    assert "--name" not in result.stdout


def test_reannounce_with_unique_hash_prefix_selects_one_torrent(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A_HASH, name="A", state="uploading"),
            make_torrent(hash=UNIQUE_HASH, name="B", state="uploading"),
        ],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "reannounce", "--hash", "zz0011", "--no-dry-run"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.reannounced_hashes == [[UNIQUE_HASH]]


def test_reannounce_with_ambiguous_hash_performs_no_mutation(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A_HASH, name="Debian ISO"),
            make_torrent(hash=TORRENT_B_HASH, name="Debian live image"),
        ],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "reannounce", "--hash", "abc", "--no-dry-run"],
    )

    assert result.exit_code == ExitCode.ERROR
    assert client.reannounced_hashes == []


def test_reannounce_with_unknown_hash_performs_no_mutation(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A_HASH, name="A")],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "reannounce", "--hash", "doesnotexist", "--no-dry-run"],
    )

    assert result.exit_code == ExitCode.NO_MATCH
    assert client.reannounced_hashes == []


def test_inspect_name_search_remains_functional(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[make_torrent(hash=UNIQUE_HASH, name="Debian ISO")],
        ),
    )

    result = runner.invoke(
        app,
        ["torrents", "inspect", "--name", "debian"],
        env={"COLUMNS": "200"},
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "Debian ISO" in result.stdout
    assert UNIQUE_HASH in _collapse_whitespace(result.stdout)
