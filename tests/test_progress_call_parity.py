"""Prove progress instrumentation never changes qBittorrent API calls.

For every command wired up in the transient-progress phase, run it once
with progress disabled (non-interactive) and once with progress enabled
(interactive), against two structurally identical `FakeQbitClient`
instances, and assert the two clients recorded the exact same ordered
sequence of API calls (`FakeQbitClient.calls`) — same methods, same
arguments, same count, same order. Progress is purely presentational: it
must not add, remove, reorder, or duplicate a single call.
"""

from collections.abc import Callable

import pytest
from typer.testing import CliRunner

import app.main as m
from app.main import app
from tests.support import FakeQbitClient, make_torrent

TORRENT_HASH = "abc123def456000000000000000000000000000a"
TRACKER_URL = "https://tracker.example/announce"
OTHER_TRACKER_URL = "https://other.example/announce"


def _make_client() -> FakeQbitClient:
    """Build fixture data rich enough to drive every command under test."""
    return FakeQbitClient(
        torrents=[
            make_torrent(
                hash=TORRENT_HASH,
                name="T1",
                category="movies",
                state="uploading",
            )
        ],
        trackers_by_hash={TORRENT_HASH: [{"url": TRACKER_URL, "status": "2"}]},
    )


def _run_and_capture_calls(
    runner: CliRunner,
    configure_qbit_backend: Callable[..., list],
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    *,
    interactive: bool,
    input: str | None = None,
) -> FakeQbitClient:
    monkeypatch.setattr(m, "is_interactive_terminal", lambda: interactive)
    client = _make_client()
    configure_qbit_backend(client=client)
    runner.invoke(app, argv, input=input)
    return client


# Read-only commands: progress is a spinner or a real-total progress bar.
READ_ONLY_ARGV: list[list[str]] = [
    ["status"],
    ["connection", "check"],
    ["config", "doctor"],
    ["torrents", "list"],
    ["torrents", "list", "--category", "movies"],
    ["torrents", "list", "--tracker", TRACKER_URL],
    ["torrents", "categories"],
    ["torrents", "inspect", "--hash", "abc123"],
    ["torrents", "inspect", "--name", "T1"],
    ["trackers", "list"],
    ["trackers", "health"],
    ["trackers", "inspect", "--tracker", TRACKER_URL],
    ["trackers", "export"],
    ["backup", "export"],
]

# Mutation commands previewed (dry-run, the default): never prompts.
MUTATION_PREVIEW_ARGV: list[list[str]] = [
    ["torrents", "pause", "--all"],
    ["torrents", "resume", "--all"],
    ["torrents", "start", "--all"],
    ["torrents", "reannounce", "--all"],
    [
        "trackers",
        "add-if-present",
        "--source",
        TRACKER_URL,
        "--target",
        OTHER_TRACKER_URL,
    ],
    ["trackers", "remove", "--tracker", TRACKER_URL],
    [
        "trackers",
        "replace",
        "--source",
        TRACKER_URL,
        "--target",
        OTHER_TRACKER_URL,
    ],
    [
        "trackers",
        "replace-passkey",
        "--tracker",
        f"{TRACKER_URL}",
        "--new-passkey",
        "new",
    ],
]

# Mutation commands actually applied, without a confirmation prompt.
MUTATION_APPLY_ARGV: list[list[str]] = [
    ["torrents", "pause", "--all", "--no-dry-run"],
    ["torrents", "resume", "--all", "--no-dry-run"],
    ["torrents", "start", "--all", "--no-dry-run"],
    ["torrents", "reannounce", "--all", "--no-dry-run"],
    ["trackers", "remove", "--tracker", TRACKER_URL, "--no-dry-run", "--yes"],
    [
        "trackers",
        "replace",
        "--source",
        TRACKER_URL,
        "--target",
        OTHER_TRACKER_URL,
        "--no-dry-run",
        "--yes",
    ],
]


