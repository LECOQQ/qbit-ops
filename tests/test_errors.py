"""Test the shared error-category, validation, and exit-code contract.

Covers the cross-cutting guarantees introduced alongside `app.errors`:
local validation before any qBittorrent API call, the distinction
between invalid input / not found / ambiguous / unavailable /
authentication / internal failures, and that an unexpected programming
defect is never silently relabelled as a remote failure. Command-
specific health/severity exit codes (`status`, `doctor`, `trackers
status`, `explain`) are covered by their own CLI test files; this file
only asserts the shared contract layered on top of them.
"""

import json

import pytest
import typer
from typer.testing import CliRunner

from app.errors import AppError, ErrorCategory
from app.main import EXIT_CODE_TABLE, ExitCode, app
from tests.support import FakeQbitClient, make_torrent

pytestmark = pytest.mark.usefixtures("configure_qbit_backend")


@pytest.fixture
def runner() -> CliRunner:
    """Provide a Typer CLI test runner."""
    return CliRunner()


# --- 1-4: blank/whitespace --hash / --tracker rejected before any API call


@pytest.mark.parametrize("blank_hash", ["", "   "])
def test_explain_torrent_blank_hash_rejected_before_api_call(
    runner: CliRunner,
    configure_qbit_backend,
    blank_hash: str,
) -> None:
    """Ensure a blank/whitespace `--hash` never reaches qBittorrent."""
    calls = configure_qbit_backend(client=FakeQbitClient())

    result = runner.invoke(app, ["explain", "torrent", "--hash", blank_hash])

    assert result.exit_code == ExitCode.ERROR
    assert calls == []


@pytest.mark.parametrize("blank_tracker", ["", "   "])
def test_explain_tracker_blank_tracker_rejected_before_api_call(
    runner: CliRunner,
    configure_qbit_backend,
    blank_tracker: str,
) -> None:
    """Ensure a blank/whitespace `--tracker` never reaches qBittorrent."""
    calls = configure_qbit_backend(client=FakeQbitClient())

    result = runner.invoke(
        app, ["explain", "tracker", "--tracker", blank_tracker]
    )

    assert result.exit_code == ExitCode.ERROR
    assert calls == []


@pytest.mark.parametrize("blank_hash", ["", "   "])
def test_torrents_inspect_blank_hash_rejected_before_api_call(
    runner: CliRunner,
    configure_qbit_backend,
    blank_hash: str,
) -> None:
    """Ensure `torrents inspect --hash` validates locally first."""
    calls = configure_qbit_backend(client=FakeQbitClient())

    result = runner.invoke(app, ["torrents", "inspect", "--hash", blank_hash])

    assert result.exit_code == ExitCode.ERROR
    assert calls == []


@pytest.mark.parametrize("blank_tracker", ["", "   "])
def test_trackers_inspect_blank_tracker_rejected_before_api_call(
    runner: CliRunner,
    configure_qbit_backend,
    blank_tracker: str,
) -> None:
    """Ensure `trackers inspect --tracker` validates locally first."""
    calls = configure_qbit_backend(client=FakeQbitClient())

    result = runner.invoke(
        app, ["trackers", "inspect", "--tracker", blank_tracker]
    )

    assert result.exit_code == ExitCode.ERROR
    assert calls == []


