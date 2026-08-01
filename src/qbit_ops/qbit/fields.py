"""Safe, tolerant access to qbittorrent-api response shapes.

Every field read goes through a `Mapping`-or-`getattr` accessor, since
a response item can be either a plain `dict`/qbittorrent-api mapping
type or an attribute-bearing object.
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

    Raises `TypeError` on a non-mapping payload instead of an
    accidental `AttributeError` deeper in the call stack.
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
    """Read a tracker's raw `status` field, preserving its original type."""
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

    Never raises: an unparsable value is simply "not disabled".
    """
    if isinstance(raw_status, str) and raw_status.strip().lower() == "disabled":
        return True

    # qbittorrent-api may return TrackerStatus.DISABLED, an IntEnum whose
    # str() is "TrackerStatus.DISABLED", not "0" -- coerce through int()
    # first so a plain int, numeric string, or IntEnum all compare correctly.
    try:
        return int(raw_status) == 0  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def is_disabled_tracker(tracker: Any) -> bool:
    """Return whether qBittorrent reports a tracker as disabled."""
    return is_disabled_tracker_status(get_raw_tracker_status(tracker))


def get_active_tracker_urls(trackers: Any) -> list[str]:
    """Extract non-disabled tracker URLs from qBittorrent tracker objects."""
    return [
        tracker_url
        for tracker in trackers
        if not is_disabled_tracker(tracker)
        and (tracker_url := get_field_as_string(tracker, "url")) != ""
    ]
