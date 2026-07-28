"""Register the root-level `status` command.

Moved from `qbit_ops.main`: collecting a bounded status snapshot,
rendering it, and running `status --watch`. No behavior change.
"""

import math
import time
from typing import Annotated

import typer

from qbit_ops.cli import error_boundary, rendering
from qbit_ops.cli.exit_codes import ExitCode
from qbit_ops.cli.rendering import OutputFormat
from qbit_ops.cli.validation import validate_format_support
from qbit_ops.config import ConfigError
from qbit_ops.status import (
    StatusSnapshot,
    build_unavailable_snapshot,
    collect_status_snapshot,
    status_exit_code,
    watch_status,
)

DEFAULT_STATUS_WATCH_INTERVAL_SECONDS = 5.0


def _classify_recoverable_qbit_failure(error: Exception):
    from qbit_ops.app_services import classify_recoverable_qbit_failure

    return classify_recoverable_qbit_failure(error)


def _collect_status_snapshot_safely() -> StatusSnapshot:
    """Collect a status snapshot, degrading to `unavailable` on failure.

    Errors always reach stderr, regardless of `--quiet` or `--format`:
    per docs/PLAN.md Phase 1, `--quiet` suppresses successful normal
    output, never genuine failures. The connection spinner/banner is
    always suppressed (`quiet=True` below) since the rendered snapshot
    (or the error message above) already communicates the outcome.

    Only degrades to `unavailable` for *recoverable* failures:
    `QbitAuthenticationError`, `QbitConnectionError`, and `OSError`
    (every `qbittorrentapi` exception raised directly from a post-login
    API call — see `_collect_status_snapshot_for_watch`). Any other
    exception is an unexpected programming error, not a remote
    failure, and is deliberately left to propagate to
    `error_boundary.catch_internal_errors` instead of being silently
    relabelled as "unavailable" (see docs/ERRORS_AND_EXIT_CODES.md,
    "Internal error behavior").
    """
    try:
        config = error_boundary.load_qbit_config()
    except ConfigError as error:
        message = f"Configuration error: {error}"
        rendering.print_error(message)
        return build_unavailable_snapshot(
            code="configuration_invalid",
            message=message,
        )

    host = config.host

    try:
        client = error_boundary.create_qbit_client()
        return collect_status_snapshot(client, host=host)
    except Exception as error:
        failure = _classify_recoverable_qbit_failure(error)
        if failure is None:
            raise
        rendering.print_error(failure.message)
        return build_unavailable_snapshot(
            code=failure.code,
            message=failure.message,
            host=host,
        )


def _collect_status_snapshot_for_watch(host: str) -> StatusSnapshot:
    """Collect one snapshot for `status --watch`.

    Mirrors `_collect_status_snapshot_safely()`'s connection handling via
    the same shared `qbit_ops.app_services.classify_recoverable_qbit_failure`
    classifier, but never prints to stderr on this path: the unavailable
    snapshot's own health/alert already carries the failure, and
    repeating it there would spam stderr on every retry — see
    docs/DECISIONS.md.

    Any other exception is an unexpected programming error, not a
    remote/temporary failure, and is deliberately left to propagate so
    the watch loop stops instead of silently reporting "unavailable"
    forever.
    """
    try:
        client = error_boundary.create_qbit_client()
        return collect_status_snapshot(client, host=host)
    except Exception as error:
        failure = _classify_recoverable_qbit_failure(error)
        if failure is None:
            raise
        return build_unavailable_snapshot(
            code=failure.code,
            message=failure.message,
            host=host,
        )


