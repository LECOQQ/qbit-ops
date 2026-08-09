"""Register the `trackers` command group."""

from enum import StrEnum
from typing import Annotated

import typer

from qbit_core.errors import ErrorCategory, InvalidInputError, require_non_blank
from qbit_core.features.torrents import build_torrent_filter
from qbit_core.features.tracker_status import (
    collect_tracker_status,
    tracker_status_exit_code,
)
from qbit_core.features.trackers import (
    apply_tracker_addition,
    apply_tracker_passkey_replacement,
    apply_tracker_removal,
    apply_tracker_replacement,
    export_tracker_state,
    inspect_tracker,
    plan_tracker_addition,
    plan_tracker_passkey_replacement,
    plan_tracker_removal,
    plan_tracker_replacement,
)
from qbit_core.shared.execution import MutationOperation
from qbit_ops.cli import error_boundary, rendering
from qbit_ops.cli.commands._shared import (
    exit_if_no_targeted_matches,
    run_mutation,
)
from qbit_ops.cli.exit_codes import TrackerStatusExitCode
from qbit_ops.cli.rendering import OutputFormat
from qbit_ops.cli.selector_options import (
    CATEGORY_FILTER_HELP,
    STATE_FILTER_HELP,
    TRACKER_FILTER_HELP,
)
from qbit_ops.cli.validation import validate_format_support

trackers_app = typer.Typer(help="Manage qBittorrent trackers.")


class TrackerMatchModeOption(StrEnum):
    exact = "exact"
    without_query = "without-query"


@trackers_app.command()
@error_boundary.catch_internal_errors
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
    try:
        source = require_non_blank(source, field_name="--source")
        target = require_non_blank(target, field_name="--target")
    except InvalidInputError as error:
        error_boundary.fail(str(error), ErrorCategory.INVALID_INPUT)
    enabled = rendering.progress_enabled(
        interactive=rendering.is_interactive_terminal()
    )
    with error_boundary.qbit_error_boundary():
        client = error_boundary.create_qbit_client()
        with rendering.transient_progress(
            "Scanning torrents...", enabled=enabled
        ) as advance:
            plan = plan_tracker_addition(
                client=client,
                source_tracker=source,
                target_tracker=target,
                match_mode=match.value,
                on_progress=advance,
            )

    def _apply() -> None:
        try:
            apply_tracker_addition(client, plan)
        except RuntimeError as error:
            error_boundary.fail(str(error))

    applied = run_mutation(
        operation=MutationOperation.TRACKERS_ADD_IF_PRESENT,
        dry_run=dry_run,
        assume_yes=assume_yes,
        matched=plan.matched_source,
        has_changes=bool(plan.changes),
        apply_fn=_apply,
        summary_rows=lambda status: rendering.addition_summary_rows(
            plan, status=status
        ),
        confirmation_message=rendering.addition_confirmation_message(plan),
    )

    if verbose:
        rendering.print_addition_details(plan, applied=applied)
    exit_if_no_targeted_matches(plan.matched_source)


@trackers_app.command(name="list")
@error_boundary.catch_internal_errors
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
    full announce URL), torrent count, and endpoint count. Always exits
    `0` -- unlike `trackers status`, this has no health concept, so a
    script checking "what trackers exist" is never broken by a degraded
    tracker elsewhere. Use `trackers status` for health classification.
    """
    validate_format_support("trackers_list", output_format)
    enabled = rendering.progress_enabled(
        output_format=output_format,
        interactive=rendering.is_interactive_terminal(),
    )
    with error_boundary.qbit_error_boundary():
        client = error_boundary.create_qbit_client()
        with rendering.transient_progress(
            "Scanning torrent trackers...", enabled=enabled
        ) as advance:
            report = collect_tracker_status(
                client, build_torrent_filter(), on_progress=advance
            )

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
        rendering.print_json_output(payload)
        return

    if output_format == OutputFormat.jsonl:
        rendering.print_jsonl_output(payload)
        return

    if output_format == OutputFormat.csv:
        rendering.print_csv_rows(
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

    rendering.print_table(
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
    rendering.print_summary(payload["summary"])


@trackers_app.command(name="status")
@error_boundary.catch_internal_errors
def trackers_status_command(
    category: Annotated[
        list[str],
        typer.Option("--category", help=CATEGORY_FILTER_HELP),
    ] = [],  # noqa: B006 - Typer requires a literal default to detect list options
    state: Annotated[
        list[str],
        typer.Option("--state", help=STATE_FILTER_HELP),
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

    Aggregates into stable, redacted tracker identities (`host` or
    `host:port` -- never a full announce URL or passkey) and reports
    torrent/endpoint counts and health per identity. Filters use the
    same vocabulary as `torrents list`. Exit code reflects overall
    health (`TrackerStatusExitCode`), not a plain success/failure.
    """
    validate_format_support("trackers_status", output_format)
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
        rendering.print_error(str(error))
        raise typer.Exit(code=TrackerStatusExitCode.INVALID_USAGE) from None

    enabled = rendering.progress_enabled(
        output_format=output_format,
        interactive=rendering.is_interactive_terminal(),
    )
    with error_boundary.qbit_error_boundary():
        client = error_boundary.create_qbit_client()
        with rendering.transient_progress(
            "Scanning torrent trackers...", enabled=enabled
        ) as advance:
            report = collect_tracker_status(
                client, filters, on_progress=advance
            )

    rendering.render_tracker_status(report, output_format, verbose=verbose)
    raise typer.Exit(code=tracker_status_exit_code(report.overall_health))


