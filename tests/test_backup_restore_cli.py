"""Test the `backup restore` CLI command: options, output, exit codes."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from qbit_ops.cli.app import app
from qbit_ops.cli.exit_codes import ExitCode
from tests.support import FakeQbitClient

TORRENT_HASH = "hash-a"


def _write_export(tmp_path: Path, torrents: list[dict[str, Any]]) -> Path:
    path = tmp_path / "export.json"
    path.write_text(json.dumps({"torrents": torrents}), encoding="utf-8")
    return path


def _export_torrent(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "hash": TORRENT_HASH,
        "name": "Torrent A",
        "category": "movies",
        "tags": ["imported"],
        "trackers": [
            {
                "url": "https://tracker.example/announce",
                "status": "2",
                "disabled": False,
            }
        ],
    }
    entry.update(overrides)
    return entry


def _make_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "qbit_ops.cli.rendering.is_interactive_terminal", lambda: True
    )


def test_restore_defaults_to_dry_run_and_performs_no_mutation(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    export_file = _write_export(tmp_path, [_export_torrent()])
    client = FakeQbitClient(
        torrents=[{"hash": TORRENT_HASH, "name": "Torrent A", "category": ""}]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["backup", "restore", str(export_file)])

    assert result.exit_code == ExitCode.SUCCESS
    assert "PREVIEW" in result.stdout
    assert client.set_categories == []
    assert client.added_tags == []
    assert client.added_trackers == []


def test_restore_refuses_non_interactive_real_run_without_yes(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    export_file = _write_export(tmp_path, [_export_torrent()])
    client = FakeQbitClient(
        torrents=[{"hash": TORRENT_HASH, "name": "Torrent A", "category": ""}]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["backup", "restore", str(export_file), "--no-dry-run"]
    )

    assert result.exit_code == ExitCode.ERROR
    assert "--yes" in result.stderr
    assert client.set_categories == []


def test_restore_yes_applies_without_prompting(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    export_file = _write_export(tmp_path, [_export_torrent()])
    client = FakeQbitClient(
        torrents=[{"hash": TORRENT_HASH, "name": "Torrent A", "category": ""}]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["backup", "restore", str(export_file), "--no-dry-run", "--yes"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "APPLIED" in result.stdout
    assert client.set_categories == [(TORRENT_HASH, "movies")]
    assert client.added_tags == [(TORRENT_HASH, ["imported"])]
    assert client.added_trackers == [
        (TORRENT_HASH, ["https://tracker.example/announce"])
    ]


def test_restore_never_overwrites_an_existing_category(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    export_file = _write_export(tmp_path, [_export_torrent(category="movies")])
    client = FakeQbitClient(
        torrents=[
            {"hash": TORRENT_HASH, "name": "Torrent A", "category": "kept"}
        ]
    )
    configure_qbit_backend(client=client)

    runner.invoke(
        app,
        ["backup", "restore", str(export_file), "--no-dry-run", "--yes"],
    )

    assert client.set_categories == []


def test_restore_no_match_when_nothing_present_locally(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    export_file = _write_export(tmp_path, [_export_torrent(hash="missing")])
    client = FakeQbitClient(torrents=[])
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["backup", "restore", str(export_file)])

    assert result.exit_code == ExitCode.NO_MATCH


def test_restore_api_failure_is_reported_and_exits_non_zero(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    export_file = _write_export(tmp_path, [_export_torrent()])
    client = FakeQbitClient(
        torrents=[{"hash": TORRENT_HASH, "name": "Torrent A", "category": ""}],
        set_category_error=RuntimeError("boom"),
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["backup", "restore", str(export_file), "--no-dry-run", "--yes"],
    )

    assert result.exit_code == ExitCode.ERROR


def test_restore_invalid_export_file_fails_cleanly(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    export_file = tmp_path / "bad.json"
    export_file.write_text("not json", encoding="utf-8")
    client = FakeQbitClient()
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["backup", "restore", str(export_file)])

    assert result.exit_code == ExitCode.ERROR


def test_restore_json_output_schema(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    export_file = _write_export(tmp_path, [_export_torrent()])
    client = FakeQbitClient(
        torrents=[{"hash": TORRENT_HASH, "name": "Torrent A", "category": ""}]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["backup", "restore", str(export_file), "--format", "json"]
    )

    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["matched"] == 1
    assert payload["category_changes"] == 1
    assert payload["result"] is None


def test_restore_json_output_reports_result_on_real_run(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    export_file = _write_export(tmp_path, [_export_torrent()])
    client = FakeQbitClient(
        torrents=[{"hash": TORRENT_HASH, "name": "Torrent A", "category": ""}]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "backup",
            "restore",
            str(export_file),
            "--no-dry-run",
            "--yes",
            "--format",
            "json",
        ],
    )

    payload = json.loads(result.stdout)
    assert payload["dry_run"] is False
    assert payload["result"]["categories_restored"] == 1
    assert payload["result"]["tags_restored"] == 1
    assert payload["result"]["trackers_restored"] == 1
    assert payload["result"]["failures"] == []


def test_restore_verbose_never_prints_raw_tracker_urls(
    runner: CliRunner,
    configure_qbit_backend,
    tmp_path: Path,
) -> None:
    export_file = _write_export(tmp_path, [_export_torrent()])
    client = FakeQbitClient(
        torrents=[{"hash": TORRENT_HASH, "name": "Torrent A", "category": ""}]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["backup", "restore", str(export_file), "--verbose"]
    )

    assert "tracker.example" not in result.stdout


def test_restore_interactive_confirmation_can_be_declined(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _make_interactive(monkeypatch)
    export_file = _write_export(tmp_path, [_export_torrent()])
    client = FakeQbitClient(
        torrents=[{"hash": TORRENT_HASH, "name": "Torrent A", "category": ""}]
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["backup", "restore", str(export_file), "--no-dry-run"],
        input="n\n",
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "Operation cancelled." in result.stdout
    assert client.set_categories == []
