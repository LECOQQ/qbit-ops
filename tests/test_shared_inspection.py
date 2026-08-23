"""Test the INSPECT stage's call-budget optimization (`include_trackers`).

`qbit_core.shared.inspection.inspect_trackers`/`select_and_inspect` can
collect tracker data two ways: one bulk `torrents_info(include_trackers=
True)` call, or one `torrents_trackers()` call per torrent. Which path
ran must never be visible in the *result* -- only in the call count.
"""

from qbit_core.qbit.fields import get_field_as_string, get_raw_tracker_status
from qbit_core.shared.inspection import inspect_trackers, select_and_inspect
from qbit_core.shared.selection import SelectionRequest, select_from_items
from tests.support import FakeQbitClient, make_torrent

HASH_A = "a" * 40
HASH_B = "b" * 40
HASH_C = "c" * 40

_TRACKERS_BY_HASH = {
    HASH_A: [{"url": "https://one.example/announce", "status": 2}],
    HASH_B: [
        {"url": "https://two.example/announce", "status": 4, "msg": "banned"},
    ],
    HASH_C: [],
}


def _client(*, include_trackers_supported: bool) -> FakeQbitClient:
    return FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="A"),
            make_torrent(hash=HASH_B, name="B"),
            make_torrent(hash=HASH_C, name="C"),
        ],
        trackers_by_hash=_TRACKERS_BY_HASH,
        include_trackers_supported=include_trackers_supported,
    )


def _endpoint_shapes(inspection) -> dict[str, list[tuple[str, object]]]:
    """Reduce an `Inspection` to what a caller actually reads off it:
    per-hash (url, raw_status) pairs, order-independent."""
    return {
        inspected.snapshot.hash: sorted(
            (
                get_field_as_string(raw, "url"),
                get_raw_tracker_status(raw),
            )
            for raw in inspected.raw_trackers
        )
        for inspected in inspection.torrents
    }


# --- Same output on both paths ----------------------------------------------


def test_bulk_and_fallback_paths_produce_the_same_tracker_data() -> None:
    """The repli must render the same result, not a degraded one: run
    both paths over identical data and compare the observed output, not
    just the call count."""
    capable = _client(include_trackers_supported=True)
    incapable = _client(include_trackers_supported=False)

    capable_inspection = select_and_inspect(capable, SelectionRequest())
    incapable_inspection = select_and_inspect(incapable, SelectionRequest())

    assert _endpoint_shapes(capable_inspection) == _endpoint_shapes(
        incapable_inspection
    )
    assert {t.snapshot.hash for t in capable_inspection.torrents} == {
        HASH_A,
        HASH_B,
        HASH_C,
    }


# --- Call budget: the actual point of this module ---------------------------


def test_a_capable_server_costs_one_call_regardless_of_library_size() -> None:
    client = _client(include_trackers_supported=True)

    select_and_inspect(client, SelectionRequest())

    assert client.torrents_info_calls == 1
    assert client.torrents_trackers_calls == 0


def test_an_incapable_server_falls_back_to_one_call_per_torrent() -> None:
    """Reintroduce the regression this guards against by asserting
    `torrents_trackers_calls == 0` instead: with the fix removed (no
    bulk attempt at all), this line would already fail as `3 != 0`, but
    the case that actually matters is the opposite direction -- an
    incapable server must still get every torrent's real data, not an
    empty result silently accepted as "no trackers"."""
    client = _client(include_trackers_supported=False)

    inspection = select_and_inspect(client, SelectionRequest())

    assert client.torrents_trackers_calls == 3
    assert _endpoint_shapes(inspection) == {
        HASH_A: [("https://one.example/announce", 2)],
        HASH_B: [("https://two.example/announce", 4)],
        HASH_C: [],
    }


def test_inspect_trackers_bulk_probe_is_scoped_to_the_selection() -> None:
    """`inspect_trackers` (unlike `select_and_inspect`) receives an
    already-narrowed `Selection` -- its bulk probe must request only
    those hashes, not the whole library."""
    client = _client(include_trackers_supported=True)
    narrowed = select_from_items(
        client.torrents, SelectionRequest(torrent_hash=HASH_A)
    )

    inspect_trackers(client, narrowed)

    call = next(call for call in client.calls if call[0] == "torrents_info")
    assert call[2]["torrent_hashes"] == [HASH_A]


# --- Bulk failure degrades to the legacy path, not to an error --------------


def test_a_bulk_call_failure_falls_back_instead_of_raising() -> None:
    client = _client(include_trackers_supported=True)

    def _broken_torrents_info(*_args: object, **_kwargs: object) -> list:
        raise RuntimeError("simulated transport failure")

    client.torrents_info = _broken_torrents_info  # type: ignore[method-assign]

    selection = select_from_items(
        [make_torrent(hash=HASH_A, name="A")], SelectionRequest()
    )
    inspection = inspect_trackers(client, selection)

    assert inspection.torrents[0].raw_trackers == tuple(
        _TRACKERS_BY_HASH[HASH_A]
    )
    assert client.torrents_trackers_calls == 1


# --- The qbittorrent-api trap this module's docstring warns about -----------


def test_reading_the_embedded_field_never_triggers_a_live_property() -> None:
    """A real qbittorrent-api `TorrentDictionary` is a `dict` subclass
    that also defines `trackers` as a live property calling
    `torrents_trackers()` again. `_extract_bulk_trackers` must read the
    raw embedded key, never that property -- reintroduce the bug by
    reading `item.trackers` instead of `item.get("trackers")` in
    `_extract_bulk_trackers` to see this test fail with an
    `AttributeError` (no client attached) instead of returning data."""
    from qbittorrentapi.torrents import TorrentDictionary

    from qbit_core.shared.inspection import _extract_bulk_trackers

    item = TorrentDictionary(
        data={
            "hash": HASH_A,
            "name": "A",
            "trackers": [{"url": "https://one.example/announce", "status": 2}],
        },
        client=None,  # type: ignore[arg-type]
    )

    result = _extract_bulk_trackers([item])

    assert result[HASH_A] == (
        {"url": "https://one.example/announce", "status": 2},
    )