@trackers_app.command(name="inspect")
@error_boundary.catch_internal_errors
def inspect_tracker_usage(
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
    """Inspect torrents using a tracker.

    Matches by normalized host[:port] (a full announce URL is also
    accepted; only its host and port are used) -- the same identity
    `trackers status` and `torrents list --tracker` use. Every matching
    endpoint is reduced to secret-free structural fields; a full
    announce URL or passkey is never present in the output.
    """
    validate_format_support("trackers_inspect", output_format)
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
            report = inspect_tracker(
                client=client,
                tracker=tracker,
                on_progress=advance,
            )

    if output_format == OutputFormat.json:
        rendering.print_json_output(report)
        exit_if_no_targeted_matches(report["matched_tracker"])
        return

    if output_format == OutputFormat.jsonl:
        rendering.print_jsonl_output(report)
        exit_if_no_targeted_matches(report["matched_tracker"])
        return

    if output_format == OutputFormat.csv:
        rendering.print_csv_rows(
            [*rendering.TORRENT_CSV_FIELDNAMES, "matching_endpoints"],
            [
                (
                    *rendering.torrent_csv_row(
                        torrent,
                        tracker_count_field="active_tracker_count",
                    ),
                    "; ".join(
                        rendering.format_endpoint_summary(endpoint)
                        for endpoint in torrent["matching_endpoints"]
                    ),
                )
                for torrent in report["torrents"]
            ],
        )
        exit_if_no_targeted_matches(report["matched_tracker"])
        return

    if not report["torrents"]:
        typer.echo("No matching torrents found.")
    else:
        rendering.print_table(
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
                    *rendering.torrent_audit_row(
                        torrent,
                        tracker_count_field="active_tracker_count",
                    ),
                    "\n".join(
                        rendering.format_endpoint_summary(endpoint)
                        for endpoint in torrent["matching_endpoints"]
                    ),
                ]
                for torrent in report["torrents"]
            ],
            fold_columns={"Hash"},
        )

    rendering.print_summary(
        {
            "tracker": report["tracker"],
            "scanned": report["scanned"],
            "matched_tracker": report["matched_tracker"],
        }
    )
    exit_if_no_targeted_matches(report["matched_tracker"])


@trackers_app.command()
@error_boundary.catch_internal_errors
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
    try:
        source = require_non_blank(source, field_name="--source")
        target = require_non_blank(target, field_name="--target")
    except InvalidInputError as error:
        error_boundary.fail(str(error), ErrorCategory.INVALID_INPUT)
    enabled = rendering.progress_enabled(
        interactive=rendering.is_interactive_terminal()
    )
    with error_boundary.qbit_error_boundary():
        client = error_boundary.create_qbit_client()
        with rendering.transient_progress(
            "Scanning torrents...", enabled=enabled
        ) as advance:
            plan = plan_tracker_replacement(
                client=client,
                source_tracker=source,
                target_tracker=target,
                match_mode=match.value,
                on_progress=advance,
            )

    def _apply() -> None:
        try:
            apply_tracker_replacement(client, plan)
        except RuntimeError as error:
            error_boundary.fail(str(error))

    applied = run_mutation(
        operation=MutationOperation.TRACKERS_REPLACE,
        dry_run=dry_run,
        assume_yes=assume_yes,
        matched=plan.matched_source,
        has_changes=bool(plan.changes),
        apply_fn=_apply,
        summary_rows=lambda status: rendering.replacement_summary_rows(
            plan, status=status
        ),
        confirmation_message=rendering.replacement_confirmation_message(plan),
    )

    if verbose:
        rendering.print_replacement_details(plan, applied=applied)
    exit_if_no_targeted_matches(plan.matched_source)


