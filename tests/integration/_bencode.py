"""Minimal BEP 0003 bencode encoder for the synthetic torrent corpus.

Encoding only -- the harness never needs to decode bencode.
"""

from __future__ import annotations

from typing import Any


def bencode(value: Any) -> bytes:
    """Encode `value` (int, bytes, str, list, or dict) as bencode.

    Dict keys are sorted by their raw byte encoding, per BEP 0003 --
    required for a deterministic, canonical encoding (and hence a
    reproducible infohash).
    """
    if isinstance(value, bool):
        raise TypeError("bencode does not support bool; use int")
    if isinstance(value, int):
        return f"i{value}e".encode("ascii")
    if isinstance(value, bytes):
        return str(len(value)).encode("ascii") + b":" + value
    if isinstance(value, str):
        return bencode(value.encode("utf-8"))
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        raw_keys = {_raw_key_bytes(key): key for key in value}
        body = b"".join(
            bencode(raw_key) + bencode(value[raw_keys[raw_key]])
            for raw_key in sorted(raw_keys)
        )
        return b"d" + body + b"e"

    raise TypeError(f"bencode cannot encode {type(value).__name__!r}")


def _raw_key_bytes(key: Any) -> bytes:
    if isinstance(key, bytes):
        return key
    if isinstance(key, str):
        return key.encode("utf-8")
    raise TypeError(
        f"bencode dict keys must be str or bytes, got {type(key).__name__!r}"
    )
