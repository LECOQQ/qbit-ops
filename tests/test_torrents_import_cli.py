"""Test the `torrents import` CLI command: options, output, exit codes."""

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from qbit_core.shared.bencode import decode_torrent
from qbit_ops.cli.app import app
from qbit_ops.cli.exit_codes import ExitCode
from tests.integration._bencode import bencode
from tests.support import FakeQbitClient


def _build_torrent(name: str) -> bytes:
    payload = (f"synthetic payload for {name}".encode() * 64)[:16384]
    info = {
        "name": name,
        "piece length": 16384,
        "pieces": hashlib.sha1(payload, usedforsecurity=False).digest(),
        "length": len(payload),
    }
    return bencode({"info": info, "creation date": 0})


def _write_torrent(tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"{name}.torrent"
    path.write_bytes(_build_torrent(name))
    return path


def _make_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "qbit_ops.cli.rendering.is_interactive_terminal", lambda: True
    )


def _torrents_add_calls(client: FakeQbitClient) -> list[dict[str, Any]]:
    return [
        kwargs for name, _args, kwargs in client.calls if name == "torrents_add"
    ]


# --- dry-run is the default, and never mutates ---------------------------


def test_import_defaults_to_dry_run_and_performs_no_mutation(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    path = _write_torrent(tmp_path, "sample")
    client = FakeQbitClient()
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["torrents", "import", str(path)])

    assert result.exit_code == ExitCode.SUCCESS
    assert "PREVIEW" in result.stdout
    assert client.added_torrent_files == []


def test_import_refuses_non_interactive_real_run_without_yes(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    path = _write_torrent(tmp_path, "sample")
    client = FakeQbitClient()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "import", str(path), "--no-dry-run"]
    )

    assert result.exit_code == ExitCode.ERROR
    assert "--yes" in result.stderr
    assert client.added_torrent_files == []


# --- real import ----------------------------------------------------------


def test_import_yes_applies_without_prompting(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    path = _write_torrent(tmp_path, "sample")
    client = FakeQbitClient()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "import", str(path), "--no-dry-run", "--yes"]
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "APPLIED" in result.stdout
    assert len(client.added_torrent_files) == 1


def test_import_added_paused_by_default(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    path = _write_torrent(tmp_path, "sample")
    client = FakeQbitClient()
    configure_qbit_backend(client=client)

    runner.invoke(
        app, ["torrents", "import", str(path), "--no-dry-run", "--yes"]
    )

    calls = _torrents_add_calls(client)
    assert calls[0]["is_stopped"] is True


def test_import_start_flag_adds_unpaused(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    path = _write_torrent(tmp_path, "sample")
    client = FakeQbitClient()
    configure_qbit_backend(client=client)

    runner.invoke(
        app,
        ["torrents", "import", str(path), "--no-dry-run", "--yes", "--start"],
    )

    calls = _torrents_add_calls(client)
    assert calls[0]["is_stopped"] is False


def test_import_forwards_save_path_category_and_tags(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    path = _write_torrent(tmp_path, "sample")
    client = FakeQbitClient()
    configure_qbit_backend(client=client)

    runner.invoke(
        app,
        [
            "torrents",
            "import",
            str(path),
            "--no-dry-run",
            "--yes",
            "--save-path",
            "/downloads",
            "--category",
            "movies",
            "--tag",
            "imported",
            "--tag",
            "batch-1",
        ],
    )

    calls = _torrents_add_calls(client)
    assert calls[0]["save_path"] == "/downloads"
    assert calls[0]["category"] == "movies"
    assert calls[0]["tags"] == ["imported", "batch-1"]


# --- directory and zip sources ---------------------------------------------


def test_import_directory_recursive(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    _write_torrent(tmp_path, "a")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b.torrent").write_bytes(_build_torrent("b"))
    client = FakeQbitClient()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "import",
            str(tmp_path),
            "--no-dry-run",
            "--yes",
            "--recursive",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert len(client.added_torrent_files) == 1
    assert len(client.added_torrent_files[0]) == 2


def test_import_zip_archive(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    zip_path = tmp_path / "archive.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("a.torrent", _build_torrent("a"))
        archive.writestr("b.torrent", _build_torrent("b"))
    client = FakeQbitClient()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "import", str(zip_path), "--no-dry-run", "--yes"]
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert len(client.added_torrent_files[0]) == 2


# --- existing / duplicate / invalid classification -------------------------


def test_import_no_match_when_nothing_ready(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    client = FakeQbitClient()
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["torrents", "import", str(empty_dir)])

    assert result.exit_code == ExitCode.NO_MATCH


def test_import_invalid_file_forces_non_zero_exit_even_in_dry_run(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    _write_torrent(tmp_path, "good")
    (tmp_path / "bad.torrent").write_bytes(b"not bencode")
    client = FakeQbitClient()
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["torrents", "import", str(tmp_path)])

    assert result.exit_code == ExitCode.ERROR
    assert "1" in result.stdout  # invalid count is reported


def test_import_skip_existing_silences_the_notice(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    torrent_bytes = _build_torrent("present")
    path = tmp_path / "present.torrent"
    path.write_bytes(torrent_bytes)
    info_hash = decode_torrent(torrent_bytes).info_hash
    client = FakeQbitClient(torrents=[{"hash": info_hash}])
    configure_qbit_backend(client=client)

    loud = runner.invoke(app, ["torrents", "import", str(path)])
    quiet = runner.invoke(
        app, ["torrents", "import", str(path), "--skip-existing"]
    )

    assert "already present" in loud.stderr
    assert "already present" not in quiet.stderr


# --- API failure ------------------------------------------------------------


def test_import_api_failure_is_reported_and_exits_non_zero(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    path = _write_torrent(tmp_path, "sample")
    client = FakeQbitClient(torrents_add_error=RuntimeError("boom"))
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "import", str(path), "--no-dry-run", "--yes"]
    )

    assert result.exit_code == ExitCode.ERROR


# --- JSON output ------------------------------------------------------------


def test_import_json_output_schema(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    _write_torrent(tmp_path, "a")
    client = FakeQbitClient()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "import", str(tmp_path), "--format", "json"]
    )

    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["ready"] == 1
    assert payload["results"] == []


def test_import_json_output_lists_results_on_real_import(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    _write_torrent(tmp_path, "a")
    client = FakeQbitClient()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "import",
            str(tmp_path),
            "--no-dry-run",
            "--yes",
            "--format",
            "json",
        ],
    )

    payload = json.loads(result.stdout)
    assert payload["dry_run"] is False
    assert len(payload["results"]) == 1
    assert payload["results"][0]["status"] == "imported"


# --- confirmation flow (interactive, medium risk) --------------------------


def test_import_interactive_confirmation_can_be_declined(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _make_interactive(monkeypatch)
    path = _write_torrent(tmp_path, "sample")
    client = FakeQbitClient()
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "import", str(path), "--no-dry-run"],
        input="n\n",
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "Operation cancelled." in result.stdout
    assert client.added_torrent_files == []
