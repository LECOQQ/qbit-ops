"""Behaviour of `qbit-ops init` at the CLI boundary.

Prompts, the TTY check and the connection attempt are patched at the
same seams every other command test uses, so nothing here reads a real
terminal or reaches a real instance.
"""

from __future__ import annotations

import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from qbit_core.config import CONNECTION_VARIABLES, QbitConfig
from qbit_core.errors import QbitConnectionError
from qbit_core.features.connection_setup import ENV_FILE_MODE
from qbit_ops.cli.app import app
from qbit_ops.cli.exit_codes import EXIT_CODE_TABLE, ExitCode
from qbit_ops.config import (
    APP_ENV_FILE_VARIABLE,
    PROJECT_ENV_FILE,
    get_user_env_file,
    load_qbit_config,
)

HOST = "http://qbit.example:8080"
USER = "operator"
PASSWORD = "s3cr3t"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def isolated_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Point every configuration source at a private, empty tree."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv(APP_ENV_FILE_VARIABLE, raising=False)
    for variable in CONNECTION_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    working_directory = tmp_path / "cwd"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)
    monkeypatch.setattr(
        "qbit_ops.cli.rendering.is_interactive_terminal", lambda: True
    )
    yield get_user_env_file()


def _answer_prompts(
    monkeypatch: pytest.MonkeyPatch,
    *,
    hosts: list[str] | None = None,
    users: list[str] | None = None,
    passwords: list[str] | None = None,
) -> None:
    queues = {
        "line": list(hosts or [HOST]),
        "user": list(users or [USER]),
        "password": list(passwords or [PASSWORD]),
    }

    def _prompt_line(label: str, *, default: str | None = None) -> str:
        queue = queues["line"] if label == "Host" else queues["user"]
        return queue.pop(0)

    def _prompt_secret(label: str) -> str:
        return queues["password"].pop(0)

    monkeypatch.setattr("qbit_ops.cli.rendering.prompt_line", _prompt_line)
    monkeypatch.setattr("qbit_ops.cli.rendering.prompt_secret", _prompt_secret)


def _connection(
    monkeypatch: pytest.MonkeyPatch, *, error: Exception | None = None
) -> list[QbitConfig]:
    attempts: list[QbitConfig] = []

    def _factory(config: QbitConfig) -> Any:
        attempts.append(config)
        if error is not None:
            raise error
        return object()

    monkeypatch.setattr(
        "qbit_core.features.connection_setup.create_qbit_client", _factory
    )
    return attempts


