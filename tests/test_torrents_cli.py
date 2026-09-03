"""Test hash-centric torrent selection at the CLI level."""

import json

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


def test_pause_with_ambiguous_hash_keeps_a_bracketed_candidate_name_literal(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """A candidate name in the ambiguous-hash error list is the same
    third-party text as any other rendered torrent name."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A_HASH, name="A [bold red]FAKE[/] B"),
            make_torrent(hash=TORRENT_B_HASH, name="Debian live image"),
        ],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "pause", "--hash", "abc", "--no-dry-run"],
    )

    assert result.exit_code == ExitCode.ERROR
    # `typer.Exit` surfaces here as `SystemExit`: the controlled exit
    # this command takes on ambiguity. Anything else means a second,
    # unhandled exception replaced it.
    assert isinstance(result.exception, SystemExit)
    assert "A [bold red]FAKE[/] B" in result.stderr


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


def test_inspect_rejects_the_removed_name_option(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """`--name` is gone (S8, `.agents/features/search/SPEC.md`'s breaking-change
    table): the fuzzy search now lives at `torrents search`, a
    read-only, ranked cul-de-sac, never a selector for `inspect`."""
    configure_qbit_backend(client=FakeQbitClient(torrents=[]))

    result = runner.invoke(app, ["torrents", "inspect", "--name", "debian"])

    assert result.exit_code != ExitCode.SUCCESS
    assert "no such option" in result.stdout.lower() + result.stderr.lower()


def test_inspect_rejects_the_removed_limit_option(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    configure_qbit_backend(client=FakeQbitClient(torrents=[]))

    result = runner.invoke(
        app, ["torrents", "inspect", "--hash", "abc123", "--limit", "5"]
    )

    assert result.exit_code != ExitCode.SUCCESS
    assert "no such option" in result.stdout.lower() + result.stderr.lower()


def test_inspect_separates_peer_discovery_from_trackers(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """DHT/PeX/LSD are counted nowhere and listed nowhere among the
    trackers -- but an operator still reads their state, on their own
    line."""
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[make_torrent(hash=UNIQUE_HASH, name="Debian ISO")],
            trackers_by_hash={
                UNIQUE_HASH: [
                    {"url": "** [DHT] **", "status": 2},
                    {"url": "** [PeX] **", "status": 0},
                    {"url": "https://tracker.example/announce", "status": 2},
                ]
            },
        ),
    )

    result = runner.invoke(
        app,
        ["torrents", "inspect", "--hash", "zz0011"],
        env={"COLUMNS": "200"},
    )
    collapsed = _collapse_whitespace(result.stdout)

    trackers_section, _, peer_discovery_line = result.stdout.partition(
        "Peer discovery"
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "Peerdiscovery:DHThealthy,PeXdisabled" in collapsed
    # The pseudo-trackers appear only on that line: not in the table,
    # and not in the active-tracker count above it.
    assert "DHT" not in trackers_section
    assert "PeX" not in trackers_section
    assert "tracker.example" in trackers_section
    assert "tracker.example" not in peer_discovery_line


def test_list_limit_caps_rows_without_changing_what_matched(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """`--limit` bounds the output, never the selection. `matched` keeps
    answering "how many are there", which is what a caller needs to know
    it is looking at a slice -- the same contract `torrents search` has.
    """
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[
                make_torrent(hash=f"{index:040x}", name=f"T{index}")
                for index in range(1, 6)
            ]
        )
    )

    result = runner.invoke(
        app, ["torrents", "list", "--limit", "2", "--format", "json"]
    )

    assert result.exit_code == ExitCode.SUCCESS
    payload = json.loads(result.stdout)
    assert payload["summary"] == {
        "scanned": 5,
        "matched": 5,
        "returned": 2,
        "truncated": True,
    }
    assert len(payload["torrents"]) == 2


def test_list_without_limit_returns_everything(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """The default is `0`, so existing callers keep the whole list."""
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[
                make_torrent(hash=f"{index:040x}", name=f"T{index}")
                for index in range(1, 4)
            ]
        )
    )

    result = runner.invoke(app, ["torrents", "list", "--format", "json"])

    payload = json.loads(result.stdout)
    assert payload["summary"]["truncated"] is False
    assert payload["summary"]["returned"] == 3
    assert len(payload["torrents"]) == 3


def test_list_sort_applies_before_limit(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """The three largest of the whole selection, not three arbitrary
    rows truncated after the fact."""
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[
                make_torrent(hash=f"{index:040x}", name=f"T{index}", size=index)
                for index in range(1, 6)
            ]
        )
    )

    result = runner.invoke(
        app,
        ["torrents", "list", "--sort", "size", "--desc", "--limit", "3"],
        env={"COLUMNS": "200"},
    )

    assert result.exit_code == ExitCode.SUCCESS
    order = [
        name
        for name in ("T5", "T4", "T3", "T2", "T1")
        if name in _collapse_whitespace(result.stdout)
    ]
    assert order == ["T5", "T4", "T3"]


def test_list_without_sort_keeps_the_pre_existing_order(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """No `--sort` means row order is unchanged from before this
    option existed -- covered at the domain level by
    `qbit_core.shared.selection`'s own tests; this only proves the CLI
    does not impose an order of its own on top."""
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[
                make_torrent(hash="b" * 40, name="Bravo", size=900),
                make_torrent(hash="a" * 40, name="Alpha", size=100),
            ]
        )
    )

    result = runner.invoke(app, ["torrents", "list", "--format", "json"])

    payload = json.loads(result.stdout)
    assert [t["name"] for t in payload["torrents"]] == ["Alpha", "Bravo"]


def test_list_sort_matched_still_counts_every_selected_torrent(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """`--sort` is table-only (see
    `test_list_sort_rejects_a_machine_format`), so `summary.matched` is
    read from the table footer here, not `json`."""
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[
                make_torrent(hash=f"{index:040x}", name=f"T{index}", size=index)
                for index in range(1, 6)
            ]
        )
    )

    result = runner.invoke(
        app,
        ["torrents", "list", "--sort", "size", "--limit", "2"],
        env={"COLUMNS": "200"},
    )

    assert result.exit_code == ExitCode.SUCCESS
    collapsed = _collapse_whitespace(result.stdout)
    assert "matched5" in collapsed
    assert "returned2" in collapsed


def test_list_sort_rejects_a_machine_format(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(
        client=FakeQbitClient(torrents=[make_torrent(hash=UNIQUE_HASH)])
    )

    result = runner.invoke(
        app, ["torrents", "list", "--sort", "size", "--format", "json"]
    )

    assert result.exit_code == ExitCode.ERROR
    assert "--format json" in result.stderr


def test_list_sort_rejects_an_unknown_field_and_lists_the_ten_valid_ones(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(
        client=FakeQbitClient(torrents=[make_torrent(hash=UNIQUE_HASH)])
    )

    result = runner.invoke(app, ["torrents", "list", "--sort", "taille"])

    assert result.exit_code == ExitCode.ERROR
    for field in (
        "name",
        "state",
        "progress",
        "down",
        "up",
        "ratio",
        "category",
        "size",
        "added_on",
        "seeding_time",
    ):
        assert field in result.stderr


def test_list_desc_without_sort_is_rejected(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(
        client=FakeQbitClient(torrents=[make_torrent(hash=UNIQUE_HASH)])
    )

    result = runner.invoke(app, ["torrents", "list", "--desc"])

    assert result.exit_code == ExitCode.ERROR


def test_list_repeated_runs_over_an_unchanged_library_sort_identically(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Acceptance criterion: ties resolve the same way every time.
    `--sort` is table-only (see `test_list_sort_rejects_a_machine_format`),
    so the two renders are compared as table output.
    """
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="b" * 40, name="Same", size=10),
            make_torrent(hash="a" * 40, name="Same", size=10),
            make_torrent(hash="c" * 40, name="Other", size=10),
        ]
    )
    configure_qbit_backend(client=client)
    first = runner.invoke(
        app, ["torrents", "list", "--sort", "size"], env={"COLUMNS": "200"}
    )

    configure_qbit_backend(client=client)
    second = runner.invoke(
        app, ["torrents", "list", "--sort", "size"], env={"COLUMNS": "200"}
    )

    assert first.exit_code == ExitCode.SUCCESS
    assert first.stdout == second.stdout