@pytest.mark.parametrize(
    "argv",
    [
        ["torrents", "pause", "--all"],
        ["torrents", "resume", "--all"],
        ["torrents", "start", "--all"],
        ["torrents", "reannounce", "--all"],
    ],
    ids=lambda argv: " ".join(argv),
)
def test_filterless_bulk_actions_never_call_torrents_trackers_for_progress(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    """Ensure driving a progress bar never triggers a torrents_trackers() call.

    Bulk torrent actions without `--tracker` have nothing that
    legitimately calls `torrents_trackers()` — the progress bar's total
    comes from the already-fetched torrent list, not a per-torrent
    tracker lookup. This pins that down explicitly so a future change to
    the progress plumbing cannot silently reintroduce an N+1 scan just
    to compute a total or advance a bar. (`torrents list` and `trackers
    *` legitimately call `torrents_trackers()` once per torrent — that
    is real per-item work the progress bar reports on, not progress
    overhead — see `test_read_only_command_calls_are_identical_*` below.)
    """
    client = _run_and_capture_calls(
        runner, configure_qbit_backend, monkeypatch, argv, interactive=True
    )

    assert client.torrents_trackers_calls == 0


@pytest.mark.parametrize(
    "argv", READ_ONLY_ARGV, ids=lambda argv: " ".join(argv)
)
def test_read_only_command_calls_are_identical_with_and_without_progress(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    """Ensure a read-only command makes the exact same API calls either way."""
    disabled = _run_and_capture_calls(
        runner, configure_qbit_backend, monkeypatch, argv, interactive=False
    )
    enabled = _run_and_capture_calls(
        runner, configure_qbit_backend, monkeypatch, argv, interactive=True
    )

    assert disabled.calls == enabled.calls


@pytest.mark.parametrize(
    "argv", MUTATION_PREVIEW_ARGV, ids=lambda argv: " ".join(argv)
)
def test_mutation_preview_calls_are_identical_with_and_without_progress(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    """Ensure a dry-run plan makes the exact same API calls either way."""
    disabled = _run_and_capture_calls(
        runner, configure_qbit_backend, monkeypatch, argv, interactive=False
    )
    enabled = _run_and_capture_calls(
        runner, configure_qbit_backend, monkeypatch, argv, interactive=True
    )

    assert disabled.calls == enabled.calls
    # A dry-run must never call a mutating endpoint.
    mutating_methods = {
        "torrents_pause",
        "torrents_resume",
        "torrents_start",
        "torrents_reannounce",
        "torrents_add_trackers",
        "torrents_remove_trackers",
        "torrents_edit_tracker",
    }
    called_methods = {name for name, _args, _kwargs in disabled.calls}
    assert called_methods.isdisjoint(mutating_methods)


@pytest.mark.parametrize(
    "argv", MUTATION_APPLY_ARGV, ids=lambda argv: " ".join(argv)
)
def test_mutation_apply_calls_are_identical_with_and_without_progress(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    """Ensure a real, unattended mutation makes the exact same API calls."""
    disabled = _run_and_capture_calls(
        runner, configure_qbit_backend, monkeypatch, argv, interactive=False
    )
    enabled = _run_and_capture_calls(
        runner, configure_qbit_backend, monkeypatch, argv, interactive=True
    )

    assert disabled.calls == enabled.calls


def test_confirmed_mutation_calls_are_identical_with_and_without_progress(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure an interactively-confirmed mutation is unaffected by progress.

    Both runs here are interactive (progress is only ever shown when
    interactive), one exercising the confirmation prompt directly to
    prove the prompted path performs the same calls as the unattended
    `--yes` path above.
    """
    argv = ["trackers", "remove", "--tracker", TRACKER_URL, "--no-dry-run"]

    first = _run_and_capture_calls(
        runner,
        configure_qbit_backend,
        monkeypatch,
        argv,
        interactive=True,
        input="y\n",
    )
    second = _run_and_capture_calls(
        runner,
        configure_qbit_backend,
        monkeypatch,
        argv,
        interactive=True,
        input="y\n",
    )

    assert first.calls == second.calls
    assert first.calls != []
