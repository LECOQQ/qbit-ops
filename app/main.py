"""Provide the command-line application."""

import csv
import io
import json
import math
import sys
import time
from collections.abc import Callable
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Annotated, Any, NoReturn

import qbittorrentapi
import typer

from app import __version__
from app.backup import (
    BackupExportError,
    diff_backup_exports,
    export_instance_state,
    has_backup_diff,
    load_export_file,
    redact_backup_diff,
)
from app.config import ConfigError, QbitConfig, load_qbit_config
from app.doctor import (
    ConnectionOutcome,
    DoctorReport,
    collect_doctor_report,
    doctor_exit_code,
    doctor_report_to_csv_rows,
    doctor_report_to_json_dict,
)
from app.execution import (
    MUTATION_RISK,
    ExecutionDecision,
    ExecutionPolicy,
    MutationOperation,
    MutationStatus,
    is_interactive_terminal,
)
from app.explain import (
    ExplanationReport,
    explain_torrent,
    explain_tracker,
    explanation_exit_code,
    explanation_report_to_dict,
)
from app.selectors import AmbiguousTorrentHashError
from app.status import (
    StatusSnapshot,
    build_unavailable_snapshot,
    collect_status_snapshot,
    snapshot_to_csv_rows,
    snapshot_to_json_dict,
    status_exit_code,
    watch_status,
)
from app.torrents import (
    STATE_FILTER_VALUES,
    BulkTorrentActionPlan,
    SelectedTorrent,
    TorrentBulkAction,
    TorrentSelection,
    apply_bulk_torrent_action,
    build_torrent_filter,
    describe_torrent_filter,
    inspect_torrent,
    list_category_usage,
    plan_bulk_torrent_action,
    search_torrents_by_name,
    select_torrents,
    torrent_filter_to_dict,
    validate_torrent_selector,
)
from app.tracker_status import (
    TRACKER_STATUS_CSV_FIELDNAMES,
    TrackerStatusReport,
    collect_tracker_status,
    tracker_status_exit_code,
    tracker_status_report_to_csv_rows,
    tracker_status_report_to_dict,
)
from app.trackers import (
    PasskeyReplacementPlan,
    TrackerAdditionPlan,
    TrackerRemovalPlan,
    TrackerReplacementPlan,
    apply_tracker_addition,
    apply_tracker_passkey_replacement,
    apply_tracker_removal,
    apply_tracker_replacement,
    export_tracker_state,
    inspect_tracker,
    normalize_tracker_host,
    plan_tracker_addition,
    plan_tracker_passkey_replacement,
    plan_tracker_removal,
    plan_tracker_replacement,
    redact_tracker_identity,
)
from app.ui import (
    OutputFormat,
    WatchRenderContext,
    confirm,
    live_status_display,
    print_ambiguous_hash_error,
    print_applied,
    print_cancelled,
    print_error,
    print_summary,
    print_table,
    progress_enabled,
    render_doctor_table,
    render_explanation,
    render_status_table,
    transient_progress,
    transient_spinner,
)

PROJECT_NAME = "qbit-ops"
DEFAULT_STATUS_WATCH_INTERVAL_SECONDS = 5.0

# Shared help text for the torrent-filter options common to `torrents list`
# and every bulk mutation command, so wording cannot silently drift between
# them (see docs/COMMANDS.md, "Torrent Filters").
_CATEGORY_FILTER_HELP = (
    "Restrict to a category (repeatable; combines with OR). Use "
    "'uncategorized' for torrents without a category."
)
_STATE_FILTER_HELP = (
    "Restrict to a state group (repeatable; combines with OR). Supported: "
    f"{', '.join(sorted(STATE_FILTER_VALUES))}."
)
_TRACKER_FILTER_HELP = (
    "Restrict to torrents using a tracker, matched by host[:port] (a full "
    "announce URL is also accepted; only its host and port are used)."
)

app = typer.Typer(add_completion=True, help="Administer qBittorrent.")
connection_app = typer.Typer(help="Check qBittorrent connectivity.")
backup_app = typer.Typer(help="Export qBittorrent state.")
torrents_app = typer.Typer(help="Inspect qBittorrent torrents.")
trackers_app = typer.Typer(help="Manage qBittorrent trackers.")
explain_app = typer.Typer(
    help="Explain torrent or tracker state using deterministic evidence."
)
app.add_typer(connection_app, name="connection")
app.add_typer(backup_app, name="backup")
app.add_typer(torrents_app, name="torrents")
app.add_typer(trackers_app, name="trackers")
app.add_typer(explain_app, name="explain")


class ExitCode(IntEnum):
    """Define explicit process exit codes.

    These remain the exit-code semantics for every command other than
    `status`, which documents its own health-based exit codes below.
    """

    SUCCESS = 0
    ERROR = 1
    NO_MATCH = 2


class StatusExitCode(IntEnum):
    """Define exit codes specific to the `status` command.

    Deliberately separate from `ExitCode`: `status` reports operational
    health rather than success/failure, so its exit codes carry
    different semantics (0-3 map to `app.status.Health`; 4 is reserved
    for invalid local configuration or invalid CLI usage). Other
    commands keep `ExitCode` until a later consolidation decision.

    `status --watch` does **not** use this health mapping: a running
    watch cannot continuously update the process exit code, and
    warning/critical/unavailable snapshots must never stop the loop. Its
    process exit code instead reports how the *process* ended: `0` for
    a clean user interrupt (`Ctrl+C`), `INVALID_USAGE` (4) for a
    pre-loop CLI/configuration failure, and `ExitCode.ERROR` (1) for an
    unexpected fatal error. See `docs/COMMANDS.md`.
    """

    HEALTHY = 0
    WARNING = 1
    CRITICAL = 2
    UNAVAILABLE = 3
    INVALID_USAGE = 4


class DoctorExitCode(IntEnum):
    """Define exit codes specific to the `doctor` command.

    `0`/`1`/`2` mirror `app.doctor.doctor_exit_code`'s
    pass/warning/failure mapping exactly (kept here only for readability
    at call sites and in tests). `3` is intentionally unused: doctor has
    no condition that needs a fourth severity tier. `4` is reserved for
    invalid CLI invocation preventing doctor from starting — today
    `doctor` takes no options besides `--format`, and an invalid
    `--format` value is rejected by Click before the command body runs
    (Click's own usage-error exit code, not this one), so `4` is
    currently unreachable in practice. It is kept, not removed, so a
    future doctor-specific flag has a documented code to use instead of
    silently reusing `2` (already meaning "failure").
    """

    PASS = 0
    WARNING = 1
    FAILURE = 2
    INVALID_USAGE = 4


class TrackerStatusExitCode(IntEnum):
    """Define exit codes specific to the `trackers status` command.

    Mirrors `app.tracker_status.tracker_status_exit_code`'s
    healthy/warning/critical/unavailable mapping exactly (kept here only
    for readability at call sites and in tests). Deliberately its own
    scheme rather than `ExitCode`: `2` here means CRITICAL, not
    `ExitCode.NO_MATCH` -- an empty selection (no torrents matched, or
    `--tracker` matched no observed identity) is not an error condition
    for this command and exits `0` via `HEALTHY`, not `2`, unlike
    `trackers inspect`'s no-match convention. `4` is reserved for
    invalid CLI invocation (e.g. contradictory filters), rejected before
    any qBittorrent API call.
    """

    HEALTHY = 0
    WARNING = 1
    CRITICAL = 2
    UNAVAILABLE = 3
    INVALID_USAGE = 4


class ExplainExitCode(IntEnum):
    """Define exit codes specific to the `explain` command group.

    `0`/`1`/`2` mirror `app.explain.explanation_exit_code`'s
    info/warning-or-unknown/critical mapping exactly (kept here only for
    readability at call sites and in tests). `3` (`TARGET_UNAVAILABLE`)
    is used when there is nothing to explain at all -- an unresolved
    torrent hash, or a tracker identity with zero observations -- and is
    deliberately distinct from `ExplanationSeverity`-based `2`
    (CRITICAL): reusing a plain "not found" `2` here would be
    indistinguishable from a real critical finding, the same collision
    `TrackerStatusExitCode` already avoids for an empty selection. This
    also deliberately diverges from `ExitCode.NO_MATCH`/`trackers
    inspect`'s no-match convention (both `2`) for the same reason. An
    ambiguous hash prefix still reuses the shared `ExitCode.ERROR` (via
    `_fail_ambiguous_hash`), consistent with every other hash-driven
    command -- it is rejected before an explanation is ever computed, so
    it never enters this scheme at all. `4` is reserved for invalid CLI
    invocation; today `explain torrent`/`explain tracker` have no
    combination of options that can be locally contradictory, so it is
    currently unreachable in practice (see `DoctorExitCode` for the same
    pattern).
    """

    INFO = 0
    WARNING = 1
    CRITICAL = 2
    TARGET_UNAVAILABLE = 3
    INVALID_USAGE = 4


class TrackerMatchModeOption(StrEnum):
    """Expose tracker matching modes for Typer options."""

    exact = "exact"
    without_query = "without-query"


class QbitConnectionError(RuntimeError):
    """Report that qBittorrent could not be reached."""


class QbitAuthenticationError(RuntimeError):
    """Report that qBittorrent rejected the configured credentials."""


# Every read-only command uses the same `app.ui.OutputFormat` enum and the
# same `--format` option, but not every command can meaningfully render
# every format (see docs/COMMANDS.md "Format Support Matrix" and
# docs/DECISIONS.md, 2026-07-24). This table is the single source of truth:
# both `_validate_format_support` and the CLI test suite read it, so the
# implementation, its validation, and its tests cannot drift apart.
FORMAT_SUPPORT: dict[str, frozenset[OutputFormat]] = {
    "status": frozenset(OutputFormat),
    "connection_check": frozenset(OutputFormat),
    "doctor": frozenset(OutputFormat),
    "torrents_list": frozenset(OutputFormat),
    "torrents_categories": frozenset(OutputFormat),
    "torrents_inspect": frozenset(
        {OutputFormat.table, OutputFormat.json, OutputFormat.jsonl}
    ),
    "trackers_list": frozenset(OutputFormat),
    "trackers_status": frozenset(OutputFormat),
    "trackers_inspect": frozenset(OutputFormat),
    "trackers_export": frozenset(
        {OutputFormat.table, OutputFormat.json, OutputFormat.jsonl}
    ),
    "backup_export": frozenset(
        {OutputFormat.table, OutputFormat.json, OutputFormat.jsonl}
    ),
    "backup_diff": frozenset(
        {OutputFormat.table, OutputFormat.json, OutputFormat.jsonl}
    ),
    "explain_torrent": frozenset(
        {OutputFormat.table, OutputFormat.json, OutputFormat.jsonl}
    ),
    "explain_tracker": frozenset(
        {OutputFormat.table, OutputFormat.json, OutputFormat.jsonl}
    ),
}


