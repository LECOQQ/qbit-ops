"""Test the Typer application entry point."""

import pytest
import typer
from typer.testing import CliRunner

from app.main import ExitCode, _exit_if_no_targeted_matches, app

runner = CliRunner()


def test_main_prints_project_identity() -> None:
    """Ensure the CLI prints the project name and version."""
    result = runner.invoke(app)

    assert result.exit_code == ExitCode.SUCCESS
    assert result.stdout == "qbit-ops 0.0.1\n"


def test_exit_if_no_targeted_matches_exits_with_no_match_code() -> None:
    """Ensure targeted commands expose a no-match exit code."""
    with pytest.raises(typer.Exit) as error:
        _exit_if_no_targeted_matches(0)

    assert error.value.exit_code == ExitCode.NO_MATCH


def test_exit_if_no_targeted_matches_allows_successful_matches() -> None:
    """Ensure targeted commands keep success when matches exist."""
    _exit_if_no_targeted_matches(1)
