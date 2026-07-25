"""Test the root `tui` command's CLI-boundary behavior.

Deliberately does not launch the real Textual app (that would attach to
a real terminal) -- Textual `Pilot` interface tests live in
`tests/test_tui_app.py`. This file covers what only a real Typer
invocation can: the missing-optional-dependency message, local
validation before any client/import, and exit-code-table registration.
"""

import ast
import builtins
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

import app.main as main_module
from app.main import EXIT_CODE_TABLE, ExitCode, app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_tui_missing_extra_fails_before_creating_a_client(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    """`tui` must fail with an actionable message when Textual is absent.

    Simulates the "extra not installed" case by making `import textual`
    raise `ModuleNotFoundError`, without touching the real installed
    package or `sys.modules` (which could leak into other tests).
    """
    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "textual":
            raise ModuleNotFoundError("No module named 'textual'")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    client_created = {"called": False}

    def _fail_if_called() -> None:
        client_created["called"] = True

    monkeypatch.setattr("app.main._create_qbit_client", _fail_if_called)

    result = runner.invoke(app, ["tui"])

    assert result.exit_code == ExitCode.ERROR
    assert client_created["called"] is False
    assert "pipx install" in result.stderr
    assert "qbit-ops[tui]" in result.stderr
    assert "Traceback" not in result.stderr


def test_tui_rejects_non_positive_interval(runner: CliRunner) -> None:
    result = runner.invoke(app, ["tui", "--interval", "0"])

    assert result.exit_code == ExitCode.ERROR
    assert "positive" in result.stderr


def test_tui_rejects_infinite_interval(runner: CliRunner) -> None:
    result = runner.invoke(app, ["tui", "--interval", "inf"])

    assert result.exit_code == ExitCode.ERROR
    assert "finite" in result.stderr


def test_tui_interval_validated_before_missing_extra_import(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    """A validation error should not depend on Textual being importable."""
    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "textual":
            raise AssertionError(
                "textual should never be imported for an invalid --interval"
            )
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    result = runner.invoke(app, ["tui", "--interval", "-1"])

    assert result.exit_code == ExitCode.ERROR


def test_tui_is_registered_in_the_exit_code_table() -> None:
    assert "tui" in EXIT_CODE_TABLE


def test_tui_help_works(runner: CliRunner) -> None:
    result = runner.invoke(app, ["tui", "--help"])
    assert result.exit_code == 0
    assert "interactive TUI" in result.output


def test_app_main_source_never_imports_textual_at_module_level() -> None:
    """`app/main.py` must only import Textual lazily, inside `tui()`.

    A static check, independent of what other test modules already
    loaded into `sys.modules` in this process: every other command must
    remain importable/runnable without ever loading Textual.
    """
    source = Path(main_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in tree.body:  # module-level statements only, not nested
        if isinstance(node, ast.Import):
            assert not any(
                alias.name == "textual" or alias.name.startswith("textual.")
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module != "textual" and not node.module.startswith(
                "textual."
            )


def test_status_doctor_and_torrents_list_never_import_textual() -> None:
    """Dynamic guarantee, in a fresh subprocess: ordinary commands never
    load Textual, whether or not the `tui` extra happens to be
    installed in this environment.
    """
    script = (
        "import sys\n"
        "from typer.testing import CliRunner\n"
        "from app.main import app\n"
        "runner = CliRunner()\n"
        'runner.invoke(app, ["status", "--quiet"])\n'
        'runner.invoke(app, ["doctor"])\n'
        'runner.invoke(app, ["torrents", "list"])\n'
        'print("textual" in sys.modules)\n'
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.stdout.strip() == "False", result.stderr
