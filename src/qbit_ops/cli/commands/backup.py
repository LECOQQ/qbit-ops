"""Register the `backup` command group (`export`, `diff`)."""

from pathlib import Path
from typing import Annotated, Any

import typer

from qbit_core.features.backup import (
    BackupExportError,
    diff_backup_exports,
    export_instance_state,
    has_backup_diff,
    load_export_file,
    redact_backup_diff,
)
from qbit_ops import __version__
from qbit_ops.cli import error_boundary, rendering
from qbit_ops.cli.commands.trackers import TrackerMatchModeOption
from qbit_ops.cli.exit_codes import ExitCode
from qbit_ops.cli.rendering import OutputFormat
from qbit_ops.cli.validation import validate_format_support

backup_app = typer.Typer(help="Export qBittorrent state.")


def _get_optional_client_value(client: Any, method_name: str) -> str:
    method = getattr(client, method_name, None)
    if method is None:
        return "unknown"

    try:
        value = method()
    except Exception:
        return "unknown"

    return str(value)


def _exit_if_backup_diff(report: dict[str, Any]) -> None:
    if has_backup_diff(report):
        raise typer.Exit(code=ExitCode.NO_MATCH)


@backup_app.command(name="export")
@error_boundary.catch_internal_errors
def export_backup(
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            help="Output format.",
        ),
    ] = OutputFormat.table,
    match: Annotated[
        TrackerMatchModeOption,
        typer.Option(
            "--match",
            help="Tracker normalization mode for exported identities.",
        ),
    ] = TrackerMatchModeOption.exact,
) -> None:
    """Export torrents, trackers and metadata for backup or audit."""
    validate_format_support("backup_export", output_format)
    enabled = rendering.progress_enabled(
        output_format=output_format,
        interactive=rendering.is_interactive_terminal(),
    )
    with error_boundary.qbit_error_boundary():
        config = error_boundary.load_qbit_config()
        client = error_boundary.create_qbit_client()
        with rendering.transient_spinner(
            "Collecting backup data...", enabled=enabled
        ):
            state = export_instance_state(
                client=client,
                config=config,
                qbit_ops_version=__version__,
                qbittorrent_version=_get_optional_client_value(
                    client,
                    "app_version",
                ),
                web_api_version=_get_optional_client_value(
                    client,
                    "app_web_api_version",
                ),
                match_mode=match.value,
            )

    if output_format == OutputFormat.json:
        rendering.print_json_output(state)
        return

    if output_format == OutputFormat.jsonl:
        rendering.print_jsonl_output(state)
        return

    summary = state["summary"]
    metadata = state["metadata"]
    rendering.print_summary(
        {
            "exported_at": metadata["exported_at"],
            "torrents": summary["torrents"],
            "unique_trackers": summary["unique_trackers"],
            "tracker_match": summary["tracker_match"],
        },
        title="Backup Export",
    )
    typer.echo("Use --format json for the full backup payload.")


@backup_app.command(name="diff")
@error_boundary.catch_internal_errors
def diff_backup(
    baseline: Annotated[
        Path,
        typer.Argument(
            help="Baseline export JSON file.",
        ),
    ],
    target: Annotated[
        Path,
        typer.Argument(
            help="Target export JSON file.",
        ),
    ],
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            help="Output format.",
        ),
    ] = OutputFormat.table,
    reveal_sensitive: Annotated[
        bool,
        typer.Option(
            "--reveal-sensitive",
            help=(
                "Show raw tracker announce URLs (including any passkey) "
                "in the diff instead of redacted host[:port] identities."
            ),
        ),
    ] = False,
) -> None:
    """Compare two backup or tracker export JSON files.

    Redacts tracker identities to `host[:port]` by default, in every
    `--format` -- a backup export legitimately contains raw announce
    URLs (see `backup export`), but a diff of two such files is
    ordinary terminal/log output and must not echo them. Pass
    `--reveal-sensitive` to show the raw URLs instead.
    """
    validate_format_support("backup_diff", output_format)
    try:
        baseline_export = load_export_file(baseline)
        target_export = load_export_file(target)
        report = diff_backup_exports(
            baseline_export,
            target_export,
            baseline_source=str(baseline),
            target_source=str(target),
        )
    except BackupExportError as error:
        error_boundary.fail(str(error))

    rendered_report = report if reveal_sensitive else redact_backup_diff(report)

    if output_format == OutputFormat.json:
        rendering.print_json_output(rendered_report)
    elif output_format == OutputFormat.jsonl:
        rendering.print_jsonl_output(rendered_report)
    else:
        rendering.print_backup_diff(rendered_report)

    _exit_if_backup_diff(report)
