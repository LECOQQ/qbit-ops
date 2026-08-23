"""Test `--tracker-health`: its verdict, its parity with `explain`, and
the API-call budget it must not exceed.

The property that matters most here is not which torrents the filter
selects but that its verdict and `explain torrent`'s come out of a
single computation. Two implementations agreeing on the cases someone
thought to test is not the same guarantee.
"""

from typing import Any

import pytest
import typer
from typer.core import TyperGroup
from typer.testing import CliRunner

from qbit_core.errors import InvalidInputError
from qbit_core.features.explain import explain_torrent
from qbit_core.features.torrents import (
    TRACKER_HEALTH_FILTER_VALUES,
    build_torrent_filter,
    inspect_filtered_torrents,
    matches_tracker_health,
    select_torrents,
)
from qbit_core.features.tracker_status import collect_tracker_status
from qbit_ops.cli.app import app
from tests.support import FakeQbitClient, make_torrent

HASH_A = "a" * 40
HASH_B = "b" * 40
HASH_C = "c" * 40

TRACKER_ONE = "https://one.example/announce"
TRACKER_TWO = "https://two.example/announce"

# qBittorrent's own `TrackerStatus` codes, written literally so these
# fixtures never follow a change in the mapping under test.
DISABLED = 0
WORKING = 2
NOT_WORKING = 4
UNRECOGNIZED = 99

HEALTH_VALUES = sorted(health.value for health in TRACKER_HEALTH_FILTER_VALUES)


def _endpoint(url: str, status: int) -> dict[str, Any]:
    return {"url": url, "status": status}


def _client(
    trackers: list[dict[str, Any]],
    *,
    lookup_fails: bool = False,
    state: str = "stalledDL",
) -> FakeQbitClient:
    """One torrent, its endpoints, and whether reading them fails."""
    return FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1", state=state)],
        trackers_by_hash={HASH_A: trackers},
        tracker_error_hashes={HASH_A} if lookup_fails else set(),
    )


# --- The single verdict path ------------------------------------------------


def _filter_verdict(
    trackers: list[dict[str, Any]], *, lookup_fails: bool = False
) -> str | None:
    """The one health value that selects this torrent, or `None`.

    Asks the filter's own decision function once per value of the
    vocabulary, so "matches nothing" is observed rather than assumed.
    """
    selected = [
        value
        for value in HEALTH_VALUES
        if matches_tracker_health(
            build_torrent_filter(tracker_health=(value,)),
            tuple(trackers),
            lookup_failed=lookup_fails,
        )
    ]
    assert len(selected) <= 1, selected
    return selected[0] if selected else None


def _explain_verdict(
    trackers: list[dict[str, Any]], *, lookup_fails: bool = False
) -> str | None:
    """The tracker health `explain torrent` reports, or `None`."""
    report = explain_torrent(
        _client(trackers, lookup_fails=lookup_fails), HASH_A
    )
    assert report is not None
    reported = [
        item.value
        for finding in report.findings
        for item in finding.evidence
        if item.code == "tracker_health"
    ]
    assert all(isinstance(value, str) for value in reported)
    return str(reported[0]) if reported else None


