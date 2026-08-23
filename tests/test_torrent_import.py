"""Test `.torrent` import: bencode decoding, discovery, planning, apply."""

import hashlib
import zipfile
from pathlib import Path
from typing import Any

import pytest

from qbit_core.features.torrent_import import (
    IMPORT_BATCH_SIZE,
    MAX_ZIP_ENTRIES,
    TorrentImportError,
    apply_torrent_import,
    plan_torrent_import,
)
from qbit_core.shared.bencode import BencodeError, decode, decode_torrent
from tests.integration._bencode import bencode


def _build_torrent(name: str, *, announce: str | None = None) -> bytes:
    payload = (f"synthetic payload for {name}".encode() * 64)[:16384]
    info = {
        "name": name,
        "piece length": 16384,
        "pieces": hashlib.sha1(payload, usedforsecurity=False).digest(),
        "length": len(payload),
    }
    torrent: dict[str, Any] = {"info": info, "creation date": 0}
    if announce is not None:
        torrent["announce"] = announce
    return bencode(torrent)


def _info_hash(torrent_bytes: bytes) -> str:
    return decode_torrent(torrent_bytes).info_hash


class _FakeImportClient:
    """Minimal client fake for `qbit_core`-level import tests.

    `add_error_for` maps an infohash to an exception raised only when a
    batch containing that hash is added, letting tests build mixed
    success/failure batches deterministically.
    """

    def __init__(
        self,
        *,
        existing_hashes: list[str] | None = None,
        add_error_for: dict[str, Exception] | None = None,
    ) -> None:
        self._existing = [{"hash": h} for h in (existing_hashes or [])]
        self._add_error_for = add_error_for or {}
        self.add_calls: list[dict[str, Any]] = []

    def torrents_info(self) -> list[dict[str, Any]]:
        return self._existing

    def torrents_add(self, **kwargs: Any) -> str:
        self.add_calls.append(kwargs)
        batch_files: list[bytes] = kwargs["torrent_files"]
        for data in batch_files:
            info_hash = _info_hash(data)
            if info_hash in self._add_error_for:
                raise self._add_error_for[info_hash]
        return "Ok."


# --- bencode decoding ----------------------------------------------------


def test_decode_round_trips_encoder_output() -> None:
    value = {"a": 1, "b": [1, 2, b"raw"], "c": {"d": "e"}}
    assert decode(bencode(value)) == {
        b"a": 1,
        b"b": [1, 2, b"raw"],
        b"c": {b"d": b"e"},
    }


def test_decode_rejects_trailing_data() -> None:
    with pytest.raises(BencodeError):
        decode(bencode(1) + b"garbage")


@pytest.mark.parametrize(
    "data",
    [b"", b"x", b"i1", b"3:ab", b"d3:abe", b"l3:abc"],
)
def test_decode_rejects_malformed_input(data: bytes) -> None:
    with pytest.raises(BencodeError):
        decode(data)


def test_decode_torrent_computes_infohash_from_raw_info_span() -> None:
    torrent_bytes = _build_torrent("sample")
    decoded = decode_torrent(torrent_bytes)
    info = decode(torrent_bytes)[b"info"]
    assert decoded.info_hash == hashlib.sha1(bencode(info)).hexdigest()


def test_decode_torrent_rejects_missing_info_dict() -> None:
    with pytest.raises(BencodeError):
        decode_torrent(bencode({"announce": "http://example/announce"}))


def test_decode_torrent_rejects_non_dict_root() -> None:
    with pytest.raises(BencodeError):
        decode_torrent(bencode([1, 2, 3]))


def test_decode_torrent_rejects_deeply_nested_input_as_bencode_error() -> None:
    hostile = b"d4:infod1:x" + b"l" * 2000 + b"e" * 2000 + b"ee"
    with pytest.raises(BencodeError):
        decode_torrent(hostile)


# --- single file source ---------------------------------------------------


def test_plan_import_valid_file(tmp_path: Path) -> None:
    torrent_bytes = _build_torrent("t1")
    path = tmp_path / "t1.torrent"
    path.write_bytes(torrent_bytes)

    plan = plan_torrent_import(_FakeImportClient(), path)

    assert plan.discovered == 1
    assert len(plan.ready) == 1
    assert plan.ready[0].info_hash == _info_hash(torrent_bytes)
    assert not plan.invalid_entries


def test_plan_import_invalid_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.torrent"
    path.write_bytes(b"not bencode")

    plan = plan_torrent_import(_FakeImportClient(), path)

    assert plan.discovered == 1
    assert not plan.ready
    assert len(plan.invalid_entries) == 1


def test_plan_import_rejects_non_torrent_file(tmp_path: Path) -> None:
    path = tmp_path / "readme.txt"
    path.write_text("hello")

    with pytest.raises(TorrentImportError):
        plan_torrent_import(_FakeImportClient(), path)


def test_plan_import_rejects_missing_source(tmp_path: Path) -> None:
    with pytest.raises(TorrentImportError):
        plan_torrent_import(_FakeImportClient(), tmp_path / "missing.torrent")


