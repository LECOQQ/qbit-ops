"""Safe, tolerant access to qbittorrent-api response shapes.

Every field read goes through a `Mapping`-or-`getattr` accessor, since
a response item can be either a plain `dict`/qbittorrent-api mapping
type or an attribute-bearing object.

Two deliberately different reading policies coexist here:

- `get_field_as_*` **coerce**: an absent, null or unparsable field
  becomes `0` / `0.0` / `""`. Right for display and for counting, where
  a blank is the honest rendering and callers already rely on it.
- `get_optional_*` **report absence**: the same cases become `None`,
  meaning *unknown*. Required by any bounded predicate, where coercing
  a missing `size` to `0` would silently satisfy `--size-max` and widen
  a destructive selection.

Never swap one family for the other to "simplify": the difference is
the safety property, not a style choice.
"""

from collections.abc import Collection, Mapping
from typing import Any

# Distinguishes "field absent" from "field present and set to None",
# which `get_field`'s caller-supplied default cannot express.
_MISSING = object()


def get_field(item: Any, field_name: str, default: Any) -> Any:
    """Read a field from an object or mapping."""
    if isinstance(item, Mapping):
        return item.get(field_name, default)

    return getattr(item, field_name, default)


def get_optional_field(item: Any, field_name: str) -> Any | None:
    """Read a field, returning `None` when absent *or* explicitly null.

    qBittorrent sends both shapes -- an omitted key on an older
    version, an explicit `null` on a field it cannot compute -- and
    both mean the same thing to a predicate: unknown.
    """
    value = get_field(item, field_name, _MISSING)
    return None if value is _MISSING else value


def get_optional_int(
    item: Any,
    field_name: str,
    *,
    sentinels: Collection[int] = (),
) -> int | None:
    """Read an integer field as a known value, or `None` when unknown.

    Unknown covers four cases: absent, explicitly null, unparsable, and
    listed in `sentinels`. qBittorrent encodes "unset" as a negative
    value on several fields (`-1` for an uncomputed `availability`,
    `-2` for "use the global setting"), and `seen_complete` even uses a
    *different* sentinel per version -- so the caller declares which
    values mean nothing for the field it is reading, rather than this
    module guessing.
    """
    value = get_optional_field(item, field_name)
    if value is None or isinstance(value, bool):
        return None

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None

    return None if parsed in sentinels else parsed


def get_optional_float(
    item: Any,
    field_name: str,
    *,
    sentinels: Collection[float] = (),
) -> float | None:
    """Read a float field as a known value, or `None` when unknown.

    Same policy as `get_optional_int`; see its docstring for why
    sentinels are declared by the caller.
    """
    value = get_optional_field(item, field_name)
    if value is None or isinstance(value, bool):
        return None

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None

    return None if parsed in sentinels else parsed


def get_optional_bool(item: Any, field_name: str) -> bool | None:
    """Read a boolean field, or `None` when the field is unknown.

    Deliberately strict: only a real `bool` counts. A field qBittorrent
    does not expose on this version (`private` before 5.0) must read as
    unknown, never as `False` -- otherwise `--public` would match every
    torrent on an older instance.
    """
    value = get_optional_field(item, field_name)
    return value if isinstance(value, bool) else None


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


def get_field_as_tag_list(item: Any) -> list[str]:
    """Parse qBittorrent's comma-separated `tags` field into a sorted list."""
    raw = get_field_as_string(item, "tags")
    return sorted({tag.strip() for tag in raw.split(",") if tag.strip() != ""})


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
