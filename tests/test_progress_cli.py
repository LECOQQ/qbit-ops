"""Test transient progress feedback at the CLI level.

Covers the read-only and mutation command wiring introduced alongside
`qbit_ops.ui.progress_enabled`/`transient_spinner`/`transient_progress`:
interactive table mode shows progress, machine-readable/non-interactive
modes stay silent, progress uses a real known total where the domain
does one API call per item, and progress never changes mutation
outcomes, exit codes, or the plan/apply invariant.
"""

from collections.abc import Generator
from contextlib import contextmanager

import pytest
from typer.testing import CliRunner

import qbit_ops.main as m
from qbit_ops.main import ExitCode, TrackerStatusExitCode, app
from qbit_ops.ui import ProgressCallback
from tests.support import FakeQbitClient, make_torrent

TORRENT_HASH = "abc123def456000000000000000000000000000a"


def _make_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m, "is_interactive_terminal", lambda: True)


def _spy_on_progress_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[int, int]]:
    """Record every `(completed, total)` advance call via `transient_progress`.

    Wraps `qbit_ops.main.transient_progress` (the name bound inside
    `qbit_ops.main`, not `qbit_ops.ui`'s own copy — see
    `from ... import ...` binding) so the real context manager still
    runs, but every `advance()` call is also captured for assertions.
    """
    calls: list[tuple[int, int]] = []
    real_transient_progress = m.transient_progress

    def _spying_transient_progress(
        message: str, *, enabled: bool, total: int | None = None
    ) -> Generator[ProgressCallback]:
        with real_transient_progress(
            message, enabled=enabled, total=total
        ) as advance:

            def _spying_advance(completed: int, total: int) -> None:
                calls.append((completed, total))
                advance(completed, total)

            yield _spying_advance

    monkeypatch.setattr(
        m, "transient_progress", contextmanager(_spying_transient_progress)
    )
    return calls


def _client_with_torrents(count: int) -> FakeQbitClient:
    torrents = [
        make_torrent(hash=str(index).zfill(40), name=f"T{index}")
        for index in range(count)
    ]
    trackers_by_hash = {
        torrent["hash"]: [
            {"url": "https://tracker.example/announce", "status": "2"}
        ]
        for torrent in torrents
    }
    return FakeQbitClient(torrents=torrents, trackers_by_hash=trackers_by_hash)


# --- Read-only commands ----------------------------------------------------


def test_torrents_list_unfiltered_uses_a_spinner_not_a_progress_bar(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure unfiltered `torrents list` never drives a progress bar.

    Since this phase, a filter-less (or non-tracker-filtered) listing
    never calls `torrents_trackers()`, so there is nothing with a real
    per-item total to report -- it uses a spinner instead (see
    `test_progress.py` and docs/COMMANDS.md, "Progress & Spinner
    Behavior").
    """
    _make_interactive(monkeypatch)
    calls = _spy_on_progress_calls(monkeypatch)
    configure_qbit_backend(client=_client_with_torrents(5))

    result = runner.invoke(app, ["torrents", "list"])

    assert result.exit_code == ExitCode.SUCCESS
    assert calls == []


def test_torrents_list_with_tracker_filter_enables_configured_progress(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure `torrents list --tracker ...` still drives a real progress bar."""
    _make_interactive(monkeypatch)
    calls = _spy_on_progress_calls(monkeypatch)
    configure_qbit_backend(client=_client_with_torrents(5))

    result = runner.invoke(
        app, ["torrents", "list", "--tracker", "tracker.example"]
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert calls, "expected at least one progress advance call"
    assert calls[-1] == (5, 5)


def test_torrents_list_json_emits_no_stderr(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure the same command in --format json stays silent on stderr."""
    _make_interactive(monkeypatch)
    configure_qbit_backend(client=_client_with_torrents(5))

    result = runner.invoke(app, ["torrents", "list", "--format", "json"])

    assert result.exit_code == ExitCode.SUCCESS
    assert result.stderr == ""


def test_trackers_status_progress_uses_the_real_torrent_total(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure tracker-status progress reports the real scanned total."""
    _make_interactive(monkeypatch)
    calls = _spy_on_progress_calls(monkeypatch)
    configure_qbit_backend(client=_client_with_torrents(7))

    result = runner.invoke(app, ["trackers", "status"])

    assert result.exit_code == TrackerStatusExitCode.HEALTHY
    assert calls[-1] == (7, 7)


def test_unsupported_format_fails_before_any_progress(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure an unsupported --format fails before progress or an API call."""
    _make_interactive(monkeypatch)
    calls = _spy_on_progress_calls(monkeypatch)
    client = _client_with_torrents(3)
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["trackers", "export", "--format", "csv"])

    assert result.exit_code == ExitCode.ERROR
    assert calls == []
    assert client.torrents_info_calls == 0


@pytest.mark.parametrize(
    "argv",
    [
        ["status"],
        ["torrents", "list"],
        ["torrents", "categories"],
        ["trackers", "list"],
        ["trackers", "status"],
    ],
)
def test_read_only_command_non_interactive_emits_no_stderr(
    runner: CliRunner,
    configure_qbit_backend,
    argv: list[str],
) -> None:
    """Ensure non-interactive (CliRunner's default) execution stays silent."""
    configure_qbit_backend(client=_client_with_torrents(3))

    result = runner.invoke(app, argv)

    assert result.stderr == ""


# --- Mutation commands -------------------------------------------------


def test_mutation_planning_enables_transient_progress(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure mutation planning drives progress on a real TTY."""
    _make_interactive(monkeypatch)
    calls = _spy_on_progress_calls(monkeypatch)
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_HASH, name="T1")],
        trackers_by_hash={
            TORRENT_HASH: [
                {"url": "https://tracker.example/announce", "status": "2"}
            ]
        },
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["trackers", "remove", "--tracker", "https://tracker.example/announce"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert calls


def test_progress_never_causes_an_additional_scan(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure enabling progress does not add an extra torrents_info() call."""
    _make_interactive(monkeypatch)
    _spy_on_progress_calls(monkeypatch)
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_HASH, name="T1")],
        trackers_by_hash={
            TORRENT_HASH: [
                {"url": "https://tracker.example/announce", "status": "2"}
            ]
        },
    )
    configure_qbit_backend(client=client)

    runner.invoke(
        app,
        ["trackers", "remove", "--tracker", "https://tracker.example/announce"],
    )

    # Exactly one full torrent scan: the plan's own scan, never re-scanned
    # for progress or for the (later, unconfirmed) apply.
    assert client.torrents_info_calls == 1


@pytest.mark.parametrize(
    "interactive",
    [True, False],
)
def test_no_match_still_reports_no_match_regardless_of_progress(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
    interactive: bool,
) -> None:
    """Ensure progress never changes the NO_MATCH mutation outcome."""
    monkeypatch.setattr(m, "is_interactive_terminal", lambda: interactive)
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_HASH, name="T1")],
        trackers_by_hash={
            TORRENT_HASH: [
                {"url": "https://tracker.example/announce", "status": "2"}
            ]
        },
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "trackers",
            "remove",
            "--tracker",
            "https://nonexistent.example/announce",
            "--no-dry-run",
        ],
    )

    assert result.exit_code == ExitCode.NO_MATCH
    assert "NO_MATCH" in result.stdout
    assert client.removed_trackers == []