# --- directory source -------------------------------------------------


def test_plan_import_directory_with_multiple_torrents(tmp_path: Path) -> None:
    for name in ("b", "a", "c"):
        (tmp_path / f"{name}.torrent").write_bytes(_build_torrent(name))

    plan = plan_torrent_import(_FakeImportClient(), tmp_path)

    assert plan.discovered == 3
    assert len(plan.ready) == 3


def test_plan_import_directory_with_one_hostile_torrent_does_not_abort(
    tmp_path: Path,
) -> None:
    (tmp_path / "good.torrent").write_bytes(_build_torrent("good"))
    hostile = b"d4:infod1:x" + b"l" * 2000 + b"e" * 2000 + b"ee"
    (tmp_path / "hostile.torrent").write_bytes(hostile)

    plan = plan_torrent_import(_FakeImportClient(), tmp_path)

    assert plan.discovered == 2
    assert len(plan.ready) == 1
    assert len(plan.invalid_entries) == 1
    assert plan.invalid_entries[0].source_name == "hostile.torrent"


def test_plan_import_directory_ignores_non_torrent_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.torrent").write_bytes(_build_torrent("a"))
    (tmp_path / "notes.txt").write_text("not a torrent")

    plan = plan_torrent_import(_FakeImportClient(), tmp_path)

    assert plan.discovered == 1


