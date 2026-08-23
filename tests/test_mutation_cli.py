"""Test the CLI-level mutation confirmation policy and secret redaction.

Covers the execution-safety model introduced alongside
`qbit_core.shared.execution`: dry-run-by-default, risk-tiered
confirmation, `--yes`, non-interactive refusal, cancellation semantics,
and passkey redaction in prompts and
verbose output.
"""

import pytest
from typer.testing import CliRunner

from qbit_ops.cli.app import app
from qbit_ops.cli.exit_codes import ExitCode
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
    monkeypatch.setattr(
        "qbit_ops.cli.rendering.is_interactive_terminal", lambda: True
    )


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
            "--new-passkey-stdin",
        ],
    ],
)
def test_mutation_defaults_to_dry_run_and_performs_no_mutation(
    runner: CliRunner,
    configure_qbit_backend,
    argv: list[str],
) -> None:
    """The fixture torrent is active, so idempotent actions (`resume`, `start`)
    report `NO_CHANGES` rather than `PREVIEW`, and `replace-passkey`'s path-
    shaped template does not match the fixture tracker's URL shape, reporting
    `NO_MATCH` -- any of the three is correct here; what matters is that a dry-
    run never reports `APPLIED` and never calls a mutation API.
    """
    client = _client_with_tracker("https://tracker.example/announce")
    configure_qbit_backend(client=client)

    # Harmless for every other row: only replace-passkey reads stdin.
    result = runner.invoke(app, argv, input="new\n")

    assert result.exit_code in (ExitCode.SUCCESS, ExitCode.NO_MATCH)
    assert "APPLIED" not in result.stdout
    assert any(
        status in result.stdout
        for status in ("PREVIEW", "NO_CHANGES", "NO_MATCH")
    )
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
    result = runner.invoke(app, ["torrents", command, "--help"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "--yes" not in result.stdout


def test_low_risk_mutation_applies_without_confirmation_non_interactively(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
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
    """Regression test: an unmatched `--no-dry-run` selector used to report
    `status APPLIED` even though nothing was ever sent to qBittorrent.
    """
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
    assert "APPLIED" not in result.stdout
    assert "status" in result.stdout and "NO_MATCH" in result.stdout
    assert client.removed_trackers == []


# --- NO_MATCH vs NO_CHANGES vs APPLIED, for every mutation command ----------


@pytest.mark.parametrize(
    "argv,attribute",
    [
        (
            [
                "trackers",
                "add-if-present",
                "--source",
                "https://nonexistent.example/announce",
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
                "https://nonexistent.example/announce",
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
                "https://nonexistent.example/announce",
                "--target",
                "https://other.example/announce",
                "--no-dry-run",
                "--yes",
            ],
            "edited_trackers",
        ),
        (
            [
                "trackers",
                "replace-passkey",
                "--tracker",
                "https://nonexistent.example/announce/{passkey}",
                "--new-passkey-stdin",
                "--no-dry-run",
                "--yes",
            ],
            "edited_trackers",
        ),
    ],
)
def test_unmatched_tracker_selector_reports_no_match_not_applied(
    runner: CliRunner,
    configure_qbit_backend,
    argv: list[str],
    attribute: str,
) -> None:
    client = _client_with_tracker("https://tracker.example/announce")
    configure_qbit_backend(client=client)

    # Harmless for every other row: only replace-passkey reads stdin.
    result = runner.invoke(app, argv, input="new\n")

    assert result.exit_code == ExitCode.NO_MATCH
    assert "APPLIED" not in result.stdout
    assert "NO_MATCH" in result.stdout
    assert getattr(client, attribute) == []


@pytest.mark.parametrize(
    "command,state",
    [
        ("pause", "pausedUP"),
        ("resume", "uploading"),
        ("start", "uploading"),
        # reannounce has no idempotent no-op state; omitted.
    ],
)
def test_already_satisfied_torrent_action_reports_no_changes(
    runner: CliRunner,
    configure_qbit_backend,
    command: str,
    state: str,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_HASH, state=state)],
        trackers_by_hash={TORRENT_HASH: []},
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["torrents", command, "--all", "--no-dry-run"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "APPLIED" not in result.stdout
    assert "NO_CHANGES" in result.stdout
    assert client.paused_hashes == []
    assert client.resumed_hashes == []
    assert client.started_hashes == []


def test_add_if_present_already_had_target_reports_no_changes(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_HASH, name="Fake Torrent")],
        trackers_by_hash={
            TORRENT_HASH: [
                {"url": "https://tracker.example/announce", "status": "2"},
                {"url": "https://other.example/announce", "status": "2"},
            ]
        },
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
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
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "APPLIED" not in result.stdout
    assert "NO_CHANGES" in result.stdout
    assert client.added_trackers == []


def test_replace_passkey_already_current_reports_no_changes(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = _client_with_tracker(
        f"https://tracker.example/announce/{NEW_PASSKEY_MARKER}"
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "trackers",
            "replace-passkey",
            "--tracker",
            "https://tracker.example/announce/{passkey}",
            "--new-passkey-stdin",
            "--no-dry-run",
            "--yes",
        ],
        input=f"{NEW_PASSKEY_MARKER}\n",
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "APPLIED" not in result.stdout
    assert "NO_CHANGES" in result.stdout
    assert NEW_PASSKEY_MARKER not in result.stdout
    assert client.edited_trackers == []


# --- Secret redaction: passkeys never reach stdout/stderr -------------------


def test_replace_passkey_confirmation_never_shows_the_passkey(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_interactive(monkeypatch)
    monkeypatch.setattr(
        "qbit_ops.cli.rendering.prompt_secret", lambda label: NEW_PASSKEY_MARKER
    )
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
            "--new-passkey-stdin",
            "--verbose",
        ],
        input=f"{NEW_PASSKEY_MARKER}\n",
    )

    combined = result.stdout + result.stderr
    assert SECRET_PASSKEY_MARKER not in combined
    assert NEW_PASSKEY_MARKER not in combined
    assert client.edited_trackers == []


def test_replace_passkey_stdin_apply_without_yes_refuses_before_prompting(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once --new-passkey-stdin has consumed stdin, the confirmation
    prompt cannot read it again -- refuse instead of hanging or reading
    garbage, and never touch the client."""
    _make_interactive(monkeypatch)
    client = _client_with_tracker("https://tracker.example/announce")
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "trackers",
            "replace-passkey",
            "--tracker",
            "https://tracker.example/announce/{passkey}",
            "--new-passkey-stdin",
            "--no-dry-run",
        ],
        input=f"{NEW_PASSKEY_MARKER}\n",
    )

    assert result.exit_code == ExitCode.ERROR
    assert "--yes" in result.stderr
    assert client.edited_trackers == []


def test_remove_confirmation_redacts_tracker_query_string(
    runner: CliRunner,
    configure_qbit_backend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    client = _client_with_tracker("https://tracker.example/announce")
    calls = configure_qbit_backend(client=client)

    runner.invoke(
        app,
        ["trackers", "remove", "--tracker", "https://tracker.example/announce"],
    )

    assert calls == [{}]


# --- add-if-present: torrent filters scope the scan ------------------------


def test_add_if_present_filter_scopes_both_the_scan_and_the_mutation(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """A torrent filter narrows which torrents are inspected at all, so a
    torrent outside it is never looked at and never mutated -- even when
    it uses the source tracker.
    """
    in_scope = "a" * 40
    out_of_scope = "b" * 40
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=in_scope, name="A", category="movies"),
            make_torrent(hash=out_of_scope, name="B", category="series"),
        ],
        trackers_by_hash={
            in_scope: [{"url": "https://tracker.example/announce"}],
            out_of_scope: [{"url": "https://tracker.example/announce"}],
        },
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "trackers",
            "add-if-present",
            "--source",
            "https://tracker.example/announce",
            "--target",
            "https://other.example/announce",
            "--category",
            "movies",
            "--no-dry-run",
            "--yes",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.added_trackers == [
        (in_scope, "https://other.example/announce")
    ]
    assert client.torrents_trackers_calls == 1


def test_add_if_present_without_a_filter_still_scans_everything(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="a" * 40, name="A", category="movies"),
            make_torrent(hash="b" * 40, name="B", category="series"),
        ],
        trackers_by_hash={
            "a" * 40: [{"url": "https://tracker.example/announce"}],
            "b" * 40: [{"url": "https://tracker.example/announce"}],
        },
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
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
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert len(client.added_trackers) == 2
    assert client.torrents_trackers_calls == 2