@pytest.mark.parametrize(
    ("case", "trackers", "lookup_fails", "expected"),
    [
        # The discriminating case: dropping disabled endpoints would
        # empty this set and turn a real verdict into "no verdict".
        (
            "only a disabled endpoint",
            [_endpoint(TRACKER_ONE, DISABLED)],
            False,
            "disabled",
        ),
        ("no endpoint at all", [], False, None),
        (
            "tracker lookup failed",
            [_endpoint(TRACKER_ONE, WORKING)],
            True,
            None,
        ),
        (
            "one failing, one working",
            [
                _endpoint(TRACKER_ONE, NOT_WORKING),
                _endpoint(TRACKER_TWO, WORKING),
            ],
            False,
            "warning",
        ),
        (
            "every endpoint failing",
            [_endpoint(TRACKER_ONE, NOT_WORKING)],
            False,
            "critical",
        ),
        (
            "an unrecognized status",
            [_endpoint(TRACKER_ONE, UNRECOGNIZED)],
            False,
            "unknown",
        ),
        (
            "a working endpoint next to a disabled one",
            [
                _endpoint(TRACKER_ONE, WORKING),
                _endpoint(TRACKER_TWO, DISABLED),
            ],
            False,
            "healthy",
        ),
    ],
)
def test_the_filter_and_explain_never_disagree(
    case: str,
    trackers: list[dict[str, Any]],
    lookup_fails: bool,
    expected: str | None,
) -> None:
    """Same endpoint set, same counting, same aggregation function.

    An operator who sees a torrent come out of `--tracker-health X` can
    ask `explain torrent` why and must be told the same thing.
    """
    assert _filter_verdict(trackers, lookup_fails=lookup_fails) == expected
    assert _explain_verdict(trackers, lookup_fails=lookup_fails) == expected


def test_a_torrent_without_a_verdict_matches_no_health_value() -> None:
    """Not knowing something about a torrent must never be enough to
    put it in a mutation's target set."""
    for trackers, lookup_fails in (([], False), ([], True)):
        for value in HEALTH_VALUES:
            assert not matches_tracker_health(
                build_torrent_filter(tracker_health=(value,)),
                tuple(trackers),
                lookup_failed=lookup_fails,
            )


# --- Vocabulary, rejected before any API call -------------------------------


@pytest.mark.parametrize("value", HEALTH_VALUES)
def test_every_value_a_torrent_can_carry_is_accepted(value: str) -> None:
    assert build_torrent_filter(tracker_health=[value]).tracker_health == (
        value,
    )


def test_the_vocabulary_is_exactly_the_five_per_torrent_verdicts() -> None:
    assert HEALTH_VALUES == [
        "critical",
        "disabled",
        "healthy",
        "unknown",
        "warning",
    ]


def test_unavailable_is_refused_before_any_api_call() -> None:
    """`unavailable` describes a whole report whose collection failed,
    never one torrent, so no torrent could ever carry it."""
    client = _client([_endpoint(TRACKER_ONE, WORKING)])

    with pytest.raises(ValueError):
        build_torrent_filter(tracker_health=["unavailable"])

    assert client.torrents_info_calls == 0
    assert client.torrents_trackers_calls == 0


def test_no_tracker_with_tracker_health_is_refused_before_any_call() -> None:
    client = _client([_endpoint(TRACKER_ONE, WORKING)])

    with pytest.raises(InvalidInputError):
        build_torrent_filter(has_trackers=False, tracker_health=["critical"])

    assert client.torrents_info_calls == 0
    assert client.torrents_trackers_calls == 0


def test_repeating_the_option_is_never_a_contradiction() -> None:
    filters = build_torrent_filter(tracker_health=["critical", "warning"])

    assert filters.tracker_health == ("critical", "warning")


# --- Selection over the real surfaces ---------------------------------------


def _library() -> FakeQbitClient:
    return FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="Failing"),
            make_torrent(hash=HASH_B, name="Working"),
            make_torrent(hash=HASH_C, name="Mixed"),
        ],
        trackers_by_hash={
            HASH_A: [_endpoint(TRACKER_ONE, NOT_WORKING)],
            HASH_B: [_endpoint(TRACKER_ONE, WORKING)],
            HASH_C: [
                _endpoint(TRACKER_ONE, NOT_WORKING),
                _endpoint(TRACKER_TWO, WORKING),
            ],
        },
    )


def _selected_names(client: FakeQbitClient, **options: Any) -> set[str]:
    selection, _ = select_torrents(client, build_torrent_filter(**options))
    return {snapshot.name for snapshot in selection.matched}


def test_the_filter_selects_torrents_by_their_own_endpoints() -> None:
    assert _selected_names(_library(), tracker_health=["critical"]) == {
        "Failing"
    }
    assert _selected_names(_library(), tracker_health=["warning"]) == {"Mixed"}
    assert _selected_names(_library(), tracker_health=["healthy"]) == {
        "Working"
    }


