"""Register the root-level `doctor` command.

Moved from `qbit_ops.main`. No behavior change -- still delegates every
check's implementation to `qbit_ops.features.doctor.collect_doctor_report`.
"""

from typing import Annotated, Any

import typer

from qbit_ops.cli import error_boundary, rendering
from qbit_ops.cli.rendering import OutputFormat
from qbit_ops.cli.validation import validate_format_support
from qbit_ops.config import ConfigError, QbitConfig
from qbit_ops.errors import QbitAuthenticationError
from qbit_ops.features.doctor import (
    ConnectionOutcome,
    DoctorReport,
    collect_doctor_report,
    doctor_exit_code,
)


def _collect_doctor_report() -> DoctorReport:
    """Collect a doctor report, classifying failures instead of aborting.

    Unlike every other command, `doctor` never calls `error_boundary.fail()`
    on a configuration/connection/authentication error: the failure
    itself is the payload the report exists to describe. Nothing is
    written to stderr here (that contract is reserved for genuinely
    invalid CLI invocation) — a `fail`/`warning` overall status and
    non-zero exit code already carry the outcome, in every `--format`.
    """
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
    """Register the `doctor` command on the root Typer app."""

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

        Read-only: performs at most one authenticated login plus up to four
        bounded read calls (`app_version`, `app_web_api_version`,
        `transfer_info`, `torrents_info`), the same budget `status` uses,
        never a per-torrent call. Every check runs independently: a failed
        prerequisite (e.g. invalid configuration) produces `skipped`
        downstream checks instead of aborting the whole report.
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
