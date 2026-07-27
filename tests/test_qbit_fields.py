"""Characterize the qBittorrent field-access boundary (`qbit_ops.qbit.fields`).

Uses both plain dicts (what `tests.support.FakeQbitClient` returns) and
the real qbittorrent-api response types (`TorrentDictionary`, `Tracker`,
`TransferInfoDictionary`) so this boundary is proven against the actual
object shapes the installed library returns, not only against test
doubles -- see `docs/audits/2026-07-qbittorrent-api-inventory.md`
constat P-1.
"""

from __future__ import annotations

from typing import Any

import pytest
import qbittorrentapi.torrents as qbt_torrents
import qbittorrentapi.transfer as qbt_transfer

from qbit_ops.qbit.fields import (
    get_active_tracker_urls,
    get_field,
    get_field_as_float,
    get_field_as_int,
    get_field_as_string,
    get_raw_tracker_status,
    get_transfer_rates,
    is_disabled_tracker,
    is_disabled_tracker_status,
)


def _real_torrent(**fields: Any) -> qbt_torrents.TorrentDictionary:
    """Build a real `qbittorrentapi.TorrentDictionary`, not a plain dict."""
    return qbt_torrents.TorrentDictionary(data=dict(fields), client=None)  # type: ignore[arg-type]


def _real_tracker(**fields: Any) -> qbt_torrents.Tracker:
    """Build a real `qbittorrentapi.Tracker`, not a plain dict."""
    return qbt_torrents.Tracker(data=dict(fields))


def _real_transfer_info(**fields: Any) -> qbt_transfer.TransferInfoDictionary:
    """Build a real `qbittorrentapi.TransferInfoDictionary`, not a dict."""
    return qbt_transfer.TransferInfoDictionary(data=dict(fields))


# --- get_field* accept both plain dicts and real qbittorrent-api objects ----


@pytest.mark.parametrize(
    "item",
    [
        {"hash": "abc123", "size": "1000", "progress": "0.5"},
        _real_torrent(hash="abc123", size="1000", progress="0.5"),
    ],
)
def test_field_helpers_accept_dict_and_real_torrent_payloads(
    item: object,
) -> None:
    assert get_field_as_string(item, "hash") == "abc123"
    assert get_field_as_int(item, "size") == 1000
    assert get_field_as_float(item, "progress") == 0.5


def test_field_helpers_accept_attribute_based_object() -> None:
    """The non-Mapping branch (P-1) still works for an attribute object."""

    class _AttrItem:
        hash = "attr-hash"

    assert get_field_as_string(_AttrItem(), "hash") == "attr-hash"
    assert get_field_as_string(_AttrItem(), "missing") == ""


def test_optional_missing_field_uses_documented_fallback() -> None:
    """A missing optional field degrades to its documented default."""
    assert get_field_as_string({}, "category") == ""
    assert get_field_as_int({}, "size") == 0
    assert get_field_as_float({}, "progress") == 0.0
    assert get_field({}, "anything", "sentinel") == "sentinel"


def test_unknown_extra_fields_are_harmless() -> None:
    """An item carrying fields this project never reads must not break."""
    item = {
        "hash": "abc",
        "a_field_from_a_future_qbittorrent_release": {"nested": True},
    }
    assert get_field_as_string(item, "hash") == "abc"


# --- transfer-info boundary (constat P-2) ------------------------------------


@pytest.mark.parametrize(
    "transfer_info",
    [
        {"dl_info_speed": 1024, "up_info_speed": 2048},
        _real_transfer_info(dl_info_speed=1024, up_info_speed=2048),
    ],
)
def test_get_transfer_rates_accepts_valid_mapping_shapes(
    transfer_info: object,
) -> None:
    assert get_transfer_rates(transfer_info) == (1024, 2048)


def test_get_transfer_rates_rejects_non_mapping_with_explicit_type_error() -> (
    None
):
    """A malformed non-mapping payload fails explicitly, not with a bare
    `AttributeError` from an unguarded `.get()` call (constat P-2)."""

    class _NotAMapping:
        pass

    with pytest.raises(TypeError, match="mapping"):
        get_transfer_rates(_NotAMapping())


def test_get_transfer_rates_missing_keys_default_to_zero() -> None:
    assert get_transfer_rates({}) == (0, 0)


# --- tracker-status normalization (constat P-3) ------------------------------


@pytest.mark.parametrize(
    ("raw_status", "expected_disabled"),
    [
        (0, True),
        ("0", True),
        ("disabled", True),
        ("DISABLED", True),
        (2, False),
        ("2", False),
        (4, False),
        (None, False),
    ],
)
def test_is_disabled_tracker_status_classifies_ints_and_strings(
    raw_status: int | str | None,
    expected_disabled: bool,
) -> None:
    assert is_disabled_tracker_status(raw_status) is expected_disabled


def test_is_disabled_tracker_status_handles_enum_like_int_subclass() -> None:
    """An `IntEnum`-like value (e.g. `qbittorrentapi.definitions.TrackerStatus`)
    must classify correctly even though its `str()` does not read as a
    plain digit -- the exact defect constat P-3 identified."""

    import enum

    # Mirrors qbittorrentapi.definitions.TrackerStatus's actual base
    # classes (`class TrackerStatus(int, Enum)`), verified against the
    # installed library -- not `enum.IntEnum`, whose `str()` behavior
    # changed in Python 3.11+ to no longer exhibit this trap.
    class _TrackerStatusLike(int, enum.Enum):
        DISABLED = 0
        WORKING = 2

    assert str(_TrackerStatusLike.DISABLED) != "0"  # the trap P-3 describes
    assert is_disabled_tracker_status(_TrackerStatusLike.DISABLED) is True
    assert is_disabled_tracker_status(_TrackerStatusLike.WORKING) is False


def test_is_disabled_tracker_status_unknown_value_is_safe_and_explicit() -> (
    None
):
    assert is_disabled_tracker_status("not-a-status") is False
    assert is_disabled_tracker_status(object()) is False  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "tracker",
    [
        {"url": "https://tracker.example/announce", "status": 0},
        {"url": "https://tracker.example/announce", "status": "disabled"},
        _real_tracker(url="https://tracker.example/announce", status=0),
    ],
)
def test_is_disabled_tracker_true_for_disabled_endpoint(
    tracker: object,
) -> None:
    assert is_disabled_tracker(tracker) is True


def test_is_disabled_tracker_false_for_active_endpoint() -> None:
    assert (
        is_disabled_tracker(
            {"url": "https://tracker.example/announce", "status": 2}
        )
        is False
    )


def test_get_raw_tracker_status_preserves_original_type() -> None:
    assert get_raw_tracker_status({"status": 0}) == 0
    assert isinstance(get_raw_tracker_status({"status": 0}), int)
    assert get_raw_tracker_status({"status": "disabled"}) == "disabled"
    assert get_raw_tracker_status({"status": None}) is None
    assert get_raw_tracker_status({}) is None


def test_get_active_tracker_urls_excludes_disabled_trackers() -> None:
    """A disabled pseudo-tracker (int 0) never reaches a mutation plan's
    active-URL list -- the exact mutation-safety guarantee constat P-3
    is about."""
    trackers = [
        {"url": "** [DHT] **", "status": 0},
        {"url": "https://disabled.example/announce", "status": "disabled"},
        {"url": "https://active.example/announce", "status": 2},
    ]

    assert get_active_tracker_urls(trackers) == [
        "https://active.example/announce"
    ]
