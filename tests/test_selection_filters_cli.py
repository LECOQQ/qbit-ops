"""Test the composable selector flags through the real CLI wiring.

`tests/test_selection_predicates.py` proves the matching rules in
isolation; this file proves the flags reach them -- parsing, option
plumbing, and rejection before any qBittorrent call.
"""

import json

from typer.testing import CliRunner

from qbit_ops.cli.app import app
from qbit_ops.cli.exit_codes import ExitCode
from tests.support import FakeQbitClient, make_torrent

TORRENT_A = "a" * 40
TORRENT_B = "b" * 40


def _client() -> FakeQbitClient:
    return FakeQbitClient(
        torrents=[
            make_torrent(
                hash=TORRENT_A,
                name="Debian 12 ISO",
                category="linux",
                tags="archive, verified",
                save_path="/downloads/linux",
                size=4_700_000_000,
                progress=1.0,
                ratio=2.5,
                uploaded=9_400_000_000,
                seeding_time=86_400 * 30,
                added_on=1_600_000_000,
                trackers_count=2,
                private=True,
            ),
            make_torrent(
                hash=TORRENT_B,
                name="Ubuntu 24.04",
                state="downloading",
                category="movies",
                tags="keep",
                save_path="/downloads/movies",
                size=1_000_000,
                progress=0.5,
                ratio=0.1,
                uploaded=100_000,
                seeding_time=60,
                added_on=1_700_000_000,
                trackers_count=1,
                private=False,
            ),
        ]
    )


def _names(runner: CliRunner, *options: str) -> list[str]:
    result = runner.invoke(
        app, ["torrents", "list", "--format", "json", *options]
    )
    assert result.exit_code in (ExitCode.SUCCESS, ExitCode.NO_MATCH), (
        result.stdout,
        result.stderr,
    )
    return [t["name"] for t in json.loads(result.stdout)["torrents"]]


# --- each family reaches its predicate --------------------------------------


def test_exclusions_narrow_the_selection(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client())

    assert _names(runner, "--exclude-category", "movies") == ["Debian 12 ISO"]
    assert _names(runner, "--exclude-tag", "keep") == ["Debian 12 ISO"]
    assert _names(runner, "--exclude-name", "ubuntu") == ["Debian 12 ISO"]
    assert _names(runner, "--exclude-state", "downloading") == ["Debian 12 ISO"]


def test_tag_any_and_tag_all(runner: CliRunner, configure_qbit_backend) -> None:
    configure_qbit_backend(client=_client())

    assert _names(runner, "--tag", "archive", "--tag", "keep") == [
        "Debian 12 ISO",
        "Ubuntu 24.04",
    ]
    assert _names(runner, "--tag-all", "archive", "--tag-all", "verified") == [
        "Debian 12 ISO"
    ]
    assert _names(runner, "--tag-all", "archive", "--tag-all", "keep") == []


def test_save_path_and_name_filters(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client())

    assert _names(runner, "--save-path", "/downloads/linux") == [
        "Debian 12 ISO"
    ]
    assert _names(runner, "--name-contains", "DEBIAN") == ["Debian 12 ISO"]
    assert _names(runner, "--name-regex", r"^Ubuntu") == ["Ubuntu 24.04"]


def test_private_and_public(runner: CliRunner, configure_qbit_backend) -> None:
    configure_qbit_backend(client=_client())

    assert _names(runner, "--private") == ["Debian 12 ISO"]
    assert _names(runner, "--public") == ["Ubuntu 24.04"]


# --- value parsing reaches the predicates -----------------------------------


def test_size_accepts_both_unit_families(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client())

    assert _names(runner, "--size-min", "1GB") == ["Debian 12 ISO"]
    assert _names(runner, "--size-max", "2MiB") == ["Ubuntu 24.04"]


def test_progress_accepts_percent_and_fraction(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client())

    assert _names(runner, "--progress-min", "99%") == ["Debian 12 ISO"]
    assert _names(runner, "--progress-max", "0.9") == ["Ubuntu 24.04"]


def test_ratio_uploaded_and_seeded_for(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client())

    assert _names(runner, "--ratio-min", "1") == ["Debian 12 ISO"]
    assert _names(runner, "--uploaded-min", "1GiB") == ["Debian 12 ISO"]
    assert _names(runner, "--seeded-for", "7d") == ["Debian 12 ISO"]


