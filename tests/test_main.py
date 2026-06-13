"""Test the Typer application entry point."""

from typer.testing import CliRunner

from app.main import app

runner = CliRunner()


def test_main_prints_project_identity() -> None:
    """Ensure the CLI prints the project name and version."""
    result = runner.invoke(app)

    assert result.exit_code == 0
    assert result.stdout == "qbit-ops 0.0.1\n"
