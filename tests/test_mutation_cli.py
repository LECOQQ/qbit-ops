"""Test the CLI-level mutation confirmation policy and secret redaction.

Covers the execution-safety model introduced alongside `app.execution`:
dry-run-by-default, risk-tiered confirmation, `--yes`, non-interactive
refusal, cancellation semantics, and passkey redaction in prompts and
verbose output.
"""

import pytest
from typer.testing import CliRunner

from app.main import ExitCode, app
from tests.support import FakeQbitClient, make_torrent

TORRENT_HASH = "abc123def456000000000000000000000000000a"
SECRET_PASSKEY_MARKER = "UNMISTAKABLE-SECRET-PASSKEY-42"
NEW_PASSKEY_MARKER = "UNMISTAKABLE-NEW-PASSKEY-99"


def _client_with_tracker(tracker_url: str) -> FakeQbitClient:
    return FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_HASH, name="Fake Torrent")],
        trackers_by_hash={TORRENT_HASH: [{"url": tracker_url, "status": "2"}]},
    )


def _make_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.main.is_interactive_terminal", lambda: True)


# --- Dry-run is the default for every mutation command ------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["torrents", "pause", "--all"],
        ["torrents", "resume", "--all"],
        ["torrents", "start", "--all"],
        ["torrents", "reannounce", "--all"],
        [
            "trackers",
            "add-if-present",
            "--source",
            "https://tracker.example/announce",
            "--target",
            "https://other.example/announce",
        ],
        ["trackers", "remove", "--tracker", "https://tracker.example/announce"],
        [
            "trackers",
            "replace",
            "--source",
            "https://tracker.example/announce",
            "--target",
            "https://other.example/announce",
        ],
        [
            "trackers",
            "replace-passkey",
            "--tracker",
            "https://tracker.example/announce/{passkey}",
            "--new-passkey",
            "new",
        ],
    ],
)
def test_mutation_defaults_to_dry_run_and_performs_no_mutation(
    runner: CliRunner,
    configure_qbit_backend,
    argv: list[str],
) -> None:
    """Ensure every mutation command previews only by default."""
    client = _client_with_tracker("https://tracker.example/announce")
    configure_qbit_backend(client=client)

    result = runner.invoke(app, argv)

    assert result.exit_code in (ExitCode.SUCCESS, ExitCode.NO_MATCH)
    assert "PREVIEW" in result.stdout
    assert client.paused_hashes == []
    assert client.resumed_hashes == []
    assert client.started_hashes == []
    assert client.reannounced_hashes == []
    assert client.added_trackers == []
    assert client.removed_trackers == []
    assert client.edited_trackers == []


# --- Low risk: no confirmation ever, no --yes flag exposed ---------------