def test_plan_import_directory_recursive_disabled_ignores_subdirs(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.torrent").write_bytes(_build_torrent("a"))
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b.torrent").write_bytes(_build_torrent("b"))

    plan = plan_torrent_import(_FakeImportClient(), tmp_path, recursive=False)

    assert plan.discovered == 1


def test_plan_import_directory_recursive_enabled_descends(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.torrent").write_bytes(_build_torrent("a"))
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b.torrent").write_bytes(_build_torrent("b"))

    plan = plan_torrent_import(_FakeImportClient(), tmp_path, recursive=True)

    assert plan.discovered == 2


def test_plan_import_directory_order_is_deterministic(tmp_path: Path) -> None:
    for name in ("z", "y", "x"):
        (tmp_path / f"{name}.torrent").write_bytes(_build_torrent(name))

    first = plan_torrent_import(_FakeImportClient(), tmp_path)
    second = plan_torrent_import(_FakeImportClient(), tmp_path)

    names = [c.source_name for c in first.candidates]
    assert names == sorted(names)
    assert [c.source_name for c in second.candidates] == names


# --- zip source ---------------------------------------------------------


def _write_zip(
    path: Path, members: dict[str, bytes], **zip_kwargs: Any
) -> None:
    with zipfile.ZipFile(path, "w", **zip_kwargs) as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def test_plan_import_valid_zip(tmp_path: Path) -> None:
    zip_path = tmp_path / "archive.zip"
    _write_zip(
        zip_path,
        {"a.torrent": _build_torrent("a"), "b.torrent": _build_torrent("b")},
    )

    plan = plan_torrent_import(_FakeImportClient(), zip_path)

    assert plan.discovered == 2
    assert len(plan.ready) == 2


def test_plan_import_zip_with_subdirectories(tmp_path: Path) -> None:
    zip_path = tmp_path / "archive.zip"
    _write_zip(
        zip_path,
        {
            "dir/a.torrent": _build_torrent("a"),
            "dir/sub/b.torrent": _build_torrent("b"),
        },
    )

    plan = plan_torrent_import(_FakeImportClient(), zip_path)

    assert plan.discovered == 2
    assert [c.source_name for c in plan.candidates] == [
        "dir/a.torrent",
        "dir/sub/b.torrent",
    ]


def test_plan_import_empty_zip(tmp_path: Path) -> None:
    zip_path = tmp_path / "empty.zip"
    _write_zip(zip_path, {})

    plan = plan_torrent_import(_FakeImportClient(), zip_path)

    assert plan.discovered == 0
    assert not plan.ready


def test_plan_import_zip_with_invalid_entries(tmp_path: Path) -> None:
    zip_path = tmp_path / "archive.zip"
    _write_zip(
        zip_path,
        {"good.torrent": _build_torrent("good"), "bad.torrent": b"garbage"},
    )

    plan = plan_torrent_import(_FakeImportClient(), zip_path)

    assert plan.discovered == 2
    assert len(plan.ready) == 1
    assert len(plan.invalid_entries) == 1


def test_plan_import_zip_rejects_path_traversal(tmp_path: Path) -> None:
    zip_path = tmp_path / "evil.zip"
    _write_zip(zip_path, {"../escape.torrent": _build_torrent("escape")})

    with pytest.raises(TorrentImportError):
        plan_torrent_import(_FakeImportClient(), zip_path)


def _set_zip_encryption_bit(zip_path: Path) -> None:
    """Flip the general-purpose encryption bit on a single-entry zip.

    `zipfile` always overwrites `ZipInfo.flag_bits` on write, so the
    only way to produce an "encrypted" archive without a real
    encryption backend is to patch both header copies after the fact.
    """
    data = bytearray(zip_path.read_bytes())
    local_offset = data.find(b"PK\x03\x04") + 6
    central_offset = data.find(b"PK\x01\x02") + 8
    data[local_offset] |= 0x1
    data[central_offset] |= 0x1
    zip_path.write_bytes(bytes(data))


def test_plan_import_zip_rejects_encrypted_archive(tmp_path: Path) -> None:
    zip_path = tmp_path / "encrypted.zip"
    _write_zip(zip_path, {"a.torrent": _build_torrent("a")})
    _set_zip_encryption_bit(zip_path)

    with pytest.raises(TorrentImportError):
        plan_torrent_import(_FakeImportClient(), zip_path)


def test_plan_import_zip_rejects_archive_exceeding_entry_limit(
    tmp_path: Path,
) -> None:
    zip_path = tmp_path / "huge.zip"
    members = {f"{i}.torrent": b"x" for i in range(MAX_ZIP_ENTRIES + 1)}
    _write_zip(zip_path, members)

    with pytest.raises(TorrentImportError):
        plan_torrent_import(_FakeImportClient(), zip_path)


# --- duplicate / existing classification --------------------------------


def test_plan_import_detects_exact_duplicate_in_source(tmp_path: Path) -> None:
    data = _build_torrent("dup")
    (tmp_path / "first.torrent").write_bytes(data)
    (tmp_path / "second.torrent").write_bytes(data)

    plan = plan_torrent_import(_FakeImportClient(), tmp_path)

    assert len(plan.ready) == 1
    assert len(plan.duplicate_hashes) == 1


def test_plan_import_detects_existing_torrent(tmp_path: Path) -> None:
    data = _build_torrent("already-present")
    path = tmp_path / "t.torrent"
    path.write_bytes(data)
    info_hash = _info_hash(data)

    plan = plan_torrent_import(
        _FakeImportClient(existing_hashes=[info_hash.upper()]), path
    )

    assert not plan.ready
    assert plan.existing_hashes == (info_hash,)


# --- apply ----------------------------------------------------------------


def test_apply_import_sends_ready_candidates_paused_by_default(
    tmp_path: Path,
) -> None:
    path = tmp_path / "t.torrent"
    path.write_bytes(_build_torrent("t"))
    client = _FakeImportClient()
    plan = plan_torrent_import(client, path)

    result = apply_torrent_import(client, plan)

    assert result.imported_hashes == tuple(c.info_hash for c in plan.ready)
    assert not result.failed
    assert client.add_calls[0]["is_stopped"] is True


def test_apply_import_start_flag_adds_unpaused(tmp_path: Path) -> None:
    path = tmp_path / "t.torrent"
    path.write_bytes(_build_torrent("t"))
    client = _FakeImportClient()
    plan = plan_torrent_import(client, path)

    apply_torrent_import(client, plan, paused=False)

    assert client.add_calls[0]["is_stopped"] is False


def test_apply_import_forwards_save_path_category_and_tags(
    tmp_path: Path,
) -> None:
    path = tmp_path / "t.torrent"
    path.write_bytes(_build_torrent("t"))
    client = _FakeImportClient()
    plan = plan_torrent_import(client, path)

    apply_torrent_import(
        client,
        plan,
        save_path="/downloads",
        category="movies",
        tags=["imported"],
    )

    call = client.add_calls[0]
    assert call["save_path"] == "/downloads"
    assert call["category"] == "movies"
    assert call["tags"] == ["imported"]


def test_apply_import_records_api_failure_without_raising(
    tmp_path: Path,
) -> None:
    data = _build_torrent("t")
    path = tmp_path / "t.torrent"
    path.write_bytes(data)
    info_hash = _info_hash(data)
    client = _FakeImportClient(add_error_for={info_hash: RuntimeError("boom")})
    plan = plan_torrent_import(client, path)

    result = apply_torrent_import(client, plan)

    assert not result.imported_hashes
    assert len(result.failed) == 1
    assert result.failed[0].info_hash == info_hash


def test_apply_import_one_bad_batch_does_not_hide_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "qbit_core.features.torrent_import.IMPORT_BATCH_SIZE", 1
    )
    good = _build_torrent("good")
    bad = _build_torrent("bad")
    (tmp_path / "good.torrent").write_bytes(good)
    (tmp_path / "bad.torrent").write_bytes(bad)
    bad_hash = _info_hash(bad)
    client = _FakeImportClient(add_error_for={bad_hash: RuntimeError("boom")})
    plan = plan_torrent_import(client, tmp_path)

    result = apply_torrent_import(client, plan)

    assert result.imported_hashes == (_info_hash(good),)
    assert [f.info_hash for f in result.failed] == [bad_hash]


def test_import_batch_size_is_a_module_constant() -> None:
    assert IMPORT_BATCH_SIZE > 0
