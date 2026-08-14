"""Register the `torrents` command group."""

from pathlib import Path
from typing import Annotated

import typer

from qbit_core.features.stats import (
    collect_torrent_stats,
    validate_stats_request,
)
from qbit_core.features.torrent_import import (
    TorrentImportError,
    TorrentImportResult,
    apply_torrent_import,
    plan_torrent_import,
)
from qbit_core.features.torrents import (
    TorrentBulkAction,
    apply_bulk_torrent_action,
    inspect_filtered_torrents,
    inspect_torrent,
    list_category_usage,
    plan_bulk_torrent_action,
    search_torrents_by_name,
    select_torrents,
)
from qbit_core.shared.execution import MutationOperation
from qbit_core.shared.selection import (
    AmbiguousTorrentHashError,
    SelectionRequest,
    describe_torrent_filter,
    validate_selection_request,
)
from qbit_ops.cli import error_boundary, rendering
from qbit_ops.cli.commands._shared import (
    exit_if_no_targeted_matches,
    run_mutation,
)
from qbit_ops.cli.exit_codes import ExitCode
from qbit_ops.cli.rendering import OutputFormat
from qbit_ops.cli.selector_options import (
    ActiveOption,
    ActiveWithinOption,
    CategoryOption,
    CompletedBeforeOption,
    CompletedOption,
    CompletedWithinOption,
    DeleteAllOption,
    DeleteHashOption,
    DryRunOption,
    ErroredOption,
    ExcludeCategoryOption,
    ExcludeNameOption,
    ExcludeSavePathOption,
    ExcludeStateOption,
    ExcludeTagOption,
    ExcludeTrackerOption,
    InactiveForOption,
    InactiveOption,
    IncompleteOption,
    NameContainsOption,
    NameRegexOption,
    NewerThanOption,
    NoTrackerOption,
    OlderThanOption,
    PauseAllOption,
    PauseHashOption,
    PrivateOption,
    ProgressMaxOption,
    ProgressMinOption,
    RatioMaxOption,
    RatioMinOption,
    ReannounceAllOption,
    ReannounceHashOption,
    ResumeAllOption,
    ResumeHashOption,
    SavePathOption,
    SeededForOption,
    SizeMaxOption,
    SizeMinOption,
    StalledOption,
    StartAllOption,
    StartCompletedOption,
    StartHashOption,
    StateOption,
    StatsAllOption,
    StatsHashOption,
    TagAllOption,
    TagOption,
    TrackerOption,
    UploadedMaxOption,
    UploadedMinOption,
    VerboseOption,
    build_filter_from_options,
)
from qbit_ops.cli.validation import (
    validate_format_support,
    validate_hash_option,
)

torrents_app = typer.Typer(help="Inspect qBittorrent torrents.")