def test_several_values_combine_with_or() -> None:
    assert _selected_names(
        _library(), tracker_health=["critical", "healthy"]
    ) == {"Failing", "Working"}


def test_trackers_list_narrows_its_report_by_tracker_health() -> None:
    report = collect_tracker_status(
        _library(), build_torrent_filter(tracker_health=["critical"])
    )

    assert report.matched_torrents == 1
    assert {aggregate.identity for aggregate in report.trackers} == {
        "one.example"
    }


# --- API-call budget --------------------------------------------------------


def test_tracker_and_tracker_health_together_cost_one_inspect_pass() -> None:
    """Both criteria are answered from the same tracker data. A second
    pass would double the cost of the most expensive stage there is.
    """
    client = _library()

    _selected_names(
        client, trackers=["one.example"], tracker_health=["critical"]
    )

    # 1 SELECT + 1 bulk `include_trackers` probe this fake ignores --
    # see `qbit_core.shared.inspection._fetch_trackers_in_bulk`.
    assert client.torrents_info_calls == 2
    assert client.torrents_trackers_calls == 3


def test_tracker_health_inspects_only_torrents_surviving_cheap_filters() -> (
    None
):
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="Kept", category="movies"),
            make_torrent(hash=HASH_B, name="Dropped", category="music"),
        ],
        trackers_by_hash={
            HASH_A: [_endpoint(TRACKER_ONE, NOT_WORKING)],
            HASH_B: [_endpoint(TRACKER_ONE, NOT_WORKING)],
        },
    )

    _selected_names(client, categories=["movies"], tracker_health=["critical"])

    assert client.torrents_trackers_calls == 1


def test_trackers_list_with_volume_costs_one_lookup_per_torrent() -> None:
    """The count `trackers list` made before it reported any volume."""
    client = _library()

    collect_tracker_status(
        client, build_torrent_filter(tracker_health=["critical"])
    )

    # 1 SELECT + 1 bulk `include_trackers` probe this fake ignores --
    # see `qbit_core.shared.inspection._fetch_trackers_in_bulk`.
    assert client.torrents_info_calls == 2
    assert client.torrents_trackers_calls == 3


# --- The mutation surface ---------------------------------------------------


def test_tracker_health_alone_is_a_valid_mutation_selector(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """A mutation refuses to run without a selector. `--tracker-health`
    must count as one on its own, or the filter is unusable exactly
    where it was meant to be most useful.
    """
    client = _library()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "pause", "--tracker-health", "critical"]
    )

    assert result.exit_code == 0
    assert client.paused_hashes == []  # dry-run by default


def test_a_mutation_targets_only_the_torrents_the_verdict_selects(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _library()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "pause", "--tracker-health", "critical", "--no-dry-run"],
    )

    assert result.exit_code == 0
    assert client.paused_hashes == [[HASH_A]]


def test_the_four_tracker_mutations_do_not_offer_the_filter() -> None:
    """Selecting torrents by the health of their trackers in order to
    act on those same trackers would be circular."""
    root = typer.main.get_command(app)
    assert isinstance(root, TyperGroup)
    trackers_group = root.commands["trackers"]
    assert isinstance(trackers_group, TyperGroup)

    for name in ("add-if-present", "remove", "replace", "replace-passkey"):
        flags = {
            flag
            for parameter in trackers_group.commands[name].params
            for flag in parameter.opts
        }
        assert "--tracker-health" not in flags, name


def test_a_filtered_inspection_honours_the_verdict_too() -> None:
    """`torrents inspect` resolves filters through its own loop. It
    exposes no `--tracker-health` option today, but a filter it cannot
    honour must be refused or applied -- never silently ignored.
    """
    report = inspect_filtered_torrents(
        _library(), build_torrent_filter(tracker_health=["critical"])
    )

    assert [torrent["name"] for torrent in report["torrents"]] == ["Failing"]