def test_no_changes_still_reports_no_changes(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure progress never changes the NO_CHANGES mutation outcome."""
    _make_interactive(monkeypatch)
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_HASH, state="uploading")],
        trackers_by_hash={TORRENT_HASH: []},
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["torrents", "resume", "--all", "--no-dry-run"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "NO_CHANGES" in result.stdout
    assert client.resumed_hashes == []


def test_dry_run_still_reports_preview(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure progress never changes the PREVIEW mutation outcome."""
    _make_interactive(monkeypatch)
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_HASH, name="T1")],
        trackers_by_hash={
            TORRENT_HASH: [
                {"url": "https://tracker.example/announce", "status": "2"}
            ]
        },
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["trackers", "remove", "--tracker", "https://tracker.example/announce"],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "PREVIEW" in result.stdout
    assert client.removed_trackers == []


def test_accepted_execution_still_reports_applied(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure progress never changes the APPLIED mutation outcome."""
    _make_interactive(monkeypatch)
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_HASH, name="T1")],
        trackers_by_hash={
            TORRENT_HASH: [
                {"url": "https://tracker.example/announce", "status": "2"}
            ]
        },
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "trackers",
            "remove",
            "--tracker",
            "https://tracker.example/announce",
            "--no-dry-run",
        ],
        input="y\n",
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "Applied." in result.stdout
    assert client.removed_trackers == [
        (TORRENT_HASH, ["https://tracker.example/announce"])
    ]


def test_declined_confirmation_still_reports_cancelled(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure progress is stopped before the prompt, and CANCELLED holds."""
    _make_interactive(monkeypatch)
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_HASH, name="T1")],
        trackers_by_hash={
            TORRENT_HASH: [
                {"url": "https://tracker.example/announce", "status": "2"}
            ]
        },
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "trackers",
            "remove",
            "--tracker",
            "https://tracker.example/announce",
            "--no-dry-run",
        ],
        input="n\n",
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "Operation cancelled." in result.stdout
    assert client.removed_trackers == []


def test_mutation_plan_is_built_once_and_reused_for_apply(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure the same plan drives both the preview and the real apply.

    A regression here would show up as a second `torrents_info()` call
    (a rescan) between the confirmation prompt and the actual mutation.
    """
    _make_interactive(monkeypatch)
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_HASH, name="T1")],
        trackers_by_hash={
            TORRENT_HASH: [
                {"url": "https://tracker.example/announce", "status": "2"}
            ]
        },
    )
    configure_qbit_backend(client=client)

    runner.invoke(
        app,
        [
            "trackers",
            "remove",
            "--tracker",
            "https://tracker.example/announce",
            "--no-dry-run",
        ],
        input="y\n",
    )

    assert client.torrents_info_calls == 1