def _build_selection_request(
    *,
    torrent_hash: str | None = None,
    category: list[str],
    state: list[str],
    tracker: list[str],
    completed: bool,
    incomplete: bool,
    active: bool,
    inactive: bool,
    stalled: bool,
    errored: bool,
    exclude_category: list[str],
    tag: list[str],
    tag_all: list[str],
    exclude_tag: list[str],
    save_path: list[str],
    exclude_save_path: list[str],
    name_contains: list[str],
    exclude_name: list[str],
    name_regex: str | None,
    exclude_state: list[str],
    exclude_tracker: list[str],
    no_tracker: bool,
    private: bool | None,
    ratio_min: str | None,
    ratio_max: str | None,
    size_min: str | None,
    size_max: str | None,
    progress_min: str | None,
    progress_max: str | None,
    uploaded_min: str | None,
    uploaded_max: str | None,
    seeded_for: str | None,
    older_than: str | None,
    newer_than: str | None,
    completed_before: str | None,
    completed_within: str | None,
    inactive_for: str | None,
    active_within: str | None,
    select_all: bool = False,
) -> SelectionRequest:
    """Fold raw CLI selector inputs into one validated `SelectionRequest`.

    Every rejection happens here, before any qBittorrent call: a blank
    `--hash`, a contradictory filter combination, and an unsafe
    selector (`--hash` with `--all`, or no selector at all).
    """
    validated_hash = validate_hash_option(torrent_hash)
    try:
        filters = build_filter_from_options(
            category=category,
            state=state,
            tracker=tracker,
            completed=completed,
            incomplete=incomplete,
            active=active,
            inactive=inactive,
            stalled=stalled,
            errored=errored,
            exclude_category=exclude_category,
            tag=tag,
            tag_all=tag_all,
            exclude_tag=exclude_tag,
            save_path=save_path,
            exclude_save_path=exclude_save_path,
            name_contains=name_contains,
            exclude_name=exclude_name,
            name_regex=name_regex,
            exclude_state=exclude_state,
            exclude_tracker=exclude_tracker,
            no_tracker=no_tracker,
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

    return SelectionRequest(
        torrent_hash=validated_hash, select_all=select_all, filters=filters
    )


def _run_bulk_torrent_action(
    *,
    operation: MutationOperation,
    action: TorrentBulkAction,
    request: SelectionRequest,
    dry_run: bool,
    verbose: bool,
    assume_yes: bool = False,
    delete_files: bool = False,
) -> None:
    try:
        validate_selection_request(request)
    except ValueError as error:
        error_boundary.fail(str(error))

    enabled = rendering.progress_enabled(
        interactive=rendering.is_interactive_terminal()
    )
    try:
        with error_boundary.qbit_error_boundary():
            client = error_boundary.create_qbit_client()
            with rendering.transient_progress(
                f"Scanning torrents to {action}...", enabled=enabled
            ) as advance:
                plan = plan_bulk_torrent_action(
                    client=client,
                    action=action,
                    torrent_hash=request.torrent_hash,
                    select_all=request.select_all,
                    filters=request.filters,
                    delete_files=delete_files,
                    on_progress=advance,
                )
    except AmbiguousTorrentHashError as error:
        error_boundary.fail_ambiguous_hash(error)
    except ValueError as error:
        error_boundary.fail(str(error))

    def _apply() -> None:
        try:
            apply_bulk_torrent_action(client, plan)
        except RuntimeError as error:
            error_boundary.fail(str(error))

    applied = run_mutation(
        operation=operation,
        dry_run=dry_run,
        assume_yes=assume_yes,
        matched=plan.matched,
        has_changes=bool(plan.changes),
        apply_fn=_apply,
        summary_rows=lambda status: rendering.bulk_torrent_summary_rows(
            plan, status=status
        ),
        confirmation_message=(
            rendering.bulk_delete_confirmation_message(plan)
            if action == "delete"
            else None
        ),
    )

    if verbose:
        rendering.print_bulk_torrent_details(plan, applied=applied)
    exit_if_no_targeted_matches(plan.matched)


@torrents_app.command(name="list")
@error_boundary.catch_internal_errors
def list_qbit_torrents(
    category: CategoryOption = [],  # noqa: B006 - Typer requires a literal default to detect list options
    state: StateOption = [],  # noqa: B006
    tracker: TrackerOption = [],  # noqa: B006
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
    exclude_tracker: ExcludeTrackerOption = [],  # noqa: B006
    no_tracker: NoTrackerOption = False,
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
    validate_format_support("torrents_list", output_format)
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
            exclude_tracker=exclude_tracker,
            no_tracker=no_tracker,
            tracker=tracker,
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
    if filters.requires_inspection:
        progress_cm = rendering.transient_progress(
            "Scanning torrent trackers...", enabled=enabled
        )
    else:
        progress_cm = rendering.transient_spinner(
            "Loading torrents...", enabled=enabled
        )

    with error_boundary.qbit_error_boundary():
        client = error_boundary.create_qbit_client()
        with progress_cm as advance:
            selection, tracker_counts = select_torrents(
                client, filters, on_progress=advance
            )

    rendering.print_torrent_selection(
        selection, output_format, tracker_counts=tracker_counts
    )
    if not filters.is_empty:
        exit_if_no_targeted_matches(len(selection.matched))


@torrents_app.command(name="stats")
@error_boundary.catch_internal_errors
def show_torrent_stats(
    torrent_hash: StatsHashOption = None,
    category: CategoryOption = [],  # noqa: B006 - Typer requires a literal default to detect list options
    state: StateOption = [],  # noqa: B006
    tracker: TrackerOption = [],  # noqa: B006
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
    exclude_tracker: ExcludeTrackerOption = [],  # noqa: B006
    no_tracker: NoTrackerOption = False,
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
    select_all: StatsAllOption = False,
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            help="Output format.",
        ),
    ] = OutputFormat.table,
) -> None:
    """Report volume, transfer and time aggregates over a torrent selection.

    Read-only, and accepts every filter `torrents list` does, with the
    same semantics. Without any selector -- no filter, no `--hash`, no
    `--all` -- the report also carries qBittorrent's own all-time
    counters, which include torrents since deleted and cannot be
    filtered. Any selector drops that block rather than inviting a
    comparison between a filtered total and a global one. An empty
    selection is an answer, not an error: it exits 0.
    """
    validate_format_support("torrents_stats", output_format)
    request = _build_selection_request(
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
        exclude_category=exclude_category,
        tag=tag,
        tag_all=tag_all,
        exclude_tag=exclude_tag,
        save_path=save_path,
        exclude_save_path=exclude_save_path,
        name_contains=name_contains,
        exclude_name=exclude_name,
        name_regex=name_regex,
        exclude_state=exclude_state,
        exclude_tracker=exclude_tracker,
        no_tracker=no_tracker,
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
        select_all=select_all,
    )

    # Validated here, outside the boundary below, so a `ValueError` from
    # a defect can never be reported as bad operator input -- the same
    # split the bulk mutations use.
    try:
        validate_stats_request(request)
    except ValueError as error:
        error_boundary.fail(str(error))

    enabled = rendering.progress_enabled(
        output_format=output_format,
        interactive=rendering.is_interactive_terminal(),
    )
    if request.requires_inspection:
        progress_cm = rendering.transient_progress(
            "Scanning torrent trackers...", enabled=enabled
        )
    else:
        progress_cm = rendering.transient_spinner(
            "Loading torrents...", enabled=enabled
        )

    try:
        with error_boundary.qbit_error_boundary():
            client = error_boundary.create_qbit_client()
            with progress_cm as advance:
                report = collect_torrent_stats(
                    client, request, on_progress=advance
                )
    except AmbiguousTorrentHashError as error:
        error_boundary.fail_ambiguous_hash(error)

    rendering.render_torrent_stats(report, output_format)


