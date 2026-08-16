"""Test `torrents throttle`, the per-torrent rate-limit bulk action.

See `.agents/features/bulk-throttle/SPEC.md`. Three layers:
`parse_rate`, which owns the mandatory-unit rule; domain planning and
applying; and the CLI command, routed through
`_run_bulk_torrent_action` exactly like the other bulk actions.

The machine payload gets its own section: the whole point of the
feature is an external caller that throttles on a signal and releases
on its lift, and its failure mode is a limit set and never removed. It
verifies by reading back what was applied, so the payload carrying the
applied limits is a contract, not a convenience.
"""

import json

import pytest
from typer.testing import CliRunner

from qbit_core.errors import InvalidInputError
from qbit_core.features.torrents import (
    apply_bulk_torrent_action,
    build_torrent_filter,
    plan_bulk_torrent_action,
)
from qbit_core.shared.execution import (
    MUTATION_RISK,
    MutationOperation,
    MutationRisk,
)
from qbit_core.shared.parsers import UNLIMITED_RATE, parse_rate
from qbit_core.shared.selection import TorrentFilter
from qbit_ops.cli.app import app
from qbit_ops.cli.exit_codes import ExitCode
from tests.support import FakeQbitClient, make_torrent

TORRENT_A = "abc123def456000000000000000000000000000a"
TORRENT_B = "abc987fed654000000000000000000000000000b"


def _category_filter(name: str) -> TorrentFilter:
    """Build a category-only filter, the way the CLI would."""
    return build_torrent_filter(categories=[name])


# --- Acceptance 1: a size with a unit, or the `unlimited` keyword ----------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("500KB", 500_000),
        ("1MB", 1_000_000),
        ("1MiB", 1_048_576),
        ("1.5GiB", 1_610_612_736),
        # `/s` and `ps` are decoration: a rate is per second either way.
        ("500KB/s", 500_000),
        ("500KBps", 500_000),
        ("unlimited", UNLIMITED_RATE),
        ("UNLIMITED", UNLIMITED_RATE),
    ],
)
def test_parse_rate_accepts_a_unit_or_the_keyword(
    value: str, expected: int
) -> None:
    assert parse_rate(value) == expected


# --- Acceptance 2: a bare number is refused, and the message suggests -----
# --- the unit -------------------------------------------------------------


@pytest.mark.parametrize("value", ["500", "1024", "1.5"])
def test_parse_rate_refuses_a_bare_number(value: str) -> None:
    with pytest.raises(InvalidInputError, match="needs a unit"):
        parse_rate(value)


def test_parse_rate_suggests_the_kilobyte_form_it_probably_meant() -> None:
    with pytest.raises(InvalidInputError, match="Did you mean '500KB'"):
        parse_rate("500")


def test_parse_rate_keeps_the_case_that_was_typed_in_its_message() -> None:
    """`500M` is ambiguous, and the operator must recognise their own
    input in the refusal -- quoting back a lowercased `500m` would read
    as a different value."""
    with pytest.raises(InvalidInputError, match="'500M'"):
        parse_rate("500M")


# --- Acceptance 3: zero is refused, whatever its spelling -----------------


@pytest.mark.parametrize("value", ["0", "0KB", "0MiB", "0.4b"])
def test_parse_rate_refuses_every_spelling_of_zero(value: str) -> None:
    """qBittorrent reads zero as "no limit", a human reads it as "no
    bytes". One spelling only, and it is the word."""
    with pytest.raises(InvalidInputError, match="unlimited"):
        parse_rate(value)


# --- Acceptance 4: neither option is a refusal, before any mutation -------


def test_throttle_without_a_limit_refuses_before_any_api_call(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(torrents=[make_torrent(hash=TORRENT_A)])
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["torrents", "throttle", "--all"])

    assert result.exit_code == ExitCode.ERROR
    assert "--down" in result.stderr
    assert client.download_limits == []
    assert client.upload_limits == []
    assert client.torrents_info_calls == 0


