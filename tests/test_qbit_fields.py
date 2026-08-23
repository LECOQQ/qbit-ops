"""Characterize the qBittorrent field-access boundary (`qbit_core.qbit.fields`).

Uses both plain dicts (what `tests.support.FakeQbitClient` returns) and
the real qbittorrent-api response types (`TorrentDictionary`, `Tracker`,
`TransferInfoDictionary`) so this boundary is proven against the actual
object shapes the installed library returns, not only against test
doubles.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
import qbittorrentapi.torrents as qbt_torrents
import qbittorrentapi.transfer as qbt_transfer

from qbit_core.qbit.fields import (
    get_active_tracker_urls,
    get_field,
    get_field_as_float,
    get_field_as_int,
    get_field_as_string,
    get_optional_bool,
    get_optional_field,
    get_optional_float,
    get_optional_int,
    get_raw_tracker_status,
    get_transfer_rates,
    is_disabled_tracker,
    is_disabled_tracker_status,
    is_pseudo_tracker_marker,
    pseudo_tracker_label,
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
    """The non-Mapping branch still works for an attribute object."""

    class _AttrItem:
        hash = "attr-hash"

    assert get_field_as_string(_AttrItem(), "hash") == "attr-hash"
    assert get_field_as_string(_AttrItem(), "missing") == ""


class _MappingNotDict(Mapping):
    """A real `Mapping`, deliberately not a `dict` subclass."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Any:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


def test_get_field_takes_the_dict_branch_not_any_mapping() -> None:
    """`get_field` checks `isinstance(item, dict)`, not the broader
    `isinstance(item, Mapping)` -- measured ~4.5x cheaper (see
    `fields.py`'s module docstring), and every qbittorrent-api response
    type read here is itself a `dict` subclass. A `Mapping` that is not
    a `dict` must fall to the attribute branch instead of the mapping
    one, and since it exposes no matching attribute, reads as absent.
    Nothing in this codebase constructs such an object; this pins the
    contract against a well-meaning revert to `Mapping`."""
    mapping_not_dict = _MappingNotDict({"hash": "abc123"})

    assert get_field_as_string(mapping_not_dict, "hash") == ""


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


