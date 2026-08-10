"""Test hash-centric torrent selection at the CLI level."""

import pytest
from typer.testing import CliRunner

from qbit_core.features.torrents import plan_bulk_torrent_action
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


def test_pause_help_documents_only_deterministic_name_targeting(
    runner: CliRunner,
) -> None:
    """The fuzzy `--name` selector stays gone from mutations (see
    `test_pause_rejects_the_removed_name_option`, the behavioural
    guarantee). `--name-contains` and `--name-regex` are a different
    thing: exact, deterministic predicates that cannot silently widen a
    selection, so they are legitimate mutation targeting.
    """
    result = runner.invoke(app, ["torrents", "pause", "--help"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "--hash" in result.stdout
    assert "--name-contains" in result.stdout
    assert "--name-regex" in result.stdout


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


# --- delete (HIGH risk, destructive) ----------------------------------------


def _make_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "qbit_ops.cli.rendering.is_interactive_terminal", lambda: True
    )


def test_delete_defaults_to_dry_run_and_performs_no_mutation(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A_HASH, name="A")],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["torrents", "delete", "--all"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "PREVIEW" in result.stdout
    assert client.deleted_hashes == []


def test_delete_refuses_non_interactive_real_run_without_yes(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A_HASH, name="A")],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["torrents", "delete", "--all", "--no-dry-run"])

    assert result.exit_code == ExitCode.ERROR
    assert "--yes" in result.stderr
    assert client.deleted_hashes == []


def test_delete_with_yes_applies_without_prompting(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A_HASH, name="A")],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "delete", "--all", "--no-dry-run", "--yes"]
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "APPLIED" in result.stdout
    assert client.deleted_hashes == [([TORRENT_A_HASH], False)]


def test_delete_keeps_data_by_default(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A_HASH, name="A")],
    )
    configure_qbit_backend(client=client)

    runner.invoke(app, ["torrents", "delete", "--all", "--no-dry-run", "--yes"])

    assert client.deleted_hashes[0][1] is False


def test_delete_with_data_flag_deletes_files(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A_HASH, name="A")],
    )
    configure_qbit_backend(client=client)

    runner.invoke(
        app,
        [
            "torrents",
            "delete",
            "--all",
            "--with-data",
            "--no-dry-run",
            "--yes",
        ],
    )

    assert client.deleted_hashes == [([TORRENT_A_HASH], True)]


def test_delete_interactive_confirmation_can_be_declined(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_interactive(monkeypatch)
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A_HASH, name="A")],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "delete", "--all", "--no-dry-run"],
        input="n\n",
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "Operation cancelled." in result.stdout
    assert client.deleted_hashes == []


def test_delete_interactive_confirmation_message_mentions_data_kept(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_interactive(monkeypatch)
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A_HASH, name="A")],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "delete", "--all", "--no-dry-run"],
        input="n\n",
    )

    assert "kept on disk" in result.stderr


def test_delete_interactive_confirmation_message_warns_with_data(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_interactive(monkeypatch)
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A_HASH, name="A")],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "delete", "--all", "--with-data", "--no-dry-run"],
        input="n\n",
    )

    assert "downloaded data" in result.stderr
    assert "kept on disk" not in result.stderr


def test_delete_with_unique_hash_prefix_selects_one_torrent(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A_HASH, name="A"),
            make_torrent(hash=UNIQUE_HASH, name="B"),
        ],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "delete", "--hash", "zz0011", "--no-dry-run", "--yes"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.deleted_hashes == [([UNIQUE_HASH], False)]


def test_delete_with_unknown_hash_performs_no_mutation(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A_HASH, name="A")],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "delete",
            "--hash",
            "doesnotexist",
            "--no-dry-run",
            "--yes",
        ],
    )

    assert result.exit_code == ExitCode.NO_MATCH
    assert client.deleted_hashes == []


def test_delete_never_skips_active_torrents() -> None:
    """Sanity check at the CLI boundary: unlike pause, an active torrent
    is never treated as already satisfied by delete."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A_HASH, name="A", state="uploading")
        ],
    )

    plan = plan_bulk_torrent_action(
        client=client, action="delete", select_all=True
    )

    assert len(plan.changes) == 1
    assert plan.skipped == ()


def test_delete_does_not_expose_yes_low_risk_style_help() -> None:
    """`--yes` must be present on `delete` (HIGH risk), unlike pause/
    resume/start/reannounce which never expose it."""
    result = CliRunner().invoke(app, ["torrents", "delete", "--help"])

    assert "--yes" in result.stdout
    assert "--with-data" in result.stdout


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