@trackers_app.command(name="replace-passkey")
@error_boundary.catch_internal_errors
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
    try:
        tracker = require_non_blank(tracker, field_name="--tracker")
        new_passkey = require_non_blank(new_passkey, field_name="--new-passkey")
    except InvalidInputError as error:
        error_boundary.fail(str(error), ErrorCategory.INVALID_INPUT)
    enabled = rendering.progress_enabled(
        interactive=rendering.is_interactive_terminal()
    )
    with error_boundary.qbit_error_boundary():
        client = error_boundary.create_qbit_client()
        with rendering.transient_progress(
            "Scanning torrents...", enabled=enabled
        ) as advance:
            plan = plan_tracker_passkey_replacement(
                client=client,
                tracker_template=tracker,
                new_passkey=new_passkey,
                on_progress=advance,
            )

    def _apply() -> None:
        try:
            apply_tracker_passkey_replacement(client, plan)
        except RuntimeError as error:
            error_boundary.fail(str(error))

    applied = run_mutation(
        operation=MutationOperation.TRACKERS_REPLACE_PASSKEY,
        dry_run=dry_run,
        assume_yes=assume_yes,
        matched=plan.matched_source,
        has_changes=bool(plan.changes),
        apply_fn=_apply,
        summary_rows=lambda status: rendering.passkey_summary_rows(
            plan, status=status
        ),
        confirmation_message=rendering.passkey_confirmation_message(plan),
    )

    if verbose:
        rendering.print_passkey_details(plan, applied=applied)
    exit_if_no_targeted_matches(plan.matched_source)


@trackers_app.command(name="export")
@error_boundary.catch_internal_errors
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
    validate_format_support("trackers_export", output_format)
    enabled = rendering.progress_enabled(
        output_format=output_format,
        interactive=rendering.is_interactive_terminal(),
    )
    with error_boundary.qbit_error_boundary():
        client = error_boundary.create_qbit_client()
        with rendering.transient_progress(
            "Scanning torrent trackers...", enabled=enabled
        ) as advance:
            state = export_tracker_state(client=client, on_progress=advance)

    if output_format == OutputFormat.json:
        rendering.print_json_output(state)
        return

    if output_format == OutputFormat.jsonl:
        rendering.print_jsonl_output(state)
        return

    rendering.print_summary(state["summary"], title="Tracker Export")
    typer.echo("Use --format json for the full export payload.")


@trackers_app.command()
@error_boundary.catch_internal_errors
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
    try:
        tracker = require_non_blank(tracker, field_name="--tracker")
    except InvalidInputError as error:
        error_boundary.fail(str(error), ErrorCategory.INVALID_INPUT)
    enabled = rendering.progress_enabled(
        interactive=rendering.is_interactive_terminal()
    )
    with error_boundary.qbit_error_boundary():
        client = error_boundary.create_qbit_client()
        with rendering.transient_progress(
            "Scanning torrents...", enabled=enabled
        ) as advance:
            plan = plan_tracker_removal(
                client=client,
                tracker=tracker,
                match_mode=match.value,
                on_progress=advance,
            )

    def _apply() -> None:
        try:
            apply_tracker_removal(client, plan)
        except RuntimeError as error:
            error_boundary.fail(str(error))

    applied = run_mutation(
        operation=MutationOperation.TRACKERS_REMOVE,
        dry_run=dry_run,
        assume_yes=assume_yes,
        matched=plan.matched_tracker,
        has_changes=bool(plan.changes),
        apply_fn=_apply,
        summary_rows=lambda status: rendering.removal_summary_rows(
            plan, status=status
        ),
        confirmation_message=rendering.removal_confirmation_message(plan),
    )

    if verbose:
        rendering.print_removal_details(plan, applied=applied)
    exit_if_no_targeted_matches(plan.matched_tracker)
