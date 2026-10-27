"""Read and change qBittorrent's global speed limits and alternative mode.

Limits are bytes per second, `0` meaning unlimited. `read_transfer_settings`
is the snapshot a caller keeps before changing anything; giving settings
back is the caller's decision, never a blind rewrite (docs/LIBRARY.md).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from qbit_core.errors import InvalidInputError, QbitCoreError
from qbit_core.qbit.fields import (
    get_field,
    get_field_as_int,
    get_field_as_string,
)
from qbit_core.qbit.protocols import (
    QbitTransferReader,
    QbitTransferSettingsMutator,
    QbitTransferSettingsReader,
)

__all__ = [
    "SpeedLimits",
    "TransferInfo",
    "TransferSettings",
    "read_transfer_info",
    "read_transfer_settings",
    "set_alternative_limits",
    "set_alternative_mode",
    "set_global_limits",
]


@dataclass(frozen=True)
class SpeedLimits:
    """A download/upload limit pair in bytes/s; `0` means unlimited."""

    download: int
    upload: int


@dataclass(frozen=True)
class TransferSettings:
    """The speed-limit state a caller snapshots before changing it."""

    alternative_mode: bool
    global_limits: SpeedLimits
    alternative_limits: SpeedLimits


@dataclass(frozen=True)
class TransferInfo:
    """Current global transfer figures; the rate limits are those in force."""

    download_speed: int
    upload_speed: int
    download_rate_limit: int
    upload_rate_limit: int
    connection_status: str
    dht_nodes: int
    session_downloaded: int
    session_uploaded: int


_GLOBAL_KEYS = ("dl_limit", "up_limit")
_ALTERNATIVE_KEYS = ("alt_dl_limit", "alt_up_limit")


def read_transfer_settings(
    client: QbitTransferSettingsReader,
) -> TransferSettings:
    """Snapshot the mode and both limit pairs, in bytes/s.

    The preferences carry bytes/s on every supported version (verified
    against the Docker matrix), although the Web API reference says
    KiB/s. A negative value means unlimited and is normalized to `0`.
    Raises `QbitCoreError` on a payload missing a field: restoring a
    guessed `0` would silently remove a limit.
    """
    mode = _parse_mode(client.transfer_speed_limits_mode())
    preferences = client.app_preferences()
    return TransferSettings(
        alternative_mode=mode,
        global_limits=_limits_from_preferences(preferences, _GLOBAL_KEYS),
        alternative_limits=_limits_from_preferences(
            preferences, _ALTERNATIVE_KEYS
        ),
    )


def read_transfer_info(client: QbitTransferReader) -> TransferInfo:
    """One `transfer_info()` call reduced to `TransferInfo`."""
    data = client.transfer_info()
    return TransferInfo(
        download_speed=get_field_as_int(data, "dl_info_speed"),
        upload_speed=get_field_as_int(data, "up_info_speed"),
        download_rate_limit=max(0, get_field_as_int(data, "dl_rate_limit")),
        upload_rate_limit=max(0, get_field_as_int(data, "up_rate_limit")),
        connection_status=get_field_as_string(data, "connection_status"),
        dht_nodes=get_field_as_int(data, "dht_nodes"),
        session_downloaded=get_field_as_int(data, "dl_info_data"),
        session_uploaded=get_field_as_int(data, "up_info_data"),
    )


def set_alternative_mode(
    client: QbitTransferSettingsMutator, enabled: bool
) -> bool:
    """Enable or disable alternative limits; return whether it changed.

    Reads the mode first and writes nothing when it already matches.
    Below Web API 2.8.14 the library can only toggle, so a mode change
    by someone else between its read and its toggle inverts the result.
    """
    if _parse_mode(client.transfer_speed_limits_mode()) == enabled:
        return False
    client.transfer_set_speed_limits_mode(intended_state=enabled)
    return True


def set_alternative_limits(
    client: QbitTransferSettingsMutator, limits: SpeedLimits
) -> None:
    """Set the alternative limits (not the ones of the current mode).

    qBittorrent keeps KiB/s granularity: a value is rounded down to a
    multiple of 1024, and a non-zero value below 1024 becomes 1024.
    """
    _validate_limits(limits)
    client.app_set_preferences(_preferences_for(_ALTERNATIVE_KEYS, limits))


def set_global_limits(
    client: QbitTransferSettingsMutator, limits: SpeedLimits
) -> None:
    """Set the normal limits, same rounding as `set_alternative_limits`."""
    _validate_limits(limits)
    client.app_set_preferences(_preferences_for(_GLOBAL_KEYS, limits))


def _validate_limits(limits: SpeedLimits) -> None:
    for label, value in (
        ("download", limits.download),
        ("upload", limits.upload),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise InvalidInputError(
                f"The {label} speed limit must be an integer >= 0 in "
                f"bytes/s (0 means unlimited); got {value!r}."
            )


def _preferences_for(
    keys: tuple[str, str], limits: SpeedLimits
) -> dict[str, int]:
    return {keys[0]: limits.download, keys[1]: limits.upload}


def _limits_from_preferences(
    preferences: Mapping[str, Any], keys: tuple[str, str]
) -> SpeedLimits:
    return SpeedLimits(
        download=_read_limit(preferences, keys[0]),
        upload=_read_limit(preferences, keys[1]),
    )


def _read_limit(preferences: Mapping[str, Any], key: str) -> int:
    raw = get_field(preferences, key, None)
    if raw is None or isinstance(raw, bool):
        raise QbitCoreError(
            f"qBittorrent preferences have no usable {key!r} value."
        )
    try:
        value = int(raw)
    except (TypeError, ValueError) as error:
        raise QbitCoreError(
            f"qBittorrent preferences hold a non-numeric {key!r} value."
        ) from error
    return max(0, value)


def _parse_mode(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    normalized = str(raw).strip().lower()
    if normalized in ("1", "true"):
        return True
    if normalized in ("0", "false"):
        return False
    raise QbitCoreError(
        f"Unexpected alternative-speed mode from qBittorrent: {raw!r}."
    )
