"""Test the Typer application entry point."""

import pytest
import typer
from typer.testing import CliRunner

from qbit_ops import __version__
from qbit_ops.cli.app import app
from qbit_ops.cli.commands._shared import exit_if_no_targeted_matches
from qbit_ops.cli.exit_codes import ExitCode

runner = CliRunner()


def test_main_prints_project_identity() -> None:
    result = runner.invoke(app)

    assert result.exit_code == ExitCode.SUCCESS
    assert result.stdout == f"qbit-ops {__version__}\n"


def test_exit_if_no_targeted_matches_exits_with_no_match_code() -> None:
    with pytest.raises(typer.Exit) as error:
        exit_if_no_targeted_matches(0)

    assert error.value.exit_code == ExitCode.NO_MATCH


def test_exit_if_no_targeted_matches_allows_successful_matches() -> None:
    exit_if_no_targeted_matches(1)