def test_list_table_output_keeps_a_bracketed_name_literal(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """A torrent name is third-party text (whoever built the `.torrent`
    controls it), and release names commonly carry `[amd64]`-style
    brackets. Rich reads square brackets as markup by default, so an
    unescaped name is silently swallowed instead of shown."""
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[make_torrent(hash=UNIQUE_HASH, name="Ubuntu [amd64] iso")]
        )
    )

    result = runner.invoke(app, ["torrents", "list"], env={"COLUMNS": "200"})

    assert result.exit_code == ExitCode.SUCCESS
    assert result.exception is None
    assert "Ubuntu [amd64] iso" in result.stdout


def test_list_table_output_does_not_crash_on_unbalanced_markup(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """A closing tag with no opener (`[/]`) is a `rich.errors.MarkupError`
    when interpreted as markup, not a decoding failure the command can
    otherwise anticipate."""
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[make_torrent(hash=UNIQUE_HASH, name="Movie [/] x264")]
        )
    )

    result = runner.invoke(app, ["torrents", "list"], env={"COLUMNS": "200"})

    assert result.exit_code == ExitCode.SUCCESS
    assert result.exception is None
    assert "Movie [/] x264" in result.stdout


def test_bulk_pause_emits_a_machine_readable_preview(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """A dry-run preview is the moment an operator decides whether to
    apply. Before this, that decision was only available as a Rich table
    whose width depends on the terminal -- so a script could read how
    many matched, never which ones.
    """
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[
                make_torrent(hash=TORRENT_A_HASH, name="A", state="uploading"),
                make_torrent(hash=UNIQUE_HASH, name="B", state="uploading"),
            ]
        )
    )

    result = runner.invoke(
        app, ["torrents", "pause", "--all", "--format", "json"]
    )

    payload = json.loads(result.stdout)
    assert payload["action"] == "pause"
    assert payload["dry_run"] is True
    assert payload["applied"] is False
    assert payload["matched"] == 2
    assert sorted(payload["hashes"]) == sorted([TORRENT_A_HASH, UNIQUE_HASH])