# --- Acceptance 5: one option leaves the other direction untouched --------


def test_download_only_throttle_never_touches_the_upload_limit() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, dl_limit=0, up_limit=0)]
    )

    plan = plan_bulk_torrent_action(
        client=client,
        action="throttle",
        select_all=True,
        download_limit=500_000,
    )
    apply_bulk_torrent_action(client, plan)

    assert client.download_limits == [([TORRENT_A], 500_000)]
    assert client.upload_limits == []


def test_upload_only_throttle_never_touches_the_download_limit() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, dl_limit=0, up_limit=0)]
    )

    plan = plan_bulk_torrent_action(
        client=client,
        action="throttle",
        select_all=True,
        upload_limit=1_000_000,
    )
    apply_bulk_torrent_action(client, plan)

    assert client.upload_limits == [([TORRENT_A], 1_000_000)]
    assert client.download_limits == []


def test_a_matching_upload_limit_never_skips_a_real_download_change() -> None:
    """The skip test covers only the directions actually asked for. A
    torrent already at the upload limit still gets its download limit
    set when `--down` names a new value."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, dl_limit=0, up_limit=1_000_000)]
    )

    plan = plan_bulk_torrent_action(
        client=client,
        action="throttle",
        select_all=True,
        download_limit=500_000,
    )

    assert [change.hash for change in plan.changes] == [TORRENT_A]


def test_both_limits_are_applied_when_both_are_requested() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, dl_limit=0, up_limit=0)]
    )

    plan = plan_bulk_torrent_action(
        client=client,
        action="throttle",
        select_all=True,
        download_limit=500_000,
        upload_limit=1_000_000,
    )
    apply_bulk_torrent_action(client, plan)

    assert client.download_limits == [([TORRENT_A], 500_000)]
    assert client.upload_limits == [([TORRENT_A], 1_000_000)]


def test_releasing_both_directions_sends_zero_to_each() -> None:
    """`unlimited` is a qbit-ops word; the wire value is qBittorrent's
    zero."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A, dl_limit=500_000, up_limit=250_000)
        ]
    )

    plan = plan_bulk_torrent_action(
        client=client,
        action="throttle",
        select_all=True,
        download_limit=UNLIMITED_RATE,
        upload_limit=UNLIMITED_RATE,
    )
    apply_bulk_torrent_action(client, plan)

    assert client.download_limits == [([TORRENT_A], 0)]
    assert client.upload_limits == [([TORRENT_A], 0)]


# --- Acceptance 6: the payload carries the applied limits -----------------


def test_json_payload_carries_the_limit_that_was_applied(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, dl_limit=0, up_limit=0)]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "throttle",
            "--all",
            "--down",
            "500KB",
            "--no-dry-run",
            "--format",
            "json",
        ],
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == ExitCode.SUCCESS
    assert payload["action"] == "throttle"
    assert payload["download_limit"] == 500_000
    assert payload["hashes"] == [TORRENT_A]
    assert payload["status"] == "applied"
    assert payload["applied"] is True
    assert payload["dry_run"] is False


def test_an_untouched_direction_is_null_not_zero(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """`0` already means unlimited on the wire, so it cannot also mean
    "not requested" -- a caller reading `0` would believe it had
    released a limit it never named."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, dl_limit=0, up_limit=0)]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "throttle",
            "--all",
            "--down",
            "500KB",
            "--format",
            "json",
        ],
    )

    payload = json.loads(result.stdout)
    assert payload["upload_limit"] is None
    assert payload["download_limit"] == 500_000


def test_releasing_a_limit_reports_zero_matching_what_qbittorrent_shows(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """The payload uses qBittorrent's own encoding, so a caller can
    compare it straight against `torrents list` rather than translate."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A, dl_limit=500_000, up_limit=500_000)
        ]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "throttle",
            "--all",
            "--down",
            "unlimited",
            "--up",
            "unlimited",
            "--no-dry-run",
            "--format",
            "json",
        ],
    )

    payload = json.loads(result.stdout)
    assert payload["download_limit"] == 0
    assert payload["upload_limit"] == 0
    assert payload["applied"] is True