def _validate_format_support(
    command_id: str,
    output_format: OutputFormat,
) -> None:
    """Reject a format a command cannot meaningfully render.

    Called before any qBittorrent API call, so an unsupported format
    fails fast without a network round-trip. `table` and `json` are
    supported by every command; `jsonl` and `csv` are only offered where
    they have a stable, non-artificial representation (see
    `FORMAT_SUPPORT` above).
    """
    supported = FORMAT_SUPPORT[command_id]
    if output_format in supported:
        return

    supported_names = ", ".join(sorted(fmt.value for fmt in supported))
    _fail(
        f"--format {output_format.value} is not supported here "
        f"(no stable representation). Supported formats: {supported_names}."
    )


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Print the project name and version when no command is provided."""
    if ctx.invoked_subcommand is None:
        typer.echo(f"{PROJECT_NAME} {__version__}")
        raise typer.Exit(code=ExitCode.SUCCESS)


@app.command()
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
                "Suppress normal output; only the exit code reflects health."
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
    _validate_format_support("status", output_format)

    if watch:
        if quiet:
            _fail_status_usage(
                "--quiet cannot be combined with --watch: a watch that "
                "never prints anything has no meaningful output contract."
            )
        if output_format not in (OutputFormat.table, OutputFormat.jsonl):
            _fail_status_usage(
                f"--format {output_format.value} is not supported with "
                "--watch. Supported formats: jsonl, table."
            )

        resolved_interval = (
            interval
            if interval is not None
            else DEFAULT_STATUS_WATCH_INTERVAL_SECONDS
        )
        if not (resolved_interval > 0) or math.isinf(resolved_interval):
            _fail_status_usage(
                "--interval must be a finite, positive number of seconds."
            )

        _run_status_watch(output_format, resolved_interval)
        return

    if interval is not None:
        _fail_status_usage("--interval requires --watch.")

    if quiet and output_format != OutputFormat.table:
        _fail_status_usage(
            "Use --quiet on its own; it already suppresses --format output."
        )

    enabled = progress_enabled(
        output_format=output_format,
        quiet=quiet,
        interactive=is_interactive_terminal(),
    )
    with transient_spinner("Loading status...", enabled=enabled):
        snapshot = _collect_status_snapshot_safely()

    if not quiet:
        _render_status(snapshot, output_format)

    raise typer.Exit(code=status_exit_code(snapshot.health))


@connection_app.command()
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
    _validate_format_support("connection_check", output_format)
    enabled = progress_enabled(
        output_format=output_format,
        interactive=is_interactive_terminal(),
    )
    try:
        with transient_spinner("Checking connection...", enabled=enabled):
            _create_qbit_client()
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

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
        _print_json_output(report)
    elif output_format == OutputFormat.jsonl:
        _print_jsonl_output(report)
    else:
        _print_csv_rows(
            ["key", "value"],
            [(key, str(value)) for key, value in report.items()],
        )


@app.command()
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
    _validate_format_support("doctor", output_format)
    enabled = progress_enabled(
        output_format=output_format,
        interactive=is_interactive_terminal(),
    )
    with transient_spinner("Running diagnostics...", enabled=enabled):
        report = _collect_doctor_report()

    _render_doctor_report(report, output_format)
    raise typer.Exit(code=doctor_exit_code(report.overall_status))


@torrents_app.command(name="list")
def list_qbit_torrents(
    category: Annotated[
        list[str],
        typer.Option("--category", help=_CATEGORY_FILTER_HELP),
    ] = [],  # noqa: B006 - Typer requires a literal default to detect list options
    state: Annotated[
        list[str],
        typer.Option("--state", help=_STATE_FILTER_HELP),
    ] = [],  # noqa: B006
    tracker: Annotated[
        str | None,
        typer.Option("--tracker", help=_TRACKER_FILTER_HELP),
    ] = None,
    completed: Annotated[
        bool,
        typer.Option("--completed", help="Restrict to completed torrents."),
    ] = False,
    incomplete: Annotated[
        bool,
        typer.Option("--incomplete", help="Restrict to incomplete torrents."),
    ] = False,
    active: Annotated[
        bool,
        typer.Option(
            "--active", help="Restrict to torrents that are not stopped."
        ),
    ] = False,
    inactive: Annotated[
        bool,
        typer.Option("--inactive", help="Restrict to stopped torrents."),
    ] = False,
    stalled: Annotated[
        bool,
        typer.Option("--stalled", help="Restrict to stalled torrents."),
    ] = False,
    errored: Annotated[
        bool,
        typer.Option(
            "--errored", help="Restrict to torrents reporting an error."
        ),
    ] = False,
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            help="Output format.",
        ),
    ] = OutputFormat.table,
) -> None:
    """List torrents, optionally restricted by one or more filters.

    Different filter types combine with AND; repeated `--category`/
    `--state` values combine with OR. No filter means every torrent, as
    before. See docs/COMMANDS.md ("Torrent Filters") for the full
    vocabulary and combination rules.
    """
    _validate_format_support("torrents_list", output_format)
    try:
        filters = build_torrent_filter(
            categories=category,
            states=state,
            tracker=tracker,
            completed=completed,
            incomplete=incomplete,
            active=active,
            inactive=inactive,
            stalled=stalled,
            errored=errored,
        )
    except ValueError as error:
        _fail(str(error))

    enabled = progress_enabled(
        output_format=output_format,
        interactive=is_interactive_terminal(),
    )
    if filters.requires_tracker_data:
        progress_cm = transient_progress(
            "Scanning torrent trackers...", enabled=enabled
        )
    else:
        progress_cm = transient_spinner("Loading torrents...", enabled=enabled)

    try:
        client = _create_qbit_client()
        with progress_cm as advance:
            selection = select_torrents(client, filters, on_progress=advance)
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    _print_torrent_selection(selection, output_format)
    if not filters.is_empty:
        _exit_if_no_targeted_matches(len(selection.matched))


@torrents_app.command(name="categories")
def list_qbit_categories(
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            help="Output format.",
        ),
    ] = OutputFormat.table,
) -> None:
    """List torrent categories and usage counts."""
    _validate_format_support("torrents_categories", output_format)
    enabled = progress_enabled(
        output_format=output_format,
        interactive=is_interactive_terminal(),
    )
    try:
        client = _create_qbit_client()
        with transient_spinner("Loading torrents...", enabled=enabled):
            category_usage = list_category_usage(client)
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    payload = {
        "summary": {"categories": len(category_usage)},
        "categories": category_usage,
    }

    if output_format == OutputFormat.json:
        _print_json_output(payload)
        return

    if output_format == OutputFormat.jsonl:
        _print_jsonl_output(payload)
        return

    if output_format == OutputFormat.csv:
        _print_csv_rows(
            ["category", "torrents"],
            [
                (category_name, str(torrent_count))
                for category_name, torrent_count in category_usage.items()
            ],
        )
        return

    if not category_usage:
        typer.echo("No categories found.")
        return

    print_table(
        "Categories",
        ["Category", "Torrents"],
        [
            [category_name, str(torrent_count)]
            for category_name, torrent_count in category_usage.items()
        ],
    )
    print_summary({"categories": len(category_usage)})


@torrents_app.command(name="inspect")
def inspect_qbit_torrent(
    torrent_hash: Annotated[
        str | None,
        typer.Option(
            "--hash",
            help=(
                "Full infohash or unique leading prefix to inspect "
                "(case-insensitive)."
            ),
        ),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option(
            "--name",
            help="Search torrents by name and rank matches by relevance.",
        ),
    ] = None,
    limit: Annotated[
        int,
        typer.Option(
            "--limit",
            help="Maximum number of name search results. Use 0 for no limit.",
        ),
    ] = 20,
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            help="Output format.",
        ),
    ] = OutputFormat.table,
) -> None:
    """Inspect a torrent by hash (or prefix) or search torrents by name."""
    _validate_format_support("torrents_inspect", output_format)
    if (torrent_hash is None) == (name is None):
        _fail("Provide exactly one of --hash or --name.")

    enabled = progress_enabled(
        output_format=output_format,
        interactive=is_interactive_terminal(),
    )
    try:
        client = _create_qbit_client()
        if name is not None:
            with transient_spinner("Loading torrents...", enabled=enabled):
                report = search_torrents_by_name(client, name, limit=limit)
            _print_torrent_name_search(report, output_format)
            _exit_if_no_targeted_matches(report["summary"]["matched"])
            return

        with transient_spinner("Loading torrent...", enabled=enabled):
            report = inspect_torrent(client, torrent_hash or "")
    except AmbiguousTorrentHashError as error:
        _fail_ambiguous_hash(error)
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    if report is None:
        if output_format == OutputFormat.json:
            _print_json_output({"torrent": None, "hash": torrent_hash})
        elif output_format == OutputFormat.jsonl:
            _print_jsonl_output({"torrent": None, "hash": torrent_hash})
        else:
            typer.echo(f"No torrent found for hash prefix: {torrent_hash}")

        _exit_if_no_targeted_matches(0)
        return

    if output_format == OutputFormat.json:
        _print_json_output({"torrent": report})
        return

    if output_format == OutputFormat.jsonl:
        _print_jsonl_output({"torrent": report})
        return

    _print_torrent_details(report)


@torrents_app.command()
def pause(
    torrent_hash: Annotated[
        str | None,
        typer.Option(
            "--hash",
            help=(
                "Pause the torrent matching a full infohash or unique "
                "leading prefix (case-insensitive)."
            ),
        ),
    ] = None,
    category: Annotated[
        list[str],
        typer.Option("--category", help=_CATEGORY_FILTER_HELP),
    ] = [],  # noqa: B006 - Typer requires a literal default to detect list options
    state: Annotated[
        list[str],
        typer.Option("--state", help=_STATE_FILTER_HELP),
    ] = [],  # noqa: B006
    tracker: Annotated[
        str | None,
        typer.Option("--tracker", help=_TRACKER_FILTER_HELP),
    ] = None,
    completed: Annotated[
        bool,
        typer.Option("--completed", help="Restrict to completed torrents."),
    ] = False,
    incomplete: Annotated[
        bool,
        typer.Option("--incomplete", help="Restrict to incomplete torrents."),
    ] = False,
    active: Annotated[
        bool,
        typer.Option(
            "--active", help="Restrict to torrents that are not stopped."
        ),
    ] = False,
    inactive: Annotated[
        bool,
        typer.Option("--inactive", help="Restrict to stopped torrents."),
    ] = False,
    stalled: Annotated[
        bool,
        typer.Option("--stalled", help="Restrict to stalled torrents."),
    ] = False,
    errored: Annotated[
        bool,
        typer.Option(
            "--errored", help="Restrict to torrents reporting an error."
        ),
    ] = False,
    select_all: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Pause all torrents.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/--no-dry-run",
            help="Apply changes instead of previewing them.",
        ),
    ] = True,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Print impacted torrent details.",
        ),
    ] = False,
) -> None:
    """Pause torrents matching a hash, one or more filters, or all."""
    _run_bulk_torrent_action(
        operation=MutationOperation.TORRENTS_PAUSE,
        action="pause",
        torrent_hash=torrent_hash,
        category=category,
        state=state,
        tracker=tracker,
        completed=completed,
        incomplete=incomplete,
        active=active,
        inactive=inactive,
        stalled=stalled,
        errored=errored,
        select_all=select_all,
        dry_run=dry_run,
        verbose=verbose,
    )


@torrents_app.command()
def resume(
    torrent_hash: Annotated[
        str | None,
        typer.Option(
            "--hash",
            help=(
                "Resume the torrent matching a full infohash or unique "
                "leading prefix (case-insensitive)."
            ),
        ),
    ] = None,
    category: Annotated[
        list[str],
        typer.Option("--category", help=_CATEGORY_FILTER_HELP),
    ] = [],  # noqa: B006 - Typer requires a literal default to detect list options
    state: Annotated[
        list[str],
        typer.Option("--state", help=_STATE_FILTER_HELP),
    ] = [],  # noqa: B006
    tracker: Annotated[
        str | None,
        typer.Option("--tracker", help=_TRACKER_FILTER_HELP),
    ] = None,
    completed: Annotated[
        bool,
        typer.Option("--completed", help="Restrict to completed torrents."),
    ] = False,
    incomplete: Annotated[
        bool,
        typer.Option("--incomplete", help="Restrict to incomplete torrents."),
    ] = False,
    active: Annotated[
        bool,
        typer.Option(
            "--active", help="Restrict to torrents that are not stopped."
        ),
    ] = False,
    inactive: Annotated[
        bool,
        typer.Option("--inactive", help="Restrict to stopped torrents."),
    ] = False,
    stalled: Annotated[
        bool,
        typer.Option("--stalled", help="Restrict to stalled torrents."),
    ] = False,
    errored: Annotated[
        bool,
        typer.Option(
            "--errored", help="Restrict to torrents reporting an error."
        ),
    ] = False,
    select_all: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Resume all stopped torrents.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/--no-dry-run",
            help="Apply changes instead of previewing them.",
        ),
    ] = True,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Print impacted torrent details.",
        ),
    ] = False,
) -> None:
    """Resume torrents matching a hash, one or more filters, or all."""
    _run_bulk_torrent_action(
        operation=MutationOperation.TORRENTS_RESUME,
        action="resume",
        torrent_hash=torrent_hash,
        category=category,
        state=state,
        tracker=tracker,
        completed=completed,
        incomplete=incomplete,
        active=active,
        inactive=inactive,
        stalled=stalled,
        errored=errored,
        select_all=select_all,
        dry_run=dry_run,
        verbose=verbose,
    )


@torrents_app.command()
def start(
    torrent_hash: Annotated[
        str | None,
        typer.Option(
            "--hash",
            help=(
                "Start the torrent matching a full infohash or unique "
                "leading prefix (case-insensitive)."
            ),
        ),
    ] = None,
    category: Annotated[
        list[str],
        typer.Option("--category", help=_CATEGORY_FILTER_HELP),
    ] = [],  # noqa: B006 - Typer requires a literal default to detect list options
    state: Annotated[
        list[str],
        typer.Option("--state", help=_STATE_FILTER_HELP),
    ] = [],  # noqa: B006
    tracker: Annotated[
        str | None,
        typer.Option("--tracker", help=_TRACKER_FILTER_HELP),
    ] = None,
    completed: Annotated[
        bool,
        typer.Option(
            "--completed",
            help=(
                "Restrict to completed torrents (Web UI 'Start All' is "
                "--completed --all)."
            ),
        ),
    ] = False,
    incomplete: Annotated[
        bool,
        typer.Option("--incomplete", help="Restrict to incomplete torrents."),
    ] = False,
    active: Annotated[
        bool,
        typer.Option(
            "--active", help="Restrict to torrents that are not stopped."
        ),
    ] = False,
    inactive: Annotated[
        bool,
        typer.Option("--inactive", help="Restrict to stopped torrents."),
    ] = False,
    stalled: Annotated[
        bool,
        typer.Option("--stalled", help="Restrict to stalled torrents."),
    ] = False,
    errored: Annotated[
        bool,
        typer.Option(
            "--errored", help="Restrict to torrents reporting an error."
        ),
    ] = False,
    select_all: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Start all stopped torrents.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/--no-dry-run",
            help="Apply changes instead of previewing them.",
        ),
    ] = True,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Print impacted torrent details.",
        ),
    ] = False,
) -> None:
    """Start stopped torrents matching a hash, one or more filters, or all."""
    _run_bulk_torrent_action(
        operation=MutationOperation.TORRENTS_START,
        action="start",
        torrent_hash=torrent_hash,
        category=category,
        state=state,
        tracker=tracker,
        completed=completed,
        incomplete=incomplete,
        active=active,
        inactive=inactive,
        stalled=stalled,
        errored=errored,
        select_all=select_all,
        dry_run=dry_run,
        verbose=verbose,
    )


@torrents_app.command()
def reannounce(
    torrent_hash: Annotated[
        str | None,
        typer.Option(
            "--hash",
            help=(
                "Reannounce the torrent matching a full infohash or "
                "unique leading prefix (case-insensitive)."
            ),
        ),
    ] = None,
    category: Annotated[
        list[str],
        typer.Option("--category", help=_CATEGORY_FILTER_HELP),
    ] = [],  # noqa: B006 - Typer requires a literal default to detect list options
    state: Annotated[
        list[str],
        typer.Option("--state", help=_STATE_FILTER_HELP),
    ] = [],  # noqa: B006
    tracker: Annotated[
        str | None,
        typer.Option("--tracker", help=_TRACKER_FILTER_HELP),
    ] = None,
    completed: Annotated[
        bool,
        typer.Option("--completed", help="Restrict to completed torrents."),
    ] = False,
    incomplete: Annotated[
        bool,
        typer.Option("--incomplete", help="Restrict to incomplete torrents."),
    ] = False,
    active: Annotated[
        bool,
        typer.Option(
            "--active", help="Restrict to torrents that are not stopped."
        ),
    ] = False,
    inactive: Annotated[
        bool,
        typer.Option("--inactive", help="Restrict to stopped torrents."),
    ] = False,
    stalled: Annotated[
        bool,
        typer.Option("--stalled", help="Restrict to stalled torrents."),
    ] = False,
    errored: Annotated[
        bool,
        typer.Option(
            "--errored", help="Restrict to torrents reporting an error."
        ),
    ] = False,
    select_all: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Reannounce all torrents.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/--no-dry-run",
            help="Apply changes instead of previewing them.",
        ),
    ] = True,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Print impacted torrent details.",
        ),
    ] = False,
) -> None:
    """Reannounce torrents matching a hash, one or more filters, or all."""
    _run_bulk_torrent_action(
        operation=MutationOperation.TORRENTS_REANNOUNCE,
        action="reannounce",
        torrent_hash=torrent_hash,
        category=category,
        state=state,
        tracker=tracker,
        completed=completed,
        incomplete=incomplete,
        active=active,
        inactive=inactive,
        stalled=stalled,
        errored=errored,
        select_all=select_all,
        dry_run=dry_run,
        verbose=verbose,
    )


@trackers_app.command()
def add_if_present(
    source: Annotated[
        str,
        typer.Option(
            "--source",
            help="Source tracker that must already be present.",
        ),
    ],
    target: Annotated[
        str,
        typer.Option(
            "--target",
            help="Target tracker to add when missing.",
        ),
    ],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/--no-dry-run",
            help="Apply changes instead of previewing them.",
        ),
    ] = True,
    match: Annotated[
        TrackerMatchModeOption,
        typer.Option(
            "--match",
            help="Tracker comparison mode.",
        ),
    ] = TrackerMatchModeOption.exact,
    assume_yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help="Skip confirmation for real execution.",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Print impacted torrent details.",
        ),
    ] = False,
) -> None:
    """Add a target tracker when a source tracker is already present."""
    enabled = progress_enabled(interactive=is_interactive_terminal())
    try:
        client = _create_qbit_client()
        with transient_progress(
            "Scanning torrents...", enabled=enabled
        ) as advance:
            plan = plan_tracker_addition(
                client=client,
                source_tracker=source,
                target_tracker=target,
                match_mode=match.value,
                on_progress=advance,
            )
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    def _apply() -> None:
        try:
            apply_tracker_addition(client, plan)
        except RuntimeError as error:
            _fail(str(error))

    applied = _run_mutation(
        operation=MutationOperation.TRACKERS_ADD_IF_PRESENT,
        dry_run=dry_run,
        assume_yes=assume_yes,
        matched=plan.matched_source,
        has_changes=bool(plan.changes),
        apply_fn=_apply,
        summary_rows=lambda status: _addition_summary_rows(plan, status=status),
        confirmation_message=_addition_confirmation_message(plan),
    )

    if verbose:
        _print_addition_details(plan, applied=applied)
    _exit_if_no_targeted_matches(plan.matched_source)


@trackers_app.command(name="list")
def list_trackers(
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            help="Output format.",
        ),
    ] = OutputFormat.table,
) -> None:
    """List normalized tracker identities in use, with usage counts.

    A lightweight inventory: tracker identity (`host[:port]`, never a
    full announce URL), how many torrents use it, and how many tracker
    endpoints that represents. Built on the same collection as `trackers
    status` (see docs/COMMANDS.md, "Tracker Status") but always exits
    `0` -- unlike `trackers status`, this is a plain inventory with no
    health concept, so a script depending only on "what trackers exist"
    is never broken by a degraded tracker elsewhere on the instance. Use
    `trackers status` for endpoint health classification.
    """
    _validate_format_support("trackers_list", output_format)
    enabled = progress_enabled(
        output_format=output_format,
        interactive=is_interactive_terminal(),
    )
    try:
        client = _create_qbit_client()
        with transient_progress(
            "Scanning torrent trackers...", enabled=enabled
        ) as advance:
            report = collect_tracker_status(
                client, build_torrent_filter(), on_progress=advance
            )
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    trackers = [
        {
            "identity": aggregate.identity,
            "torrent_count": aggregate.torrent_count,
            "endpoint_count": aggregate.endpoint_count,
        }
        for aggregate in report.trackers
    ]
    payload = {
        "schema_version": report.schema_version,
        "summary": {
            "trackers": len(trackers),
            "torrents_scanned": report.scanned_torrents,
        },
        "trackers": trackers,
    }

    if output_format == OutputFormat.json:
        _print_json_output(payload)
        return

    if output_format == OutputFormat.jsonl:
        _print_jsonl_output(payload)
        return

    if output_format == OutputFormat.csv:
        _print_csv_rows(
            ["tracker", "torrents", "endpoints"],
            [
                (
                    tracker["identity"],
                    str(tracker["torrent_count"]),
                    str(tracker["endpoint_count"]),
                )
                for tracker in trackers
            ],
        )
        return

    if not trackers:
        typer.echo("No trackers found.")
        return

    print_table(
        "Trackers",
        ["Tracker", "Torrents", "Endpoints"],
        [
            [
                tracker["identity"],
                str(tracker["torrent_count"]),
                str(tracker["endpoint_count"]),
            ]
            for tracker in trackers
        ],
        fold_columns={"Tracker"},
    )
    print_summary(payload["summary"])


@trackers_app.command(name="status")
def trackers_status_command(
    category: Annotated[
        list[str],
        typer.Option("--category", help=_CATEGORY_FILTER_HELP),
    ] = [],  # noqa: B006 - Typer requires a literal default to detect list options
    state: Annotated[
        list[str],
        typer.Option("--state", help=_STATE_FILTER_HELP),
    ] = [],  # noqa: B006
    tracker: Annotated[
        str | None,
        typer.Option(
            "--tracker",
            help=(
                "Restrict the report to one tracker, matched by "
                "host[:port] (a full announce URL is also accepted; only "
                "its host and port are used)."
            ),
        ),
    ] = None,
    completed: Annotated[
        bool,
        typer.Option("--completed", help="Restrict to completed torrents."),
    ] = False,
    incomplete: Annotated[
        bool,
        typer.Option("--incomplete", help="Restrict to incomplete torrents."),
    ] = False,
    active: Annotated[
        bool,
        typer.Option(
            "--active", help="Restrict to torrents that are not stopped."
        ),
    ] = False,
    inactive: Annotated[
        bool,
        typer.Option("--inactive", help="Restrict to stopped torrents."),
    ] = False,
    stalled: Annotated[
        bool,
        typer.Option("--stalled", help="Restrict to stalled torrents."),
    ] = False,
    errored: Annotated[
        bool,
        typer.Option(
            "--errored", help="Restrict to torrents reporting an error."
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Show each tracker's representative message in table output.",
        ),
    ] = False,
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            help="Output format.",
        ),
    ] = OutputFormat.table,
) -> None:
    """Report aggregated tracker health across selected torrents.

    Aggregates tracker observations into stable, redacted tracker
    identities (`host` or `host:port` -- never a full announce URL or
    passkey) and reports how many torrents/endpoints depend on each and
    whether it is healthy, degraded, failing, disabled, or unknown.
    Filters use the same shared vocabulary as `torrents list`; see
    docs/COMMANDS.md ("Torrent Filters", "Tracker Status"). Exit code
    reflects overall health (see `TrackerStatusExitCode`), not a plain
    success/failure result.
    """
    _validate_format_support("trackers_status", output_format)
    try:
        filters = build_torrent_filter(
            categories=category,
            states=state,
            tracker=tracker,
            completed=completed,
            incomplete=incomplete,
            active=active,
            inactive=inactive,
            stalled=stalled,
            errored=errored,
        )
    except ValueError as error:
        print_error(str(error))
        raise typer.Exit(code=TrackerStatusExitCode.INVALID_USAGE) from None

    enabled = progress_enabled(
        output_format=output_format,
        interactive=is_interactive_terminal(),
    )
    try:
        client = _create_qbit_client()
        with transient_progress(
            "Scanning torrent trackers...", enabled=enabled
        ) as advance:
            report = collect_tracker_status(
                client, filters, on_progress=advance
            )
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    _render_tracker_status(report, output_format, verbose=verbose)
    raise typer.Exit(code=tracker_status_exit_code(report.overall_health))


@trackers_app.command(name="inspect")
def inspect_tracker_usage(
    tracker: Annotated[
        str,
        typer.Option(
            "--tracker",
            help=_TRACKER_FILTER_HELP,
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
    """Inspect torrents using a tracker.

    Matches by normalized host[:port] (`--tracker
    tracker.example`; a full announce URL is also accepted, only its
    host and port are used) -- the same identity `trackers status` and
    `torrents list --tracker` use. Every matching endpoint is reduced to
    secret-free structural fields (raw status, health, enabled, a
    sanitized message, scheme, path shape, query key names); a full
    announce URL or passkey is never present in the output.
    """
    _validate_format_support("trackers_inspect", output_format)
    enabled = progress_enabled(
        output_format=output_format,
        interactive=is_interactive_terminal(),
    )
    try:
        client = _create_qbit_client()
        with transient_progress(
            "Scanning torrent trackers...", enabled=enabled
        ) as advance:
            report = inspect_tracker(
                client=client,
                tracker=tracker,
                on_progress=advance,
            )
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    if output_format == OutputFormat.json:
        _print_json_output(report)
        _exit_if_no_targeted_matches(report["matched_tracker"])
        return

    if output_format == OutputFormat.jsonl:
        _print_jsonl_output(report)
        _exit_if_no_targeted_matches(report["matched_tracker"])
        return

    if output_format == OutputFormat.csv:
        _print_csv_rows(
            [*_TORRENT_CSV_FIELDNAMES, "matching_endpoints"],
            [
                (
                    *_torrent_csv_row(
                        torrent,
                        tracker_count_field="active_tracker_count",
                    ),
                    "; ".join(
                        _format_endpoint_summary(endpoint)
                        for endpoint in torrent["matching_endpoints"]
                    ),
                )
                for torrent in report["torrents"]
            ],
        )
        _exit_if_no_targeted_matches(report["matched_tracker"])
        return

    if not report["torrents"]:
        typer.echo("No matching torrents found.")
    else:
        print_table(
            "Matching Torrents",
            [
                "Name",
                "Hash",
                "Category",
                "State",
                "Progress",
                "Ratio",
                "Trackers",
                "Matching Endpoints",
            ],
            [
                [
                    *_torrent_audit_row(
                        torrent,
                        tracker_count_field="active_tracker_count",
                    ),
                    "\n".join(
                        _format_endpoint_summary(endpoint)
                        for endpoint in torrent["matching_endpoints"]
                    ),
                ]
                for torrent in report["torrents"]
            ],
            fold_columns={"Hash"},
        )

    print_summary(
        {
            "tracker": report["tracker"],
            "scanned": report["scanned"],
            "matched_tracker": report["matched_tracker"],
        }
    )
    _exit_if_no_targeted_matches(report["matched_tracker"])


@trackers_app.command()
def replace(
    source: Annotated[
        str,
        typer.Option(
            "--source",
            help="Source tracker to replace.",
        ),
    ],
    target: Annotated[
        str,
        typer.Option(
            "--target",
            help="Target tracker to keep after replacement.",
        ),
    ],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/--no-dry-run",
            help="Apply changes instead of previewing them.",
        ),
    ] = True,
    match: Annotated[
        TrackerMatchModeOption,
        typer.Option(
            "--match",
            help="Tracker comparison mode.",
        ),
    ] = TrackerMatchModeOption.exact,
    assume_yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help="Skip confirmation for real execution.",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Print impacted torrent details.",
        ),
    ] = False,
) -> None:
    """Replace a tracker on every torrent using it."""
    enabled = progress_enabled(interactive=is_interactive_terminal())
    try:
        client = _create_qbit_client()
        with transient_progress(
            "Scanning torrents...", enabled=enabled
        ) as advance:
            plan = plan_tracker_replacement(
                client=client,
                source_tracker=source,
                target_tracker=target,
                match_mode=match.value,
                on_progress=advance,
            )
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    def _apply() -> None:
        try:
            apply_tracker_replacement(client, plan)
        except RuntimeError as error:
            _fail(str(error))

    applied = _run_mutation(
        operation=MutationOperation.TRACKERS_REPLACE,
        dry_run=dry_run,
        assume_yes=assume_yes,
        matched=plan.matched_source,
        has_changes=bool(plan.changes),
        apply_fn=_apply,
        summary_rows=lambda status: _replacement_summary_rows(
            plan, status=status
        ),
        confirmation_message=_replacement_confirmation_message(plan),
    )

    if verbose:
        _print_replacement_details(plan, applied=applied)
    _exit_if_no_targeted_matches(plan.matched_source)


@trackers_app.command(name="replace-passkey")
def replace_tracker_passkey_command(
    tracker: Annotated[
        str,
        typer.Option(
            "--tracker",
            help="Tracker URL template with a literal '{passkey}' "
            "placeholder marking the passkey position, either as a "
            "query parameter value (e.g. '?passkey={passkey}') or as "
            "a full path segment (e.g. '/announce/{passkey}').",
        ),
    ],
    new_passkey: Annotated[
        str,
        typer.Option(
            "--new-passkey",
            help="New passkey value to apply.",
        ),
    ],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/--no-dry-run",
            help="Apply changes instead of previewing them.",
        ),
    ] = True,
    assume_yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help="Skip confirmation for real execution.",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Print impacted torrent details.",
        ),
    ] = False,
) -> None:
    """Replace a tracker's passkey on every torrent using that tracker."""
    enabled = progress_enabled(interactive=is_interactive_terminal())
    try:
        client = _create_qbit_client()
        with transient_progress(
            "Scanning torrents...", enabled=enabled
        ) as advance:
            plan = plan_tracker_passkey_replacement(
                client=client,
                tracker_template=tracker,
                new_passkey=new_passkey,
                on_progress=advance,
            )
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    def _apply() -> None:
        try:
            apply_tracker_passkey_replacement(client, plan)
        except RuntimeError as error:
            _fail(str(error))

    applied = _run_mutation(
        operation=MutationOperation.TRACKERS_REPLACE_PASSKEY,
        dry_run=dry_run,
        assume_yes=assume_yes,
        matched=plan.matched_source,
        has_changes=bool(plan.changes),
        apply_fn=_apply,
        summary_rows=lambda status: _passkey_summary_rows(plan, status=status),
        confirmation_message=_passkey_confirmation_message(plan),
    )

    if verbose:
        _print_passkey_details(plan, applied=applied)
    _exit_if_no_targeted_matches(plan.matched_source)


