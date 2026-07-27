"""Characterize the current CLI command-tree contract (T1).

Protects the command tree before a future `src/qbit_ops/` package move
or a `main.py` split (phase 8) could silently drop or fail to register a
command. Deliberately uses semantic assertions (command/option names,
absence of a traceback, exit status) rather than a byte-for-byte
terminal snapshot, so the tests stay stable across Typer/Rich decoration
changes, terminal width, or harmless whitespace -- see
`docs/audits/2026-07-package-refactor-plan.md` §5 (T1) and §7.

`qbit_ops.main.EXIT_CODE_TABLE` is the single source of truth for the set of
registered commands (already used the same way by
`tests/test_errors.py::test_exit_code_table_matches_registered_commands`),
so this file's parametrization cannot drift from the actual CLI surface.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from qbit_ops.main import EXIT_CODE_TABLE, app

runner = CliRunner()

TOP_LEVEL_COMMAND_NAMES = {
    "status",
    "doctor",
    "tui",
    "connection",
    "backup",
    "torrents",
    "trackers",
    "explain",
}

GROUP_SUBCOMMAND_NAMES = {
    "connection": {"check"},
    "backup": {"export", "diff"},
    "torrents": {
        "list",
        "categories",
        "inspect",
        "pause",
        "resume",
        "start",
        "reannounce",
    },
    "trackers": {
        "add-if-present",
        "list",
        "status",
        "inspect",
        "replace",
        "replace-passkey",
        "export",
        "remove",
    },
    "explain": {"torrent", "tracker"},
}

# Representative option names for a sample of commands spanning every
# risk tier and every command group -- not exhaustive, but enough to
# catch a dropped or renamed option on the commands most likely to be
# touched by a future refactor.
REPRESENTATIVE_COMMAND_OPTIONS: dict[str, set[str]] = {
    "status": {"--format", "--quiet", "--watch", "--interval"},
    "doctor": {"--format"},
    "tui": {"--interval"},
    "connection check": set(),
    "torrents list": {"--format", "--category", "--state"},
    "torrents inspect": {"--hash", "--format"},
    "torrents pause": {"--hash", "--all", "--dry-run"},
    "torrents resume": {"--hash", "--all", "--dry-run"},
    "torrents start": {"--hash", "--all", "--dry-run"},
    "torrents reannounce": {"--hash", "--all", "--dry-run"},
    "trackers add-if-present": {"--source", "--target", "--dry-run"},
    "trackers list": {"--format"},
    "trackers status": {"--format"},
    "trackers inspect": {"--tracker", "--format"},
    "trackers replace": {"--source", "--target", "--dry-run", "--match"},
    "trackers replace-passkey": {
        "--tracker",
        "--new-passkey",
        "--dry-run",
    },
    "backup export": {"--format", "--match"},
    "backup diff": set(),
    "trackers export": {"--format"},
    "trackers remove": {"--tracker", "--dry-run"},
    "explain torrent": {"--hash", "--format"},
    "explain tracker": {"--tracker", "--format"},
}


def _assert_clean_help(result) -> None:
    assert result.exit_code == 0
    assert "Traceback" not in result.output
    assert "Usage:" in result.output


def test_exit_code_table_covers_exactly_twenty_three_commands() -> None:
    """Lock the current command-tree size so a silent drop is visible."""
    assert len(EXIT_CODE_TABLE) == 23


def test_root_help_succeeds_without_traceback() -> None:
    result = runner.invoke(app, ["--help"])
    _assert_clean_help(result)


def test_root_help_lists_every_top_level_command() -> None:
    result = runner.invoke(app, ["--help"])
    _assert_clean_help(result)
    for name in sorted(TOP_LEVEL_COMMAND_NAMES):
        assert (
            name in result.output
        ), f"root --help no longer lists top-level command {name!r}"


def test_root_invocation_without_arguments_prints_identity_and_succeeds() -> (
    None
):
    result = runner.invoke(app)
    assert result.exit_code == 0
    assert "qbit-ops" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    "group,subcommands", sorted(GROUP_SUBCOMMAND_NAMES.items())
)
def test_command_group_help_lists_its_registered_subcommands(
    group: str,
    subcommands: set[str],
) -> None:
    result = runner.invoke(app, [group, "--help"])
    _assert_clean_help(result)
    for name in sorted(subcommands):
        assert (
            name in result.output
        ), f"{group!r} --help no longer lists subcommand {name!r}"


@pytest.mark.parametrize("command_id", sorted(EXIT_CODE_TABLE))
def test_every_registered_command_help_succeeds_without_traceback(
    command_id: str,
) -> None:
    """Every one of the 23 commands in `EXIT_CODE_TABLE` must still register.

    This is the test that fails if a future migration accidentally
    drops or fails to register a command: an unregistered command makes
    Click/Typer print an error and exit non-zero, or Click raises before
    `CliRunner` can even capture clean output.
    """
    argv = [*command_id.split(" "), "--help"]
    result = runner.invoke(app, argv)
    _assert_clean_help(result)


@pytest.mark.parametrize("command_id", sorted(REPRESENTATIVE_COMMAND_OPTIONS))
def test_representative_command_options_stay_present(
    command_id: str,
) -> None:
    """Assert stable option names for a representative command sample.

    Deliberately not asserting option ordering, help wording, or
    default-value rendering -- only that the flag itself is still
    offered, which is what a future `cli/commands/*.py` split (phase 8)
    must not silently drop.
    """
    expected_options = REPRESENTATIVE_COMMAND_OPTIONS[command_id]
    argv = [*command_id.split(" "), "--help"]
    result = runner.invoke(app, argv)
    _assert_clean_help(result)
    for option in sorted(expected_options):
        assert (
            option in result.output
        ), f"{command_id!r} --help no longer offers option {option!r}"
