"""Register the `torrents` command group."""

from pathlib import Path
from typing import Annotated

import typer

from qbit_core.features.torrent_import import (
    TorrentImportError,
    TorrentImportResult,
    apply_torrent_import,
    plan_torrent_import,
)
from qbit_core.features.torrents import (
    TorrentBulkAction,
    apply_bulk_torrent_action,
    build_torrent_filter,
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
    CategoryOption,
    CompletedOption,
    DeleteAllOption,
    DeleteHashOption,
    DryRunOption,
    ErroredOption,
    InactiveOption,
    IncompleteOption,
    PauseAllOption,
    PauseHashOption,
    ReannounceAllOption,
    ReannounceHashOption,
    ResumeAllOption,
    ResumeHashOption,
    StalledOption,
    StartAllOption,
    StartCompletedOption,
    StartHashOption,
    StateOption,
    TrackerOption,
    VerboseOption,
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
    tracker: str | None,
    completed: bool,
    incomplete: bool,
    active: bool,
    inactive: bool,
    stalled: bool,
    errored: bool,
    select_all: bool = False,
) -> SelectionRequest:
    """Fold raw CLI selector inputs into one validated `SelectionRequest`.

    Every rejection happens here, before any qBittorrent call: a blank
    `--hash`, a contradictory filter combination, and an unsafe
    selector (`--hash` with `--all`, or no selector at all).
    """
    validated_hash = validate_hash_option(torrent_hash)
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
    tracker: TrackerOption = None,
    completed: CompletedOption = False,
    incomplete: IncompleteOption = False,
    active: ActiveOption = False,
    inactive: InactiveOption = False,
    stalled: StalledOption = False,
    errored: ErroredOption = False,
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
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            help="Output format.",
        ),
    ] = OutputFormat.table,
) -> None:
    """Inspect a torrent by hash (or prefix) or search torrents by name."""
    validate_format_support("torrents_inspect", output_format)
    torrent_hash = validate_hash_option(torrent_hash)
    if (torrent_hash is None) == (name is None):
        error_boundary.fail("Provide exactly one of --hash or --name.")

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
    tracker: TrackerOption = None,
    completed: CompletedOption = False,
    incomplete: IncompleteOption = False,
    active: ActiveOption = False,
    inactive: InactiveOption = False,
    stalled: StalledOption = False,
    errored: ErroredOption = False,
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
    tracker: TrackerOption = None,
    completed: CompletedOption = False,
    incomplete: IncompleteOption = False,
    active: ActiveOption = False,
    inactive: InactiveOption = False,
    stalled: StalledOption = False,
    errored: ErroredOption = False,
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
    tracker: TrackerOption = None,
    completed: StartCompletedOption = False,
    incomplete: IncompleteOption = False,
    active: ActiveOption = False,
    inactive: InactiveOption = False,
    stalled: StalledOption = False,
    errored: ErroredOption = False,
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
    tracker: TrackerOption = None,
    completed: CompletedOption = False,
    incomplete: IncompleteOption = False,
    active: ActiveOption = False,
    inactive: InactiveOption = False,
    stalled: StalledOption = False,
    errored: ErroredOption = False,
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
    tracker: TrackerOption = None,
    completed: CompletedOption = False,
    incomplete: IncompleteOption = False,
    active: ActiveOption = False,
    inactive: InactiveOption = False,
    stalled: StalledOption = False,
    errored: ErroredOption = False,
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
