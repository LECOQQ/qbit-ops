"""Test `torrents stats` at the CLI level."""

from __future__ import annotations

import csv
import io
import json

import pytest
import typer
from typer.core import TyperGroup
from typer.testing import CliRunner

from qbit_core.errors import InvalidInputError
from qbit_core.shared.parsers import parse_duration
from qbit_ops.cli import rendering
from qbit_ops.cli.app import app
from qbit_ops.cli.exit_codes import ExitCode
from qbit_ops.cli.rendering import (
    format_byte_rate,
    format_bytes,
    format_duration,
)
from tests.support import FakeQbitClient, make_torrent

TORRENT_A_HASH = "abc123def456000000000000000000000000000a"
TORRENT_B_HASH = "def456abc123000000000000000000000000000b"
WIDE_TERMINAL = {"COLUMNS": "200"}


def _client(**overrides) -> FakeQbitClient:
    """Two torrents plus non-trivial all-time counters."""
    defaults = {
        "torrents": [
            make_torrent(
                hash=TORRENT_A_HASH,
                name="A",
                category="movies",
                size=2_000_000_000,
                downloaded=1_000_000_000,
                uploaded=3_000_000_000,
                seeding_time=86_400 * 47,
                added_on=1_700_000_000,
            ),
            make_torrent(
                hash=TORRENT_B_HASH,
                name="B",
                category="music",
                size=1_000_000_000,
                downloaded=1_000_000_000,
                uploaded=1_000_000_000,
                seeding_time=86_400 * 3,
                added_on=1_600_000_000,
            ),
        ],
        "all_time_downloaded": 88_000_000_000_000,
        "all_time_uploaded": 213_000_000_000_000,
        "global_ratio": "2.42",
    }
    defaults.update(overrides)
    return FakeQbitClient(**defaults)


def _collapse_whitespace(text: str) -> str:
    return "".join(text.split())


def _payload(result) -> dict:
    return json.loads(result.stdout)


# --- the report itself ------------------------------------------------------


def test_stats_reports_the_aggregates_of_the_whole_library(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client())

    result = runner.invoke(app, ["torrents", "stats", "--format", "json"])

    assert result.exit_code == ExitCode.SUCCESS
    library = _payload(result)["library"]
    assert library["torrents"] == 2
    assert library["scanned"] == 2
    assert library["total_size_bytes"] == 3_000_000_000
    assert library["average_size_bytes"] == 1_500_000_000
    assert library["largest_size_bytes"] == 2_000_000_000
    assert library["downloaded_bytes"] == 2_000_000_000
    assert library["uploaded_bytes"] == 4_000_000_000
    assert library["aggregate_ratio"] == 2.0
    assert library["seeding_time_total_seconds"] == 86_400 * 50
    assert library["seeding_time_median_seconds"] == 86_400 * 25
    assert library["oldest_added_at"].startswith("2020-09-13")
    assert library["newest_added_at"].startswith("2023-11-14")


