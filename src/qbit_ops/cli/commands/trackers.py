"""Register the `trackers` command group."""

import sys
from dataclasses import replace as replace_dataclass_fields
from enum import StrEnum
from typing import Annotated

import typer

from qbit_core.errors import ErrorCategory, InvalidInputError, require_non_blank
from qbit_core.features.tracker_status import (
    TrackerSortField,
    collect_tracker_status,
    parse_tracker_sort_field,
    sort_tracker_aggregates,
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
from qbit_core.shared.inspection import select_and_inspect
from qbit_core.shared.selection import SelectionRequest
from qbit_core.shared.sorting import SortDirection
from qbit_ops.cli import error_boundary, rendering
from qbit_ops.cli.commands._shared import (
    exit_if_no_targeted_matches,
    resolve_tracker_target,
    run_mutation,
)
from qbit_ops.cli.completion import complete_choices
from qbit_ops.cli.exit_codes import TrackerStatusExitCode
from qbit_ops.cli.rendering import OutputFormat
from qbit_ops.cli.selector_options import (
    REPORT_TRACKER_FILTER_HELP,
    ActiveOption,
    ActiveWithinOption,
    CategoryOption,
    CompletedBeforeOption,
    CompletedOption,
    CompletedWithinOption,
    ErroredOption,
    ExcludeCategoryOption,
    ExcludeNameOption,
    ExcludeSavePathOption,
    ExcludeStateOption,
    ExcludeTagOption,
    InactiveForOption,
    InactiveOption,
    IncompleteOption,
    NameContainsOption,
    NameRegexOption,
    NewerThanOption,
    OlderThanOption,
    PrivateOption,
    ProgressMaxOption,
    ProgressMinOption,
    RatioMaxOption,
    RatioMinOption,
    ReportTrackerOption,
    SavePathOption,
    SeededForOption,
    SizeMaxOption,
    SizeMinOption,
    StalledOption,
    StateOption,
    TagAllOption,
    TagOption,
    TrackerHealthOption,
    UploadedMaxOption,
    UploadedMinOption,
    build_filter_from_options,
)
from qbit_ops.cli.validation import (
    validate_format_support,
    validate_sort_option,
)

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
            help="Source tracker that must already be present: a host "
            "(e.g. 'old.example', 'old.example:8080') matched the same "
            "way '--tracker' filters do, or a full URL with a literal "
            "'{passkey}' placeholder when one host serves more than one "
            "announce path. Never needs the source's own passkey.",
        ),
    ],
    target: Annotated[
        str,
        typer.Option(
            "--target",
            help="Target tracker to add when missing -- always the full "
            "announce URL qBittorrent will use, verbatim. A tracker this "
            "tool has never seen cannot have its passkey position "
            "guessed, so a placeholder is required when there is a "
            "secret: 'https://new.example/announce/{passkey}' (path) or "
            "'https://new.example/announce?passkey={passkey}' (query), "
            "filled from --passkey-stdin or a hidden prompt. A tracker "
            "with no secret in its URL is passed as-is.",
        ),
    ],
    category: CategoryOption = [],  # noqa: B006
    exclude_category: ExcludeCategoryOption = [],  # noqa: B006
    tag: TagOption = [],  # noqa: B006
    tag_all: TagAllOption = [],  # noqa: B006
    exclude_tag: ExcludeTagOption = [],  # noqa: B006
    save_path: SavePathOption = [],  # noqa: B006
    exclude_save_path: ExcludeSavePathOption = [],  # noqa: B006
    name_contains: NameContainsOption = [],  # noqa: B006
    exclude_name: ExcludeNameOption = [],  # noqa: B006
    name_regex: NameRegexOption = None,
    state: StateOption = [],  # noqa: B006
    exclude_state: ExcludeStateOption = [],  # noqa: B006
    completed: CompletedOption = False,
    incomplete: IncompleteOption = False,
    active: ActiveOption = False,
    inactive: InactiveOption = False,
    stalled: StalledOption = False,
    errored: ErroredOption = False,
    private: PrivateOption = None,
    ratio_min: RatioMinOption = None,
    ratio_max: RatioMaxOption = None,
    size_min: SizeMinOption = None,
    size_max: SizeMaxOption = None,
    progress_min: ProgressMinOption = None,
    progress_max: ProgressMaxOption = None,
    uploaded_min: UploadedMinOption = None,
    uploaded_max: UploadedMaxOption = None,
    seeded_for: SeededForOption = None,
    older_than: OlderThanOption = None,
    newer_than: NewerThanOption = None,
    completed_before: CompletedBeforeOption = None,
    completed_within: CompletedWithinOption = None,
    inactive_for: InactiveForOption = None,
    active_within: ActiveWithinOption = None,
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
            help="Comparison mode for whether --target is already "
            "present on a torrent. Does not affect how --source is "
            "identified.",
        ),
    ] = TrackerMatchModeOption.exact,
    source_index: Annotated[
        int | None,
        typer.Option(
            "--source-index",
            help="Pick one candidate (1-based) when --source matches "
            "more than one distinct tracker URL in the selection -- the "
            "refusal message lists them.",
        ),
    ] = None,
    passkey_stdin: Annotated[
        bool,
        typer.Option(
            "--passkey-stdin",
            help="Read the target's passkey from stdin (one line), like "
            "'docker login --password-stdin'. Only meaningful when "
            "--target carries a '{passkey}' placeholder; without it, "
            "the passkey is asked for interactively with the input "
            "hidden. Combined with --no-dry-run, requires --yes: the "
            "confirmation prompt cannot also read stdin.",
        ),
    ] = False,
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
    """Add a target tracker when a source tracker is already present.

    Torrent filters narrow which torrents are scanned at all, so the
    source tracker is only looked for inside that subset. No filter
    means the whole instance, as before.
    """
    try:
        source = require_non_blank(source, field_name="--source")
        target = require_non_blank(target, field_name="--target")
    except InvalidInputError as error:
        error_boundary.fail(str(error), ErrorCategory.INVALID_INPUT)

    target = resolve_tracker_target(
        target,
        passkey_stdin=passkey_stdin,
        dry_run=dry_run,
        assume_yes=assume_yes,
    )

    try:
        filters = build_filter_from_options(
            category=category,
            exclude_category=exclude_category,
            tag=tag,
            tag_all=tag_all,
            exclude_tag=exclude_tag,
            save_path=save_path,
            exclude_save_path=exclude_save_path,
            name_contains=name_contains,
            exclude_name=exclude_name,
            name_regex=name_regex,
            state=state,
            exclude_state=exclude_state,
            completed=completed,
            incomplete=incomplete,
            active=active,
            inactive=inactive,
            stalled=stalled,
            errored=errored,
            private=private,
            ratio_min=ratio_min,
            ratio_max=ratio_max,
            size_min=size_min,
            size_max=size_max,
            progress_min=progress_min,
            progress_max=progress_max,
            uploaded_min=uploaded_min,
            uploaded_max=uploaded_max,
            seeded_for=seeded_for,
            older_than=older_than,
            newer_than=newer_than,
            completed_before=completed_before,
            completed_within=completed_within,
            inactive_for=inactive_for,
            active_within=active_within,
        )
    except ValueError as error:
        error_boundary.fail(str(error))

    enabled = rendering.progress_enabled(
        interactive=rendering.is_interactive_terminal()
    )
    with error_boundary.qbit_error_boundary():
        client = error_boundary.create_qbit_client()
        with rendering.transient_progress(
            "Scanning torrents...", enabled=enabled
        ) as advance:
            inspection = select_and_inspect(
                client,
                SelectionRequest(filters=filters),
                on_progress=advance,
            )
            try:
                plan = plan_tracker_addition(
                    inspection,
                    source_tracker=source,
                    target_tracker=target,
                    match_mode=match.value,
                    source_index=source_index,
                )
            except RuntimeError as error:
                error_boundary.fail(str(error))

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
    category: CategoryOption = [],  # noqa: B006 - Typer requires a literal default to detect list options
    state: StateOption = [],  # noqa: B006
    tracker: ReportTrackerOption = None,
    tracker_health: TrackerHealthOption = [],  # noqa: B006
    completed: CompletedOption = False,
    incomplete: IncompleteOption = False,
    active: ActiveOption = False,
    inactive: InactiveOption = False,
    stalled: StalledOption = False,
    errored: ErroredOption = False,
    exclude_category: ExcludeCategoryOption = [],  # noqa: B006
    tag: TagOption = [],  # noqa: B006
    tag_all: TagAllOption = [],  # noqa: B006
    exclude_tag: ExcludeTagOption = [],  # noqa: B006
    save_path: SavePathOption = [],  # noqa: B006
    exclude_save_path: ExcludeSavePathOption = [],  # noqa: B006
    name_contains: NameContainsOption = [],  # noqa: B006
    exclude_name: ExcludeNameOption = [],  # noqa: B006
    name_regex: NameRegexOption = None,
    exclude_state: ExcludeStateOption = [],  # noqa: B006
    private: PrivateOption = None,
    ratio_min: RatioMinOption = None,
    ratio_max: RatioMaxOption = None,
    size_min: SizeMinOption = None,
    size_max: SizeMaxOption = None,
    progress_min: ProgressMinOption = None,
    progress_max: ProgressMaxOption = None,
    uploaded_min: UploadedMinOption = None,
    uploaded_max: UploadedMaxOption = None,
    seeded_for: SeededForOption = None,
    older_than: OlderThanOption = None,
    newer_than: NewerThanOption = None,
    completed_before: CompletedBeforeOption = None,
    completed_within: CompletedWithinOption = None,
    inactive_for: InactiveForOption = None,
    active_within: ActiveWithinOption = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Show all nine columns instead of the five-column default.",
        ),
    ] = False,
    sort: Annotated[
        str | None,
        typer.Option(
            "--sort",
            help=(
                "Sort rendered rows by this field. Refused with a "
                "machine --format -- sort at the caller there instead."
            ),
            autocompletion=complete_choices(
                field.value for field in TrackerSortField
            ),
        ),
    ] = None,
    desc: Annotated[
        bool,
        typer.Option("--desc", help="Reverse --sort's direction."),
    ] = False,
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            help="Output format.",
        ),
    ] = OutputFormat.table,
) -> None:
    """List tracker identities in use, with their volume.

    Tracker identity is always `host[:port]`, never a full announce URL.
    Accepts the same filters as `trackers status`, with the same
    semantics -- `--tracker` restricts the report after aggregation
    rather than the selection.

    A torrent announcing to several trackers counts *entirely* in each
    aggregate, so summing a column over every tracker exceeds the
    library total; `--verbose`'s `Excl` column counts the torrents this
    tracker is the only identity of.

    Table output is a fixed five-column set (Tracker, Torrents, Size,
    Uploaded, Ratio) that never adapts to the detected terminal width;
    `--verbose` renders all nine columns instead. `--format json`,
    `jsonl` and `csv` always carry every measure regardless of
    `--verbose`.

    Without `--sort`, the busiest tracker (most torrents) renders
    first -- unlike `torrents list`, this command had a chosen order
    before (hostname), so this is a replacement, not a new default.

    Always exits `0` on tracker health: this is an inventory, not a
    diagnostic, so a script asking "what trackers exist" is never broken
    by a tracker degraded elsewhere. Use `trackers status` for health.
    """
    validate_format_support("trackers_list", output_format)
    resolved_sort = validate_sort_option(
        sort, desc, output_format, parse_field=parse_tracker_sort_field
    )
    try:
        filters = build_filter_from_options(
            category=category,
            exclude_category=exclude_category,
            tag=tag,
            tag_all=tag_all,
            exclude_tag=exclude_tag,
            save_path=save_path,
            exclude_save_path=exclude_save_path,
            name_contains=name_contains,
            exclude_name=exclude_name,
            name_regex=name_regex,
            state=state,
            exclude_state=exclude_state,
            tracker=[] if tracker is None else [tracker],
            tracker_health=tracker_health,
            completed=completed,
            incomplete=incomplete,
            active=active,
            inactive=inactive,
            stalled=stalled,
            errored=errored,
            private=private,
            ratio_min=ratio_min,
            ratio_max=ratio_max,
            size_min=size_min,
            size_max=size_max,
            progress_min=progress_min,
            progress_max=progress_max,
            uploaded_min=uploaded_min,
            uploaded_max=uploaded_max,
            seeded_for=seeded_for,
            older_than=older_than,
            newer_than=newer_than,
            completed_before=completed_before,
            completed_within=completed_within,
            inactive_for=inactive_for,
            active_within=active_within,
        )
    except ValueError as error:
        error_boundary.fail(str(error))

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

    # Busiest tracker first by default -- a replacement for the
    # previous hostname-ascending order, not a new one; see
    # `.agents/specs/list-sort.md`. `collect_tracker_status`'s own
    # order stays hostname-ascending: `trackers status` renders that
    # same report unsorted, and must not inherit this command's
    # default.
    sort_field, sort_direction = resolved_sort or (
        TrackerSortField.TORRENTS,
        SortDirection.DESCENDING,
    )
    report = replace_dataclass_fields(
        report,
        trackers=sort_tracker_aggregates(
            report.trackers, sort_field, sort_direction
        ),
    )

    rendering.render_tracker_inventory(report, output_format, verbose=verbose)


