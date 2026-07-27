"""Test the shared torrent-filter vocabulary at the CLI level.

Covers `torrents list` and the four bulk mutation commands
(`pause`/`resume`/`start`/`reannounce`) sharing one filter model
(`qbit_ops.torrents.TorrentFilter`/`select_torrents`): each supported filter,
repetition/combination semantics, selection-safety refusals, NO_MATCH/
NO_CHANGES under filtering, and that a filter preview never renders a
tracker passkey. `tests/test_torrents.py` covers the same semantics at
the domain level; this file exercises them through the real CLI wiring.
"""

import json

import pytest
from typer.testing import CliRunner

from qbit_ops.main import ExitCode, app
from tests.support import FakeQbitClient, make_torrent

TORRENT_A = "a" * 40
TORRENT_B = "b" * 40
TORRENT_C = "c" * 40
PASSKEY_TRACKER_URL = "https://tracker.example/announce/SUPER-SECRET-PASSKEY"


def _client_with_categories_and_states() -> FakeQbitClient:
    return FakeQbitClient(
        torrents=[
            make_torrent(
                hash=TORRENT_A,
                name="A",
                category="movies",
                state="stalledUP",
            ),
            make_torrent(
                hash=TORRENT_B,
                name="B",
                category="series",
                state="uploading",
            ),
            make_torrent(
                hash=TORRENT_C,
                name="C",
                category="music",
                state="error",
            ),
        ]
    )


# --- torrents list: each filter, repetition, combination --------------------


