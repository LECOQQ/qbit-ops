"""Register the root-level `version` command."""

from typing import Annotated

import typer

from qbit_core.features.version import VersionReport, collect_version_report
from qbit_ops import __version__, app_services
from qbit_ops.cli import error_boundary, rendering
from qbit_ops.cli.exit_codes import ExitCode
from qbit_ops.cli.rendering import OutputFormat
from qbit_ops.cli.validation import validate_format_support
from qbit_ops.config import ConfigError


def _collect_version_report() -> VersionReport:
    """Collect the four versions, degrading remote ones to `None`.

    Invalid configuration, connection and authentication failures
    degrade silently (no stderr) to `null`/`unavailable` in the report
    itself -- same derogation as `doctor`. Any other exception is an
    unexpected error and propagates to `catch_internal_errors`.
    """
    try:
        client = error_boundary.create_qbit_client()
        return collect_version_report(
            qbit_ops_version=__version__, client=client
        )
    except ConfigError:
        return collect_version_report(qbit_ops_version=__version__)
    except Exception as error:
        if app_services.classify_recoverable_qbit_failure(error) is None:
            raise
        return collect_version_report(qbit_ops_version=__version__)


def register(app: typer.Typer) -> None:
    @app.command()
    @error_boundary.catch_internal_errors
    def version(
        output_format: Annotated[
            OutputFormat,
            typer.Option(
                "--format",
                help="Output format.",
            ),
        ] = OutputFormat.table,
    ) -> None:
        """Show the qbit-ops, Python, qBittorrent, and Web API versions.

        Read-only: at most one authenticated login plus one call per
        remote version. An unreachable instance is not a failure here --
        the local versions are still reported and the command still
        exits `0`; use `doctor` to diagnose the instance itself.
        """
        validate_format_support("version", output_format)
        enabled = rendering.progress_enabled(
            output_format=output_format,
            interactive=rendering.is_interactive_terminal(),
        )
        with rendering.transient_spinner(
            "Collecting versions...", enabled=enabled
        ):
            report = _collect_version_report()

        rendering.render_version_report(report, output_format)
        raise typer.Exit(code=ExitCode.SUCCESS)
