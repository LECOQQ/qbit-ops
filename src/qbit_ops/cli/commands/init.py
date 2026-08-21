"""Register the root-level `init` command: guided connection setup."""

from pathlib import Path
from typing import Annotated, NoReturn

import typer

from qbit_core.config import QbitConfig
from qbit_core.errors import ErrorCategory, InvalidInputError
from qbit_core.features.connection_setup import (
    DEFAULT_HOST,
    DEFAULT_USERNAME,
    ENV_FILE_MODE,
    ConnectionAttempt,
    EnvFileExistsError,
    build_connection_config,
    try_connection,
    write_connection_env_file,
)
from qbit_ops.cli import error_boundary, rendering
from qbit_ops.cli.exit_codes import ExitCode
from qbit_ops.config import collect_masking_sources, get_user_env_file


def register(app: typer.Typer) -> None:
    @app.command()
    @error_boundary.catch_internal_errors
    def init(
        force: Annotated[
            bool,
            typer.Option(
                "--force",
                help="Overwrite an existing configuration file.",
            ),
        ] = False,
    ) -> None:
        """Set up the qBittorrent connection, interactively.

        Asks for the host, user and password, tests them, and saves
        them. Needs a terminal; for scripts, set QBIT_HOST, QBIT_USER
        and QBIT_PASSWORD in the environment instead.
        """
        target = get_user_env_file()

        if not rendering.is_interactive_terminal():
            error_boundary.fail(
                "'init' needs a terminal. Set QBIT_HOST, QBIT_USER and "
                "QBIT_PASSWORD in the environment instead.",
                ErrorCategory.CONFIGURATION,
            )

        if target.exists() and not force:
            _fail_already_exists(target)

        config = _prompt_for_config()
        attempt = _test_connection(config)

        if not attempt.ok and not rendering.confirm("Save anyway?"):
            rendering.print_cancelled()
            raise typer.Exit(code=ExitCode.SUCCESS)

        _write(target, config, force=force)


def _fail_already_exists(path: Path) -> NoReturn:
    error_boundary.fail(
        f"{path} already exists. Use --force to overwrite it.",
        ErrorCategory.CONFIGURATION,
    )


def _prompt_for_config() -> QbitConfig:
    """Ask the three questions, re-asking on a locally invalid answer."""
    while True:
        host = rendering.prompt_line("Host", default=DEFAULT_HOST)
        user = rendering.prompt_line("User", default=DEFAULT_USERNAME)
        password = rendering.prompt_secret("Password")
        try:
            return build_connection_config(
                host=host, username=user, password=password
            )
        except InvalidInputError as error:
            rendering.print_warning(str(error))


def _test_connection(config: QbitConfig) -> ConnectionAttempt:
    enabled = rendering.progress_enabled(
        interactive=rendering.is_interactive_terminal()
    )
    with rendering.transient_spinner(
        f"Testing {config.host}...", enabled=enabled
    ):
        attempt = try_connection(config)

    if attempt.ok:
        rendering.print_notice(f"Testing {config.host} ... ✓")
    else:
        rendering.print_notice(f"Testing {config.host} ... ✗")
        rendering.print_warning(attempt.detail or "Connection failed.")
    return attempt


def _write(target: Path, config: QbitConfig, *, force: bool) -> None:
    try:
        written = write_connection_env_file(target, config, force=force)
    except EnvFileExistsError as error:
        _fail_already_exists(error.path)
    except OSError as error:
        error_boundary.fail(
            f"Could not write {target}: {error}",
            ErrorCategory.CONFIGURATION,
        )

    rendering.print_notice(f"Wrote {written} ({ENV_FILE_MODE:04o})")
    for source in collect_masking_sources(written):
        rendering.print_warning(source.message)
