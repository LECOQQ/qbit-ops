"""What the Overview's two windows may and may not put on screen.

The Trackers window shows a *derived* truth -- activity inferred from
torrent rates, never an announce status read from a tracker. These are
the checks that keep it from being mistaken for the real thing, and the
checks that keep the hue meaning one thing everywhere it appears.
"""

from __future__ import annotations

import pytest
from rich.text import Text

from qbit_ops.tui.formatting import (
    _TRACKER_GLYPHS,
    DOWN_RATE_ACCENT,
    IDLE_RATE_STYLE,
    UP_RATE_ACCENT,
    _format_global_rate,
)
from qbit_ops.tui.state import (
    GRAPH_SLOTS,
    RateHistory,
    TrackerActivityKind,
    build_tracker_breakdown,
)
from qbit_ops.tui.widgets.overview_windows import (
    TRACKER_ACTIVITY_GLYPHS,
    TRACKERS_DISCLAIMER,
    build_trackers_window,
)
from qbit_ops.tui.widgets.rate_graph import build_rate_graph
from tests.support import make_torrent

pytestmark = pytest.mark.tui


def _breakdown(*torrents: dict[str, object]):
    return build_tracker_breakdown(list(torrents))


def _window(*torrents: dict[str, object], history: RateHistory | None = None):
    return build_trackers_window(
        _breakdown(*torrents),
        history or RateHistory(),
        width=80,
        height=24,
    )


def _styles_of(text: Text, needle: str) -> set[str]:
    """Every style covering the first occurrence of `needle`."""
    start = text.plain.index(needle)
    return {
        str(span.style) for span in text.spans if span.start <= start < span.end
    }


# --- two alphabets that cannot be confused -------------------------------


def test_the_activity_alphabet_shares_no_glyph_with_tracker_health() -> None:
    """An operator who has learnt one vocabulary must never read a row
    of the other by resemblance."""
    activity = set(TRACKER_ACTIVITY_GLYPHS.values())
    health = set(_TRACKER_GLYPHS.values())

    assert activity, "the activity alphabet must not be empty"
    assert health, "the health alphabet must not be empty"
    assert not activity & health


def test_every_activity_kind_has_a_glyph_of_its_own() -> None:
    glyphs = [TRACKER_ACTIVITY_GLYPHS[kind] for kind in TrackerActivityKind]

    assert len(glyphs) == len(TrackerActivityKind)
    assert len(set(glyphs)) == len(glyphs)


def test_no_tracker_health_glyph_ever_reaches_the_trackers_window() -> None:
    rendered = _window(
        make_torrent(hash="a" * 40, tracker="http://one.tld/a", upspeed=10),
        make_torrent(hash="b" * 40, tracker="http://two.tld/a", state="error"),
        make_torrent(hash="c" * 40, tracker="** [DHT] **"),
        make_torrent(hash="d" * 40, tracker="http://idle.tld/a"),
    ).plain

    for glyph in set(_TRACKER_GLYPHS.values()):
        assert glyph not in rendered, glyph
    # Non-vacuous: the window genuinely drew its own glyphs.
    assert TRACKER_ACTIVITY_GLYPHS[TrackerActivityKind.SEEDING] in rendered


def test_the_window_names_what_it_does_not_read() -> None:
    rendered = _window(
        make_torrent(hash="a" * 40, tracker="http://one.tld/a")
    ).plain

    assert TRACKERS_DISCLAIMER in rendered
    assert "announce status not read here" in rendered
    # Said once, against the data it qualifies -- not again in the
    # border eleven rows above.
    assert rendered.count("announce status not read here") == 1


# --- the hue names the direction, in all three places --------------------


def test_the_rate_readout_paints_each_direction_in_its_own_hue() -> None:
    both = _format_global_rate(2_000_000, 3_000_000)

    assert _styles_of(both, "↓") == {DOWN_RATE_ACCENT}
    assert _styles_of(both, "↑") == {UP_RATE_ACCENT}


