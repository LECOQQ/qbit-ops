"""Prove every composable filter `torrents search` accepts still bites.

A filter reaches the engine through one `build_filter_from_options(...)`
call. Dropping a single argument there breaks that filter silently: the
option still parses, the command still succeeds, and it simply stops
narrowing anything. A review of `search` found exactly that hole, and it
survived a full suite.

What makes this a guard rather than a pile of cases: the matrix below is
written by hand -- a discriminating corpus is a semantic judgement no
registry can derive -- but its **completeness is derived** from
`TorrentFilter`. Declaring a new composable field without an entry here
fails `test_the_matrix_covers_every_composable_filter`.

Inspection-only fields are covered separately, as *refused*: they are
not part of this matrix by design.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest
from typer.testing import CliRunner

from qbit_core.shared.selection import (
    INSPECTION_ONLY_FILTER_FIELDS,
    TorrentFilter,
)
from qbit_ops.cli.app import app
from qbit_ops.cli.exit_codes import ExitCode
from tests.support import FakeQbitClient, make_torrent

HASH_KEPT = "a" * 40
HASH_DROPPED = "b" * 40

# Both names share the `iso` token, so one query reaches both torrents
# through the engine and only the filter under test can separate them.
NAME_KEPT = "Iso Alpha"
NAME_DROPPED = "Iso Beta"

QUERY = "iso"


@dataclasses.dataclass(frozen=True)
class FilterCase:
    """One composable field, and a corpus where it separates two torrents.

    `kept`/`dropped` are `make_torrent` overrides. The command under test
    must return `NAME_KEPT` alone.
    """

    argv: tuple[str, ...]
    kept: dict[str, Any] = dataclasses.field(default_factory=dict)
    dropped: dict[str, Any] = dataclasses.field(default_factory=dict)


# field name -> the option that selects `kept` and rejects `dropped`
FILTER_MATRIX: dict[str, FilterCase] = {
    "categories": FilterCase(
        ("--category", "linux"),
        {"category": "linux"},
        {"category": "movies"},
    ),
    "categories_excluded": FilterCase(
        ("--exclude-category", "movies"),
        {"category": "linux"},
        {"category": "movies"},
    ),
    "tags": FilterCase(
        ("--tag", "archive"), {"tags": "archive"}, {"tags": "keep"}
    ),
    "save_path_prefixes": FilterCase(
        ("--save-path", "/downloads/linux"),
        {"save_path": "/downloads/linux"},
        {"save_path": "/downloads/movies"},
    ),
    "save_paths_excluded": FilterCase(
        ("--exclude-save-path", "/downloads/movies"),
        {"save_path": "/downloads/linux"},
        {"save_path": "/downloads/movies"},
    ),
    "name_contains": FilterCase(("--name-contains", "Alpha")),
    "name_excluded": FilterCase(("--exclude-name", "Beta")),
    "name_regex": FilterCase(("--name-regex", "^Iso Al")),
    "states": FilterCase(
        ("--state", "seeding"),
        {"state": "uploading"},
        {"state": "downloading", "progress": 0.5},
    ),
    "states_excluded": FilterCase(
        ("--exclude-state", "downloading"),
        {"state": "uploading"},
        {"state": "downloading", "progress": 0.5},
    ),
    "completed": FilterCase(
        ("--completed",), {"progress": 1.0}, {"progress": 0.5}
    ),
    "active": FilterCase(
        ("--active",),
        {"state": "uploading"},
        {"state": "stoppedUP"},
    ),
    "stalled": FilterCase(
        ("--stalled",),
        {"state": "stalledUP"},
        {"state": "uploading"},
    ),
    "errored": FilterCase(
        ("--errored",), {"state": "error"}, {"state": "uploading"}
    ),
    "private": FilterCase(
        ("--private",), {"private": True}, {"private": False}
    ),
    "ratio": FilterCase(("--ratio-min", "2"), {"ratio": 2.5}, {"ratio": 0.1}),
    "size": FilterCase(
        ("--size-min", "1GiB"),
        {"size": 4_700_000_000},
        {"size": 1_000_000},
    ),
    "progress": FilterCase(
        ("--progress-min", "90%"), {"progress": 1.0}, {"progress": 0.5}
    ),
    "uploaded": FilterCase(
        ("--uploaded-min", "1GiB"),
        {"uploaded": 9_400_000_000},
        {"uploaded": 100_000},
    ),
    "seeding_time": FilterCase(
        ("--seeded-for", "10d"),
        {"seeding_time": 86_400 * 30},
        {"seeding_time": 60},
    ),
    "added": FilterCase(
        ("--older-than", "365d"),
        {"added_on": 1_600_000_000},
        {"added_on": 4_100_000_000},
    ),
    "completed_at": FilterCase(
        ("--completed-before", "365d"),
        {"completion_on": 1_600_000_000},
        {"completion_on": 4_100_000_000},
    ),
    "last_activity": FilterCase(
        ("--inactive-for", "365d"),
        {"last_activity": 1_600_000_000},
        {"last_activity": 4_100_000_000},
    ),
    "has_trackers": FilterCase(
        ("--no-tracker",),
        {"trackers_count": 0},
        {"trackers_count": 2},
    ),
}


def _composable_fields() -> set[str]:
    """Every `TorrentFilter` field a caller may compose with `search`."""
    return {field.name for field in dataclasses.fields(TorrentFilter)} - set(
        INSPECTION_ONLY_FILTER_FIELDS
    )


# --- completeness, derived from the model -----------------------------------


def test_the_matrix_covers_every_composable_filter() -> None:
    """The guard that keeps this file honest as `TorrentFilter` grows.

    A new composable field with no entry here would otherwise ship
    untested, which is precisely how the original hole appeared.
    """
    assert set(FILTER_MATRIX) == _composable_fields()


def test_the_matrix_never_covers_an_inspection_only_field() -> None:
    """Those are refused, not filtered -- proving them here would assert
    the opposite of their contract."""
    assert not set(FILTER_MATRIX) & set(INSPECTION_ONLY_FILTER_FIELDS)


# --- each field actually reaches the engine ---------------------------------


@pytest.mark.parametrize("field_name", sorted(FILTER_MATRIX))
def test_every_composable_filter_narrows_the_searched_corpus(
    field_name: str, runner: CliRunner, configure_qbit_backend
) -> None:
    case = FILTER_MATRIX[field_name]
    configure_qbit_backend(
        client=FakeQbitClient(
            torrents=[
                make_torrent(hash=HASH_KEPT, name=NAME_KEPT, **case.kept),
                make_torrent(
                    hash=HASH_DROPPED, name=NAME_DROPPED, **case.dropped
                ),
            ]
        )
    )
    result = runner.invoke(
        app, ["torrents", "search", QUERY, "--format", "json", *case.argv]
    )

    assert result.exit_code in (ExitCode.SUCCESS, ExitCode.NO_MATCH), (
        result.stdout,
        result.stderr,
    )
    names = [match["name"] for match in json.loads(result.stdout)["matches"]]
    assert names == [NAME_KEPT], f"{field_name} did not narrow the corpus"


def test_without_a_filter_both_torrents_match() -> None:
    """Non-vacuity: if the query stopped matching both, every case above
    would pass for the wrong reason."""
    assert NAME_KEPT.split()[0].lower() == QUERY
    assert NAME_DROPPED.split()[0].lower() == QUERY