@torrents_app.command(name="categories")
@error_boundary.catch_internal_errors
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
    validate_format_support("torrents_categories", output_format)
    enabled = rendering.progress_enabled(
        output_format=output_format,
        interactive=rendering.is_interactive_terminal(),
    )
    with error_boundary.qbit_error_boundary():
        client = error_boundary.create_qbit_client()
        with rendering.transient_spinner(
            "Loading torrents...", enabled=enabled
        ):
            category_usage = list_category_usage(client)

    payload = {
        "summary": {"categories": len(category_usage)},
        "categories": category_usage,
    }

    if output_format == OutputFormat.json:
        rendering.print_json_output(payload)
        return

    if output_format == OutputFormat.jsonl:
        rendering.print_jsonl_output(payload)
        return

    if output_format == OutputFormat.csv:
        rendering.print_csv_rows(
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

    rendering.print_table(
        "Categories",
        ["Category", "Torrents"],
        [
            [category_name, str(torrent_count)]
            for category_name, torrent_count in category_usage.items()
        ],
    )
    rendering.print_summary({"categories": len(category_usage)})


@torrents_app.command(name="inspect")
@error_boundary.catch_internal_errors
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
    tracker: TrackerOption = [],  # noqa: B006
    exclude_tracker: ExcludeTrackerOption = [],  # noqa: B006
    no_tracker: NoTrackerOption = False,
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
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            help="Output format.",
        ),
    ] = OutputFormat.table,
) -> None:
    """Inspect torrents by hash, by name search, or by filter.

    Exactly one selector: `--hash` for one torrent, `--name` for the
    read-only fuzzy search, or any combination of filters for every
    matching torrent. Filtered mode reports the same secret-free
    per-torrent detail as `--hash`.
    """
    validate_format_support("torrents_inspect", output_format)
    torrent_hash = validate_hash_option(torrent_hash)

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
            tracker=tracker,
            exclude_tracker=exclude_tracker,
            no_tracker=no_tracker,
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

    selectors = [
        torrent_hash is not None,
        name is not None,
        not filters.is_empty,
    ]
    if sum(selectors) != 1:
        error_boundary.fail(
            "Provide exactly one of --hash, --name, or one or more filters."
        )

    if not filters.is_empty:
        enabled = rendering.progress_enabled(
            output_format=output_format,
            interactive=rendering.is_interactive_terminal(),
        )
        with error_boundary.qbit_error_boundary():
            client = error_boundary.create_qbit_client()
            with rendering.transient_progress(
                "Inspecting torrents...", enabled=enabled
            ) as advance:
                report = inspect_filtered_torrents(
                    client, filters, on_progress=advance
                )
        rendering.print_filtered_torrent_inspection(
            report,
            output_format,
            description=describe_torrent_filter(filters),
        )
        exit_if_no_targeted_matches(report["summary"]["matched"])
        return

    enabled = rendering.progress_enabled(
        output_format=output_format,
        interactive=rendering.is_interactive_terminal(),
    )
    try:
        with error_boundary.qbit_error_boundary():
            client = error_boundary.create_qbit_client()
            if name is not None:
                with rendering.transient_spinner(
                    "Loading torrents...", enabled=enabled
                ):
                    report = search_torrents_by_name(client, name, limit=limit)
                rendering.print_torrent_name_search(report, output_format)
                exit_if_no_targeted_matches(report["summary"]["matched"])
                return

            with rendering.transient_spinner(
                "Loading torrent...", enabled=enabled
            ):
                report = inspect_torrent(client, torrent_hash or "")
    except AmbiguousTorrentHashError as error:
        error_boundary.fail_ambiguous_hash(error)

    if report is None:
        if output_format == OutputFormat.json:
            rendering.print_json_output({"torrent": None, "hash": torrent_hash})
        elif output_format == OutputFormat.jsonl:
            rendering.print_jsonl_output(
                {"torrent": None, "hash": torrent_hash}
            )
        else:
            typer.echo(f"No torrent found for hash prefix: {torrent_hash}")

        exit_if_no_targeted_matches(0)
        return

    if output_format == OutputFormat.json:
        rendering.print_json_output({"torrent": report})
        return

    if output_format == OutputFormat.jsonl:
        rendering.print_jsonl_output({"torrent": report})
        return

    rendering.print_torrent_details(report)


