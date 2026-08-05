"""Register the root-level `doctor` command."""

from typing import Annotated, Any

import typer

from qbit_core.errors import QbitAuthenticationError
from qbit_core.features.doctor import (
    ConnectionOutcome,
    DoctorReport,
    collect_doctor_report,
    doctor_exit_code,
)
from qbit_ops.cli import error_boundary, rendering
from qbit_ops.cli.rendering import OutputFormat
from qbit_ops.cli.validation import validate_format_support
from qbit_ops.config import ConfigError, QbitConfig


def _collect_doctor_report() -> DoctorReport:
    config: QbitConfig | None = None
    config_error: Exception | None = None
    try:
        config = error_boundary.load_qbit_config()
    except ConfigError as error:
        config_error = error

    connection_outcome: ConnectionOutcome | None = None
    connection_error: Exception | None = None
    client: Any | None = None

    if config is not None:
        try:
            client = error_boundary.create_qbit_client()
            connection_outcome = ConnectionOutcome.OK
        except QbitAuthenticationError as error:
            connection_outcome = ConnectionOutcome.AUTHENTICATION_FAILED
            connection_error = error
        except Exception as error:
            connection_outcome = ConnectionOutcome.CONNECTION_FAILED
            connection_error = error

    return collect_doctor_report(
        config=config,
        config_error=config_error,
        connection_outcome=connection_outcome,
        connection_error=connection_error,
        client=client,
    )


def register(app: typer.Typer) -> None:
    @app.command()
    @error_boundary.catch_internal_errors
    def doctor(
        output_format: Annotated[
            OutputFormat,
            typer.Option(
                "--format",
                help="Output format.",
            ),
        ] = OutputFormat.table,
    ) -> None:
        """Diagnose configuration, connectivity, and compatibility issues.

        Read-only: at most one authenticated login plus up to four
        bounded read calls, never a per-torrent call. Every check runs
        independently: a failed prerequisite produces `skipped`
        downstream checks instead of aborting the report. Unlike other
        commands, a configuration/connection/authentication failure here
        is not fatal -- it becomes a `fail`/`warning` check, and the
        exit code reflects `report.overall_status`.
        """
        validate_format_support("doctor", output_format)
        enabled = rendering.progress_enabled(
            output_format=output_format,
            interactive=rendering.is_interactive_terminal(),
        )
        with rendering.transient_spinner(
            "Running diagnostics...", enabled=enabled
        ):
            report = _collect_doctor_report()

        rendering.render_doctor_report(report, output_format)
        raise typer.Exit(code=doctor_exit_code(report.overall_status))
