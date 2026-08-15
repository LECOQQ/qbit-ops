"""Test `torrents search`, the CLI wiring around the shared search engine.

The ladder's own behaviour (tiers, monotonicity, normalization) is
tested in `tests/test_search.py`; this file covers only what the CLI
layer adds: option surface, format rendering, exit codes, and the
`INSPECTION_ONLY_FILTER_FIELDS` refusal reaching the engine.
"""

import csv
import io
import json

import pytest
from typer.testing import CliRunner

from qbit_core.shared.selection import INSPECTION_ONLY_FILTER_FIELDS
from qbit_ops.cli.app import app
from qbit_ops.cli.exit_codes import ExitCode
from tests.support import FakeQbitClient, make_torrent

TORRENT_HASH = "3f2a1b9cdeadbeef0123456789abcdef01234567"


def _client() -> FakeQbitClient:
    return FakeQbitClient(
        torrents=[
            make_torrent(
                hash=TORRENT_HASH,
                name="Amour Est Dans Le Pre",
                category="tv",
                state="stalledUP",
                progress=1.0,
                ratio=3.42,
                size=2254857830,
            ),
            make_torrent(
                hash="b" * 40,
                name="Completely Unrelated",
                category="",
            ),
        ]
    )


def test_search_ranks_and_reports_the_tier_name(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client())

    result = runner.invoke(app, ["torrents", "search", "amour est"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "Amour Est Dans Le Pre" in result.stdout
    assert "Completely Unrelated" not in result.stdout
    # The tier name reaches the table's Match column -- "amour est"
    # prefixes the normalized name, so it must read "prefix".
    assert "prefix" in result.stdout


def test_search_json_matches_the_documented_contract_shape(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client())

    result = runner.invoke(
        app, ["torrents", "search", "amour", "--format", "json"]
    )

    assert result.exit_code == ExitCode.SUCCESS
    payload = json.loads(result.stdout)
    assert set(payload) == {
        "query",
        "normalized_query",
        "mode",
        "summary",
        "matches",
    }
    assert set(payload["summary"]) == {
        "scanned",
        "matched",
        "returned",
        "limit",
        "truncated",
    }
    assert len(payload["matches"]) == 1
    match = payload["matches"][0]
    assert set(match) == {
        "hash",
        "name",
        "match",
        "state",
        "category",
        "size",
        "progress",
        "ratio",
    }
    assert match["hash"] == TORRENT_HASH
    assert match["match"] in {
        "hash",
        "exact",
        "prefix",
        "substring",
        "all_tokens",
        "token_prefix",
        "fuzzy",
    }
    # AC1/contract: no numeric score anywhere in machine output.
    assert "match_score" not in match
    assert "similarity" not in match


def test_search_matched_counts_before_truncation(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Amour Un"),
            make_torrent(hash="b" * 40, name="Amour Deux"),
            make_torrent(hash="c" * 40, name="Amour Trois"),
        ]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "search",
            "amour",
            "--mode",
            "tokens",
            "--limit",
            "2",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    payload = json.loads(result.stdout)
    assert payload["summary"]["matched"] == 3
    assert payload["summary"]["returned"] == 2
    assert payload["summary"]["truncated"] is True
    assert len(payload["matches"]) == 2


# --- Composable filters actually narrow matches[], not just parse ----------
#
# `build_filter_from_options(...)` in `search_qbit_torrents` takes ~40
# keyword arguments; each is an independent wire from a CLI option to a
# dataclass field, and none of the tests above (which only vary the
# query and --mode/--limit) would catch one going missing. --category
# and --ratio-min are two structurally different families (categorical
# membership vs. a parsed bounded Range) picked to cover more than one
# code path without asserting on all ~40.


def test_category_filter_narrows_matches_before_ranking(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Amour Un", category="tv"),
            make_torrent(hash="b" * 40, name="Amour Deux", category="film"),
        ]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "search",
            "amour",
            "--category",
            "tv",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    payload = json.loads(result.stdout)
    assert [match["name"] for match in payload["matches"]] == ["Amour Un"]


def test_ratio_min_filter_narrows_matches_before_ranking(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Amour Un", ratio=3.0),
            make_torrent(hash="b" * 40, name="Amour Deux", ratio=0.1),
        ]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "search",
            "amour",
            "--ratio-min",
            "1",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    payload = json.loads(result.stdout)
    assert [match["name"] for match in payload["matches"]] == ["Amour Un"]


def test_search_rejects_blank_query_before_any_api_call(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _client()
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["torrents", "search", "   "])

    assert result.exit_code == ExitCode.ERROR
    assert client.torrents_info_calls == 0


def test_search_rejects_a_query_that_normalizes_to_blank(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _client()
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["torrents", "search", "..."])

    assert result.exit_code == ExitCode.ERROR
    assert client.torrents_info_calls == 0


def test_search_rejects_an_all_dash_query_past_the_option_separator(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """`---` also normalizes to blank, but Click treats a bare leading-dash
    token as an option first -- it needs the `--` end-of-options marker to
    reach the query argument at all. Documented in docs/COMMANDS.md."""
    client = _client()
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["torrents", "search", "--", "---"])

    assert result.exit_code == ExitCode.ERROR
    assert client.torrents_info_calls == 0


def test_search_exits_no_match_on_zero_hits(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client())

    result = runner.invoke(
        app, ["torrents", "search", "nonexistent-torrent-title-zzz"]
    )

    assert result.exit_code == ExitCode.NO_MATCH


# --- INSPECTION_ONLY_FILTER_FIELDS: refused before any API call -----------

_CLI_INSPECTION_ONLY_FILTER_ARGV: dict[str, list[str]] = {
    "trackers": ["--tracker", "tracker.example"],
    "trackers_excluded": ["--exclude-tracker", "tracker.example"],
    "tracker_health": ["--tracker-health", "healthy"],
}


def test_cli_inspection_only_filter_samples_cover_every_field() -> None:
    """Non-vacuity: the parametrized test below must exercise every field
    `INSPECTION_ONLY_FILTER_FIELDS` names, driven by the constant --
    never a hand-copied subset that could silently stop tracking it."""
    assert set(_CLI_INSPECTION_ONLY_FILTER_ARGV) == set(
        INSPECTION_ONLY_FILTER_FIELDS
    )


@pytest.mark.parametrize(
    ("field_name", "argv_fragment"),
    sorted(_CLI_INSPECTION_ONLY_FILTER_ARGV.items()),
    ids=lambda value: value if isinstance(value, str) else "argv",
)
def test_search_rejects_every_inspection_only_filter(
    runner: CliRunner,
    configure_qbit_backend,
    field_name: str,
    argv_fragment: list[str],
) -> None:
    client = _client()
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["torrents", "search", "amour", *argv_fragment])

    assert result.exit_code == ExitCode.ERROR
    assert "torrents list" in result.stderr
    assert client.torrents_info_calls == 0


# --- Piège 3: --verbose is a local option, not the shared mutation one ----


def test_search_verbose_help_is_local_not_the_mutation_wording(
    runner: CliRunner,
) -> None:
    result = runner.invoke(app, ["torrents", "search", "--help"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "--verbose" in result.stdout
    assert "Print impacted torrent details." not in result.stdout


def test_search_verbose_shows_similarity_only_for_fuzzy_hits(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """`similarity` is a `fuzzy`-only field on `SearchHit`; the exact-tier
    hit in the same result set must show a blank cell, not a stale or
    leaked value from a neighbouring row."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="Amuor Est La"),
            make_torrent(hash="b" * 40, name="Amour Est La"),
        ]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "search", "amour est la", "--mode", "fuzzy", "--verbose"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "Similarity" in result.stdout
    lines = result.stdout.splitlines()
    fuzzy_row = next(line for line in lines if "Amuor Est La" in line)
    exact_row = next(
        line for line in lines if "Amour Est La" in line and "Amuor" not in line
    )
    # Not just the header: the rendered value itself must reach the
    # table, or a column that stays permanently blank would pass too.
    assert "0.93" in fuzzy_row
    # The exact-tier row's Similarity cell must stay blank -- no leakage
    # of the neighbouring fuzzy row's value onto a `similarity=None` hit.
    assert "0.93" not in exact_row


def test_search_similarity_never_appears_without_verbose(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, name="Amuor Est La")]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "search", "amour est la", "--mode", "fuzzy"]
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "Similarity" not in result.stdout


def test_search_csv_never_carries_a_similarity_column(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client())

    result = runner.invoke(
        app,
        [
            "torrents",
            "search",
            "amour",
            "--format",
            "csv",
            "--verbose",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    rows = list(csv.reader(io.StringIO(result.stdout)))
    assert rows[0] == [
        "hash",
        "name",
        "match",
        "state",
        "category",
        "size",
        "progress",
        "ratio",
    ]


# --- AC4 mechanism is the constant, demonstrated by a temporary defect ----
#
# Reverting the CLI command to pass `without_inspection_criteria(filters)`
# into `search_torrents` (silently dropping the INSPECT-only criteria
# before the engine ever sees them) makes
# `test_search_rejects_every_inspection_only_filter` above go red --
# pasted in the Builder's report rather than kept as a test, since the
# defect it demonstrates lives in the CLI command body, not in a
# reusable assertion.