# --- transfer-info boundary ---------------------------------------------


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
    `AttributeError` from an unguarded `.get()` call."""

    class _NotAMapping:
        pass

    with pytest.raises(TypeError, match="mapping"):
        get_transfer_rates(_NotAMapping())


def test_get_transfer_rates_missing_keys_default_to_zero() -> None:
    assert get_transfer_rates({}) == (0, 0)


# --- tracker-status normalization -----------------------------------------


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
    plain digit."""

    import enum

    # Mirrors qbittorrentapi.definitions.TrackerStatus's actual base
    # classes (`class TrackerStatus(int, Enum)`), verified against the
    # installed library -- not `enum.IntEnum`, whose `str()` behavior
    # changed in Python 3.11+ to no longer exhibit this trap.
    class _TrackerStatusLike(int, enum.Enum):
        DISABLED = 0
        WORKING = 2

    assert str(_TrackerStatusLike.DISABLED) != "0"  # the trap this test targets
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
    active-URL list."""
    trackers = [
        {"url": "** [DHT] **", "status": 0},
        {"url": "https://disabled.example/announce", "status": "disabled"},
        {"url": "https://active.example/announce", "status": 2},
    ]

    assert get_active_tracker_urls(trackers) == [
        "https://active.example/announce"
    ]


# --- Pseudo-trackers are not trackers ---------------------------------------
#
# qBittorrent lists DHT/PeX/LSD alongside real trackers, as bracketed
# markers. They are peer-discovery mechanisms: they name no host, cannot
# be added or removed, and qBittorrent's own `trackers_count` excludes
# them. So every counter and every selection here excludes them too.


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("** [DHT] **", "DHT"),
        ("** [PeX] **", "PeX"),
        ("** [LSD] **", "LSD"),
        ("  ** [DHT] **  ", "DHT"),
        ("**[DHT]**", "DHT"),
        ("https://tracker.example/announce", None),
        ("", None),
        ("** [] **", None),
    ],
)
def test_pseudo_tracker_label_reads_the_marker_not_a_url(
    url: str, expected: str | None
) -> None:
    assert pseudo_tracker_label(url) == expected
    assert is_pseudo_tracker_marker(url) is (expected is not None)


def test_get_active_tracker_urls_excludes_working_pseudo_trackers() -> None:
    """The regression that made tracker filters crash: on 5.2.3 every
    pseudo-tracker is reported as *working* (status 2), so excluding only
    the disabled ones let `** [DHT] **` reach `urlsplit` -- and be
    counted as a tracker."""
    trackers = [
        {"url": "** [DHT] **", "status": 2},
        {"url": "** [PeX] **", "status": 2},
        {"url": "** [LSD] **", "status": 2},
        {"url": "https://active.example/announce", "status": 2},
    ]

    assert get_active_tracker_urls(trackers) == [
        "https://active.example/announce"
    ]


# --- get_optional_*: absence is reported, never coerced ---------------------
#
# The coercing family (`get_field_as_*`) and this one answer different
# questions on purpose. A bounded predicate that read a missing `size`
# as `0` would silently satisfy `--size-max` and widen a destructive
# selection.


@pytest.mark.parametrize(
    "item",
    [
        {"size": 1000, "ratio": 1.5, "private": True},
        _real_torrent(size=1000, ratio=1.5, private=True),
    ],
)
def test_optional_helpers_read_present_values(item: object) -> None:
    assert get_optional_int(item, "size") == 1000
    assert get_optional_float(item, "ratio") == 1.5
    assert get_optional_bool(item, "private") is True


def test_optional_helpers_report_an_absent_field_as_unknown() -> None:
    assert get_optional_field({}, "size") is None
    assert get_optional_int({}, "size") is None
    assert get_optional_float({}, "ratio") is None
    assert get_optional_bool({}, "private") is None


def test_optional_helpers_report_an_explicit_null_as_unknown() -> None:
    """qBittorrent sends both an omitted key and an explicit null; both
    mean unknown to a predicate."""
    item = {"size": None, "ratio": None, "private": None}

    assert get_optional_int(item, "size") is None
    assert get_optional_float(item, "ratio") is None
    assert get_optional_bool(item, "private") is None


def test_coercing_and_optional_families_disagree_by_design() -> None:
    """The whole point of the split: the same missing field is `0` for
    display and `None` for a bounded predicate."""
    assert get_field_as_int({}, "size") == 0
    assert get_optional_int({}, "size") is None

    assert get_field_as_float({}, "ratio") == 0.0
    assert get_optional_float({}, "ratio") is None


def test_optional_helpers_report_an_unparsable_value_as_unknown() -> None:
    item = {"size": "not-a-number", "ratio": "nope"}

    assert get_optional_int(item, "size") is None
    assert get_optional_float(item, "ratio") is None


def test_optional_numeric_helpers_reject_booleans() -> None:
    """`bool` is an `int` subclass, so `int(True)` is `1`. A boolean in a
    numeric field is a malformed payload; unknown beats a wrong `1`."""
    assert get_optional_int({"size": True}, "size") is None
    assert get_optional_float({"ratio": False}, "ratio") is None


def test_optional_bool_rejects_non_boolean_values() -> None:
    """`private` must never be inferred from a truthy value."""
    assert get_optional_bool({"private": 1}, "private") is None
    assert get_optional_bool({"private": "true"}, "private") is None
    assert get_optional_bool({"private": False}, "private") is False


@pytest.mark.parametrize("sentinel", [-1, -2])
def test_declared_sentinels_read_as_unknown(sentinel: int) -> None:
    """qBittorrent encodes "unset" as a negative value on several fields
    (`-1` uncomputed, `-2` "use the global setting")."""
    item = {"availability": sentinel}

    assert get_optional_int(item, "availability") == sentinel
    assert get_optional_int(item, "availability", sentinels=(-1, -2)) is None


def test_sentinels_are_declared_per_call_not_guessed() -> None:
    """`seen_complete` uses `0` on 4.6.7 and `-1` on 5.x, so no global
    sentinel table could be correct for every field and version."""
    assert get_optional_int({"seen_complete": 0}, "seen_complete") == 0
    assert (
        get_optional_int({"seen_complete": 0}, "seen_complete", sentinels=(0,))
        is None
    )
    assert (
        get_optional_int(
            {"seen_complete": -1}, "seen_complete", sentinels=(-1,)
        )
        is None
    )


def test_a_legitimate_zero_survives_when_no_sentinel_is_declared() -> None:
    """`trackers_count == 0` is exactly what `--no-tracker` looks for:
    zero must stay a value, not become unknown."""
    assert get_optional_int({"trackers_count": 0}, "trackers_count") == 0
    assert get_optional_float({"ratio": 0.0}, "ratio") == 0.0


def test_optional_helpers_accept_attribute_based_objects() -> None:
    class _AttrItem:
        size = 4096

    assert get_optional_int(_AttrItem(), "size") == 4096
    assert get_optional_int(_AttrItem(), "ratio") is None