def test_relative_ages_resolve_into_an_absolute_window(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """`--older-than` bounds the window's upper end, `--newer-than` its
    lower end; the two fixtures are years apart so the split is stable."""
    configure_qbit_backend(client=_client())

    assert _names(runner, "--older-than", "365d") == [
        "Debian 12 ISO",
        "Ubuntu 24.04",
    ]
    assert _names(runner, "--newer-than", "1s") == []


# --- composition ------------------------------------------------------------


def test_families_combine_with_and(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """The "cleanup" use case from the design: well-seeded, high ratio,
    except anything tagged keep."""
    configure_qbit_backend(client=_client())

    assert _names(
        runner,
        "--ratio-min",
        "1",
        "--seeded-for",
        "7d",
        "--exclude-tag",
        "keep",
    ) == ["Debian 12 ISO"]


def test_a_filtered_zero_match_is_reported_as_no_match(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client())

    result = runner.invoke(
        app, ["torrents", "list", "--ratio-min", "999", "--format", "json"]
    )

    assert result.exit_code == ExitCode.NO_MATCH


# --- rejection happens before any API call ----------------------------------


def test_an_unparsable_value_is_rejected_without_calling_qbittorrent(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _client()
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["torrents", "list", "--size-min", "500M"])

    assert result.exit_code == ExitCode.ERROR
    assert "Ambiguous size unit" in result.stderr
    assert client.torrents_info_calls == 0


def test_a_contradictory_combination_is_rejected_before_any_api_call(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "list", "--tag", "keep", "--exclude-tag", "keep"],
    )

    assert result.exit_code == ExitCode.ERROR
    assert "can satisfy both" in result.stderr
    assert client.torrents_info_calls == 0


def test_an_inverted_range_is_rejected(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "list", "--ratio-min", "4", "--ratio-max", "1"],
    )

    assert result.exit_code == ExitCode.ERROR
    assert client.torrents_info_calls == 0


def test_an_invalid_regex_is_rejected(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _client()
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["torrents", "list", "--name-regex", "a["])

    assert result.exit_code == ExitCode.ERROR
    assert "regular expression" in result.stderr
    assert client.torrents_info_calls == 0


def test_an_unknown_exclude_state_value_is_rejected(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "list", "--exclude-state", "nonsense"]
    )

    assert result.exit_code == ExitCode.ERROR
    assert "--exclude-state" in result.stderr
    assert client.torrents_info_calls == 0


# --- cost -------------------------------------------------------------------


def test_cheap_filters_never_trigger_a_tracker_lookup(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Every filter added here reads from the single bulk listing."""
    client = _client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "list",
            "--tag",
            "archive",
            "--size-min",
            "1GiB",
            "--seeded-for",
            "7d",
            "--private",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.torrents_info_calls == 1
    assert client.torrents_trackers_calls == 0


# --- the same selector drives mutations -------------------------------------


def test_the_same_filters_target_a_mutation(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Selection is independent of the action: the flags that narrow a
    listing narrow a mutation identically."""
    client = _client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "pause",
            "--ratio-min",
            "1",
            "--exclude-tag",
            "keep",
            "--no-dry-run",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.paused_hashes == [[TORRENT_A]]


def test_a_mutation_filter_is_dry_run_by_default(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _client()
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["torrents", "pause", "--size-min", "1GiB"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "PREVIEW" in result.stdout
    assert client.paused_hashes == []


def test_a_mutation_filter_matching_nothing_is_no_match(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "pause", "--tag", "nonexistent", "--no-dry-run"]
    )

    assert result.exit_code == ExitCode.NO_MATCH
    assert client.paused_hashes == []


def test_a_new_filter_still_counts_as_a_selector(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """A mutation needs --hash, --all or at least one filter. The new
    families must satisfy that requirement, or they would be unusable."""
    client = _client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "pause", "--seeded-for", "7d", "--no-dry-run"]
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.paused_hashes == [[TORRENT_A]]


def test_a_new_filter_still_conflicts_with_hash(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """`--hash` stays an exclusive selector: combining it with any
    filter is refused, including the ones added here."""
    client = _client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "pause", "--hash", TORRENT_A, "--tag", "archive"],
    )

    assert result.exit_code == ExitCode.ERROR
    assert "--hash alone" in result.stderr
    assert client.paused_hashes == []


def test_a_new_filter_still_conflicts_with_all(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "pause", "--all", "--ratio-min", "1"]
    )

    assert result.exit_code == ExitCode.ERROR
    assert "--all alone" in result.stderr
    assert client.paused_hashes == []