def test_the_graph_paints_each_half_in_its_own_hue() -> None:
    history = RateHistory()
    for _ in range(GRAPH_SLOTS):
        history.record(download=4_000_000, upload=1_000_000)
    graph = build_rate_graph(history, width=76)

    down_half, _, up_half = graph.plain.partition("-60s")
    assert DOWN_RATE_ACCENT in {str(s.style) for s in graph.spans}
    assert UP_RATE_ACCENT in {str(s.style) for s in graph.spans}
    # The two halves are drawn, and drawn apart.
    assert down_half.strip() and up_half.strip()


def test_an_idle_direction_gets_no_hue_rather_than_a_third_one() -> None:
    history = RateHistory()
    for _ in range(GRAPH_SLOTS):
        history.record(download=0, upload=1_000_000)
    graph = build_rate_graph(history, width=76)
    styles = {str(span.style) for span in graph.spans}

    assert UP_RATE_ACCENT in styles
    assert DOWN_RATE_ACCENT not in styles
    assert IDLE_RATE_STYLE in styles


def test_the_tracker_columns_follow_the_same_rule() -> None:
    rendered = _window(
        make_torrent(
            hash="a" * 40,
            tracker="http://one.tld/a",
            upspeed=1_000_000,
            dlspeed=0,
        )
    )

    assert _styles_of(rendered, "976.6 KiB/s") == {UP_RATE_ACCENT}
    idle_zero = rendered.plain.index("0", rendered.plain.index("976.6 KiB/s"))
    covering = {
        str(span.style)
        for span in rendered.spans
        if span.start <= idle_zero < span.end
    }
    assert DOWN_RATE_ACCENT not in covering


# --- the window states its own totals ------------------------------------


def test_rows_beyond_the_height_are_counted_rather_than_dropped() -> None:
    torrents = [
        make_torrent(hash=f"{i:040x}", tracker=f"http://t{i}.tld/a")
        for i in range(20)
    ]
    rendered = build_trackers_window(
        build_tracker_breakdown(torrents),
        RateHistory(),
        width=80,
        height=12,
    ).plain

    shown = rendered.count(".tld")
    assert 0 < shown < 20
    assert f"+ {20 - shown} more" in rendered
    assert "20 trackers" in rendered
    # The count is the whole announcement: there is no Trackers page to
    # send anyone to, and `k` already moves a table cursor.
    assert "for the full list" not in rendered
    assert "(3/k)" not in rendered


def test_the_legend_counts_add_up_to_the_rows_it_describes() -> None:
    breakdown = _breakdown(
        make_torrent(hash="a" * 40, tracker="http://s.tld/a", upspeed=1),
        make_torrent(hash="b" * 40, tracker="http://l.tld/a", dlspeed=1),
        make_torrent(hash="c" * 40, tracker="** [DHT] **"),
    )
    total = sum(breakdown.count_by_kind(kind) for kind in TrackerActivityKind)

    assert total == len(breakdown.rows) == 3


def test_a_window_with_no_refresh_yet_says_so() -> None:
    rendered = build_trackers_window(
        None, RateHistory(), width=80, height=24
    ).plain

    assert "Waiting for the first refresh" in rendered
    assert "0 trackers" not in rendered


# --- one arrow alphabet for one physical fact ----------------------------

_RETIRED_ARROWS = ("▲", "▼", "◆")


def test_the_page_carries_no_second_arrow_alphabet() -> None:
    """Seeding *is* uploading and leeching *is* downloading. Triangles
    beside the graph's own `↑`/`↓` were two shapes for one fact."""
    rendered = _window(
        make_torrent(hash="a" * 40, tracker="http://s.tld/a", upspeed=10),
        make_torrent(hash="b" * 40, tracker="http://l.tld/a", dlspeed=10),
        make_torrent(
            hash="c" * 40, tracker="http://b.tld/a", upspeed=10, dlspeed=10
        ),
    ).plain

    for arrow in _RETIRED_ARROWS:
        assert arrow not in rendered, arrow
    assert "↑" in rendered and "↓" in rendered and "↕" in rendered