@backup_app.command(name="export")
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
    _validate_format_support("backup_export", output_format)
    enabled = progress_enabled(
        output_format=output_format,
        interactive=is_interactive_terminal(),
    )
    try:
        config = load_qbit_config()
        client = _create_qbit_client()
        with transient_spinner("Collecting backup data...", enabled=enabled):
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
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    if output_format == OutputFormat.json:
        _print_json_output(state)
        return

    if output_format == OutputFormat.jsonl:
        _print_jsonl_output(state)
        return

    summary = state["summary"]
    metadata = state["metadata"]
    print_summary(
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
    _validate_format_support("backup_diff", output_format)
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
        _fail(str(error))

    rendered_report = report if reveal_sensitive else redact_backup_diff(report)

    if output_format == OutputFormat.json:
        _print_json_output(rendered_report)
    elif output_format == OutputFormat.jsonl:
        _print_jsonl_output(rendered_report)
    else:
        _print_backup_diff(rendered_report)

    _exit_if_backup_diff(report)


@trackers_app.command(name="export")
def export_trackers(
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            help="Output format.",
        ),
    ] = OutputFormat.table,
) -> None:
    """Export normalized tracker identities per torrent.

    Safe by default: identities are always `host[:port]`, never a full
    announce URL or passkey. For a restorable backup that legitimately
    needs raw tracker URLs, use `backup export` instead (see
    docs/COMMANDS.md, "Backup").
    """
    _validate_format_support("trackers_export", output_format)
    enabled = progress_enabled(
        output_format=output_format,
        interactive=is_interactive_terminal(),
    )
    try:
        client = _create_qbit_client()
        with transient_progress(
            "Scanning torrent trackers...", enabled=enabled
        ) as advance:
            state = export_tracker_state(client=client, on_progress=advance)
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    if output_format == OutputFormat.json:
        _print_json_output(state)
        return

    if output_format == OutputFormat.jsonl:
        _print_jsonl_output(state)
        return

    print_summary(state["summary"], title="Tracker Export")
    typer.echo("Use --format json for the full export payload.")