def test_stats_accepts_the_same_filters_as_torrents_list(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client())

    result = runner.invoke(
        app,
        ["torrents", "stats", "--category", "movies", "--format", "json"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    payload = _payload(result)
    assert payload["library"]["torrents"] == 1
    assert payload["library"]["scanned"] == 2
    assert payload["library"]["total_size_bytes"] == 2_000_000_000
    assert payload["filters"]["categories"] == ["movies"]


def test_stats_rejects_a_contradictory_filter_before_any_api_call(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _client()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "stats", "--ratio-min", "5", "--ratio-max", "1"],
    )

    assert result.exit_code == ExitCode.ERROR
    assert client.torrents_info_calls == 0


def test_an_empty_selection_is_an_answer_not_a_failure(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client())

    result = runner.invoke(
        app,
        ["torrents", "stats", "--category", "absent", "--format", "json"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    library = _payload(result)["library"]
    assert library["torrents"] == 0
    assert library["total_size_bytes"] == 0
    assert library["aggregate_ratio"] is None
    assert library["average_size_bytes"] is None


def test_an_unset_byte_counter_never_reaches_the_totals(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """A `-1` marker used to be summed, so the payload reported negative
    bytes while the table clamped the same figure to `0 B` -- one datum,
    two answers depending on the format."""
    configure_qbit_backend(
        client=_client(
            torrents=[
                make_torrent(hash=TORRENT_A_HASH, downloaded=-1, uploaded=-1),
                make_torrent(
                    hash=TORRENT_B_HASH,
                    downloaded=1_000_000_000,
                    uploaded=2_000_000_000,
                ),
            ]
        )
    )

    result = runner.invoke(app, ["torrents", "stats", "--format", "json"])

    assert result.exit_code == ExitCode.SUCCESS
    library = _payload(result)["library"]
    assert library["downloaded_bytes"] == 1_000_000_000
    assert library["uploaded_bytes"] == 2_000_000_000
    assert library["aggregate_ratio"] == 2.0


def test_the_table_and_the_payload_report_the_same_transfer(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """The rendered byte counts must be a formatting of the payload's,
    never a clamped variant of a different number."""
    for output_format in ("json", "table"):
        configure_qbit_backend(
            client=_client(
                torrents=[
                    make_torrent(
                        hash=TORRENT_A_HASH, downloaded=-1, uploaded=-1
                    )
                ]
            )
        )

        result = runner.invoke(
            app,
            ["torrents", "stats", "--format", output_format],
            env=WIDE_TERMINAL,
        )

        assert result.exit_code == ExitCode.SUCCESS, output_format
        if output_format == "json":
            assert _payload(result)["library"]["downloaded_bytes"] == 0
        else:
            assert "Downloaded      0 B" in result.stdout


# --- the instance block, in every format ------------------------------------


def test_json_carries_the_instance_block_without_a_selector(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client())

    result = runner.invoke(app, ["torrents", "stats", "--format", "json"])

    assert _payload(result)["instance"] == {
        "all_time_downloaded_bytes": 88_000_000_000_000,
        "all_time_uploaded_bytes": 213_000_000_000_000,
        "all_time_ratio": 2.42,
    }


@pytest.mark.parametrize(
    "selector",
    [
        ["--category", "movies"],
        ["--hash", TORRENT_A_HASH[:8]],
        ["--all"],
    ],
)
def test_a_selector_drops_the_instance_block_in_json_and_jsonl(
    runner: CliRunner, configure_qbit_backend, selector: list[str]
) -> None:
    for output_format in ("json", "jsonl"):
        client = _client()
        configure_qbit_backend(client=client)

        result = runner.invoke(
            app,
            ["torrents", "stats", *selector, "--format", output_format],
        )

        assert result.exit_code == ExitCode.SUCCESS, output_format
        assert json.loads(result.stdout)["instance"] is None, output_format
        assert client.sync_maindata_calls == 0, output_format


@pytest.mark.parametrize(
    "selector",
    [
        ["--category", "movies"],
        ["--hash", TORRENT_A_HASH[:8]],
        ["--all"],
    ],
)
def test_a_selector_drops_the_instance_rows_from_the_table(
    runner: CliRunner, configure_qbit_backend, selector: list[str]
) -> None:
    configure_qbit_backend(client=_client())

    result = runner.invoke(
        app,
        ["torrents", "stats", *selector],
        env=WIDE_TERMINAL,
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "Instance (all time)" not in result.stdout
    assert "Instanceratio" not in _collapse_whitespace(result.stdout)


@pytest.mark.parametrize(
    "selector",
    [
        ["--category", "movies"],
        ["--hash", TORRENT_A_HASH[:8]],
        ["--all"],
    ],
)
def test_a_selector_drops_the_instance_section_from_the_csv(
    runner: CliRunner, configure_qbit_backend, selector: list[str]
) -> None:
    configure_qbit_backend(client=_client())

    result = runner.invoke(
        app,
        ["torrents", "stats", *selector, "--format", "csv"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    rows = list(csv.reader(io.StringIO(result.stdout)))
    assert rows[0] == ["section", "key", "value"]
    assert {row[0] for row in rows[1:]} == {"library"}


def test_csv_reports_both_sections_without_a_selector(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client())

    result = runner.invoke(app, ["torrents", "stats", "--format", "csv"])

    assert result.exit_code == ExitCode.SUCCESS
    rows = list(csv.reader(io.StringIO(result.stdout)))
    assert rows[0] == ["section", "key", "value"]
    assert ["library", "torrents", "2"] in rows
    assert ["instance", "all_time_ratio", "2.42"] in rows
    assert {row[0] for row in rows[1:]} == {"library", "instance"}


def test_jsonl_is_exactly_one_compact_document(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client())

    result = runner.invoke(app, ["torrents", "stats", "--format", "jsonl"])

    assert result.exit_code == ExitCode.SUCCESS
    lines = result.stdout.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["library"]["torrents"] == 2


# --- table output -----------------------------------------------------------


def test_the_two_ratios_are_never_both_labelled_ratio(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """A shared "Ratio" label would rebuild exactly the confusion the
    two blocks exist to prevent: one describes the selection, the other
    the instance since always."""
    configure_qbit_backend(client=_client())

    result = runner.invoke(app, ["torrents", "stats"], env=WIDE_TERMINAL)
    collapsed = _collapse_whitespace(result.stdout)

    assert result.exit_code == ExitCode.SUCCESS
    assert "Selectionratio" in collapsed
    assert "Instanceratio" in collapsed


def test_the_table_echoes_the_active_filters(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client())

    result = runner.invoke(
        app,
        ["torrents", "stats", "--category", "movies"],
        env=WIDE_TERMINAL,
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "category=movies" in result.stdout


def test_the_table_renders_byte_counts_and_durations_not_raw_numbers(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client())

    result = runner.invoke(app, ["torrents", "stats"], env=WIDE_TERMINAL)
    collapsed = _collapse_whitespace(result.stdout)

    assert result.exit_code == ExitCode.SUCCESS
    assert "2.8GiB" in collapsed
    # The total is cumulative, so it reads in conventional units; the
    # median beside it stays in the vocabulary `--seeded-for` accepts.
    assert "1mo20d" in collapsed
    assert "25d" in collapsed
    assert "3000000000" not in collapsed


# --- cost -------------------------------------------------------------------


def test_stats_never_looks_up_trackers_unless_a_filter_asks(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _client()
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["torrents", "stats"])

    assert result.exit_code == ExitCode.SUCCESS
    assert client.torrents_info_calls == 1
    assert client.torrents_trackers_calls == 0


def test_an_ambiguous_hash_prefix_is_refused_with_its_candidates(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(
        client=_client(
            torrents=[
                make_torrent(hash="abc1" + "0" * 36, name="A"),
                make_torrent(hash="abc2" + "0" * 36, name="B"),
            ]
        )
    )

    result = runner.invoke(app, ["torrents", "stats", "--hash", "abc"])

    assert result.exit_code == ExitCode.ERROR
    assert "abc" in result.stderr


# --- the formatters the report relies on ------------------------------------


def test_byte_counts_and_rates_share_one_binary_formatter() -> None:
    assert format_bytes(0) == "0 B"
    assert format_bytes(512) == "512 B"
    assert format_bytes(1536) == "1.5 KiB"
    assert format_bytes(5_368_709_120) == "5.0 GiB"
    assert format_byte_rate(1536) == "1.5 KiB/s"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0s"),
        (45, "45s"),
        (90, "1m 30s"),
        (86_400 * 47, "47d"),
        (86_400 * 47 + 3_600 * 6, "47d 6h"),
        (86_400 * 3_000, "3000d"),
    ],
)
def test_durations_render_in_the_units_a_filter_accepts(
    seconds: int, expected: str
) -> None:
    """No months or years: `parse_duration` refuses calendar units
    because they have no fixed length, so a rendered duration must stay
    something an operator can type back into `--seeded-for`."""
    assert format_duration(seconds) == expected


# --- filter parity with `torrents list` -------------------------------------


def _command_options(command_name: str) -> set[str]:
    """Every option flag one `torrents` subcommand accepts.

    Read from the registered command tree rather than from `--help`
    text: this must compare what the CLI accepts, not how it words it.
    """
    root = typer.main.get_command(app)
    assert isinstance(root, TyperGroup)
    torrents_group = root.commands["torrents"]
    assert isinstance(torrents_group, TyperGroup)

    return {
        flag
        for parameter in torrents_group.commands[command_name].params
        for flag in parameter.opts
    }


# `--limit` is not part of the filter vocabulary: it bounds what is
# rendered, never what is selected. `stats` aggregates over the whole
# selection, so truncating its output would misstate its own totals.
_NOT_A_FILTER = {"--limit"}


def test_stats_accepts_every_option_torrents_list_does() -> None:
    """The contract is "no exception, no subset": an operator who knows
    how to filter `torrents list` knows how to filter `torrents stats`.
    Asserted against the registered commands, so a filter added to
    `list` alone fails here instead of silently splitting the two
    vocabularies."""
    missing = _command_options("list") - _command_options("stats")
    assert missing - _NOT_A_FILTER == set()


def test_stats_adds_only_the_two_selectors_that_stand_apart() -> None:
    """`--hash` and `--all` are the whole difference -- `stats` must not
    grow a selection vocabulary of its own."""
    assert _command_options("stats") - _command_options("list") == {
        "--hash",
        "--all",
    }


def test_a_cumulative_total_uses_conventional_units_the_median_does_not() -> (
    None
):
    # 429y 5mo at 365d/30d, the shape the real library produced.
    assert rendering.format_cumulative_duration(13_544_444_400) == "429y 5mo"
    # A retypable value keeps the vocabulary `parse_duration` accepts.
    assert rendering.format_duration(4_060_800) == "47d"


def test_a_cumulative_total_below_one_day_falls_back_to_retypable_units() -> (
    None
):
    assert rendering.format_cumulative_duration(3_600) == "1h"
    assert rendering.format_cumulative_duration(90) == "1m 30s"
    assert rendering.format_cumulative_duration(0) == "0s"


def test_the_conventional_units_never_reach_the_duration_parser() -> None:
    rendered = rendering.format_cumulative_duration(13_544_444_400)

    with pytest.raises(InvalidInputError):
        parse_duration(rendered.split()[0])