def _run_status_watch(output_format: OutputFormat, interval: float) -> None:
    """Run `status --watch`: repeatedly collect, then render or serialize.

    Reuses `collect_status_snapshot`/`build_unavailable_snapshot`
    unchanged via `watch_status()` (`qbit_ops/status.py`) — no second status
    model, no duplicated health calculation, no duplicated alerts or
    rate formatting. Local invalid configuration is checked exactly
    once, before the loop starts, and terminates immediately
    (`StatusExitCode.INVALID_USAGE`); temporary remote failures
    discovered *during* the loop instead produce an `unavailable`
    snapshot each time and the loop keeps retrying — see
    `_collect_status_snapshot_for_watch`.

    Exit codes here are unrelated to `StatusExitCode`'s health mapping:
    see `StatusExitCode`'s docstring. A clean `Ctrl+C` exits `0`; an
    unexpected fatal error exits `ExitCode.ERROR` with a clear message
    instead of a raw traceback.
    """
    try:
        config = error_boundary.load_qbit_config()
    except ConfigError as error:
        error_boundary.fail_status_usage(f"Configuration error: {error}")

    host = config.host
    iteration = 0

    def _collect() -> StatusSnapshot:
        return _collect_status_snapshot_for_watch(host)

    try:
        if output_format is OutputFormat.table:
            with rendering.live_status_display() as update_display:

                def _emit_table(snapshot: StatusSnapshot) -> None:
                    nonlocal iteration
                    iteration += 1
                    update_display(
                        snapshot,
                        rendering.WatchRenderContext(
                            interval=interval, iteration=iteration
                        ),
                    )

                watch_status(
                    collect=_collect,
                    emit=_emit_table,
                    interval=interval,
                    sleep=time.sleep,
                    now=time.monotonic,
                )
        else:
            watch_status(
                collect=_collect,
                emit=rendering.emit_status_jsonl,
                interval=interval,
                sleep=time.sleep,
                now=time.monotonic,
            )
    except KeyboardInterrupt:
        raise typer.Exit(code=ExitCode.SUCCESS) from None
    except Exception as error:
        rendering.print_error(f"Unexpected error in status --watch: {error}")
        raise typer.Exit(code=ExitCode.ERROR) from error


def register(app: typer.Typer) -> None:
    """Register the `status` command on the root Typer app."""

    @app.command()
    @error_boundary.catch_internal_errors
    def status(
        output_format: Annotated[
            OutputFormat,
            typer.Option(
                "--format",
                help="Output format.",
            ),
        ] = OutputFormat.table,
        quiet: Annotated[
            bool,
            typer.Option(
                "--quiet",
                help=(
                    "Suppress normal output; only the exit code reflects "
                    "health."
                ),
            ),
        ] = False,
        watch: Annotated[
            bool,
            typer.Option(
                "--watch",
                help="Repeatedly refresh the snapshot until interrupted.",
            ),
        ] = False,
        interval: Annotated[
            float | None,
            typer.Option(
                "--interval",
                help=(
                    "Seconds between refreshes with --watch "
                    f"(default: {DEFAULT_STATUS_WATCH_INTERVAL_SECONDS:g})."
                ),
            ),
        ] = None,
    ) -> None:
        """Show a bounded operational snapshot of the qBittorrent instance."""
        validate_format_support("status", output_format)

        if watch:
            if quiet:
                error_boundary.fail_status_usage(
                    "--quiet cannot be combined with --watch: a watch that "
                    "never prints anything has no meaningful output "
                    "contract."
                )
            if output_format not in (OutputFormat.table, OutputFormat.jsonl):
                error_boundary.fail_status_usage(
                    f"--format {output_format.value} is not supported with "
                    "--watch. Supported formats: jsonl, table."
                )

            resolved_interval = (
                interval
                if interval is not None
                else DEFAULT_STATUS_WATCH_INTERVAL_SECONDS
            )
            if not (resolved_interval > 0) or math.isinf(resolved_interval):
                error_boundary.fail_status_usage(
                    "--interval must be a finite, positive number of "
                    "seconds."
                )

            _run_status_watch(output_format, resolved_interval)
            return

        if interval is not None:
            error_boundary.fail_status_usage("--interval requires --watch.")

        if quiet and output_format != OutputFormat.table:
            error_boundary.fail_status_usage(
                "Use --quiet on its own; it already suppresses --format "
                "output."
            )

        enabled = rendering.progress_enabled(
            output_format=output_format,
            quiet=quiet,
            interactive=rendering.is_interactive_terminal(),
        )
        with rendering.transient_spinner("Loading status...", enabled=enabled):
            snapshot = _collect_status_snapshot_safely()

        if not quiet:
            rendering.render_status(snapshot, output_format)

        raise typer.Exit(code=status_exit_code(snapshot.health))