def test_delete_accepts_the_filters_and_still_requires_confirmation(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """The most destructive command is the biggest beneficiary of
    scoping, and gains no shortcut around its HIGH-risk gate."""
    client = _client()
    configure_qbit_backend(client=client)

    preview = runner.invoke(
        app, ["torrents", "delete", "--exclude-tag", "keep"]
    )
    applied = runner.invoke(
        app,
        [
            "torrents",
            "delete",
            "--exclude-tag",
            "keep",
            "--no-dry-run",
            "--yes",
        ],
    )

    assert preview.exit_code == ExitCode.SUCCESS
    assert "PREVIEW" in preview.stdout
    assert applied.exit_code == ExitCode.SUCCESS
    assert client.deleted_hashes == [([TORRENT_A], False)]


def test_a_filtered_mutation_costs_one_bulk_call(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "pause", "--tag", "archive", "--size-min", "1GiB"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.torrents_info_calls == 1
    assert client.torrents_trackers_calls == 0


# --- tracker filters: cheap presence vs inspected host ----------------------


def _tracked_client() -> FakeQbitClient:
    return FakeQbitClient(
        torrents=[
            make_torrent(
                hash=TORRENT_A, name="A", category="linux", trackers_count=1
            ),
            make_torrent(
                hash=TORRENT_B, name="B", category="movies", trackers_count=1
            ),
            make_torrent(
                hash="c" * 40, name="C", category="music", trackers_count=0
            ),
        ],
        trackers_by_hash={
            TORRENT_A: [{"url": "https://old.example/announce", "status": "2"}],
            TORRENT_B: [{"url": "https://new.example/announce", "status": "2"}],
            "c" * 40: [],
        },
    )


def test_no_tracker_costs_no_extra_call(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """`trackers_count` ships with the bulk listing, so asking for
    orphaned torrents never triggers a per-torrent lookup."""
    client = _tracked_client()
    configure_qbit_backend(client=client)

    assert _names(runner, "--no-tracker") == ["C"]
    assert client.torrents_trackers_calls == 0


def test_exclude_tracker_narrows_after_inspection(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _tracked_client()
    configure_qbit_backend(client=client)

    assert _names(runner, "--exclude-tracker", "old.example") == ["B", "C"]


def test_exclude_tracker_inspects_only_cheap_survivors(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """The ordering guarantee: a cheap filter narrows first, so the
    tracker lookup runs once instead of once per torrent."""
    client = _tracked_client()
    configure_qbit_backend(client=client)

    assert _names(
        runner, "--category", "linux", "--exclude-tracker", "new.example"
    ) == ["A"]
    assert client.torrents_info_calls == 1
    assert client.torrents_trackers_calls == 1


def test_tracker_and_exclude_tracker_combine(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _tracked_client()
    configure_qbit_backend(client=client)

    assert _names(
        runner, "--tracker", "old.example", "--exclude-tracker", "new.example"
    ) == ["A"]


def test_exclude_tracker_accepts_a_full_announce_url_but_keeps_only_the_host(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Only host and port survive, which is what keeps a passkey out of
    the filter and therefore out of every summary built from it."""
    client = _tracked_client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "list",
            "--exclude-tracker",
            "https://old.example/announce/SECRET-PASSKEY",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "SECRET-PASSKEY" not in result.stdout
    assert "SECRET-PASSKEY" not in result.stderr


def test_no_tracker_conflicts_with_a_tracker_host(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _tracked_client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "list", "--no-tracker", "--tracker", "old.example"],
    )

    assert result.exit_code == ExitCode.ERROR
    assert client.torrents_info_calls == 0


def test_exclude_tracker_targets_a_mutation(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _tracked_client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "pause",
            "--exclude-tracker",
            "new.example",
            "--no-dry-run",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.paused_hashes == [[TORRENT_A, "c" * 40]]