@trackers_app.command()
def remove(
    tracker: Annotated[
        str,
        typer.Option(
            "--tracker",
            help="Tracker to remove from every torrent using it.",
        ),
    ],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/--no-dry-run",
            help="Apply changes instead of previewing them.",
        ),
    ] = True,
    match: Annotated[
        TrackerMatchModeOption,
        typer.Option(
            "--match",
            help="Tracker comparison mode.",
        ),
    ] = TrackerMatchModeOption.exact,
    assume_yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help="Skip confirmation for real execution.",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Print impacted torrent details.",
        ),
    ] = False,
) -> None:
    """Remove a tracker from every torrent using it."""
    enabled = progress_enabled(interactive=is_interactive_terminal())
    try:
        client = _create_qbit_client()
        with transient_progress(
            "Scanning torrents...", enabled=enabled
        ) as advance:
            plan = plan_tracker_removal(
                client=client,
                tracker=tracker,
                match_mode=match.value,
                on_progress=advance,
            )
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    def _apply() -> None:
        try:
            apply_tracker_removal(client, plan)
        except RuntimeError as error:
            _fail(str(error))

    applied = _run_mutation(
        operation=MutationOperation.TRACKERS_REMOVE,
        dry_run=dry_run,
        assume_yes=assume_yes,
        matched=plan.matched_tracker,
        has_changes=bool(plan.changes),
        apply_fn=_apply,
        summary_rows=lambda status: _removal_summary_rows(plan, status=status),
        confirmation_message=_removal_confirmation_message(plan),
    )

    if verbose:
        _print_removal_details(plan, applied=applied)
    _exit_if_no_targeted_matches(plan.matched_tracker)


