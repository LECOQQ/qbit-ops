"""Test the composable selector flags through the real CLI wiring.

`tests/test_selection_predicates.py` proves the matching rules in
isolation; this file proves the flags reach them -- parsing, option
plumbing, and rejection before any qBittorrent call.
"""

import json
import re
from datetime import UTC, datetime

import pytest
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


def test_json_rows_carry_the_tags_that_were_filtered_on(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """`--tag`/`--tag-all` already filter a selection; a caller who
    filtered on `tags` must be able to read it back in the result,
    rather than trust the filter without a way to verify it."""
    configure_qbit_backend(client=_client())

    result = runner.invoke(
        app,
        ["torrents", "list", "--format", "json", "--tag", "archive"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    torrents = json.loads(result.stdout)["torrents"]
    assert [t["tags"] for t in torrents] == [["archive", "verified"]]


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
    # 1 SELECT + 1 bulk `include_trackers` probe this fake ignores --
    # see `qbit_core.shared.inspection._fetch_trackers_in_bulk`.
    assert client.torrents_info_calls == 2
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


# --- the serialized filter contract -----------------------------------------


def _filters_payload(runner: CliRunner, *options: str) -> dict:
    result = runner.invoke(
        app, ["torrents", "list", "--format", "json", *options]
    )
    return json.loads(result.stdout)["filters"]


def test_the_seven_original_filter_keys_keep_their_name_and_shape(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Scripts written against the pre-composable output must keep
    working: same names, same types, same meaning."""
    configure_qbit_backend(client=_client())

    payload = _filters_payload(runner)

    assert payload["categories"] == []
    assert payload["states"] == []
    assert payload["tracker"] is None
    assert payload["completed"] is None
    assert payload["active"] is None
    assert payload["stalled"] is None
    assert payload["errored"] is None


def test_tracker_is_a_projection_of_the_authoritative_list(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """One requested host projects onto the legacy key; several do not.
    `null` rather than the first value, so a script reading the old key
    sees either what it expected or nothing -- never a partial answer
    that looks complete.
    """
    configure_qbit_backend(client=_tracked_client())

    one = _filters_payload(runner, "--tracker", "old.example")
    assert one["tracker"] == "old.example"
    assert one["trackers"] == ["old.example"]

    several = _filters_payload(
        runner, "--tracker", "old.example", "--tracker", "new.example"
    )
    assert several["tracker"] is None
    assert several["trackers"] == ["old.example", "new.example"]


def test_bounded_families_serialize_as_resolved_values(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Sizes as bytes, durations as whole seconds, dates as ISO-8601
    instants -- never the operator's `10GiB`/`90d` shorthand, which is
    intent rather than what actually gets matched."""
    configure_qbit_backend(client=_client())

    payload = _filters_payload(
        runner,
        "--size-min",
        "1GiB",
        "--ratio-max",
        "4",
        "--seeded-for",
        "7d",
        "--older-than",
        "30d",
    )

    assert payload["size"] == {"min": 1_073_741_824, "max": None}
    assert payload["ratio"] == {"min": None, "max": 4.0}
    assert payload["seeding_time"] == {"min": 604_800, "max": None}
    assert payload["added"]["min"] is None
    assert payload["added"]["max"].endswith("+00:00")


def test_tags_serialize_with_their_three_shapes(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client())

    payload = _filters_payload(
        runner,
        "--tag",
        "archive",
        "--tag-all",
        "verified",
        "--exclude-tag",
        "keep",
    )

    assert payload["tags"] == {
        "any_of": ["archive"],
        "all_of": ["verified"],
        "none_of": ["keep"],
    }


def test_the_regex_is_serialized_as_its_source_pattern(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client())

    payload = _filters_payload(runner, "--name-regex", r"^S\d+E\d+")

    assert payload["name_regex"] == r"^S\d+E\d+"


def test_json_and_jsonl_still_carry_the_same_filter_schema(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client())
    options = ["--tag", "archive", "--size-min", "1GiB"]

    as_json = runner.invoke(
        app, ["torrents", "list", "--format", "json", *options]
    )
    as_jsonl = runner.invoke(
        app, ["torrents", "list", "--format", "jsonl", *options]
    )

    assert json.loads(as_json.stdout) == json.loads(as_jsonl.stdout)


def test_the_filter_line_echoes_only_the_families_actually_set(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client())

    result = runner.invoke(
        app,
        ["torrents", "list", "--tag", "archive", "--ratio-min", "1"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "tag=archive" in result.stdout
    assert "ratio>=1" in result.stdout
    assert "size" not in result.stdout.split("Filter:")[1].split("\n")[0]


def test_the_filter_line_never_renders_a_tracker_secret(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_tracked_client())

    result = runner.invoke(
        app,
        [
            "torrents",
            "list",
            "--tracker",
            "https://old.example/announce/SUPER-SECRET",
        ],
    )

    assert "SUPER-SECRET" not in result.stdout
    assert "tracker=old.example" in result.stdout


# --- tracker operations scope their scan the same way -----------------------


def _tracker_op_client() -> FakeQbitClient:
    return FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A, name="A", category="sonarr"),
            make_torrent(hash=TORRENT_B, name="B", category="radarr"),
        ],
        trackers_by_hash={
            TORRENT_A: [{"url": "https://old.example/announce", "status": "2"}],
            TORRENT_B: [{"url": "https://old.example/announce", "status": "2"}],
        },
    )


def test_remove_scopes_its_scan_and_its_mutation(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """The whole point of the consolidation: the most destructive tracker
    operation can finally target a subset instead of the instance."""
    client = _tracker_op_client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "trackers",
            "remove",
            "--tracker",
            "https://old.example/announce",
            "--category",
            "sonarr",
            "--no-dry-run",
            "--yes",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert [call[0] for call in client.removed_trackers] == [TORRENT_A]
    assert client.torrents_trackers_calls == 1


def test_replace_scopes_its_scan(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _tracker_op_client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "trackers",
            "replace",
            "--source",
            "https://old.example/announce",
            "--target",
            "https://new.example/announce",
            "--category",
            "radarr",
            "--no-dry-run",
            "--yes",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert [call[0] for call in client.edited_trackers] == [TORRENT_B]


def test_a_tracker_operation_reports_what_the_filter_kept(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """`scanned` is the instance, `matched` what the filter kept, and
    `matched_tracker` what actually uses the tracker."""
    configure_qbit_backend(client=_tracker_op_client())

    result = runner.invoke(
        app,
        [
            "trackers",
            "remove",
            "--tracker",
            "https://old.example/announce",
            "--category",
            "sonarr",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "scanned" in result.stdout
    assert "matched" in result.stdout


def test_tracker_operations_refuse_a_tracker_family_filter(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """On these commands the tracker is the *object* of the operation, so
    a second tracker notion on the same line would be ambiguous."""
    configure_qbit_backend(client=_tracker_op_client())

    for option in ("--exclude-tracker", "--no-tracker"):
        result = runner.invoke(
            app,
            [
                "trackers",
                "remove",
                "--tracker",
                "https://old.example/announce",
                option,
                "x",
            ],
        )
        assert result.exit_code != ExitCode.SUCCESS


def test_a_tracker_operation_filter_is_still_dry_run_by_default(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _tracker_op_client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "trackers",
            "remove",
            "--tracker",
            "https://old.example/announce",
            "--category",
            "sonarr",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "PREVIEW" in result.stdout
    assert client.removed_trackers == []


def test_replace_passkey_accepts_filters_without_leaking_the_new_passkey(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Scoping must not weaken the secret-redaction invariant."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, name="A", category="sonarr")],
        trackers_by_hash={
            TORRENT_A: [
                {"url": "https://old.example/announce/OLDKEY", "status": "2"}
            ]
        },
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "trackers",
            "replace-passkey",
            "--tracker",
            "https://old.example/announce/{passkey}",
            "--new-passkey-stdin",
            "--category",
            "sonarr",
        ],
        input="BRAND-NEW-SECRET\n",
    )

    assert "BRAND-NEW-SECRET" not in result.stdout
    assert "BRAND-NEW-SECRET" not in result.stderr


# --- torrents inspect: filters as a third selector mode ---------------------


def test_inspect_by_filter_reports_every_match(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _tracked_client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "inspect", "--category", "linux", "--format", "json"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"scanned": 3, "matched": 1}
    assert [t["name"] for t in payload["torrents"]] == ["A"]


def test_inspect_by_filter_never_exposes_a_raw_announce_url(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """The filtered mode must keep the single-hash mode's guarantee:
    ordinary read commands report tracker identities, never raw URLs."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, name="A", category="linux")],
        trackers_by_hash={
            TORRENT_A: [
                {
                    "url": "https://tracker.example/announce/SUPER-SECRET",
                    "status": "2",
                }
            ]
        },
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "inspect", "--category", "linux", "--format", "json"],
    )

    assert "SUPER-SECRET" not in result.stdout
    assert "tracker.example" in result.stdout


def test_inspect_requires_exactly_one_selector(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _tracked_client()
    configure_qbit_backend(client=client)

    none_given = runner.invoke(app, ["torrents", "inspect"])
    two_given = runner.invoke(
        app, ["torrents", "inspect", "--hash", TORRENT_A, "--category", "linux"]
    )

    assert none_given.exit_code == ExitCode.ERROR
    assert two_given.exit_code == ExitCode.ERROR
    assert "exactly one" in two_given.stderr


def test_inspect_by_filter_costs_one_lookup_per_survivor(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _tracked_client()
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["torrents", "inspect", "--category", "linux"])

    assert result.exit_code == ExitCode.SUCCESS
    assert client.torrents_info_calls == 1
    assert client.torrents_trackers_calls == 1


def test_inspect_by_filter_supports_tracker_criteria(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Tracker hosts are matched against the safe identities already
    collected, so they cost no call of their own."""
    client = _tracked_client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "inspect", "--tracker", "old.example", "--format", "json"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert [t["name"] for t in json.loads(result.stdout)["torrents"]] == ["A"]


def test_trackers_status_accepts_the_full_cheap_filter_set(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _tracked_client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "trackers",
            "status",
            "--size-min",
            "1",
            "--exclude-category",
            "movies",
            "--format",
            "json",
        ],
    )

    assert result.exit_code in (0, 1, 2, 3)
    assert "trackers" in json.loads(result.stdout)


# --- inactivity through the CLI ---------------------------------------------


def _activity_client() -> FakeQbitClient:
    now = int(datetime.now(tz=UTC).timestamp())
    old = now - 86_400 * 200
    return FakeQbitClient(
        torrents=[
            make_torrent(
                hash=TORRENT_A,
                name="Idle",
                added_on=old,
                last_activity=old,
            ),
            make_torrent(
                hash=TORRENT_B,
                name="Busy",
                added_on=old,
                last_activity=now - 60,
            ),
        ]
    )


def test_inactive_for_selects_only_what_stopped_transferring(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Both torrents are 200 days old; only one still moves data."""
    configure_qbit_backend(client=_activity_client())

    assert _names(runner, "--inactive-for", "30d") == ["Idle"]
    assert _names(runner, "--active-within", "24h") == ["Busy"]


def test_inactive_for_differs_from_older_than(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """`--older-than` is about age, `--inactive-for` about activity; on
    this fixture the two disagree, which is the reason both exist."""
    configure_qbit_backend(client=_activity_client())

    assert _names(runner, "--older-than", "30d") == ["Busy", "Idle"]
    assert _names(runner, "--inactive-for", "30d") == ["Idle"]


def test_the_two_activity_bounds_describe_a_usable_window(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """ "Idle for at least a day, but not dead for a month" is a real
    request, not a contradiction: the bounds simply nest."""
    configure_qbit_backend(client=_activity_client())

    result = runner.invoke(
        app,
        ["torrents", "list", "--inactive-for", "1d", "--active-within", "30d"],
    )

    assert result.exit_code in (ExitCode.SUCCESS, ExitCode.NO_MATCH)


def test_an_inverted_activity_window_is_rejected(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Inactive for 30 days *and* active within the last day cannot both
    hold -- refused before any API call."""
    client = _activity_client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "list", "--inactive-for", "30d", "--active-within", "1d"],
    )

    assert result.exit_code == ExitCode.ERROR
    assert "empty time window" in result.stderr
    assert client.torrents_info_calls == 0


def test_inactivity_serializes_as_a_resolved_window(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_activity_client())

    payload = _filters_payload(runner, "--inactive-for", "30d")

    assert payload["last_activity"]["min"] is None
    assert payload["last_activity"]["max"].endswith("+00:00")


# --- completion date through the CLI ----------------------------------------


def _completion_client() -> FakeQbitClient:
    now = int(datetime.now(tz=UTC).timestamp())
    return FakeQbitClient(
        torrents=[
            make_torrent(
                hash=TORRENT_A,
                name="Old",
                progress=1.0,
                completion_on=now - 86_400 * 200,
            ),
            make_torrent(
                hash=TORRENT_B,
                name="Fresh",
                progress=1.0,
                completion_on=now - 86_400 * 2,
            ),
            make_torrent(
                hash="c" * 40,
                name="Unfinished",
                state="downloading",
                progress=0.4,
                completion_on=-1,
            ),
        ]
    )


def test_completion_window_splits_finished_torrents_by_date(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_completion_client())

    assert _names(runner, "--completed-before", "30d") == ["Old"]
    assert _names(runner, "--completed-within", "7d") == ["Fresh"]


def test_an_unfinished_torrent_is_never_picked_by_a_completion_bound(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """`completion_on == -1` means "never finished"; it must not read as
    a 1970 completion, which would sweep in-progress downloads into a
    cleanup selection."""
    configure_qbit_backend(client=_completion_client())

    assert "Unfinished" not in _names(runner, "--completed-before", "30d")
    assert "Unfinished" not in _names(runner, "--completed-within", "365d")


def test_an_inverted_completion_window_is_rejected(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _completion_client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "list",
            "--completed-before",
            "30d",
            "--completed-within",
            "1d",
        ],
    )

    assert result.exit_code == ExitCode.ERROR
    assert "empty" in result.stderr
    assert client.torrents_info_calls == 0


def test_completion_serializes_as_a_resolved_window(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_completion_client())

    payload = _filters_payload(runner, "--completed-before", "30d")

    assert payload["completed_at"]["min"] is None
    assert payload["completed_at"]["max"].endswith("+00:00")


# --- pseudo-trackers must never break a tracker filter ----------------------
#
# qBittorrent reports DHT/PeX/LSD as bracketed marker strings inside
# `torrents_trackers()`, and they can carry status 2 (working) on a real
# 5.2.3 instance. `normalize_tracker_host` raises `ValueError: Invalid
# IPv6 URL` on them because of the brackets, so any code matching hosts
# against qBittorrent-reported URLs has to skip them.

PEX = {"url": "** [PeX] **", "status": 2}
LSD = {"url": "** [LSD] **", "status": 2}
DHT_DISABLED = {"url": "** [DHT] **", "status": 0}

REAL_A = "a" * 40
REAL_B = "b" * 40
ORPHAN = "c" * 40


def _pseudo_tracker_client() -> FakeQbitClient:
    """One torrent per interesting shape, all with active pseudo-entries."""
    return FakeQbitClient(
        torrents=[
            make_torrent(
                hash=REAL_A, name="OnOld", category="linux", trackers_count=1
            ),
            make_torrent(
                hash=REAL_B, name="OnNew", category="linux", trackers_count=1
            ),
            make_torrent(
                hash=ORPHAN, name="Orphan", category="linux", trackers_count=0
            ),
        ],
        trackers_by_hash={
            # Pseudo entries first, so a host match must walk past them.
            REAL_A: [
                PEX,
                LSD,
                {"url": "https://old.example/announce", "status": 2},
            ],
            REAL_B: [
                PEX,
                {"url": "https://new.example/announce", "status": 2},
                LSD,
            ],
            ORPHAN: [PEX, LSD, DHT_DISABLED],
        },
    )


def test_an_active_pseudo_tracker_does_not_break_a_tracker_filter(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """The pseudo-tracker shape under test: `** [PeX] **` with status 2."""
    configure_qbit_backend(client=_pseudo_tracker_client())

    assert _names(runner, "--tracker", "old.example") == ["OnOld"]
    assert _names(runner, "--tracker", "absent.example") == []


def test_an_active_pseudo_tracker_does_not_break_an_exclusion(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Exclusions always walk the whole tracker list, so they hit the
    pseudo entries on every torrent."""
    configure_qbit_backend(client=_pseudo_tracker_client())

    assert _names(runner, "--exclude-tracker", "old.example") == [
        "OnNew",
        "Orphan",
    ]


def test_a_pseudo_tracker_is_never_matched_as_a_host(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """A marker names no host, so it can never satisfy `--tracker`."""
    configure_qbit_backend(client=_pseudo_tracker_client())

    assert _names(runner, "--tracker", "PeX") == []
    assert _names(runner, "--tracker", "LSD") == []


def test_no_tracker_stays_authoritative_on_trackers_count(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """`--no-tracker` means "no configured tracker", read from the bulk
    listing. Active PeX/LSD entries must not make an orphan look
    tracked, nor a tracked torrent look orphaned."""
    client = _pseudo_tracker_client()
    configure_qbit_backend(client=client)

    assert _names(runner, "--no-tracker") == ["Orphan"]
    assert client.torrents_trackers_calls == 0


def test_a_pseudo_tracker_does_not_break_a_mutation(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Same fixture through a real mutation: the filter must select, not
    raise, and must stay fail-closed on what it cannot match."""
    client = _pseudo_tracker_client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "pause", "--tracker", "old.example", "--no-dry-run"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.paused_hashes == [[REAL_A]]


def test_a_pseudo_tracker_does_not_break_a_tracker_operation(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _pseudo_tracker_client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "trackers",
            "remove",
            "--tracker",
            "https://old.example/announce",
            "--no-dry-run",
            "--yes",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert [call[0] for call in client.removed_trackers] == [REAL_A]


def test_an_unparsable_tracker_url_does_not_become_an_internal_error(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=REAL_A, name="Broken", trackers_count=1)],
        trackers_by_hash={
            REAL_A: [{"url": "http://[not-an-ipv6", "status": 2}]
        },
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "list", "--tracker", "real.example"]
    )

    assert result.exit_code == ExitCode.NO_MATCH
    assert "Internal error" not in result.stderr


def test_a_non_numeric_tracker_status_is_tolerated(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """`get_raw_tracker_status` keeps the original type and only `0`
    (or the literal "disabled") means disabled, so an unrecognized
    status leaves the endpoint active and matchable."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=REAL_A, name="Odd", trackers_count=1)],
        trackers_by_hash={
            REAL_A: [
                PEX,
                {"url": "https://odd.example/announce", "status": "working"},
            ]
        },
    )
    configure_qbit_backend(client=client)

    assert _names(runner, "--tracker", "odd.example") == ["Odd"]


# --- one selector, one torrent set, whatever consumes it --------------------


def _mutation_target_names(
    runner: CliRunner, client: FakeQbitClient, *options: str
) -> list[str]:
    """Run `torrents pause` for real and map the paused hashes to names."""
    by_hash = {t["hash"]: t["name"] for t in client.torrents}
    result = runner.invoke(app, ["torrents", "pause", "--no-dry-run", *options])
    assert result.exit_code in (ExitCode.SUCCESS, ExitCode.NO_MATCH), (
        result.stdout,
        result.stderr,
    )
    paused = [h for batch in client.paused_hashes for h in batch]
    return sorted(by_hash[h] for h in paused)


def _inspect_names(runner: CliRunner, *options: str) -> list[str]:
    result = runner.invoke(
        app, ["torrents", "inspect", "--format", "json", *options]
    )
    assert result.exit_code in (ExitCode.SUCCESS, ExitCode.NO_MATCH), (
        result.stdout,
        result.stderr,
    )
    return [t["name"] for t in json.loads(result.stdout)["torrents"]]


@pytest.mark.parametrize(
    "options",
    [
        ["--tracker", "old.example"],
        ["--exclude-tracker", "old.example"],
        ["--no-tracker"],
        ["--category", "linux", "--exclude-tracker", "new.example"],
        ["--tracker", "old.example", "--tracker", "new.example"],
    ],
    ids=["tracker", "exclude-tracker", "no-tracker", "combined", "repeated"],
)
def test_list_inspect_and_mutation_target_the_same_torrents(
    runner: CliRunner, configure_qbit_backend, options: list[str]
) -> None:
    """The invariant the whole pass rests on: the same selector must
    target the same set regardless of the action consuming it."""
    configure_qbit_backend(client=_pseudo_tracker_client())
    listed = sorted(_names(runner, *options))
    inspected = sorted(_inspect_names(runner, *options))

    mutation_client = _pseudo_tracker_client()
    configure_qbit_backend(client=mutation_client)
    mutated = _mutation_target_names(runner, mutation_client, *options)

    assert listed == inspected == mutated


def test_a_dry_run_mutation_reports_the_same_matched_count_as_list(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_pseudo_tracker_client())
    listed = _names(runner, "--exclude-tracker", "old.example")

    result = runner.invoke(
        app, ["torrents", "pause", "--exclude-tracker", "old.example"]
    )

    assert result.exit_code == ExitCode.SUCCESS
    matched = re.search(r"^matched\s+(\d+)", result.stdout, re.MULTILINE)
    assert matched is not None, result.stdout
    assert int(matched.group(1)) == len(listed)


def test_inspect_orders_its_results_by_name(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """`torrents inspect --format json` is a public payload, so its
    order must not depend on whatever order qBittorrent listed."""
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[
                make_torrent(hash=REAL_A, name="Zulu", category="linux"),
                make_torrent(hash=REAL_B, name="alpha", category="linux"),
                make_torrent(hash=ORPHAN, name="Mike", category="linux"),
            ],
            trackers_by_hash={REAL_A: [], REAL_B: [], ORPHAN: []},
        )
    )

    assert _inspect_names(runner, "--category", "linux") == [
        "alpha",
        "Mike",
        "Zulu",
    ]