def test_the_arrow_carries_direction_and_the_word_carries_state() -> None:
    """`↕` cannot say "both", so the word still has to."""
    rendered = _window(
        make_torrent(hash="a" * 40, tracker="http://s.tld/a", upspeed=10),
        make_torrent(hash="b" * 40, tracker="http://l.tld/a", dlspeed=10),
        make_torrent(
            hash="c" * 40, tracker="http://b.tld/a", upspeed=10, dlspeed=10
        ),
    ).plain

    for word in ("seed", "leech", "both"):
        assert word in rendered, word


def test_the_direction_is_readable_against_the_axis() -> None:
    """The peak labels sit four rows away from the axis. The eye reading
    a bar is at the axis, so the direction has to be legible there."""
    from qbit_ops.tui.state import GRAPH_SLOTS, RateHistory
    from qbit_ops.tui.widgets.rate_graph import build_rate_graph

    history = RateHistory()
    for _ in range(GRAPH_SLOTS):
        history.record(download=4_000_000, upload=1_000_000)
    lines = build_rate_graph(history, width=76).plain.splitlines()

    axis = next(i for i, line in enumerate(lines) if "-60s" in line)
    above, below = lines[axis - 1], lines[axis + 1]

    assert "↓" in above.split("┤")[0]
    assert "↑" in below.split("┤")[0]


# --- the session window's lists are capped, not unbounded ----------------


def _session_with(categories: int, tags: int, width: int = 50) -> str:
    from qbit_core.features.status import (
        build_instance_stats_from_server_state,
        build_status_snapshot_from_data,
    )
    from qbit_ops.tui.state import TuiState, build_library_breakdown
    from qbit_ops.tui.widgets.overview_windows import build_session_window

    torrents = [
        make_torrent(
            hash=f"{i:040x}",
            size=10**9,
            category=f"category-{i % categories:02d}",
            tags=f"tag-{i % tags:02d}",
        )
        for i in range(60)
    ]
    state = TuiState()
    state.status = build_status_snapshot_from_data(
        qbittorrent_version="5.2.3",
        api_version="2.11.4",
        transfer_info_data={"dl_info_speed": 0, "up_info_speed": 0},
        torrents=torrents,
    )
    state.instance_stats = build_instance_stats_from_server_state(
        {
            "alltime_dl": 1,
            "alltime_ul": 1,
            "global_ratio": "1.5",
            "total_peer_connections": 4,
            "dht_nodes": 7,
            "dl_info_data": 5,
            "up_info_data": 6,
            "dl_rate_limit": 0,
            "up_rate_limit": 0,
            "use_alt_speed_limits": False,
            "free_space_on_disk": 1_234_567_890,
            "queueing": True,
            "connection_status": "connected",
        }
    )
    state.library_breakdown = build_library_breakdown(torrents)
    return build_session_window(state, width=width).plain


def test_twenty_categories_never_push_the_last_rows_off_the_window() -> None:
    """The window's content is a fixed height, so an unbounded list does
    not push the rows below it down -- it pushes them out, and the two
    most useful are last."""
    rendered = _session_with(categories=20, tags=20)

    assert "Free space" in rendered
    assert "Queueing" in rendered
    assert "+ " in rendered and " more" in rendered


def test_a_capped_list_counts_exactly_what_it_left_out() -> None:
    """Appending `+ N more` to a finished line displaced entries the
    count then failed to mention."""
    import re

    rendered = _session_with(categories=20, tags=20)
    body = rendered.split("Categories")[1].split("Tags")[0]

    shown = len(re.findall(r"category-\d\d", body))
    more = re.search(r"\+ (\d+) more", body)
    assert more is not None, body
    assert shown + int(more.group(1)) == 20, body


def test_a_short_list_is_never_capped_at_all() -> None:
    rendered = _session_with(categories=2, tags=1)

    assert "more" not in rendered
    assert "category-00" in rendered and "category-01" in rendered
    assert "Free space" in rendered and "Queueing" in rendered