@pytest.mark.parametrize("command", ["pause", "resume", "start", "reannounce"])
def test_low_risk_torrent_commands_do_not_expose_yes(
    runner: CliRunner,
    command: str,
) -> None:
    """Ensure low-risk torrent commands never expose --yes."""
    result = runner.invoke(app, ["torrents", command, "--help"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "--yes" not in result.stdout


def test_low_risk_mutation_applies_without_confirmation_non_interactively(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure a low-risk mutation applies immediately, non-interactively too."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_HASH, state="uploading")],
        trackers_by_hash={TORRENT_HASH: []},
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["torrents", "pause", "--all", "--no-dry-run"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "APPLIED" in result.stdout
    assert client.paused_hashes == [[TORRENT_HASH]]


# --- Medium/high risk: --yes bypasses confirmation ------------------------


@pytest.mark.parametrize(
    "argv,attribute",
    [
        (
            [
                "trackers",
                "add-if-present",
                "--source",
                "https://tracker.example/announce",
                "--target",
                "https://other.example/announce",
                "--no-dry-run",
                "--yes",
            ],
            "added_trackers",
        ),
        (
            [
                "trackers",
                "remove",
                "--tracker",
                "https://tracker.example/announce",
                "--no-dry-run",
                "--yes",
            ],
            "removed_trackers",
        ),
        (
            [
                "trackers",
                "replace",
                "--source",
                "https://tracker.example/announce",
                "--target",
                "https://other.example/announce",
                "--no-dry-run",
                "--yes",
            ],
            "edited_trackers",
        ),
    ],
)
def test_yes_applies_without_prompting(
    runner: CliRunner,
    configure_qbit_backend,
    argv: list[str],
    attribute: str,
) -> None:
    """Ensure --yes applies real changes without any confirmation prompt."""
    client = _client_with_tracker("https://tracker.example/announce")
    configure_qbit_backend(client=client)

    result = runner.invoke(app, argv)

    assert result.exit_code == ExitCode.SUCCESS
    assert "APPLIED" in result.stdout
    assert getattr(client, attribute) != []


def test_yes_never_implies_no_dry_run(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure --yes alone, without --no-dry-run, keeps the command dry-run."""
    client = _client_with_tracker("https://tracker.example/announce")
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "trackers",
            "remove",
            "--tracker",
            "https://tracker.example/announce",
            "--yes",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "PREVIEW" in result.stdout
    assert client.removed_trackers == []


# --- Non-interactive refusal for medium/high risk -------------------------


@pytest.mark.parametrize(
    "argv",
    [
        [
            "trackers",
            "add-if-present",
            "--source",
            "https://tracker.example/announce",
            "--target",
            "https://other.example/announce",
            "--no-dry-run",
        ],
        [
            "trackers",
            "remove",
            "--tracker",
            "https://tracker.example/announce",
            "--no-dry-run",
        ],
        [
            "trackers",
            "replace",
            "--source",
            "https://tracker.example/announce",
            "--target",
            "https://other.example/announce",
            "--no-dry-run",
        ],
    ],
)
def test_medium_high_risk_refuses_non_interactive_without_yes(
    runner: CliRunner,
    configure_qbit_backend,
    argv: list[str],
) -> None:
    """Ensure medium/high risk fails clearly instead of hanging or mutating."""
    client = _client_with_tracker("https://tracker.example/announce")
    configure_qbit_backend(client=client)

    result = runner.invoke(app, argv)

    assert result.exit_code == ExitCode.ERROR
    assert "--yes" in result.stderr
    assert client.added_trackers == []
    assert client.removed_trackers == []
    assert client.edited_trackers == []


# --- Interactive confirmation: decline and confirm -------------------------


def test_declining_confirmation_cancels_cleanly(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure declining confirmation performs no mutation and exits 0."""
    _make_interactive(monkeypatch)
    client = _client_with_tracker("https://tracker.example/announce")
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


def test_confirming_applies_the_previously_built_plan(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure confirming applies exactly the previewed change."""
    _make_interactive(monkeypatch)
    client = _client_with_tracker("https://tracker.example/announce")
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


# --- No-match plans never prompt, even for high risk ------------------------


def test_no_match_plan_does_not_prompt_or_refuse(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure an empty plan skips confirmation entirely and exits NO_MATCH."""
    client = _client_with_tracker("https://tracker.example/announce")
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
    assert client.removed_trackers == []


# --- Secret redaction: passkeys never reach stdout/stderr -------------------


def test_replace_passkey_confirmation_never_shows_the_passkey(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure the confirmation prompt for replace-passkey omits any passkey."""
    _make_interactive(monkeypatch)
    client = _client_with_tracker(
        f"https://tracker.example/announce/{SECRET_PASSKEY_MARKER}"
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "trackers",
            "replace-passkey",
            "--tracker",
            "https://tracker.example/announce/{passkey}",
            "--new-passkey",
            NEW_PASSKEY_MARKER,
            "--no-dry-run",
            "--verbose",
        ],
        input="y\n",
    )

    combined = result.stdout + result.stderr
    assert SECRET_PASSKEY_MARKER not in combined
    assert NEW_PASSKEY_MARKER not in combined
    assert client.edited_trackers == [
        (
            TORRENT_HASH,
            f"https://tracker.example/announce/{SECRET_PASSKEY_MARKER}",
            f"https://tracker.example/announce/{NEW_PASSKEY_MARKER}",
        )
    ]


def test_replace_passkey_dry_run_never_shows_the_passkey(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure even a dry-run preview never renders the new passkey."""
    client = _client_with_tracker(
        f"https://tracker.example/announce/{SECRET_PASSKEY_MARKER}"
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "trackers",
            "replace-passkey",
            "--tracker",
            "https://tracker.example/announce/{passkey}",
            "--new-passkey",
            NEW_PASSKEY_MARKER,
            "--verbose",
        ],
    )

    combined = result.stdout + result.stderr
    assert SECRET_PASSKEY_MARKER not in combined
    assert NEW_PASSKEY_MARKER not in combined
    assert client.edited_trackers == []


def test_remove_confirmation_redacts_tracker_query_string(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure a tracker's secret query string never reaches the prompt."""
    _make_interactive(monkeypatch)
    secret_tracker = (
        f"https://tracker.example/announce?passkey={SECRET_PASSKEY_MARKER}"
    )
    client = _client_with_tracker(secret_tracker)
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "trackers",
            "remove",
            "--tracker",
            secret_tracker,
            "--no-dry-run",
        ],
        input="n\n",
    )

    assert SECRET_PASSKEY_MARKER not in result.stdout
    assert SECRET_PASSKEY_MARKER not in result.stderr


# --- Spinner/output behavior -------------------------------------------


def test_mutation_command_never_shows_connection_banner(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure mutation commands never print the removed connection banner."""
    client = _client_with_tracker("https://tracker.example/announce")
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["trackers", "remove", "--tracker", "https://tracker.example/announce"],
    )

    assert "Connected to qBittorrent" not in result.stdout
    assert "Connected to qBittorrent" not in result.stderr


def test_mutation_command_creates_client_exactly_once(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure a mutation command creates exactly one qBittorrent client."""
    client = _client_with_tracker("https://tracker.example/announce")
    calls = configure_qbit_backend(client=client)

    runner.invoke(
        app,
        ["trackers", "remove", "--tracker", "https://tracker.example/announce"],
    )

    assert calls == [{}]