def test_bulk_action_rejects_streaming_formats(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """A mutation produces one result, not a stream of rows."""
    configure_qbit_backend(client=FakeQbitClient(torrents=[]))

    result = runner.invoke(
        app, ["torrents", "pause", "--all", "--format", "csv"]
    )

    assert result.exit_code != ExitCode.SUCCESS
    assert "not supported" in result.stderr.lower()


def test_bulk_json_reports_the_status_it_actually_reached(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """`--format json` silences the Rich summary, and silencing it also
    skipped the call that computes the status -- so a mutation that had
    been applied still reported `preview`. A machine payload that
    misstates what happened is worse than no payload.
    """
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[
                make_torrent(hash=TORRENT_A_HASH, name="A", state="uploading")
            ]
        )
    )
    applied = json.loads(
        runner.invoke(
            app,
            ["torrents", "pause", "--all", "--no-dry-run", "--format", "json"],
        ).stdout
    )
    assert applied["status"] == "applied"
    assert applied["applied"] is True

    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[
                make_torrent(hash=TORRENT_A_HASH, name="A", state="pausedUP")
            ]
        )
    )
    unchanged = json.loads(
        runner.invoke(
            app,
            ["torrents", "pause", "--all", "--no-dry-run", "--format", "json"],
        ).stdout
    )
    assert unchanged["status"] == "no_changes"
    assert unchanged["applied"] is False
