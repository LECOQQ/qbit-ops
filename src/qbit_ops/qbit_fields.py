"""Read fields from qBittorrent objects, mapping- or attribute-based.

Extracted from `qbit_ops.torrents` so it can be shared with `qbit_ops.selectors`
without a circular import (the resolver needs the same safe field
access as the rest of the torrent domain logic).
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