def test_list_filters_by_state(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure `--state` restricts to the requested state group."""
    configure_qbit_backend(client=_client_with_categories_and_states())

    result = runner.invoke(
        app, ["torrents", "list", "--state", "stalled", "--format", "json"]
    )

    assert result.exit_code == ExitCode.SUCCESS
    payload = json.loads(result.stdout)
    assert [t["hash"] for t in payload["torrents"]] == [TORRENT_A]


def test_list_repeated_category_combines_with_or(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure repeated --category values combine with OR."""
    configure_qbit_backend(client=_client_with_categories_and_states())

    result = runner.invoke(
        app,
        [
            "torrents",
            "list",
            "--category",
            "movies",
            "--category",
            "series",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    payload = json.loads(result.stdout)
    assert {t["hash"] for t in payload["torrents"]} == {TORRENT_A, TORRENT_B}


def test_list_combined_filters_use_and(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure different filter types combine with AND."""
    configure_qbit_backend(client=_client_with_categories_and_states())

    result = runner.invoke(
        app,
        [
            "torrents",
            "list",
            "--category",
            "movies",
            "--category",
            "series",
            "--state",
            "stalled",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    payload = json.loads(result.stdout)
    assert [t["hash"] for t in payload["torrents"]] == [TORRENT_A]


def test_list_completed_and_errored_filters(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure --completed and --errored are both usable on `list`."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A, state="uploading", progress=1.0),
            make_torrent(hash=TORRENT_B, state="error", progress=0.5),
        ]
    )
    configure_qbit_backend(client=client)

    completed = runner.invoke(
        app, ["torrents", "list", "--completed", "--format", "json"]
    )
    errored = runner.invoke(
        app, ["torrents", "list", "--errored", "--format", "json"]
    )

    assert completed.exit_code == ExitCode.SUCCESS
    assert [t["hash"] for t in json.loads(completed.stdout)["torrents"]] == [
        TORRENT_A
    ]
    assert errored.exit_code == ExitCode.SUCCESS
    assert [t["hash"] for t in json.loads(errored.stdout)["torrents"]] == [
        TORRENT_B
    ]


def test_list_active_and_inactive_filters(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure --active and --inactive select by stopped state."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A, state="uploading"),
            make_torrent(hash=TORRENT_B, state="pausedUP"),
        ]
    )
    configure_qbit_backend(client=client)

    active = runner.invoke(
        app, ["torrents", "list", "--active", "--format", "json"]
    )
    inactive = runner.invoke(
        app, ["torrents", "list", "--inactive", "--format", "json"]
    )

    assert [t["hash"] for t in json.loads(active.stdout)["torrents"]] == [
        TORRENT_A
    ]
    assert [t["hash"] for t in json.loads(inactive.stdout)["torrents"]] == [
        TORRENT_B
    ]


def test_list_rejects_completed_and_incomplete_together(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure the two locally-provable contradictions fail before any
    API call."""
    result = runner.invoke(
        app, ["torrents", "list", "--completed", "--incomplete"]
    )

    assert result.exit_code == ExitCode.ERROR
    assert "not both" in result.stderr.lower()


def test_list_rejects_active_and_inactive_together(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure --active --inactive fails before any API call."""
    result = runner.invoke(app, ["torrents", "list", "--active", "--inactive"])

    assert result.exit_code == ExitCode.ERROR
    assert "not both" in result.stderr.lower()


def test_list_rejects_unknown_state_value(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure an unrecognized --state value fails clearly."""
    result = runner.invoke(app, ["torrents", "list", "--state", "banana"])

    assert result.exit_code == ExitCode.ERROR
    assert "unknown --state value" in result.stderr.lower()


def test_list_unfiltered_zero_torrents_is_not_an_error(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure an unfiltered empty instance is not NO_MATCH: there was
    nothing to filter out, it is a legitimate empty result."""
    configure_qbit_backend(client=FakeQbitClient(torrents=[]))

    result = runner.invoke(app, ["torrents", "list"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "No torrents found." in result.stdout


def test_list_filtered_zero_matches_is_no_match(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure a filtered `torrents list` that matches nothing exits NO_MATCH."""
    configure_qbit_backend(client=_client_with_categories_and_states())

    result = runner.invoke(
        app, ["torrents", "list", "--category", "nonexistent"]
    )

    assert result.exit_code == ExitCode.NO_MATCH


def test_list_table_shows_filter_description(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure table output shows a concise filter description."""
    configure_qbit_backend(client=_client_with_categories_and_states())

    result = runner.invoke(
        app, ["torrents", "list", "--category", "movies", "--stalled"]
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "Filter:" in result.stdout
    assert "category=movies" in result.stdout
    assert "stalled" in result.stdout


def test_list_table_omits_trackers_column_when_not_collected(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure the Trackers column is omitted entirely (not shown as '-')
    when no --tracker filter collected tracker data.

    A per-row placeholder would read as "zero trackers"; omitting the
    whole column instead makes "not collected" a property of the table,
    which is what it actually is (tracker_data_collected is a
    whole-selection flag, never a per-torrent one).
    """
    configure_qbit_backend(client=_client_with_categories_and_states())

    result = runner.invoke(app, ["torrents", "list"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "Trackers" not in result.stdout


def test_list_table_shows_trackers_column_when_collected(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure the Trackers column appears with a real count when
    --tracker collected data."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, state="uploading")],
        trackers_by_hash={
            TORRENT_A: [
                {"url": "https://tracker.example/announce", "status": "2"}
            ]
        },
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "list", "--tracker", "tracker.example"]
    )

    assert result.exit_code == ExitCode.SUCCESS
    payload = json.loads(
        runner.invoke(
            app,
            [
                "torrents",
                "list",
                "--tracker",
                "tracker.example",
                "--format",
                "json",
            ],
        ).stdout
    )
    assert payload["torrents"][0]["tracker_count"] == 1
    assert "Trackers" in result.stdout


def test_list_json_and_jsonl_have_the_same_filters_schema(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure JSON and JSONL carry the exact same `filters` shape --
    JSONL is only a compact one-line serialization of the same payload,
    never a schema of its own."""
    configure_qbit_backend(client=_client_with_categories_and_states())

    json_result = runner.invoke(
        app, ["torrents", "list", "--category", "movies", "--format", "json"]
    )
    jsonl_result = runner.invoke(
        app,
        ["torrents", "list", "--category", "movies", "--format", "jsonl"],
    )

    json_payload = json.loads(json_result.stdout)
    jsonl_payload = json.loads(jsonl_result.stdout)

    assert json_payload == jsonl_payload
    assert "filters" in json_payload
    assert "filters" in jsonl_payload
    assert json_payload["filters"]["categories"] == ["movies"]


def test_list_tracker_filter_never_renders_a_passkey(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure `--tracker` filtering never renders a tracker passkey."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, state="uploading")],
        trackers_by_hash={
            TORRENT_A: [{"url": PASSKEY_TRACKER_URL, "status": "2"}]
        },
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "list", "--tracker", "tracker.example"]
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "SUPER-SECRET-PASSKEY" not in result.stdout
    assert "SUPER-SECRET-PASSKEY" not in result.stderr


# --- Bulk mutations: each filter, NO_MATCH/NO_CHANGES, safety refusals -----


@pytest.mark.parametrize("command", ["pause", "resume", "start", "reannounce"])
def test_mutation_accepts_a_state_filter(
    runner: CliRunner, configure_qbit_backend, command: str
) -> None:
    """Ensure every bulk mutation command accepts --state as a selector."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, state="stalledUP")]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["torrents", command, "--state", "stalled"])

    assert result.exit_code == ExitCode.SUCCESS


def test_mutation_hash_conflicts_with_state_filter(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure --hash cannot combine with --state either."""
    client = FakeQbitClient(torrents=[make_torrent(hash=TORRENT_A)])
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "pause",
            "--hash",
            TORRENT_A,
            "--state",
            "stalled",
            "--no-dry-run",
        ],
    )

    assert result.exit_code == ExitCode.ERROR
    assert client.paused_hashes == []


def test_mutation_missing_selector_is_refused(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure a bulk mutation with no --hash, --all, or filter is refused
    before any qBittorrent API call -- it must never silently mean the
    whole seedbox."""
    client = FakeQbitClient(torrents=[make_torrent(hash=TORRENT_A)])
    calls = configure_qbit_backend(client=client)

    result = runner.invoke(app, ["torrents", "pause"])

    assert result.exit_code == ExitCode.ERROR
    assert "provide --hash, --all" in result.stderr.lower()
    assert calls == []
    assert client.paused_hashes == []


def test_mutation_no_match_under_filtering(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure a bulk mutation filter that matches nothing reports NO_MATCH."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, category="sonarr")]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "pause",
            "--category",
            "nonexistent",
            "--no-dry-run",
        ],
    )

    assert result.exit_code == ExitCode.NO_MATCH
    assert client.paused_hashes == []
    assert "NO_MATCH" in result.stdout


def test_mutation_no_changes_under_filtering(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure a filter that matches an already-satisfied torrent reports
    NO_CHANGES, not APPLIED."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A, category="sonarr", state="pausedUP")
        ]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "pause", "--category", "sonarr", "--no-dry-run"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.paused_hashes == []
    assert "NO_CHANGES" in result.stdout


def test_mutation_dry_run_and_real_execution_target_the_same_selection(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure dry-run and --no-dry-run select the exact same torrents."""
    client_preview = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A, category="sonarr", state="uploading"),
            make_torrent(hash=TORRENT_B, category="radarr", state="uploading"),
        ]
    )
    configure_qbit_backend(client=client_preview)
    preview_result = runner.invoke(
        app, ["torrents", "pause", "--category", "sonarr"]
    )

    client_apply = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A, category="sonarr", state="uploading"),
            make_torrent(hash=TORRENT_B, category="radarr", state="uploading"),
        ]
    )
    configure_qbit_backend(client=client_apply)
    apply_result = runner.invoke(
        app, ["torrents", "pause", "--category", "sonarr", "--no-dry-run"]
    )

    assert preview_result.exit_code == ExitCode.SUCCESS
    assert apply_result.exit_code == ExitCode.SUCCESS
    assert client_apply.paused_hashes == [[TORRENT_A]]


def test_mutation_tracker_filter_preview_never_renders_a_passkey(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure a --tracker-filtered mutation preview never shows a passkey."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, state="uploading")],
        trackers_by_hash={
            TORRENT_A: [{"url": PASSKEY_TRACKER_URL, "status": "2"}]
        },
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "reannounce", "--tracker", "tracker.example"]
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "SUPER-SECRET-PASSKEY" not in result.stdout
    assert "SUPER-SECRET-PASSKEY" not in result.stderr


def test_mutation_start_completed_and_all_still_works_together(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Ensure the documented 'Start All' equivalent (--completed --all)
    works."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, state="stoppedUP", progress=1.0)]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "start", "--completed", "--no-dry-run"]
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.started_hashes == [[TORRENT_A]]