@trackers_app.command(name="status")
@error_boundary.catch_internal_errors
def trackers_status_command(
    category: CategoryOption = [],  # noqa: B006 - Typer requires a literal default to detect list options
    state: StateOption = [],  # noqa: B006
    tracker: ReportTrackerOption = None,
    completed: CompletedOption = False,
    incomplete: IncompleteOption = False,
    active: ActiveOption = False,
    inactive: InactiveOption = False,
    stalled: StalledOption = False,
    errored: ErroredOption = False,
    exclude_category: ExcludeCategoryOption = [],  # noqa: B006
    tag: TagOption = [],  # noqa: B006
    tag_all: TagAllOption = [],  # noqa: B006
    exclude_tag: ExcludeTagOption = [],  # noqa: B006
    save_path: SavePathOption = [],  # noqa: B006
    exclude_save_path: ExcludeSavePathOption = [],  # noqa: B006
    name_contains: NameContainsOption = [],  # noqa: B006
    exclude_name: ExcludeNameOption = [],  # noqa: B006
    name_regex: NameRegexOption = None,
    exclude_state: ExcludeStateOption = [],  # noqa: B006
    private: PrivateOption = None,
    ratio_min: RatioMinOption = None,
    ratio_max: RatioMaxOption = None,
    size_min: SizeMinOption = None,
    size_max: SizeMaxOption = None,
    progress_min: ProgressMinOption = None,
    progress_max: ProgressMaxOption = None,
    uploaded_min: UploadedMinOption = None,
    uploaded_max: UploadedMaxOption = None,
    seeded_for: SeededForOption = None,
    older_than: OlderThanOption = None,
    newer_than: NewerThanOption = None,
    completed_before: CompletedBeforeOption = None,
    completed_within: CompletedWithinOption = None,
    inactive_for: InactiveForOption = None,
    active_within: ActiveWithinOption = None,
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
        filters = build_filter_from_options(
            category=category,
            exclude_category=exclude_category,
            tag=tag,
            tag_all=tag_all,
            exclude_tag=exclude_tag,
            save_path=save_path,
            exclude_save_path=exclude_save_path,
            name_contains=name_contains,
            exclude_name=exclude_name,
            name_regex=name_regex,
            state=state,
            exclude_state=exclude_state,
            tracker=[] if tracker is None else [tracker],
            completed=completed,
            incomplete=incomplete,
            active=active,
            inactive=inactive,
            stalled=stalled,
            errored=errored,
            private=private,
            ratio_min=ratio_min,
            ratio_max=ratio_max,
            size_min=size_min,
            size_max=size_max,
            progress_min=progress_min,
            progress_max=progress_max,
            uploaded_min=uploaded_min,
            uploaded_max=uploaded_max,
            seeded_for=seeded_for,
            older_than=older_than,
            newer_than=newer_than,
            completed_before=completed_before,
            completed_within=completed_within,
            inactive_for=inactive_for,
            active_within=active_within,
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
            help=REPORT_TRACKER_FILTER_HELP,
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
            help="Source tracker to replace: a host (e.g. 'old.example', "
            "'old.example:8080') matched the same way '--tracker' filters "
            "do, or a full URL with a literal '{passkey}' placeholder "
            "when one host serves more than one announce path. Never "
            "needs the source's own passkey.",
        ),
    ],
    target: Annotated[
        str,
        typer.Option(
            "--target",
            help="Target tracker to keep after replacement -- always the "
            "full announce URL qBittorrent will use, verbatim. A tracker "
            "this tool has never seen cannot have its passkey position "
            "guessed, so a placeholder is required when there is a "
            "secret: 'https://new.example/announce/{passkey}' (path) or "
            "'https://new.example/announce?passkey={passkey}' (query), "
            "filled from --passkey-stdin or a hidden prompt. A tracker "
            "with no secret in its URL is passed as-is.",
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
            help="Comparison mode for whether --target is already "
            "present on a torrent. Does not affect how --source is "
            "identified.",
        ),
    ] = TrackerMatchModeOption.exact,
    source_index: Annotated[
        int | None,
        typer.Option(
            "--source-index",
            help="Pick one candidate (1-based) when --source matches "
            "more than one distinct tracker URL in the selection -- the "
            "refusal message lists them.",
        ),
    ] = None,
    passkey_stdin: Annotated[
        bool,
        typer.Option(
            "--passkey-stdin",
            help="Read the target's passkey from stdin (one line), like "
            "'docker login --password-stdin'. Only meaningful when "
            "--target carries a '{passkey}' placeholder; without it, "
            "the passkey is asked for interactively with the input "
            "hidden. Combined with --no-dry-run, requires --yes: the "
            "confirmation prompt cannot also read stdin.",
        ),
    ] = False,
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
    category: CategoryOption = [],  # noqa: B006
    exclude_category: ExcludeCategoryOption = [],  # noqa: B006
    tag: TagOption = [],  # noqa: B006
    tag_all: TagAllOption = [],  # noqa: B006
    exclude_tag: ExcludeTagOption = [],  # noqa: B006
    save_path: SavePathOption = [],  # noqa: B006
    exclude_save_path: ExcludeSavePathOption = [],  # noqa: B006
    name_contains: NameContainsOption = [],  # noqa: B006
    exclude_name: ExcludeNameOption = [],  # noqa: B006
    name_regex: NameRegexOption = None,
    state: StateOption = [],  # noqa: B006
    exclude_state: ExcludeStateOption = [],  # noqa: B006
    completed: CompletedOption = False,
    incomplete: IncompleteOption = False,
    active: ActiveOption = False,
    inactive: InactiveOption = False,
    stalled: StalledOption = False,
    errored: ErroredOption = False,
    private: PrivateOption = None,
    ratio_min: RatioMinOption = None,
    ratio_max: RatioMaxOption = None,
    size_min: SizeMinOption = None,
    size_max: SizeMaxOption = None,
    progress_min: ProgressMinOption = None,
    progress_max: ProgressMaxOption = None,
    uploaded_min: UploadedMinOption = None,
    uploaded_max: UploadedMaxOption = None,
    seeded_for: SeededForOption = None,
    older_than: OlderThanOption = None,
    newer_than: NewerThanOption = None,
    completed_before: CompletedBeforeOption = None,
    completed_within: CompletedWithinOption = None,
    inactive_for: InactiveForOption = None,
    active_within: ActiveWithinOption = None,
) -> None:
    """Replace a tracker on every torrent using it."""
    try:
        source = require_non_blank(source, field_name="--source")
        target = require_non_blank(target, field_name="--target")
    except InvalidInputError as error:
        error_boundary.fail(str(error), ErrorCategory.INVALID_INPUT)

    target = resolve_tracker_target(
        target,
        passkey_stdin=passkey_stdin,
        dry_run=dry_run,
        assume_yes=assume_yes,
    )

    try:
        filters = build_filter_from_options(
            category=category,
            exclude_category=exclude_category,
            tag=tag,
            tag_all=tag_all,
            exclude_tag=exclude_tag,
            save_path=save_path,
            exclude_save_path=exclude_save_path,
            name_contains=name_contains,
            exclude_name=exclude_name,
            name_regex=name_regex,
            state=state,
            exclude_state=exclude_state,
            completed=completed,
            incomplete=incomplete,
            active=active,
            inactive=inactive,
            stalled=stalled,
            errored=errored,
            private=private,
            ratio_min=ratio_min,
            ratio_max=ratio_max,
            size_min=size_min,
            size_max=size_max,
            progress_min=progress_min,
            progress_max=progress_max,
            uploaded_min=uploaded_min,
            uploaded_max=uploaded_max,
            seeded_for=seeded_for,
            older_than=older_than,
            newer_than=newer_than,
            completed_before=completed_before,
            completed_within=completed_within,
            inactive_for=inactive_for,
            active_within=active_within,
        )
    except ValueError as error:
        error_boundary.fail(str(error))

    enabled = rendering.progress_enabled(
        interactive=rendering.is_interactive_terminal()
    )
    with error_boundary.qbit_error_boundary():
        client = error_boundary.create_qbit_client()
        with rendering.transient_progress(
            "Scanning torrents...", enabled=enabled
        ) as advance:
            inspection = select_and_inspect(
                client,
                SelectionRequest(filters=filters),
                on_progress=advance,
            )
            try:
                plan = plan_tracker_replacement(
                    inspection,
                    source_tracker=source,
                    target_tracker=target,
                    match_mode=match.value,
                    source_index=source_index,
                )
            except RuntimeError as error:
                error_boundary.fail(str(error))

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
    new_passkey_stdin: Annotated[
        bool,
        typer.Option(
            "--new-passkey-stdin",
            help="Read the new passkey from stdin (one line), like "
            "'docker login --password-stdin'. Without it, the passkey "
            "is asked for interactively with the input hidden. "
            "Combined with --no-dry-run, requires --yes: the "
            "confirmation prompt cannot also read stdin.",
        ),
    ] = False,
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
    category: CategoryOption = [],  # noqa: B006
    exclude_category: ExcludeCategoryOption = [],  # noqa: B006
    tag: TagOption = [],  # noqa: B006
    tag_all: TagAllOption = [],  # noqa: B006
    exclude_tag: ExcludeTagOption = [],  # noqa: B006
    save_path: SavePathOption = [],  # noqa: B006
    exclude_save_path: ExcludeSavePathOption = [],  # noqa: B006
    name_contains: NameContainsOption = [],  # noqa: B006
    exclude_name: ExcludeNameOption = [],  # noqa: B006
    name_regex: NameRegexOption = None,
    state: StateOption = [],  # noqa: B006
    exclude_state: ExcludeStateOption = [],  # noqa: B006
    completed: CompletedOption = False,
    incomplete: IncompleteOption = False,
    active: ActiveOption = False,
    inactive: InactiveOption = False,
    stalled: StalledOption = False,
    errored: ErroredOption = False,
    private: PrivateOption = None,
    ratio_min: RatioMinOption = None,
    ratio_max: RatioMaxOption = None,
    size_min: SizeMinOption = None,
    size_max: SizeMaxOption = None,
    progress_min: ProgressMinOption = None,
    progress_max: ProgressMaxOption = None,
    uploaded_min: UploadedMinOption = None,
    uploaded_max: UploadedMaxOption = None,
    seeded_for: SeededForOption = None,
    older_than: OlderThanOption = None,
    newer_than: NewerThanOption = None,
    completed_before: CompletedBeforeOption = None,
    completed_within: CompletedWithinOption = None,
    inactive_for: InactiveForOption = None,
    active_within: ActiveWithinOption = None,
) -> None:
    """Replace a tracker's passkey on every torrent using that tracker.

    The new passkey never appears on the command line: it is piped via
    `--new-passkey-stdin` or typed at a hidden prompt.
    """
    try:
        tracker = require_non_blank(tracker, field_name="--tracker")
    except InvalidInputError as error:
        error_boundary.fail(str(error), ErrorCategory.INVALID_INPUT)

    if new_passkey_stdin:
        if not assume_yes and not dry_run:
            # The confirmation prompt reads from the same stdin the
            # passkey already consumed -- refuse before either read.
            error_boundary.fail(
                "--new-passkey-stdin with --no-dry-run also needs --yes: "
                "the confirmation prompt cannot read stdin a second time.",
                ErrorCategory.INVALID_INPUT,
            )
        new_passkey = sys.stdin.readline()
    elif not rendering.is_interactive_terminal():
        error_boundary.fail(
            "No terminal to prompt for the new passkey. Pipe it instead "
            "with --new-passkey-stdin.",
            ErrorCategory.INVALID_INPUT,
        )
    else:
        new_passkey = rendering.prompt_secret("New passkey")

    try:
        new_passkey = require_non_blank(
            new_passkey, field_name="the new passkey"
        )
    except InvalidInputError as error:
        error_boundary.fail(str(error), ErrorCategory.INVALID_INPUT)
    try:
        filters = build_filter_from_options(
            category=category,
            exclude_category=exclude_category,
            tag=tag,
            tag_all=tag_all,
            exclude_tag=exclude_tag,
            save_path=save_path,
            exclude_save_path=exclude_save_path,
            name_contains=name_contains,
            exclude_name=exclude_name,
            name_regex=name_regex,
            state=state,
            exclude_state=exclude_state,
            completed=completed,
            incomplete=incomplete,
            active=active,
            inactive=inactive,
            stalled=stalled,
            errored=errored,
            private=private,
            ratio_min=ratio_min,
            ratio_max=ratio_max,
            size_min=size_min,
            size_max=size_max,
            progress_min=progress_min,
            progress_max=progress_max,
            uploaded_min=uploaded_min,
            uploaded_max=uploaded_max,
            seeded_for=seeded_for,
            older_than=older_than,
            newer_than=newer_than,
            completed_before=completed_before,
            completed_within=completed_within,
            inactive_for=inactive_for,
            active_within=active_within,
        )
    except ValueError as error:
        error_boundary.fail(str(error))

    enabled = rendering.progress_enabled(
        interactive=rendering.is_interactive_terminal()
    )
    with error_boundary.qbit_error_boundary():
        client = error_boundary.create_qbit_client()
        with rendering.transient_progress(
            "Scanning torrents...", enabled=enabled
        ) as advance:
            inspection = select_and_inspect(
                client,
                SelectionRequest(filters=filters),
                on_progress=advance,
            )
            plan = plan_tracker_passkey_replacement(
                inspection,
                tracker_template=tracker,
                new_passkey=new_passkey,
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
    docs/COMMANDS.md, "Restoring from a `backup export`").
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
            help="Tracker to remove from every torrent using it: a host "
            "(e.g. 'old.example', 'old.example:8080') matched the same "
            "way '--tracker' filters do, or a full URL with a literal "
            "'{passkey}' placeholder when one host serves more than one "
            "announce path. Never needs a passkey.",
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
    category: CategoryOption = [],  # noqa: B006
    exclude_category: ExcludeCategoryOption = [],  # noqa: B006
    tag: TagOption = [],  # noqa: B006
    tag_all: TagAllOption = [],  # noqa: B006
    exclude_tag: ExcludeTagOption = [],  # noqa: B006
    save_path: SavePathOption = [],  # noqa: B006
    exclude_save_path: ExcludeSavePathOption = [],  # noqa: B006
    name_contains: NameContainsOption = [],  # noqa: B006
    exclude_name: ExcludeNameOption = [],  # noqa: B006
    name_regex: NameRegexOption = None,
    state: StateOption = [],  # noqa: B006
    exclude_state: ExcludeStateOption = [],  # noqa: B006
    completed: CompletedOption = False,
    incomplete: IncompleteOption = False,
    active: ActiveOption = False,
    inactive: InactiveOption = False,
    stalled: StalledOption = False,
    errored: ErroredOption = False,
    private: PrivateOption = None,
    ratio_min: RatioMinOption = None,
    ratio_max: RatioMaxOption = None,
    size_min: SizeMinOption = None,
    size_max: SizeMaxOption = None,
    progress_min: ProgressMinOption = None,
    progress_max: ProgressMaxOption = None,
    uploaded_min: UploadedMinOption = None,
    uploaded_max: UploadedMaxOption = None,
    seeded_for: SeededForOption = None,
    older_than: OlderThanOption = None,
    newer_than: NewerThanOption = None,
    completed_before: CompletedBeforeOption = None,
    completed_within: CompletedWithinOption = None,
    inactive_for: InactiveForOption = None,
    active_within: ActiveWithinOption = None,
) -> None:
    """Remove a tracker from every torrent using it."""
    try:
        tracker = require_non_blank(tracker, field_name="--tracker")
    except InvalidInputError as error:
        error_boundary.fail(str(error), ErrorCategory.INVALID_INPUT)
    try:
        filters = build_filter_from_options(
            category=category,
            exclude_category=exclude_category,
            tag=tag,
            tag_all=tag_all,
            exclude_tag=exclude_tag,
            save_path=save_path,
            exclude_save_path=exclude_save_path,
            name_contains=name_contains,
            exclude_name=exclude_name,
            name_regex=name_regex,
            state=state,
            exclude_state=exclude_state,
            completed=completed,
            incomplete=incomplete,
            active=active,
            inactive=inactive,
            stalled=stalled,
            errored=errored,
            private=private,
            ratio_min=ratio_min,
            ratio_max=ratio_max,
            size_min=size_min,
            size_max=size_max,
            progress_min=progress_min,
            progress_max=progress_max,
            uploaded_min=uploaded_min,
            uploaded_max=uploaded_max,
            seeded_for=seeded_for,
            older_than=older_than,
            newer_than=newer_than,
            completed_before=completed_before,
            completed_within=completed_within,
            inactive_for=inactive_for,
            active_within=active_within,
        )
    except ValueError as error:
        error_boundary.fail(str(error))

    enabled = rendering.progress_enabled(
        interactive=rendering.is_interactive_terminal()
    )
    with error_boundary.qbit_error_boundary():
        client = error_boundary.create_qbit_client()
        with rendering.transient_progress(
            "Scanning torrents...", enabled=enabled
        ) as advance:
            inspection = select_and_inspect(
                client,
                SelectionRequest(filters=filters),
                on_progress=advance,
            )
            plan = plan_tracker_removal(
                inspection,
                tracker=tracker,
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