def test_bulk_mutation_blank_hash_rejected_before_api_call(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure a blank `--hash` on a bulk mutation validates locally first."""
    calls = configure_qbit_backend(client=FakeQbitClient())

    result = runner.invoke(app, ["torrents", "pause", "--hash", "   "])

    assert result.exit_code == ExitCode.ERROR
    assert calls == []


# --- 5: ambiguous hash remains distinct from not found ---------------------


def test_ambiguous_hash_distinct_from_not_found(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure an ambiguous prefix and an unresolved hash exit differently."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="aaaa1111", name="one"),
            make_torrent(hash="aaaa2222", name="two"),
        ]
    )
    configure_qbit_backend(client=client)

    ambiguous = runner.invoke(app, ["explain", "torrent", "--hash", "aaaa"])
    not_found = runner.invoke(app, ["explain", "torrent", "--hash", "zzzz"])

    assert ambiguous.exit_code == ExitCode.ERROR
    assert not_found.exit_code != ambiguous.exit_code
    assert "matches" in ambiguous.output.lower() or "ambiguous" in (
        ambiguous.output.lower()
    )


# --- 6: not found remains distinct from unavailable -------------------------


def test_not_found_distinct_from_unavailable(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure an unresolved target and a connection failure exit differently."""
    from app.errors import QbitConnectionError

    not_found_client = FakeQbitClient(torrents=[])
    configure_qbit_backend(client=not_found_client)
    not_found = runner.invoke(app, ["explain", "torrent", "--hash", "dead"])

    configure_qbit_backend(
        client_error=QbitConnectionError("Unable to connect to qBittorrent.")
    )
    unavailable = runner.invoke(app, ["explain", "torrent", "--hash", "dead"])

    assert not_found.exit_code != unavailable.exit_code


# --- 7: authentication remains distinct from network unavailability --------


def test_authentication_distinct_from_unavailable(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure auth and connection failures record different categories."""
    import app.main as main_module
    from app.errors import QbitAuthenticationError, QbitConnectionError

    configure_qbit_backend(
        client_error=QbitAuthenticationError("Authentication failed.")
    )
    runner.invoke(app, ["torrents", "list"])
    assert main_module.last_app_error is not None
    assert main_module.last_app_error.category == ErrorCategory.AUTHENTICATION

    configure_qbit_backend(
        client_error=QbitConnectionError("Unable to connect.")
    )
    runner.invoke(app, ["torrents", "list"])
    assert main_module.last_app_error is not None
    assert main_module.last_app_error.category == ErrorCategory.UNAVAILABLE


# --- 8: critical operational reports still serialize normally --------------


def test_critical_tracker_status_still_serializes_normally(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure a critical `trackers status` report serializes normally."""
    client = FakeQbitClient(
        torrents=[make_torrent()],
        trackers_by_hash={
            make_torrent()["hash"]: [
                {
                    "url": "https://tracker.example/announce",
                    "status": 4,
                    "msg": "unregistered torrent",
                }
            ]
        },
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(app, ["trackers", "status", "--format", "json"])

    payload = json.loads(result.stdout)
    assert "trackers" in payload
    assert result.exit_code == 2


# --- 9: internal RuntimeError is not converted into unavailable ------------


def test_internal_runtime_error_is_not_converted_to_unavailable(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure a bare `RuntimeError` becomes internal, not `unavailable`."""
    configure_qbit_backend(client_error=RuntimeError("unexpected bug"))

    result = runner.invoke(app, ["torrents", "list"])

    assert result.exit_code == ExitCode.INTERNAL


def test_internal_type_error_is_not_converted_to_unavailable(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure a bare `TypeError` becomes internal, not `unavailable`."""
    configure_qbit_backend(client_error=TypeError("unexpected bug"))

    result = runner.invoke(app, ["torrents", "list"])

    assert result.exit_code == ExitCode.INTERNAL


# --- 10: unexpected exceptions do not leak secrets --------------------------


def test_internal_error_does_not_leak_credentials(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure an internal error's message never echoes a credential."""
    configure_qbit_backend(
        client_error=RuntimeError(
            "bug near http://admin:super-secret-password@localhost:8080"
        )
    )

    result = runner.invoke(app, ["torrents", "list"])

    assert result.exit_code == ExitCode.INTERNAL
    assert "super-secret-password" not in result.output


# --- 11: handled human errors contain remediation ---------------------------


def test_ambiguous_hash_error_has_remediation() -> None:
    """Ensure the ambiguous-hash `AppError` carries actionable remediation."""
    import app.main as main_module
    from app.selectors import AmbiguousTorrentHashError, ResolvedTorrent

    error = AmbiguousTorrentHashError(
        "aaaa",
        (
            ResolvedTorrent(hash="aaaa1111", name="one"),
            ResolvedTorrent(hash="aaaa2222", name="two"),
        ),
    )

    with pytest.raises(typer.Exit):
        main_module._fail_ambiguous_hash(error)

    assert main_module.last_app_error is not None
    assert main_module.last_app_error.remediation is not None


# --- 12: machine-readable fatal errors never produce partial success JSON --


def test_fatal_error_produces_no_stdout_json(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure a fatal error never emits a partial/malformed JSON payload."""
    from app.errors import QbitConnectionError

    configure_qbit_backend(
        client_error=QbitConnectionError("Unable to connect.")
    )

    result = runner.invoke(app, ["torrents", "list", "--format", "json"])

    assert result.exit_code == ExitCode.ERROR
    assert result.stdout.strip() == ""


# --- 20: CLI validation occurs before client creation -----------------------


def test_invalid_format_rejected_before_client_creation(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure an unsupported `--format` fails before any client is created."""
    calls = configure_qbit_backend(client=FakeQbitClient())

    result = runner.invoke(
        app, ["torrents", "inspect", "--hash", "abc", "--format", "csv"]
    )

    assert result.exit_code == ExitCode.ERROR
    assert calls == []


# --- AppError / ErrorCategory are load-bearing, not scaffolding ------------


def test_app_error_is_constructed_for_a_handled_failure(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """Ensure `_fail()` actually records a structured `AppError`."""
    import app.main as main_module

    configure_qbit_backend(client=FakeQbitClient())
    runner.invoke(
        app, ["torrents", "inspect", "--hash", "abc", "--format", "csv"]
    )

    assert isinstance(main_module.last_app_error, AppError)
    assert main_module.last_app_error.category == ErrorCategory.DOMAIN_FAILURE


# --- Exit-code table completeness -------------------------------------------


def test_exit_code_table_matches_registered_commands() -> None:
    """Ensure `EXIT_CODE_TABLE` covers exactly the registered CLI commands.

    Mirrors `test_execution.py`'s registered-command walk so the exit-
    code documentation table cannot silently drift from the actual CLI
    surface.
    """
    registered: set[str] = set()
    for group in app.registered_commands:
        assert group.callback is not None
        name = group.name or group.callback.__name__.replace("_", "-")
        registered.add(name)

    for group in app.registered_groups:
        assert group.name is not None
        assert group.typer_instance is not None
        for command in group.typer_instance.registered_commands:
            assert command.callback is not None
            command_name = command.name or command.callback.__name__.replace(
                "_", "-"
            )
            registered.add(f"{group.name} {command_name}")

    assert registered == set(EXIT_CODE_TABLE)
