"""Register the `explain` command group (`torrent`, `tracker`)."""

from typing import Annotated

import typer

from qbit_ops.cli import error_boundary, rendering
from qbit_ops.cli.commands.torrents import TRACKER_FILTER_HELP
from qbit_ops.cli.exit_codes import ExplainExitCode
from qbit_ops.cli.rendering import OutputFormat
from qbit_ops.cli.validation import validate_format_support
from qbit_ops.errors import ErrorCategory, InvalidInputError, require_non_blank
from qbit_ops.features.explain import (
    explain_torrent,
    explain_tracker,
    explanation_exit_code,
)
from qbit_ops.features.trackers import normalize_tracker_host
from qbit_ops.shared.selectors import AmbiguousTorrentHashError

explain_app = typer.Typer(
    help="Explain torrent or tracker state using deterministic evidence."
)


@explain_app.command(name="torrent")
@error_boundary.catch_internal_errors
def explain_torrent_command(
    torrent_hash: Annotated[
        str,
        typer.Option(
            "--hash",
            help=(
                "Full infohash or unique leading prefix to explain "
                "(case-insensitive)."
            ),
        ),
    ],
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            help="Output format.",
        ),
    ] = OutputFormat.table,
) -> None:
    """Explain one torrent's current state using deterministic evidence.

    Read-only: never mutates qBittorrent, never prompts, and never
    claims a cause the collected evidence does not support. Bounded to
    `torrents_info()` once plus at most one `torrents_trackers()` call
    for the resolved torrent -- the same budget `torrents inspect
    --hash` uses.
    """
    validate_format_support("explain_torrent", output_format)
    try:
        torrent_hash = require_non_blank(torrent_hash, field_name="--hash")
    except InvalidInputError as error:
        error_boundary.fail(str(error), ErrorCategory.INVALID_INPUT)
    enabled = rendering.progress_enabled(
        output_format=output_format,
        interactive=rendering.is_interactive_terminal(),
    )
    try:
        with error_boundary.qbit_error_boundary():
            client = error_boundary.create_qbit_client()
            with rendering.transient_spinner(
                "Loading torrent...", enabled=enabled
            ):
                report = explain_torrent(client, torrent_hash)
    except AmbiguousTorrentHashError as error:
        error_boundary.fail_ambiguous_hash(error)

    if report is None:
        if output_format == OutputFormat.json:
            rendering.print_json_output(
                {"explanation": None, "hash": torrent_hash}
            )
        elif output_format == OutputFormat.jsonl:
            rendering.print_jsonl_output(
                {"explanation": None, "hash": torrent_hash}
            )
        else:
            typer.echo(f"No torrent found for hash prefix: {torrent_hash}")
        raise typer.Exit(code=ExplainExitCode.TARGET_UNAVAILABLE)

    rendering.render_explanation_report(report, output_format)
    raise typer.Exit(code=explanation_exit_code(report.overall_severity))


@explain_app.command(name="tracker")
@error_boundary.catch_internal_errors
def explain_tracker_command(
    tracker: Annotated[
        str,
        typer.Option(
            "--tracker",
            help=TRACKER_FILTER_HELP,
        ),
    ],
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            help="Output format.",
        ),
    ] = OutputFormat.table,
) -> None:
    """Explain one tracker's aggregate health using deterministic evidence.

    Read-only: reuses the same bounded `trackers status` collection
    (`torrents_info()` once, at most one `torrents_trackers()` call per
    surviving torrent), restricted to the requested identity afterward
    -- no second collection pass.
    """
    validate_format_support("explain_tracker", output_format)
    try:
        tracker = require_non_blank(tracker, field_name="--tracker")
    except InvalidInputError as error:
        error_boundary.fail(str(error), ErrorCategory.INVALID_INPUT)
    enabled = rendering.progress_enabled(
        output_format=output_format,
        interactive=rendering.is_interactive_terminal(),
    )
    with error_boundary.qbit_error_boundary():
        client = error_boundary.create_qbit_client()
        with rendering.transient_progress(
            "Scanning torrent trackers...", enabled=enabled
        ) as advance:
            report = explain_tracker(client, tracker, on_progress=advance)

    if report is None:
        normalized = normalize_tracker_host(tracker)
        if output_format == OutputFormat.json:
            rendering.print_json_output(
                {"explanation": None, "tracker": normalized}
            )
        elif output_format == OutputFormat.jsonl:
            rendering.print_jsonl_output(
                {"explanation": None, "tracker": normalized}
            )
        else:
            typer.echo(f"No observations found for tracker: {normalized}")
        raise typer.Exit(code=ExplainExitCode.TARGET_UNAVAILABLE)

    rendering.render_explanation_report(report, output_format)
    raise typer.Exit(code=explanation_exit_code(report.overall_severity))