def _fail_if_prompted(monkeypatch: pytest.MonkeyPatch) -> None:
    def _never(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("init must not prompt here")

    monkeypatch.setattr("qbit_ops.cli.rendering.prompt_line", _never)
    monkeypatch.setattr("qbit_ops.cli.rendering.prompt_secret", _never)


# --- registration --------------------------------------------------------


def test_init_is_registered_in_the_exit_code_table() -> None:
    assert EXIT_CODE_TABLE["init"] is ExitCode


def test_init_offers_no_way_to_pass_a_secret_on_the_command_line(
    runner: CliRunner,
) -> None:
    """A `--password` would land the secret in shell history and in any
    command collector; the scriptable path stays the environment."""
    result = runner.invoke(app, ["init", "--help"])

    assert result.exit_code == 0
    for option in ("--password", "--user", "--host"):
        assert option not in result.output


# --- refusals before any input -------------------------------------------


def test_init_refuses_without_a_terminal_and_names_the_variables(
    runner: CliRunner,
    isolated_target: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "qbit_ops.cli.rendering.is_interactive_terminal", lambda: False
    )
    _fail_if_prompted(monkeypatch)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == ExitCode.ERROR
    assert "terminal" in result.stderr
    for variable in CONNECTION_VARIABLES:
        assert variable in result.stderr
    assert not isolated_target.exists()


def test_init_refuses_an_existing_file_and_names_force(
    runner: CliRunner,
    isolated_target: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_target.parent.mkdir(parents=True)
    isolated_target.write_text("QBIT_HOST=old\n", encoding="utf-8")
    _fail_if_prompted(monkeypatch)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == ExitCode.ERROR
    assert "--force" in result.stderr
    assert str(isolated_target) in result.stderr
    assert isolated_target.read_text(encoding="utf-8") == "QBIT_HOST=old\n"


def test_force_overwrites_an_existing_file(
    runner: CliRunner,
    isolated_target: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_target.parent.mkdir(parents=True)
    isolated_target.write_text("QBIT_HOST=old\n", encoding="utf-8")
    _answer_prompts(monkeypatch)
    _connection(monkeypatch)

    result = runner.invoke(app, ["init", "--force"])

    assert result.exit_code == ExitCode.SUCCESS
    assert load_qbit_config() == QbitConfig(
        host=HOST, username=USER, password=PASSWORD
    )


# --- the happy path ------------------------------------------------------


def test_init_writes_the_three_answers_at_the_user_config_path(
    runner: CliRunner,
    isolated_target: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answer_prompts(monkeypatch)
    attempts = _connection(monkeypatch)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == ExitCode.SUCCESS
    assert attempts == [QbitConfig(host=HOST, username=USER, password=PASSWORD)]
    assert load_qbit_config() == QbitConfig(
        host=HOST, username=USER, password=PASSWORD
    )
    assert str(isolated_target) in result.stdout


def test_the_written_file_is_owner_only(
    runner: CliRunner,
    isolated_target: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answer_prompts(monkeypatch)
    _connection(monkeypatch)

    runner.invoke(app, ["init"])

    assert stat.S_IMODE(isolated_target.stat().st_mode) == ENV_FILE_MODE


def test_a_locally_invalid_answer_is_asked_again(
    runner: CliRunner,
    isolated_target: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answer_prompts(
        monkeypatch,
        hosts=[HOST, HOST],
        users=[USER, USER],
        passwords=["   ", PASSWORD],
    )
    _connection(monkeypatch)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == ExitCode.SUCCESS
    assert load_qbit_config().password == PASSWORD


# --- a failed connection informs, it does not cancel ---------------------


def test_a_failed_connection_offers_the_write_instead_of_cancelling_it(
    runner: CliRunner,
    isolated_target: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answer_prompts(monkeypatch)
    _connection(monkeypatch, error=QbitConnectionError("Connection refused."))
    asked: list[str] = []

    def _confirm(prompt: str) -> bool:
        asked.append(prompt)
        return True

    monkeypatch.setattr("qbit_ops.cli.rendering.confirm", _confirm)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == ExitCode.SUCCESS
    assert asked, "a failed test must offer the write, not skip it"
    assert isolated_target.is_file()


def test_declining_after_a_failed_connection_writes_nothing(
    runner: CliRunner,
    isolated_target: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answer_prompts(monkeypatch)
    _connection(monkeypatch, error=QbitConnectionError("Connection refused."))
    monkeypatch.setattr("qbit_ops.cli.rendering.confirm", lambda prompt: False)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == ExitCode.SUCCESS
    assert not isolated_target.exists()


def test_a_successful_connection_never_asks_for_confirmation(
    runner: CliRunner,
    isolated_target: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answer_prompts(monkeypatch)
    _connection(monkeypatch)

    def _never(prompt: str) -> bool:
        raise AssertionError("a successful test must not ask anything")

    monkeypatch.setattr("qbit_ops.cli.rendering.confirm", _never)

    assert runner.invoke(app, ["init"]).exit_code == ExitCode.SUCCESS


# --- the precedence warning ----------------------------------------------


def test_no_warning_when_the_written_file_is_the_only_source(
    runner: CliRunner,
    isolated_target: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answer_prompts(monkeypatch)
    _connection(monkeypatch)

    result = runner.invoke(app, ["init"])

    assert "takes precedence" not in result.stderr


def test_an_exported_variable_is_named_as_taking_precedence(
    runner: CliRunner,
    isolated_target: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answer_prompts(monkeypatch)
    _connection(monkeypatch)
    monkeypatch.setenv("QBIT_HOST", "http://exported:1")

    result = runner.invoke(app, ["init"])

    assert result.exit_code == ExitCode.SUCCESS
    assert isolated_target.is_file()
    assert "QBIT_HOST" in result.stderr
    assert "takes precedence" in result.stderr


def test_a_bracketed_path_survives_in_the_warning(
    runner: CliRunner,
    isolated_target: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rich reads `[...]` as markup: an unescaped path would lose the
    very segment the warning exists to name."""
    _answer_prompts(monkeypatch)
    _connection(monkeypatch)
    override = "/srv/[old]/config/.env"
    monkeypatch.setenv(APP_ENV_FILE_VARIABLE, override)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == ExitCode.SUCCESS
    assert override in result.stderr


def test_a_project_env_file_is_named_as_taking_precedence(
    runner: CliRunner,
    isolated_target: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_env_file = tmp_path / "cwd" / PROJECT_ENV_FILE
    project_env_file.write_text("QBIT_HOST=http://local:1\n", encoding="utf-8")
    _answer_prompts(monkeypatch)
    _connection(monkeypatch)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == ExitCode.SUCCESS
    assert str(project_env_file) in result.stderr
    assert "takes precedence" in result.stderr