@torrents_app.command()
@error_boundary.catch_internal_errors
def pause(
    torrent_hash: PauseHashOption = None,
    category: CategoryOption = [],  # noqa: B006 - Typer requires a literal default to detect list options
    state: StateOption = [],  # noqa: B006
    tracker: TrackerOption = [],  # noqa: B006
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
    exclude_tracker: ExcludeTrackerOption = [],  # noqa: B006
    no_tracker: NoTrackerOption = False,
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
    select_all: PauseAllOption = False,
    dry_run: DryRunOption = True,
    verbose: VerboseOption = False,
) -> None:
    """Pause torrents matching a hash, one or more filters, or all."""
    _run_bulk_torrent_action(
        operation=MutationOperation.TORRENTS_PAUSE,
        action="pause",
        request=_build_selection_request(
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
            exclude_category=exclude_category,
            tag=tag,
            tag_all=tag_all,
            exclude_tag=exclude_tag,
            save_path=save_path,
            exclude_save_path=exclude_save_path,
            name_contains=name_contains,
            exclude_name=exclude_name,
            name_regex=name_regex,
            exclude_state=exclude_state,
            exclude_tracker=exclude_tracker,
            no_tracker=no_tracker,
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
            select_all=select_all,
        ),
        dry_run=dry_run,
        verbose=verbose,
    )


@torrents_app.command()
@error_boundary.catch_internal_errors
def resume(
    torrent_hash: ResumeHashOption = None,
    category: CategoryOption = [],  # noqa: B006 - Typer requires a literal default to detect list options
    state: StateOption = [],  # noqa: B006
    tracker: TrackerOption = [],  # noqa: B006
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
    exclude_tracker: ExcludeTrackerOption = [],  # noqa: B006
    no_tracker: NoTrackerOption = False,
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
    select_all: ResumeAllOption = False,
    dry_run: DryRunOption = True,
    verbose: VerboseOption = False,
) -> None:
    """Resume torrents matching a hash, one or more filters, or all."""
    _run_bulk_torrent_action(
        operation=MutationOperation.TORRENTS_RESUME,
        action="resume",
        request=_build_selection_request(
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
            exclude_category=exclude_category,
            tag=tag,
            tag_all=tag_all,
            exclude_tag=exclude_tag,
            save_path=save_path,
            exclude_save_path=exclude_save_path,
            name_contains=name_contains,
            exclude_name=exclude_name,
            name_regex=name_regex,
            exclude_state=exclude_state,
            exclude_tracker=exclude_tracker,
            no_tracker=no_tracker,
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
            select_all=select_all,
        ),
        dry_run=dry_run,
        verbose=verbose,
    )


