"""Safe, tolerant access to qbittorrent-api response shapes.

This module is the one place allowed to know that a qBittorrent
response item can be either a `Mapping` (a plain `dict`, or
qbittorrent-api's own `TorrentDictionary`/`Tracker`/
`TransferInfoDictionary`, all of which subclass `dict`) or an
attribute-bearing object. Every other module reads fields through the
functions here rather than reimplementing the `Mapping`-or-`getattr`
check itself -- see `docs/audits/2026-07-codebase-architecture-inventory.md`
constat A-5 (duplicated field/status helpers) and
`docs/audits/2026-07-qbittorrent-api-inventory.md` constats P-1/P-2/P-3.

Moved here from `qbit_ops/qbit_fields.py` verbatim (constat A-5), plus
two narrow additions that consolidate duplicated or fragile logic
already identified by the audit:

- `get_transfer_rates` routes `transfer_info()` field access through
  the same tolerant accessor the rest of the project uses, instead of
  the raw `.get()` call `qbit_ops.features.status` used to make
  directly (constat P-2) -- a malformed non-mapping payload now fails
  with an explicit `TypeError` here rather than an accidental
  `AttributeError` three frames deeper.
- `is_disabled_tracker`/`is_disabled_tracker_status` replace two
  identical, independently fragile `_is_disabled_tracker()` copies
  (`qbit_ops.features.trackers` and `qbit_ops.features.torrents`,
  constat A-5) that stringified the raw `status` field *before*
  comparing it, which breaks silently if qbittorrent-api ever returns
  `qbittorrentapi.definitions.TrackerStatus.DISABLED` (an `IntEnum`)
  instead of a plain `0`: `str(TrackerStatus.DISABLED)` is
  `"TrackerStatus.DISABLED"`, not `"0"` (constat P-3, verified against
  the installed `qbittorrentapi` library). Coercing through `int()`
  first works for a plain int, a numeric string, *and* an `IntEnum`
  value, since an `IntEnum` instance satisfies `int()` even though its
  `str()` does not round-trip to a plain digit.
"""

from collections.abc import Mapping
from typing import Any


def get_field(item: Any, field_name: str, default: Any) -> Any:
    """Read a field from an object or mapping."""
    if isinstance(item, Mapping):
        return item.get(field_name, default)

    return getattr(item, field_name, default)


def get_field_as_string(item: Any, field_name: str) -> str:
    """Read a string field from an object or mapping."""
    value = get_field(item, field_name, "")
    if value is None:
        return ""

    return str(value)


def get_field_as_int(item: Any, field_name: str) -> int:
    """Read an integer field from an object or mapping."""
    value = get_field(item, field_name, 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def get_field_as_float(item: Any, field_name: str) -> float:
    """Read a float field from an object or mapping."""
    value = get_field(item, field_name, 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def get_transfer_rates(transfer_info: Any) -> tuple[int, int]:
    """Read `(download, upload)` byte rates from a `transfer_info()` payload.

    Requires a `Mapping`-shaped payload -- the shape qbittorrent-api's
    `TransferInfoDictionary` and every `FakeQbitClient` test double
    actually return. A non-mapping payload fails explicitly here,
    rather than crashing later with an accidental `AttributeError` from
    a raw `.get()` call (constat P-2): callers already route an
    unclassifiable exception to the project's internal-error path, so
    this preserves today's actual outcome (an internal error) while
    making the failure identifiable instead of accidental.
    """
    if not isinstance(transfer_info, Mapping):
        raise TypeError(
            "transfer_info() must return a mapping; got "
            f"{type(transfer_info).__name__!r}."
        )

    return (
        get_field_as_int(transfer_info, "dl_info_speed"),
        get_field_as_int(transfer_info, "up_info_speed"),
    )


def get_raw_tracker_status(raw_tracker: Any) -> int | str | None:
    """Read a tracker's raw `status` field, preserving its original type.

    Shared by `qbit_ops.features.trackers.inspect_tracker`,
    `qbit_ops.features.tracker_status.collect_tracker_status`, and
    `is_disabled_tracker` below, so every caller feeds
    `classify_raw_tracker_status`/`is_disabled_tracker_status` the
    exact same raw shape.
    """
    if isinstance(raw_tracker, Mapping):
        value = raw_tracker.get("status")
    else:
        value = getattr(raw_tracker, "status", None)

    if value is None:
        return None
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return value
    return str(value)


def is_disabled_tracker_status(raw_status: int | str | None) -> bool:
    """Return whether a raw tracker status represents the DISABLED code.

    Coerces through `int()` first -- works for a plain int, a numeric
    string, and any `IntEnum`-like value qbittorrent-api might return
    -- falling back to a literal `"disabled"` string comparison for
    callers that already use that convention (e.g. test fixtures).
    Never raises: an unparsable value is simply "not disabled" -- never
    guessed as active or forced disabled.
    """
    if isinstance(raw_status, str) and raw_status.strip().lower() == "disabled":
        return True

    try:
        return int(raw_status) == 0  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def is_disabled_tracker(tracker: Any) -> bool:
    """Return whether qBittorrent reports a tracker as disabled.

    The single canonical replacement for the two identical
    `_is_disabled_tracker()` copies previously in `qbit_ops.features.trackers`
    and `qbit_ops.features.torrents` (constat A-5) -- both fed a mutation-plan
    filter (`_get_active_tracker_urls`), so a stringification bug here
    would have silently included a disabled tracker in a mutation plan
    (constat P-3).
    """
    return is_disabled_tracker_status(get_raw_tracker_status(tracker))


def get_active_tracker_urls(trackers: Any) -> list[str]:
    """Extract non-disabled tracker URLs from qBittorrent tracker objects.

    The single canonical replacement for two identical
    `_get_active_tracker_urls()` copies previously in
    `qbit_ops.features.trackers` and `qbit_ops.features.torrents`
    (constat A-5).
    """
    return [
        tracker_url
        for tracker in trackers
        if not is_disabled_tracker(tracker)
        and (tracker_url := get_field_as_string(tracker, "url")) != ""
    ]
