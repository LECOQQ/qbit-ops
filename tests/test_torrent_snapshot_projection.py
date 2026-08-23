"""Force every `TorrentSnapshot` field to be classified, per projection.

`docs/ARCHITECTURE.md` promises one representation per measure. The
*model* is unique (`TorrentSnapshot`), but nothing forced its several
*serializations* -- `cli.rendering.torrent_snapshot_to_dict`,
`mcp.tools._brief`, `mcp.tools._full` -- to stay accounted for: a field
could start or stop appearing in one of them without anything noticing.

Same motif as `tests/test_search_filter_parity.py`, applied to a
different model: completeness is derived from `dataclasses.fields`,
but which projection exposes which field is a judgement call written
by hand into `FIELD_PROJECTIONS` below. A divergence between
projections can be legitimate (a narrower surface, on purpose); what
this file exists to prevent is a divergence nobody decided.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

from qbit_core.shared.torrent_states import TorrentSnapshot
from qbit_ops.cli import rendering
from qbit_ops.mcp import tools

CLI_LIST = "cli_list"
MCP_BRIEF = "mcp_brief"
MCP_FULL = "mcp_full"

# field name -> the projections that expose it. An empty set is a
# deliberate, total omission -- not an oversight this file failed to
# notice.
#
# `tags` is omitted from all three: `cli.rendering.torrent_snapshot_to_dict`
# never carried it (predates this file), and neither MCP projection
# added it either. Not a safety concern (tags are not secret), but an
# undocumented gap -- flagged for a human decision, not resolved here.
FIELD_PROJECTIONS: dict[str, frozenset[str]] = {
    "hash": frozenset({CLI_LIST, MCP_BRIEF, MCP_FULL}),
    "name": frozenset({CLI_LIST, MCP_BRIEF, MCP_FULL}),
    "category": frozenset({CLI_LIST, MCP_BRIEF, MCP_FULL}),
    "state": frozenset({CLI_LIST, MCP_BRIEF, MCP_FULL}),
    "state_group": frozenset({MCP_BRIEF, MCP_FULL}),
    "size": frozenset({CLI_LIST, MCP_FULL}),
    "progress": frozenset({CLI_LIST, MCP_BRIEF, MCP_FULL}),
    "ratio": frozenset({CLI_LIST, MCP_BRIEF, MCP_FULL}),
    "download_rate": frozenset({CLI_LIST, MCP_FULL}),
    "upload_rate": frozenset({CLI_LIST, MCP_FULL}),
    "download_limit": frozenset({CLI_LIST, MCP_FULL}),
    "upload_limit": frozenset({CLI_LIST, MCP_FULL}),
    "tags": frozenset(),
    "downloaded": frozenset({MCP_FULL}),
    "uploaded": frozenset({MCP_FULL}),
    "seeding_time": frozenset({MCP_FULL}),
    "added_at": frozenset({MCP_FULL}),
    "completed_at": frozenset({MCP_FULL}),
    "last_activity_at": frozenset({MCP_FULL}),
}


def _sample_snapshot() -> TorrentSnapshot:
    """One snapshot with every field set, so a projection either exposes
    a key or it doesn't -- nothing here depends on a particular value.
    """
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    return TorrentSnapshot(
        hash="a" * 40,
        name="Sample",
        category="movies",
        state="uploading",
        state_group="seeding",
        size=1_000_000,
        progress=1.0,
        ratio=2.5,
        download_rate=0,
        upload_rate=1_000,
        download_limit=0,
        upload_limit=0,
        tags=("archive",),
        downloaded=1_000_000,
        uploaded=2_000_000,
        seeding_time=3_600,
        added_at=moment,
        completed_at=moment,
        last_activity_at=moment,
    )


def _projections(snapshot: TorrentSnapshot) -> dict[str, set[str]]:
    cli_keys = set(
        rendering.torrent_snapshot_to_dict(snapshot, tracker_count=None)
    )
    # `tracker_count` is derived (a per-torrent tracker lookup), not a
    # `TorrentSnapshot` field -- it has no row in `FIELD_PROJECTIONS`.
    cli_keys.discard("tracker_count")
    return {
        CLI_LIST: cli_keys,
        MCP_BRIEF: set(tools._brief(snapshot)),
        MCP_FULL: set(tools._full(snapshot)),
    }


def test_the_table_covers_every_snapshot_field() -> None:
    """The guard that keeps this file honest as `TorrentSnapshot` grows:
    a new field with no entry here would otherwise reach a serializer
    -- or fail to -- with no test ever having made that a decision.
    """
    fields = {field.name for field in dataclasses.fields(TorrentSnapshot)}
    assert set(FIELD_PROJECTIONS) == fields


def test_each_projection_matches_its_declared_classification() -> None:
    actual = _projections(_sample_snapshot())

    for field_name, expected_projections in FIELD_PROJECTIONS.items():
        for projection_name, keys in actual.items():
            is_exposed = field_name in keys
            should_be_exposed = projection_name in expected_projections
            assert is_exposed == should_be_exposed, (
                field_name,
                projection_name,
                "exposed" if is_exposed else "omitted",
                "declared exposed" if should_be_exposed else "declared omitted",
            )