@torrents_app.command()
@error_boundary.catch_internal_errors
def start(
    torrent_hash: StartHashOption = None,
    category: CategoryOption = [],  # noqa: B006 - Typer requires a literal default to detect list options
    state: StateOption = [],  # noqa: B006
    tracker: TrackerOption = [],  # noqa: B006
    completed: StartCompletedOption = False,
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
    exclude_tracker: ExcludeTrackerOption = [],  # noqa: B006
    no_tracker: NoTrackerOption = False,
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
    select_all: StartAllOption = False,
    dry_run: DryRunOption = True,
    verbose: VerboseOption = False,
) -> None:
    """Start stopped torrents matching a hash, one or more filters, or all."""
    _run_bulk_torrent_action(
        operation=MutationOperation.TORRENTS_START,
        action="start",
        request=_build_selection_request(
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
            exclude_category=exclude_category,
            tag=tag,
            tag_all=tag_all,
            exclude_tag=exclude_tag,
            save_path=save_path,
            exclude_save_path=exclude_save_path,
            name_contains=name_contains,
            exclude_name=exclude_name,
            name_regex=name_regex,
            exclude_state=exclude_state,
            exclude_tracker=exclude_tracker,
            no_tracker=no_tracker,
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
            select_all=select_all,
        ),
        dry_run=dry_run,
        verbose=verbose,
    )


@torrents_app.command()
@error_boundary.catch_internal_errors
def reannounce(
    torrent_hash: ReannounceHashOption = None,
    category: CategoryOption = [],  # noqa: B006 - Typer requires a literal default to detect list options
    state: StateOption = [],  # noqa: B006
    tracker: TrackerOption = [],  # noqa: B006
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
    exclude_tracker: ExcludeTrackerOption = [],  # noqa: B006
    no_tracker: NoTrackerOption = False,
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
    select_all: ReannounceAllOption = False,
    dry_run: DryRunOption = True,
    verbose: VerboseOption = False,
) -> None:
    """Reannounce torrents matching a hash, one or more filters, or all."""
    _run_bulk_torrent_action(
        operation=MutationOperation.TORRENTS_REANNOUNCE,
        action="reannounce",
        request=_build_selection_request(
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
            exclude_category=exclude_category,
            tag=tag,
            tag_all=tag_all,
            exclude_tag=exclude_tag,
            save_path=save_path,
            exclude_save_path=exclude_save_path,
            name_contains=name_contains,
            exclude_name=exclude_name,
            name_regex=name_regex,
            exclude_state=exclude_state,
            exclude_tracker=exclude_tracker,
            no_tracker=no_tracker,
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
            select_all=select_all,
        ),
        dry_run=dry_run,
        verbose=verbose,
    )


@torrents_app.command()
@error_boundary.catch_internal_errors
def delete(
    torrent_hash: DeleteHashOption = None,
    category: CategoryOption = [],  # noqa: B006 - Typer requires a literal default to detect list options
    state: StateOption = [],  # noqa: B006
    tracker: TrackerOption = [],  # noqa: B006
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
    exclude_tracker: ExcludeTrackerOption = [],  # noqa: B006
    no_tracker: NoTrackerOption = False,
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
    select_all: DeleteAllOption = False,
    with_data: Annotated[
        bool,
        typer.Option(
            "--with-data",
            help=(
                "Also delete the torrent's downloaded files. Without "
                "this, only the torrent is removed from qBittorrent; "
                "its data stays on disk."
            ),
        ),
    ] = False,
    dry_run: DryRunOption = True,
    assume_yes: Annotated[
        bool,
        typer.Option("--yes", help="Skip confirmation for real execution."),
    ] = False,
    verbose: VerboseOption = False,
) -> None:
    """Permanently delete torrents matching a hash, one or more filters, or all.

    Irreversible. Never skipped as a no-op: every matched torrent is
    always a change, unlike pause/resume/start. `--with-data` also
    deletes the downloaded files -- omitted, the torrent is only
    removed from qBittorrent and its data stays on disk.
    """
    _run_bulk_torrent_action(
        operation=MutationOperation.TORRENTS_DELETE,
        action="delete",
        request=_build_selection_request(
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
            exclude_category=exclude_category,
            tag=tag,
            tag_all=tag_all,
            exclude_tag=exclude_tag,
            save_path=save_path,
            exclude_save_path=exclude_save_path,
            name_contains=name_contains,
            exclude_name=exclude_name,
            name_regex=name_regex,
            exclude_state=exclude_state,
            exclude_tracker=exclude_tracker,
            no_tracker=no_tracker,
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
            select_all=select_all,
        ),
        dry_run=dry_run,
        verbose=verbose,
        assume_yes=assume_yes,
        delete_files=with_data,
    )