# --- Acceptance 7: dry-run is the default and mutates nothing -------------


def test_dry_run_is_the_default_and_calls_no_mutation_api(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, dl_limit=0, up_limit=0)]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "throttle", "--all", "--down", "500KB"]
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.download_limits == []
    assert client.upload_limits == []


# --- Acceptance 8: already at the limit is NO_CHANGES, not NO_MATCH -------


def test_a_torrent_already_at_the_requested_limit_is_skipped() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, dl_limit=500_000, up_limit=0)]
    )

    plan = plan_bulk_torrent_action(
        client=client,
        action="throttle",
        select_all=True,
        download_limit=500_000,
    )

    assert plan.matched == 1
    assert plan.changes == ()
    assert [skip.reason for skip in plan.skipped] == ["already_at_limit"]


def test_already_at_the_limit_reports_no_changes_not_no_match(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """The distinction matters to a caller: `no_match` means the filter
    found nothing, `no_changes` means the throttle is already in force."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, dl_limit=500_000, up_limit=0)]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "throttle",
            "--all",
            "--down",
            "500KB",
            "--no-dry-run",
            "--format",
            "json",
        ],
    )

    payload = json.loads(result.stdout)
    assert payload["status"] == "no_changes"
    assert payload["matched"] == 1
    assert payload["applied"] is False
    assert client.download_limits == []


def test_an_unmatched_filter_reports_no_match(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(torrents=[])
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "throttle",
            "--all",
            "--down",
            "500KB",
            "--format",
            "json",
        ],
    )

    payload = json.loads(result.stdout)
    assert payload["status"] == "no_match"
    assert result.exit_code == ExitCode.NO_MATCH


# --- Acceptance 9: registered as a low-risk mutation ---------------------


def test_throttle_is_registered_as_a_low_risk_mutation() -> None:
    assert (
        MUTATION_RISK[MutationOperation.TORRENTS_THROTTLE] is MutationRisk.LOW
    )


def test_low_risk_means_a_non_interactive_apply_never_refuses(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """The external caller runs headless. A confirmation prompt it could
    not answer would make the feature unusable for its only use case."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, dl_limit=0, up_limit=0)]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "throttle", "--all", "--down", "500KB", "--no-dry-run"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.download_limits == [([TORRENT_A], 500_000)]


# --- Selection: the same grammar as every other bulk action --------------


def test_a_hash_selects_exactly_one_torrent(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A, dl_limit=0, up_limit=0),
            make_torrent(hash=TORRENT_B, dl_limit=0, up_limit=0),
        ]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "throttle",
            "--hash",
            TORRENT_A,
            "--down",
            "500KB",
            "--no-dry-run",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.download_limits == [([TORRENT_A], 500_000)]


def test_a_category_filter_throttles_only_that_category() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A, category="cross-seed", dl_limit=0),
            make_torrent(hash=TORRENT_B, category="movies", dl_limit=0),
        ]
    )

    plan = plan_bulk_torrent_action(
        client=client,
        action="throttle",
        filters=_category_filter("cross-seed"),
        upload_limit=1_000_000,
    )
    apply_bulk_torrent_action(client, plan)

    assert client.upload_limits == [([TORRENT_A], 1_000_000)]


def test_no_selector_at_all_is_refused(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(torrents=[make_torrent(hash=TORRENT_A)])
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "throttle", "--down", "500KB", "--no-dry-run"]
    )

    assert result.exit_code == ExitCode.ERROR
    assert client.download_limits == []


# --- Failure surfacing ----------------------------------------------------


def test_a_failing_limit_call_is_reported_not_swallowed(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, dl_limit=0, up_limit=0)],
        set_limit_error=RuntimeError("qBittorrent said no"),
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "throttle", "--all", "--down", "500KB", "--no-dry-run"],
    )

    assert result.exit_code == ExitCode.ERROR
    assert "throttle" in result.stderr