@explain_app.command(name="torrent")
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
    _validate_format_support("explain_torrent", output_format)
    enabled = progress_enabled(
        output_format=output_format,
        interactive=is_interactive_terminal(),
    )
    try:
        client = _create_qbit_client()
        with transient_spinner("Loading torrent...", enabled=enabled):
            report = explain_torrent(client, torrent_hash)
    except AmbiguousTorrentHashError as error:
        _fail_ambiguous_hash(error)
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    if report is None:
        if output_format == OutputFormat.json:
            _print_json_output({"explanation": None, "hash": torrent_hash})
        elif output_format == OutputFormat.jsonl:
            _print_jsonl_output({"explanation": None, "hash": torrent_hash})
        else:
            typer.echo(f"No torrent found for hash prefix: {torrent_hash}")
        raise typer.Exit(code=ExplainExitCode.TARGET_UNAVAILABLE)

    _render_explanation_report(report, output_format)
    raise typer.Exit(code=explanation_exit_code(report.overall_severity))


@explain_app.command(name="tracker")
def explain_tracker_command(
    tracker: Annotated[
        str,
        typer.Option(
            "--tracker",
            help=_TRACKER_FILTER_HELP,
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
    _validate_format_support("explain_tracker", output_format)
    enabled = progress_enabled(
        output_format=output_format,
        interactive=is_interactive_terminal(),
    )
    try:
        client = _create_qbit_client()
        with transient_progress(
            "Scanning torrent trackers...", enabled=enabled
        ) as advance:
            report = explain_tracker(client, tracker, on_progress=advance)
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    if report is None:
        normalized = normalize_tracker_host(tracker)
        if output_format == OutputFormat.json:
            _print_json_output({"explanation": None, "tracker": normalized})
        elif output_format == OutputFormat.jsonl:
            _print_jsonl_output({"explanation": None, "tracker": normalized})
        else:
            typer.echo(f"No observations found for tracker: {normalized}")
        raise typer.Exit(code=ExplainExitCode.TARGET_UNAVAILABLE)

    _render_explanation_report(report, output_format)
    raise typer.Exit(code=explanation_exit_code(report.overall_severity))


def _render_explanation_report(
    report: ExplanationReport,
    output_format: OutputFormat,
) -> None:
    """Render an explanation report in the requested format."""
    if output_format == OutputFormat.json:
        _print_json_output(explanation_report_to_dict(report))
        return

    if output_format == OutputFormat.jsonl:
        _print_jsonl_output(explanation_report_to_dict(report))
        return

    render_explanation(report)


def _run_mutation(
    *,
    operation: MutationOperation,
    dry_run: bool,
    assume_yes: bool,
    matched: int,
    has_changes: bool,
    apply_fn: Callable[[], None],
    summary_rows: Callable[[MutationStatus], dict[str, Any]],
    confirmation_message: str | None = None,
) -> bool:
    """Run the shared confirm/apply/refuse flow for an already-built plan.

    Centralizes `app.execution.ExecutionPolicy` decisions for every
    mutation command: low-risk commands apply real changes immediately
    (no `confirmation_message` needed); medium/high-risk commands show
    the plan, then confirm, then apply exactly that plan (no rescan).

    `matched` and `has_changes` are deliberately separate: a plan can
    match targets without having any actual change to apply (e.g. every
    torrent already paused, or a passkey already up to date). This
    distinguishes `NO_MATCH` (the selector matched nothing) from
    `NO_CHANGES` (matched, but already satisfied the requested state) —
    neither ever prompts or calls a mutation API, and neither is ever
    reported as `APPLIED`.

    `summary_rows(status)` must build the `print_summary` rows dict with
    its `status` key set to the given `MutationStatus`, so the same
    plan-derived counts can be shown under every terminal status.

    Returns whether the plan was applied. Exits via `typer.Exit` on a
    non-interactive refusal (`ExitCode.ERROR`) or a declined
    confirmation (`ExitCode.SUCCESS`, per the cancellation contract).
    """
    policy = ExecutionPolicy(
        dry_run=dry_run,
        assume_yes=assume_yes,
        interactive=is_interactive_terminal(),
        risk=MUTATION_RISK[operation],
    )

    if matched == 0:
        print_summary(summary_rows(MutationStatus.NO_MATCH))
        return False

    if not has_changes:
        print_summary(summary_rows(MutationStatus.NO_CHANGES))
        return False

    decision = policy.decide()

    if decision is ExecutionDecision.REFUSE_NON_INTERACTIVE:
        _fail(policy.refusal_message(operation))

    if decision is ExecutionDecision.APPLY_WITHOUT_PROMPT:
        apply_fn()
        print_summary(summary_rows(MutationStatus.APPLIED))
        return True

    print_summary(summary_rows(MutationStatus.PREVIEW))

    if decision is ExecutionDecision.PREVIEW_ONLY:
        return False

    assert confirmation_message is not None
    if not confirm(confirmation_message):
        print_cancelled()
        raise typer.Exit(code=ExitCode.SUCCESS)

    apply_fn()
    print_applied()
    return True


def _run_bulk_torrent_action(
    *,
    operation: MutationOperation,
    action: TorrentBulkAction,
    torrent_hash: str | None,
    category: list[str],
    state: list[str],
    tracker: str | None,
    completed: bool,
    incomplete: bool,
    active: bool,
    inactive: bool,
    stalled: bool,
    errored: bool,
    select_all: bool,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Execute a bulk torrent action with shared validation and output."""
    try:
        filters = build_torrent_filter(
            categories=category,
            states=state,
            tracker=tracker,
            completed=completed,
            incomplete=incomplete,
            active=active,
            inactive=inactive,
            stalled=stalled,
            errored=errored,
        )
    except ValueError as error:
        _fail(str(error))

    try:
        validate_torrent_selector(
            torrent_hash=torrent_hash, select_all=select_all, filters=filters
        )
    except ValueError as error:
        _fail(str(error))

    enabled = progress_enabled(interactive=is_interactive_terminal())
    try:
        client = _create_qbit_client()
        with transient_progress(
            f"Scanning torrents to {action}...", enabled=enabled
        ) as advance:
            plan = plan_bulk_torrent_action(
                client=client,
                action=action,
                torrent_hash=torrent_hash,
                select_all=select_all,
                filters=filters,
                on_progress=advance,
            )
    except AmbiguousTorrentHashError as error:
        _fail_ambiguous_hash(error)
    except ValueError as error:
        _fail(str(error))
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    def _apply() -> None:
        try:
            apply_bulk_torrent_action(client, plan)
        except RuntimeError as error:
            _fail(str(error))

    applied = _run_mutation(
        operation=operation,
        dry_run=dry_run,
        assume_yes=False,
        matched=plan.matched,
        has_changes=bool(plan.changes),
        apply_fn=_apply,
        summary_rows=lambda status: _bulk_torrent_summary_rows(
            plan, status=status
        ),
    )

    if verbose:
        _print_bulk_torrent_details(plan, applied=applied)
    _exit_if_no_targeted_matches(plan.matched)


def _create_qbit_client() -> Any:
    """Create and authenticate a qBittorrent API client.

    Never shows a connection spinner or banner: every command already
    communicates its own progress (a scan spinner, a preview, a
    summary), so a separate "Connected to qBittorrent" line would only
    be redundant.
    """
    config = load_qbit_config()
    client = qbittorrentapi.Client(
        host=config.host,
        username=config.username,
        password=config.password,
    )

    try:
        client.auth_log_in()
    except Exception as error:
        if _is_qbit_error(error, {"LoginFailed"}):
            raise QbitAuthenticationError(
                "Authentication to qBittorrent failed. Check QBIT_USER and "
                "QBIT_PASSWORD."
            ) from error
        if _is_qbit_error(error, {"APIConnectionError"}):
            raise QbitConnectionError(
                f"Unable to connect to qBittorrent at {config.host}."
            ) from error

        raise QbitConnectionError(
            f"Unable to initialize qBittorrent client: {error}"
        ) from error

    return client


def _collect_doctor_report() -> DoctorReport:
    """Collect a doctor report, classifying failures instead of aborting.

    Unlike every other command, `doctor` never calls `_fail()` on a
    configuration/connection/authentication error: the failure itself is
    the payload the report exists to describe. Nothing is written to
    stderr here (that contract is reserved for genuinely invalid CLI
    invocation) — a `fail`/`warning` overall status and non-zero exit
    code already carry the outcome, in every `--format`.
    """
    config: QbitConfig | None = None
    config_error: Exception | None = None
    try:
        config = load_qbit_config()
    except ConfigError as error:
        config_error = error

    connection_outcome: ConnectionOutcome | None = None
    connection_error: Exception | None = None
    client: Any | None = None

    if config is not None:
        try:
            client = _create_qbit_client()
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


def _render_doctor_report(
    report: DoctorReport,
    output_format: OutputFormat,
) -> None:
    """Render a doctor report in the requested output format."""
    if output_format == OutputFormat.table:
        render_doctor_table(report)
        return

    if output_format == OutputFormat.json:
        _print_json_output(doctor_report_to_json_dict(report))
        return

    if output_format == OutputFormat.jsonl:
        # Deliberately one document per invocation, like every other
        # command's `jsonl` (see docs/DECISIONS.md) rather than one line
        # per check, even though a per-check stream would also be a
        # reasonable contract for a checks collection.
        typer.echo(
            json.dumps(doctor_report_to_json_dict(report), sort_keys=True)
        )
        return

    _print_csv_rows(
        ["section", "code", "status", "message", "detail", "remediation"],
        doctor_report_to_csv_rows(report),
    )


def _collect_status_snapshot_safely() -> StatusSnapshot:
    """Collect a status snapshot, degrading to `unavailable` on failure.

    Errors always reach stderr, regardless of `--quiet` or `--format`:
    per docs/PLAN.md Phase 1, `--quiet` suppresses successful normal
    output, never genuine failures. The connection spinner/banner is
    always suppressed (`quiet=True` below) since the rendered snapshot
    (or the error message above) already communicates the outcome.
    """
    try:
        config = load_qbit_config()
    except ConfigError as error:
        message = f"Configuration error: {error}"
        print_error(message)
        return build_unavailable_snapshot(
            code="configuration_invalid",
            message=message,
        )

    host = config.host

    try:
        client = _create_qbit_client()
        return collect_status_snapshot(client, host=host)
    except QbitAuthenticationError as error:
        print_error(str(error))
        return build_unavailable_snapshot(
            code="authentication_failed",
            message=str(error),
            host=host,
        )
    except RuntimeError as error:
        print_error(str(error))
        return build_unavailable_snapshot(
            code="qbittorrent_unavailable",
            message=str(error),
            host=host,
        )
    except Exception as error:
        message = f"qBittorrent API error: {error}"
        print_error(message)
        return build_unavailable_snapshot(
            code="qbittorrent_unavailable",
            message=message,
            host=host,
        )


def _collect_status_snapshot_for_watch(host: str) -> StatusSnapshot:
    """Collect one snapshot for `status --watch`.

    Mirrors `_collect_status_snapshot_safely()`'s connection handling,
    but only degrades to an `unavailable` snapshot for *recoverable*
    failures: `RuntimeError` (covers `QbitAuthenticationError` and
    `QbitConnectionError`, both defined as `RuntimeError` subclasses)
    and `OSError` (covers every `qbittorrentapi` exception raised
    directly from a post-login API call — `APIConnectionError` and
    everything derived from it, e.g. `LoginFailed`, HTTP errors, are
    themselves `OSError` subclasses). Never prints to stderr on this
    path: the unavailable snapshot's own health/alert already carries
    the failure, and repeating it there would spam stderr on every
    retry — see docs/DECISIONS.md.

    Any other exception is an unexpected programming error, not a
    remote/temporary failure, and is deliberately left to propagate so
    the watch loop stops instead of silently reporting "unavailable"
    forever.
    """
    try:
        client = _create_qbit_client()
        return collect_status_snapshot(client, host=host)
    except QbitAuthenticationError as error:
        return build_unavailable_snapshot(
            code="authentication_failed",
            message=str(error),
            host=host,
        )
    except (RuntimeError, OSError) as error:
        return build_unavailable_snapshot(
            code="qbittorrent_unavailable",
            message=str(error),
            host=host,
        )


def _emit_status_jsonl(snapshot: StatusSnapshot) -> None:
    """Emit one status snapshot as a single, immediately flushed JSONL line."""
    typer.echo(json.dumps(snapshot_to_json_dict(snapshot), sort_keys=True))
    sys.stdout.flush()


def _run_status_watch(output_format: OutputFormat, interval: float) -> None:
    """Run `status --watch`: repeatedly collect, then render or serialize.

    Reuses `collect_status_snapshot`/`build_unavailable_snapshot`
    unchanged via `watch_status()` (`app/status.py`) — no second status
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
        config = load_qbit_config()
    except ConfigError as error:
        _fail_status_usage(f"Configuration error: {error}")

    host = config.host
    iteration = 0

    def _collect() -> StatusSnapshot:
        return _collect_status_snapshot_for_watch(host)

    try:
        if output_format is OutputFormat.table:
            with live_status_display() as update_display:

                def _emit_table(snapshot: StatusSnapshot) -> None:
                    nonlocal iteration
                    iteration += 1
                    update_display(
                        snapshot,
                        WatchRenderContext(
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
                emit=_emit_status_jsonl,
                interval=interval,
                sleep=time.sleep,
                now=time.monotonic,
            )
    except KeyboardInterrupt:
        raise typer.Exit(code=ExitCode.SUCCESS) from None
    except Exception as error:
        print_error(f"Unexpected error in status --watch: {error}")
        raise typer.Exit(code=ExitCode.ERROR) from error


def _render_status(
    snapshot: StatusSnapshot,
    output_format: OutputFormat,
) -> None:
    """Render a status snapshot in the requested output format."""
    if output_format == OutputFormat.table:
        render_status_table(snapshot)
        return

    if output_format == OutputFormat.json:
        _print_json_output(snapshot_to_json_dict(snapshot))
        return

    if output_format == OutputFormat.jsonl:
        typer.echo(json.dumps(snapshot_to_json_dict(snapshot), sort_keys=True))
        return

    _print_csv_rows(["section", "key", "value"], snapshot_to_csv_rows(snapshot))


def _print_csv_rows(fieldnames: list[str], rows: list[tuple[str, ...]]) -> None:
    """Print rows as CSV with a stable header, free of Rich formatting."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(fieldnames)
    writer.writerows(rows)
    typer.echo(buffer.getvalue(), nl=False)


def _get_optional_client_value(client: Any, method_name: str) -> str:
    """Read an optional value from a qBittorrent API method."""
    method = getattr(client, method_name, None)
    if method is None:
        return "unknown"

    try:
        value = method()
    except Exception:
        return "unknown"

    return str(value)


def _format_percentage(value: float) -> str:
    """Format a 0-to-1 ratio as a percentage."""
    return f"{value * 100:.1f}%"


def _print_json_output(payload: Any) -> None:
    """Print a JSON payload for audit commands."""
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


def _print_jsonl_output(payload: Any) -> None:
    """Print exactly one compact JSON document, followed by a newline."""
    typer.echo(json.dumps(payload, sort_keys=True))


def _torrent_audit_row(
    torrent: dict[str, Any],
    *,
    tracker_count_field: str = "tracker_count",
    include_tracker_count: bool = True,
) -> list[str]:
    """Build one torrent audit table row.

    `include_tracker_count=False` omits the trailing column entirely
    rather than rendering a placeholder: a whole selection either
    collected tracker data for every matched torrent or for none of
    them (`TorrentSelection.tracker_data_collected`), so "not
    collected" is a property of the table, not of one cell -- showing a
    bare `-` per row reads too easily as "zero trackers" instead of
    "not measured".
    """
    row = [
        torrent["name"],
        torrent["hash"],
        torrent.get("category", "(uncategorized)"),
        torrent["state"],
        _format_percentage(torrent["progress"]),
        f"{torrent['ratio']:.2f}",
    ]
    if include_tracker_count:
        row.append(str(torrent[tracker_count_field]))
    return row


_TORRENT_CSV_FIELDNAMES = [
    "name",
    "hash",
    "category",
    "state",
    "progress",
    "ratio",
    "tracker_count",
]


def _torrent_csv_row(
    torrent: dict[str, Any],
    *,
    tracker_count_field: str = "tracker_count",
) -> tuple[str, ...]:
    """Build one torrent CSV row with numeric fields left unformatted."""
    tracker_count = torrent.get(tracker_count_field)
    return (
        torrent["name"],
        torrent["hash"],
        torrent.get("category", "(uncategorized)"),
        torrent["state"],
        str(torrent["progress"]),
        str(torrent["ratio"]),
        "" if tracker_count is None else str(tracker_count),
    )


def _format_endpoint_summary(endpoint: dict[str, Any]) -> str:
    """Render one `trackers inspect` matching endpoint as a safe summary line.

    Built entirely from `SafeTrackerIdentity`/`TrackerHealth` fields --
    never a full announce URL, path value, or query value.
    """
    parts = [endpoint["health"]]
    if not endpoint["enabled"]:
        parts.append("disabled")
    if endpoint["scheme"]:
        parts.append(endpoint["scheme"])
    if endpoint["path_shape"]:
        parts.append(f"path={endpoint['path_shape']}")
    if endpoint["query_keys"]:
        parts.append(f"query={','.join(endpoint['query_keys'])}")
    if endpoint["message"]:
        parts.append(f"msg={endpoint['message']}")
    return " ".join(parts)


def _selected_torrent_to_dict(torrent: SelectedTorrent) -> dict[str, Any]:
    """Convert a `SelectedTorrent` into a JSON/CSV/table-ready dict."""
    return {
        "hash": torrent.hash,
        "name": torrent.name,
        "category": torrent.category,
        "state": torrent.state,
        "size": torrent.size,
        "progress": torrent.progress,
        "ratio": torrent.ratio,
        "tracker_count": torrent.tracker_count,
    }


def _print_torrent_selection(
    selection: TorrentSelection,
    output_format: OutputFormat,
) -> None:
    """Print a torrent selection, one shared shape for every filter.

    JSON/JSONL always include a normalized `filters` representation
    (`app.torrents.torrent_filter_to_dict`) alongside the usual
    `summary`/`torrents` -- a schema addition, not a removal, over the
    previous filter-specific payloads (see docs/DECISIONS.md for the
    unification this replaces).
    """
    torrents = [
        _selected_torrent_to_dict(torrent) for torrent in selection.matched
    ]
    payload = {
        "filters": torrent_filter_to_dict(selection.filters),
        "summary": {
            "scanned": selection.scanned,
            "matched": len(selection.matched),
        },
        "torrents": torrents,
    }

    if output_format == OutputFormat.json:
        _print_json_output(payload)
        return

    if output_format == OutputFormat.jsonl:
        _print_jsonl_output(payload)
        return

    if output_format == OutputFormat.csv:
        _print_csv_rows(
            _TORRENT_CSV_FIELDNAMES,
            [_torrent_csv_row(torrent) for torrent in torrents],
        )
        return

    if not selection.filters.is_empty:
        typer.echo(f"Filter: {describe_torrent_filter(selection.filters)}")

    if not torrents:
        typer.echo("No torrents found.")
    else:
        columns = ["Name", "Hash", "Category", "State", "Progress", "Ratio"]
        if selection.tracker_data_collected:
            columns.append("Trackers")

        print_table(
            "Torrents",
            columns,
            [
                _torrent_audit_row(
                    torrent,
                    include_tracker_count=selection.tracker_data_collected,
                )
                for torrent in torrents
            ],
            fold_columns={"Hash"},
        )

    print_summary(
        {"scanned": selection.scanned, "matched": len(selection.matched)}
    )


def _render_tracker_status(
    report: TrackerStatusReport,
    output_format: OutputFormat,
    *,
    verbose: bool,
) -> None:
    """Render a tracker status report in the requested format."""
    if output_format == OutputFormat.json:
        _print_json_output(tracker_status_report_to_dict(report))
        return

    if output_format == OutputFormat.jsonl:
        _print_jsonl_output(tracker_status_report_to_dict(report))
        return

    if output_format == OutputFormat.csv:
        _print_csv_rows(
            TRACKER_STATUS_CSV_FIELDNAMES,
            tracker_status_report_to_csv_rows(report),
        )
        return

    if not report.filters.is_empty:
        typer.echo(f"Filter: {describe_torrent_filter(report.filters)}")

    if not report.trackers:
        typer.echo("No trackers found.")
    else:
        print_table(
            "Trackers",
            [
                "Tracker",
                "Health",
                "Torrents",
                "Endpts",
                "Healthy",
                "Warn",
                "Crit",
                "Disab",
                "Unk",
            ],
            [
                [
                    aggregate.identity,
                    aggregate.health.value,
                    str(aggregate.torrent_count),
                    str(aggregate.endpoint_count),
                    str(aggregate.healthy_count),
                    str(aggregate.warning_count),
                    str(aggregate.critical_count),
                    str(aggregate.disabled_count),
                    str(aggregate.unknown_count),
                ]
                for aggregate in report.trackers
            ],
            fold_columns={"Tracker"},
        )

        if verbose:
            messages = [
                [aggregate.identity, aggregate.representative_message or ""]
                for aggregate in report.trackers
                if aggregate.representative_message
            ]
            if messages:
                print_table(
                    "Tracker Messages", ["Tracker", "Message"], messages
                )

    print_summary(
        {
            "scanned_torrents": report.scanned_torrents,
            "matched_torrents": report.matched_torrents,
            "trackers": len(report.trackers),
            "collection_errors": report.collection_errors,
            "overall_health": report.overall_health.value,
        }
    )


def _print_torrent_details(report: dict[str, Any]) -> None:
    """Print detailed torrent inspection output."""
    print_summary(
        {
            "state": report["state"],
            "progress": _format_percentage(report["progress"]),
            "ratio": f"{report['ratio']:.2f}",
            "size": report["size"],
            "save_path": report["save_path"],
            "category": report["category"],
            "added_on": report["added_on"],
            "active_trackers": report["active_tracker_count"],
        },
        title=f"Torrent: {report['name']} ({report['hash']})",
    )

    if report["trackers"]:
        print_table(
            "Trackers",
            ["Tracker", "Details"],
            [
                [tracker["tracker"], _format_endpoint_summary(tracker)]
                for tracker in report["trackers"]
            ],
        )
    else:
        typer.echo("Trackers: none")


def _print_torrent_name_search(
    report: dict[str, Any],
    output_format: OutputFormat,
) -> None:
    """Print torrent name search results.

    `--format csv` is intentionally not offered here: this helper also
    renders `torrents inspect --hash`'s single-torrent result elsewhere
    in this module (nested tracker details, no stable tabular shape),
    and `torrents inspect` uses one format-support rule for both modes
    — see `FORMAT_SUPPORT["torrents_inspect"]` and docs/DECISIONS.md.
    `_validate_format_support` already rejected `csv` before any API
    call was made.
    """
    if output_format == OutputFormat.json:
        _print_json_output(report)
        return

    if output_format == OutputFormat.jsonl:
        _print_jsonl_output(report)
        return

    summary = report["summary"]
    typer.echo(f"Name search: {report['query']!r}")
    print_summary({"matched": summary["matched"], "limit": summary["limit"]})

    if not report["matches"]:
        typer.echo("No matching torrents found.")
        return

    print_table(
        "Matches",
        ["Name", "Hash", "Score", "State", "Progress", "Ratio"],
        [
            [
                match["name"],
                match["hash"],
                f"{match['match_score']:.2f}",
                match["state"],
                _format_percentage(match["progress"]),
                f"{match['ratio']:.2f}",
            ]
            for match in report["matches"]
        ],
        fold_columns={"Hash"},
    )


def _print_backup_diff(report: dict[str, Any]) -> None:
    """Print a human-readable backup diff report."""
    summary = report["summary"]

    if summary["identical"]:
        typer.echo("Backup diff: exports are identical.")
        return

    print_summary(
        {
            "baseline": summary["baseline"]["source"],
            "target": summary["target"]["source"],
            "added_torrents": summary["added_torrents"],
            "removed_torrents": summary["removed_torrents"],
            "changed_torrents": summary["changed_torrents"],
            "tracker_usage_added": summary["tracker_usage_added"],
            "tracker_usage_removed": summary["tracker_usage_removed"],
            "tracker_usage_changed": summary["tracker_usage_changed"],
        },
        title="Backup Diff",
    )

    if report["added_torrents"]:
        print_table(
            "Added Torrents",
            ["Name", "Hash"],
            [
                [torrent["name"], torrent["hash"]]
                for torrent in report["added_torrents"]
            ],
        )

    if report["removed_torrents"]:
        print_table(
            "Removed Torrents",
            ["Name", "Hash"],
            [
                [torrent["name"], torrent["hash"]]
                for torrent in report["removed_torrents"]
            ],
        )

    if report["changed_torrents"]:
        print_table(
            "Changed Torrents",
            ["Name", "Hash", "Trackers Added", "Trackers Removed"],
            [
                [
                    torrent["name"],
                    torrent["hash"],
                    "\n".join(torrent["normalized_trackers"]["added"]),
                    "\n".join(torrent["normalized_trackers"]["removed"]),
                ]
                for torrent in report["changed_torrents"]
            ],
        )

    tracker_usage = report["tracker_usage"]
    if tracker_usage["added"]:
        print_table(
            "Tracker Usage Added",
            ["Tracker", "Torrents"],
            [
                [tracker_url, str(torrent_count)]
                for tracker_url, torrent_count in tracker_usage["added"].items()
            ],
        )

    if tracker_usage["removed"]:
        print_table(
            "Tracker Usage Removed",
            ["Tracker", "Torrents"],
            [
                [tracker_url, str(torrent_count)]
                for tracker_url, torrent_count in tracker_usage[
                    "removed"
                ].items()
            ],
        )

    if tracker_usage["changed"]:
        print_table(
            "Tracker Usage Changed",
            ["Tracker", "Baseline", "Target"],
            [
                [item["tracker"], str(item["baseline"]), str(item["target"])]
                for item in tracker_usage["changed"]
            ],
        )


def _fail(message: str) -> NoReturn:
    """Print an actionable error and exit with a failure code."""
    print_error(message)
    raise typer.Exit(code=ExitCode.ERROR)


def _fail_status_usage(message: str) -> NoReturn:
    """Print an actionable error and exit with `status`'s invalid-usage code.

    `status` (one-shot and `--watch`) uses `StatusExitCode`, not the
    shared `ExitCode`, for local CLI/configuration validation failures —
    see `StatusExitCode`'s docstring.
    """
    print_error(message)
    raise typer.Exit(code=StatusExitCode.INVALID_USAGE)


def _fail_ambiguous_hash(error: AmbiguousTorrentHashError) -> NoReturn:
    """Print ambiguous hash candidates and exit with a failure code.

    Deliberately reuses `ExitCode.ERROR` rather than introducing a new
    ambiguity-specific exit code: see docs/DECISIONS.md.
    """
    print_ambiguous_hash_error(error.value, error.candidates)
    raise typer.Exit(code=ExitCode.ERROR)


def _exit_if_no_targeted_matches(match_count: int) -> None:
    """Exit explicitly when a targeted command does not match any torrent."""
    if match_count == 0:
        raise typer.Exit(code=ExitCode.NO_MATCH)


def _exit_if_backup_diff(report: dict[str, Any]) -> None:
    """Exit explicitly when two exports differ."""
    if has_backup_diff(report):
        raise typer.Exit(code=ExitCode.NO_MATCH)


def _is_qbit_error(error: Exception, class_names: set[str]) -> bool:
    """Return whether an exception matches expected qBittorrent errors."""
    return type(error).__name__ in class_names


def _bulk_torrent_summary_rows(
    plan: BulkTorrentActionPlan,
    *,
    status: MutationStatus,
) -> dict[str, Any]:
    """Build `print_summary` rows for a bulk torrent action plan.

    `filter`/`value` describe whichever selector was actually used
    (`--hash`, `--all`, or one or more combined filters, rendered by
    `describe_torrent_filter` -- never a raw tracker URL, so a passkey
    can never reach this summary).
    """
    if plan.torrent_hash is not None:
        selector_kind, selector_value = "hash", plan.torrent_hash
    elif plan.select_all:
        selector_kind, selector_value = "all", "*"
    else:
        selector_kind, selector_value = "filter", describe_torrent_filter(
            plan.filters
        )

    return {
        "action": plan.action,
        "filter": selector_kind,
        "value": selector_value,
        "scanned": plan.scanned,
        "matched": plan.matched,
        "modified": len(plan.changes),
        "skipped": len(plan.skipped),
        "status": status,
    }


def _print_bulk_torrent_details(
    plan: BulkTorrentActionPlan,
    *,
    applied: bool,
) -> None:
    """Print verbose bulk torrent action details, if any."""
    if not plan.changes and not plan.skipped:
        return

    change_label = plan.action if applied else f"would_{plan.action}"
    rows = [[change_label, change.name, change.hash] for change in plan.changes]
    rows += [[skip.reason, skip.name, skip.hash] for skip in plan.skipped]

    print_table("Details", ["Action", "Name", "Hash"], rows)


def _addition_summary_rows(
    plan: TrackerAdditionPlan,
    *,
    status: MutationStatus,
) -> dict[str, Any]:
    """Build `print_summary` rows for a tracker addition plan."""
    return {
        "scanned": plan.scanned,
        "matched_source": plan.matched_source,
        "already_had_target": len(plan.already_had_target),
        "modified": len(plan.changes),
        "status": status,
    }


def _print_addition_details(
    plan: TrackerAdditionPlan, *, applied: bool
) -> None:
    """Print verbose tracker addition details, if any."""
    if not plan.changes and not plan.already_had_target:
        return

    change_label = "added" if applied else "would_add"
    rows = [[change_label, c.name, c.hash] for c in plan.changes]
    rows += [
        ["already_had_target", c.name, c.hash] for c in plan.already_had_target
    ]
    print_table("Details", ["Action", "Name", "Hash"], rows)


def _addition_confirmation_message(plan: TrackerAdditionPlan) -> str:
    """Build the confirmation prompt for `trackers add-if-present`."""
    return (
        f"Add tracker {redact_tracker_identity(plan.target_tracker)} to "
        f"{len(plan.changes)} torrent(s) already using "
        f"{redact_tracker_identity(plan.source_tracker)}?"
    )


def _removal_summary_rows(
    plan: TrackerRemovalPlan,
    *,
    status: MutationStatus,
) -> dict[str, Any]:
    """Build `print_summary` rows for a tracker removal plan."""
    return {
        "scanned": plan.scanned,
        "matched_tracker": plan.matched_tracker,
        "modified": len(plan.changes),
        "removed_urls": plan.removed_url_count,
        "status": status,
    }


def _print_removal_details(plan: TrackerRemovalPlan, *, applied: bool) -> None:
    """Print verbose tracker removal details, if any."""
    if not plan.changes:
        return

    change_label = "removed" if applied else "would_remove"
    print_table(
        "Details",
        ["Action", "Name", "Hash", "URLs"],
        [
            [change_label, c.name, c.hash, str(len(c.urls))]
            for c in plan.changes
        ],
    )


def _removal_confirmation_message(plan: TrackerRemovalPlan) -> str:
    """Build the confirmation prompt for `trackers remove`."""
    return (
        f"Remove tracker {redact_tracker_identity(plan.tracker)} from "
        f"{plan.matched_tracker} torrent(s) "
        f"({plan.removed_url_count} URL(s) total)? Private torrents relying "
        "solely on this tracker will lose their announce."
    )


def _replacement_summary_rows(
    plan: TrackerReplacementPlan,
    *,
    status: MutationStatus,
) -> dict[str, Any]:
    """Build `print_summary` rows for a tracker replacement plan."""
    already_had_target = sum(
        1 for change in plan.changes if change.already_had_target
    )
    return {
        "scanned": plan.scanned,
        "matched_source": plan.matched_source,
        "already_had_target": already_had_target,
        "modified": len(plan.changes),
        "replaced_urls": plan.replaced_url_count,
        "removed_urls": plan.removed_url_count,
        "status": status,
    }


def _print_replacement_details(
    plan: TrackerReplacementPlan,
    *,
    applied: bool,
) -> None:
    """Print verbose tracker replacement details, if any."""
    if not plan.changes:
        return

    rows: list[list[str]] = []
    for change in plan.changes:
        if change.already_had_target:
            label = "removed_source" if applied else "would_remove_source"
        else:
            label = "replaced" if applied else "would_replace"
        info = f"{len(change.remove_urls)} removed"
        if change.replace_url is not None:
            info = f"1 replaced, {info}"
        rows.append([label, change.name, change.hash, info])

    print_table("Details", ["Action", "Name", "Hash", "Info"], rows)


def _replacement_confirmation_message(plan: TrackerReplacementPlan) -> str:
    """Build the confirmation prompt for `trackers replace`."""
    return (
        f"Replace {redact_tracker_identity(plan.source_tracker)} with "
        f"{redact_tracker_identity(plan.target_tracker)} on "
        f"{plan.matched_source} torrent(s) "
        f"({plan.replaced_url_count} replaced, {plan.removed_url_count} "
        "removed)? Private torrents relying solely on the source tracker "
        "will lose their announce until the target tracker takes over."
    )


def _passkey_summary_rows(
    plan: PasskeyReplacementPlan,
    *,
    status: MutationStatus,
) -> dict[str, Any]:
    """Build `print_summary` rows for a passkey replacement plan.

    Deliberately excludes every field derived from `change.stale_urls`:
    only counts ever reach this summary.
    """
    return {
        "scanned": plan.scanned,
        "matched_source": plan.matched_source,
        "already_up_to_date": plan.already_up_to_date,
        "modified": len(plan.changes),
        "replaced_urls": plan.replaced_url_count,
        "status": status,
    }


def _print_passkey_details(
    plan: PasskeyReplacementPlan,
    *,
    applied: bool,
) -> None:
    """Print verbose passkey replacement details, if any.

    Only ever prints `change.hash` / `change.name` / `change.stale_url_count`
    — never `change.stale_urls`, which carries the new passkey.
    """
    if not plan.changes:
        return

    change_label = "replaced" if applied else "would_replace"
    print_table(
        "Details",
        ["Action", "Name", "Hash", "URLs"],
        [
            [change_label, c.name, c.hash, str(c.stale_url_count)]
            for c in plan.changes
        ],
    )


def _passkey_confirmation_message(plan: PasskeyReplacementPlan) -> str:
    """Build the confirmation prompt for `trackers replace-passkey`.

    Never includes the old or new passkey, or any tracker URL.
    """
    return (
        f"Update the passkey on {len(plan.changes)} torrent(s) "
        f"({plan.replaced_url_count} tracker URL(s) total)? An incorrect "
        "passkey will break every affected torrent's announce until "
        "corrected."
    )


if __name__ == "__main__":
    app()
