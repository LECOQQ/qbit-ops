"""Bounds, shape and read-only guarantee of the experimental MCP surface.

Written against `qbit_ops.mcp.tools`, which never imports the MCP SDK,
so these run on a normal install. The one test that needs the SDK skips
when the `mcp` extra is absent.
"""

from __future__ import annotations

import pytest

from qbit_core.qbit.protocols import (
    QbitDestructiveMutator,
    QbitTorrentMutator,
    QbitTrackerMutator,
)
from qbit_ops.mcp import tools
from tests.support import FakeQbitClient, make_torrent


def _library(count: int) -> FakeQbitClient:
    return FakeQbitClient(
        torrents=[
            make_torrent(
                hash=f"{index:040x}", name=f"T{index}", state="uploading"
            )
            for index in range(1, count + 1)
        ]
    )


def test_find_caps_a_request_larger_than_the_hard_maximum() -> None:
    """The bound is server-side. An agent that asks for five thousand
    torrents gets `MAX_LIMIT`, because a tool result cannot be piped
    through anything before it reaches the model's context.
    """
    result = tools.find_torrents(_library(120), limit=5000)

    assert result["returned"] == tools.MAX_LIMIT
    assert result["limit"] == tools.MAX_LIMIT
    assert result["matched"] == 120
    assert result["next_offset"] == tools.MAX_LIMIT


def test_find_defaults_to_a_small_page() -> None:
    result = tools.find_torrents(_library(120))

    assert result["returned"] == tools.DEFAULT_LIMIT
    assert result["matched"] == 120


def test_find_returns_only_drill_down_fields() -> None:
    """Seven fields, not the sixteen a snapshot carries: what a next step
    is chosen from. Every extra field is permanent context cost, so this
    pins the set -- adding one is a decision, never a drift."""
    result = tools.find_torrents(_library(1))

    assert set(result["torrents"][0]) == {
        "hash",
        "name",
        "category",
        "state",
        "state_group",
        "progress",
        "ratio",
    }


def test_summary_size_does_not_grow_with_the_library() -> None:
    """The only tool that reads everything must answer in fixed size --
    otherwise it reintroduces the problem the surface exists to avoid."""
    small = tools.library_summary(_library(1))
    large = tools.library_summary(_library(500))

    assert set(small) == set(large)
    assert set(small["by_state"]) == set(large["by_state"])
    assert large["torrents"] == 500


def test_empty_library_answers_without_failing() -> None:
    empty = FakeQbitClient(torrents=[])

    assert tools.library_summary(empty)["torrents"] == 0
    assert tools.find_torrents(empty)["matched"] == 0
    assert tools.inspect_torrent(empty, "a" * 40) is None


def test_inspect_resolves_a_full_hash_case_insensitively() -> None:
    client = _library(3)
    wanted = f"{2:040x}"

    found = tools.inspect_torrent(client, wanted.upper())

    assert found is not None
    assert found["hash"] == wanted
    assert found["name"] == "T2"


def test_no_mcp_tool_can_mutate() -> None:
    """The read-only guarantee, proven against the client protocols that
    define what mutation *is* -- not against a list of method names
    someone has to remember to update.
    """
    mutating = {
        name
        for protocol in (
            QbitTorrentMutator,
            QbitDestructiveMutator,
            QbitTrackerMutator,
        )
        for name in dir(protocol)
        if not name.startswith("_")
    }
    assert mutating, "expected mutator protocols to expose methods"

    source = (tools.__file__,)
    for path in source:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        for method in mutating:
            assert f".{method}(" not in text, (
                f"qbit_ops/mcp/tools.py calls client.{method}() -- the V0 "
                "surface is read-only by construction"
            )


def test_registered_mcp_tools_are_exactly_the_read_only_four() -> None:
    """Read-only is a property of what is *registered*: a mutating tool
    nobody registers cannot be called."""
    pytest.importorskip("mcp")
    import asyncio

    from qbit_ops.mcp.server import server

    names = {tool.name for tool in asyncio.run(server.list_tools())}

    assert names == {
        "library_summary",
        "find_torrents",
        "inspect_torrent",
        "explain_torrent",
    }


def test_explain_keeps_evidence_and_limitations() -> None:
    """`limitations` is what stops an agent stating a conclusion more
    firmly than the engine did. Dropping it handed the model a bare
    verdict on a product whose promise is evidence, never a guess --
    found by using the surface, not by reading it.
    """
    client = FakeQbitClient(
        torrents=[
            make_torrent(
                hash="c" * 40, name="Stalled", state="stalledUP", progress=1.0
            )
        ],
        trackers_by_hash={
            "c" * 40: [{"url": "https://tracker.example/announce", "status": 2}]
        },
    )

    report = tools.explain(client, "c" * 40)

    assert report is not None
    finding = report["findings"][0]
    assert finding["evidence"], "a finding without its evidence is a bare claim"
    assert set(finding["evidence"][0]) == {"code", "label", "value", "source"}
    assert "limitations" in finding


def test_find_returns_category_and_can_filter_on_it() -> None:
    """Category is the one axis the operator defines himself: it is what
    separates a release from a cross-seed link to that same release.
    Without it, four `inspect_torrent` calls were needed to learn one
    field.
    """
    client = FakeQbitClient(
        torrents=[
            make_torrent(
                hash=f"{index:040x}",
                name=f"T{index}",
                state="uploading",
                category="cross-seed" if index % 2 else "movies",
            )
            for index in range(1, 11)
        ]
    )

    every = tools.find_torrents(client)
    assert "category" in every["torrents"][0]

    filtered = tools.find_torrents(client, category="cross-seed")
    assert filtered["matched"] == 5
    assert {t["category"] for t in filtered["torrents"]} == {"cross-seed"}


def test_find_pages_through_everything_it_reports_as_matched() -> None:
    """Telling a caller that 69 matched and then refusing to show 19 of
    them is half an answer. The cap is a page size, not a dead end:
    depth is paid in calls, never in context.
    """
    client = _library(69)

    first = tools.find_torrents(client)
    assert first["matched"] == 69
    assert first["next_offset"] == first["returned"]

    seen: list[str] = []
    offset: int | None = 0
    while offset is not None:
        page = tools.find_torrents(client, offset=offset)
        seen.extend(torrent["hash"] for torrent in page["torrents"])
        offset = page["next_offset"]

    assert len(seen) == 69
    assert len(set(seen)) == 69, "paging must not repeat or skip a torrent"


def test_inspect_asks_upstream_for_one_torrent_not_the_library() -> None:
    """The output was bounded while the input was not: every lookup
    fetched the whole library. Invisible to the model, very real for the
    instance.
    """
    transferred = 0
    library = [
        make_torrent(hash=f"{index:040x}", name=f"T{index}", state="uploading")
        for index in range(1, 201)
    ]

    class Counting(FakeQbitClient):
        def torrents_info(self, torrent_hashes=None):  # type: ignore[override]
            nonlocal transferred
            result = super().torrents_info(torrent_hashes)
            transferred += len(result)
            return result

    client = Counting(torrents=library)
    tools.inspect_torrent(client, f"{7:040x}")

    assert transferred == 1, "one lookup must not transfer 200 torrents"