@torrents_app.command(name="import")
@error_boundary.catch_internal_errors
def import_torrents(
    source: Annotated[
        Path,
        typer.Argument(help="A .torrent file, a directory, or a .zip archive."),
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
        typer.Option("--yes", help="Skip confirmation for real execution."),
    ] = False,
    save_path: Annotated[
        str | None,
        typer.Option("--save-path", help="Save path for imported torrents."),
    ] = None,
    category: Annotated[
        str | None,
        typer.Option(
            "--category", help="Category to assign to imported torrents."
        ),
    ] = None,
    tags: Annotated[
        list[str],
        typer.Option("--tag", help="Tag to assign (repeatable)."),
    ] = [],  # noqa: B006
    start: Annotated[
        bool,
        typer.Option(
            "--start",
            help="Start torrents immediately instead of adding them paused.",
        ),
    ] = False,
    recursive: Annotated[
        bool,
        typer.Option(
            "--recursive",
            help="Recurse into subdirectories when SOURCE is a directory.",
        ),
    ] = False,
    skip_existing: Annotated[
        bool,
        typer.Option(
            "--skip-existing",
            help="Silence the already-present notice.",
        ),
    ] = False,
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", help="Output format."),
    ] = OutputFormat.table,
) -> None:
    """Import .torrent files from a file, directory, or .zip archive.

    Torrents already present in qBittorrent are never re-sent, whether
    or not `--skip-existing` is given -- the flag only silences the
    notice about them. Added paused unless `--start` is given.
    """
    validate_format_support("torrents_import", output_format)
    enabled = rendering.progress_enabled(
        output_format=output_format,
        interactive=rendering.is_interactive_terminal(),
    )
    with error_boundary.qbit_error_boundary():
        client = error_boundary.create_qbit_client()
        try:
            with rendering.transient_spinner(
                "Scanning source and qBittorrent...", enabled=enabled
            ):
                plan = plan_torrent_import(client, source, recursive=recursive)
        except TorrentImportError as error:
            error_boundary.fail(str(error))

    result: TorrentImportResult | None = None

    def _apply() -> None:
        nonlocal result
        result = apply_torrent_import(
            client,
            plan,
            save_path=save_path,
            category=category,
            tags=tags,
            paused=not start,
        )

    applied = run_mutation(
        operation=MutationOperation.TORRENTS_IMPORT,
        dry_run=dry_run,
        assume_yes=assume_yes,
        matched=len(plan.ready),
        has_changes=len(plan.ready) > 0,
        apply_fn=_apply,
        summary_rows=lambda status: rendering.import_summary_rows(
            plan,
            status=status,
            save_path=save_path,
            category=category,
            tags=tags,
            paused=not start,
        ),
        confirmation_message=rendering.import_confirmation_message(plan),
        quiet=output_format == OutputFormat.json,
    )

    if not skip_existing and plan.existing_hashes:
        rendering.print_existing_notice(len(plan.existing_hashes))

    if output_format == OutputFormat.json:
        rendering.print_json_output(
            rendering.import_result_to_dict(
                plan, result, dry_run=not applied, source=str(source)
            )
        )

    exit_if_no_targeted_matches(len(plan.ready))

    if (result is not None and result.failed) or plan.invalid_entries:
        raise typer.Exit(code=ExitCode.ERROR)
