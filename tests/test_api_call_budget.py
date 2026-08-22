"""Hold every bulk action to a declared qBittorrent API call budget.

A planning pass that quietly costs a second library scan is invisible:
nothing fails, nothing slows down enough to notice, and the extra
round-trip only shows up on someone else's instance. `throttle` shipped
that way and was found by accident days later, while reading unrelated
code -- so the count is asserted here rather than left to whoever
happens to look.

Two properties, and the second is the one that matters:

- the **budget** each action declares, so a change to it is a decision
  rather than a drift;
- **independence from library size** -- the same count on 3 torrents and
  on 60. A per-torrent call is the failure mode that actually hurts,
  and it is the one a fixed-size fixture can never catch.

Counts calls, never durations: this is a hermetic fake, so a timing
here would measure the test machine, not the instance.
"""

from typing import Any, get_args

import pytest

from qbit_core.features.torrents import (
    TorrentBulkAction,
    apply_bulk_torrent_action,
    plan_bulk_torrent_action,
)
from tests.support import FakeQbitClient, make_torrent

# Library scans (`torrents_info`) one planning pass is allowed.
#
# `1` is the norm: SELECT already fetches every torrent, and the plan is
# built from the `TorrentSnapshot`s it produced. A `2` must carry the
# reason it cannot read what SELECT already holds.
PLANNING_SCAN_BUDGET: dict[TorrentBulkAction, int] = {
    "pause": 1,
    "resume": 1,
    "start": 1,
    "reannounce": 1,
    "delete": 1,
    "category_set": 1,
    "category_clear": 1,
    "tag_add": 1,
    "tag_remove": 1,
    "throttle": 1,
}

# Mutation calls applying a plan is allowed, whatever the selection size.
# Every action batches: qBittorrent takes a hash list, so a torrent count
# must never reach these numbers.
APPLY_CALL_BUDGET: dict[TorrentBulkAction, int] = {
    "pause": 1,
    "resume": 1,
    "start": 1,
    "reannounce": 1,
    "delete": 1,
    "category_set": 1,
    "category_clear": 1,
    "tag_add": 1,
    "tag_remove": 1,
    # 2: one call per direction, and only for a direction that was named.
    "throttle": 2,
}

# What each action needs to produce a real change: a torrent it applies
# to, and the plan arguments that make it non-empty.
_Fixture = tuple[dict[str, Any], dict[str, Any]]

ACTION_FIXTURE: dict[TorrentBulkAction, _Fixture] = {
    "pause": ({"state": "uploading"}, {}),
    "resume": ({"state": "stoppedUP"}, {}),
    "start": ({"state": "stoppedUP"}, {}),
    "reannounce": ({}, {}),
    "delete": ({}, {}),
    "category_set": ({"category": ""}, {"category": "movies"}),
    "category_clear": ({"category": "movies"}, {}),
    "tag_add": ({"tags": ""}, {"tags": ["fresh"]}),
    "tag_remove": ({"tags": "stale"}, {"tags": ["stale"]}),
    "throttle": (
        {"dl_limit": 0, "up_limit": 0},
        {"download_limit": 500_000, "upload_limit": 1_000_000},
    ),
}

ALL_ACTIONS: tuple[TorrentBulkAction, ...] = get_args(TorrentBulkAction)

# Read-only calls a plan may legitimately make; everything else recorded
# during `apply` is a mutation and counts against the apply budget.
_READ_ONLY_METHODS = frozenset(
    {"torrents_info", "torrents_categories", "torrents_trackers"}
)


def _client(action: TorrentBulkAction, *, torrents: int) -> FakeQbitClient:
    overrides, _ = ACTION_FIXTURE[action]
    return FakeQbitClient(
        torrents=[
            make_torrent(hash=f"{index:040x}", name=f"T{index}", **overrides)
            for index in range(torrents)
        ]
    )


def _plan(client: FakeQbitClient, action: TorrentBulkAction) -> Any:
    _, plan_kwargs = ACTION_FIXTURE[action]
    return plan_bulk_torrent_action(
        client=client, action=action, select_all=True, **plan_kwargs
    )


# --- The table is exhaustive, or the gate is decorative -------------------


@pytest.mark.parametrize(
    "budget", [PLANNING_SCAN_BUDGET, APPLY_CALL_BUDGET, ACTION_FIXTURE]
)
def test_every_bulk_action_declares_a_budget(budget: dict[Any, Any]) -> None:
    """A new action must state its cost. Without this, the gate silently
    stops covering whatever ships next."""
    assert set(budget) == set(ALL_ACTIONS)


# --- Planning -------------------------------------------------------------


@pytest.mark.parametrize("action", ALL_ACTIONS)
def test_planning_stays_within_its_declared_scan_budget(
    action: TorrentBulkAction,
) -> None:
    client = _client(action, torrents=3)

    _plan(client, action)

    assert client.torrents_info_calls == PLANNING_SCAN_BUDGET[action]


@pytest.mark.parametrize("action", ALL_ACTIONS)
def test_planning_cost_does_not_grow_with_the_library(
    action: TorrentBulkAction,
) -> None:
    """The property a fixed-size fixture cannot express: twenty times the
    torrents must not mean twenty times the calls."""
    small = _client(action, torrents=3)
    large = _client(action, torrents=60)

    _plan(small, action)
    _plan(large, action)

    assert large.torrents_info_calls == small.torrents_info_calls


# --- Applying -------------------------------------------------------------


@pytest.mark.parametrize("action", ALL_ACTIONS)
def test_applying_stays_within_its_declared_mutation_budget(
    action: TorrentBulkAction,
) -> None:
    client = _client(action, torrents=3)
    plan = _plan(client, action)
    assert plan.changes, f"{action}: fixture produced no change to apply"

    before = len(client.calls)
    apply_bulk_torrent_action(client, plan)
    mutations = [
        name
        for name, _, _ in client.calls[before:]
        if name not in _READ_ONLY_METHODS
    ]

    assert len(mutations) == APPLY_CALL_BUDGET[action], mutations


@pytest.mark.parametrize("action", ALL_ACTIONS)
def test_applying_batches_rather_than_calling_once_per_torrent(
    action: TorrentBulkAction,
) -> None:
    """qBittorrent takes a hash list. An implementation looping over the
    selection would work, pass every behavioural test, and hammer the
    instance."""
    client = _client(action, torrents=60)
    plan = _plan(client, action)
    assert len(plan.changes) == 60

    before = len(client.calls)
    apply_bulk_torrent_action(client, plan)
    mutations = [
        name
        for name, _, _ in client.calls[before:]
        if name not in _READ_ONLY_METHODS
    ]

    assert len(mutations) == APPLY_CALL_BUDGET[action], mutations
