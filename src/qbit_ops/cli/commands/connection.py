"""Register the `connection` command group (`connection check`).

Moved from `qbit_ops.main`. No behavior change.
"""

from typing import Annotated

import typer

from qbit_ops.cli import error_boundary, rendering
from qbit_ops.cli.rendering import OutputFormat
from qbit_ops.cli.validation import validate_format_support

connection_app = typer.Typer(help="Check qBittorrent connectivity.")


@connection_app.command()
@error_boundary.catch_internal_errors
def check(
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            help="Output format.",
        ),
    ] = OutputFormat.table,
) -> None:
    """Check qBittorrent connectivity using `.env` settings."""
    validate_format_support("connection_check", output_format)
    enabled = rendering.progress_enabled(
        output_format=output_format,
        interactive=rendering.is_interactive_terminal(),
    )
    with error_boundary.qbit_error_boundary():
        with rendering.transient_spinner(
            "Checking connection...", enabled=enabled
        ):
            error_boundary.create_qbit_client()

    report = {
        "status": "ok",
        "connection": "ok",
        "message": (
            "Connection OK: qBittorrent is reachable with .env settings."
        ),
    }

    if output_format == OutputFormat.table:
        typer.echo(report["message"])
    elif output_format == OutputFormat.json:
        rendering.print_json_output(report)
    elif output_format == OutputFormat.jsonl:
        rendering.print_jsonl_output(report)
    else:
        rendering.print_csv_rows(
            ["key", "value"],
            [(key, str(value)) for key, value in report.items()],
        )
