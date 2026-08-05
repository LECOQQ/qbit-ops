"""Minimal bencode decoder (BEP 0003), scoped to `.torrent` validation.

Only decodes -- no encoder needed here. `decode_torrent` additionally
returns the raw byte span of the top-level `info` value so its SHA-1
infohash can be computed from the original bytes rather than a
re-encoding, avoiding any risk of a round-trip mismatch.
"""

import hashlib
from dataclasses import dataclass
from typing import Any


class BencodeError(ValueError):
    """Report malformed or unsupported bencode input."""


def decode(data: bytes) -> Any:
    """Decode a complete bencode buffer, rejecting trailing bytes."""
    value, offset = _decode(data, 0)
    if offset != len(data):
        raise BencodeError("trailing data after top-level value")
    return value


def _decode(data: bytes, offset: int) -> tuple[Any, int]:
    if offset >= len(data):
        raise BencodeError("unexpected end of data")

    marker = data[offset]
    if marker == ord("i"):
        return _decode_int(data, offset)
    if marker == ord("l"):
        return _decode_list(data, offset)
    if marker == ord("d"):
        return _decode_dict(data, offset)
    if ord("0") <= marker <= ord("9"):
        return _decode_bytes(data, offset)
    raise BencodeError(f"invalid marker at offset {offset}")


def _decode_int(data: bytes, offset: int) -> tuple[int, int]:
    end = data.find(b"e", offset)
    if end == -1:
        raise BencodeError("unterminated integer")
    raw = data[offset + 1 : end]
    if raw == b"" or raw == b"-" or (raw.startswith(b"0") and raw != b"0"):
        raise BencodeError(f"malformed integer literal: {raw!r}")
    try:
        return int(raw), end + 1
    except ValueError as error:
        raise BencodeError(f"malformed integer literal: {raw!r}") from error


def _decode_bytes(data: bytes, offset: int) -> tuple[bytes, int]:
    colon = data.find(b":", offset)
    if colon == -1:
        raise BencodeError("unterminated byte-string length")
    length_raw = data[offset:colon]
    if not length_raw.isdigit():
        raise BencodeError(f"malformed byte-string length: {length_raw!r}")
    length = int(length_raw)
    start = colon + 1
    end = start + length
    if end > len(data):
        raise BencodeError("byte-string length exceeds buffer")
    return data[start:end], end


def _decode_list(data: bytes, offset: int) -> tuple[list[Any], int]:
    offset += 1
    items: list[Any] = []
    while True:
        if offset >= len(data):
            raise BencodeError("unterminated list")
        if data[offset] == ord("e"):
            return items, offset + 1
        value, offset = _decode(data, offset)
        items.append(value)


def _decode_dict(data: bytes, offset: int) -> tuple[dict[bytes, Any], int]:
    offset += 1
    result: dict[bytes, Any] = {}
    while True:
        if offset >= len(data):
            raise BencodeError("unterminated dict")
        if data[offset] == ord("e"):
            return result, offset + 1
        key, offset = _decode_bytes(data, offset)
        value, offset = _decode(data, offset)
        result[key] = value


@dataclass(frozen=True)
class DecodedTorrent:
    """A parsed `.torrent` file: its root dict and computed infohash."""

    root: dict[bytes, Any]
    info_hash: str


def decode_torrent(data: bytes) -> DecodedTorrent:
    """Parse `.torrent` bytes, validating shape and computing the infohash.

    Requires a top-level dict with an `info` dict value. The infohash is
    the SHA-1 of `info`'s original byte span, not a re-encoding of the
    decoded value.
    """
    if not data.startswith(b"d"):
        raise BencodeError("torrent file is not a bencoded dict")

    offset = 1
    result: dict[bytes, Any] = {}
    info_span: tuple[int, int] | None = None
    while True:
        if offset >= len(data):
            raise BencodeError("unterminated dict")
        if data[offset] == ord("e"):
            offset += 1
            break
        key, offset = _decode_bytes(data, offset)
        value_start = offset
        value, offset = _decode(data, offset)
        result[key] = value
        if key == b"info":
            info_span = (value_start, offset)

    if offset != len(data):
        raise BencodeError("trailing data after top-level value")

    info = result.get(b"info")
    if not isinstance(info, dict) or info_span is None:
        raise BencodeError("torrent file has no 'info' dict")

    info_hash = hashlib.sha1(data[info_span[0] : info_span[1]]).hexdigest()
    return DecodedTorrent(root=result, info_hash=info_hash)
