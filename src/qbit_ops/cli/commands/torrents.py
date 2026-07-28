"""Register the `torrents` command group.

Moved from `qbit_ops.main`: `list`, `categories`, `inspect`, `pause`,
`resume`, `start`, `reannounce`. No behavior change -- filter options,
selection, and mutation planning are unchanged calls into
`qbit_ops.features.torrents`/`qbit_ops.shared.execution`.
"""

from typing import Annotated

import typer

from qbit_ops.cli import error_boundary, rendering
from qbit_ops.cli.commands._shared import (
    exit_if_no_targeted_matches,
    run_mutation,
)
from qbit_ops.cli.rendering import OutputFormat
from qbit_ops.cli.validation import (
    validate_format_support,
    validate_hash_option,
)
from qbit_ops.features.torrents import (
    STATE_FILTER_VALUES,
    TorrentBulkAction,
    apply_bulk_torrent_action,
    build_torrent_filter,
    inspect_torrent,
    list_category_usage,
    plan_bulk_torrent_action,
    search_torrents_by_name,
    select_torrents,
    validate_torrent_selector,
)
from qbit_ops.shared.execution import MutationOperation
from qbit_ops.shared.selectors import AmbiguousTorrentHashError

torrents_app = typer.Typer(help="Inspect qBittorrent torrents.")

# Shared help text for the torrent-filter options common to `torrents list`
# and every bulk mutation command, so wording cannot silently drift between
# them (see docs/COMMANDS.md, "Torrent Filters").
CATEGORY_FILTER_HELP = (
    "Restrict to a category (repeatable; combines with OR). Use "
    "'uncategorized' for torrents without a category."
)
STATE_FILTER_HELP = (
    "Restrict to a state group (repeatable; combines with OR). Supported: "
    f"{', '.join(sorted(STATE_FILTER_VALUES))}."
)
TRACKER_FILTER_HELP = (
    "Restrict to torrents using a tracker, matched by host[:port] (a full "
    "announce URL is also accepted; only its host and port are used)."
)


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
    torrent_hash = validate_hash_option(torrent_hash)
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

    try:
        validate_torrent_selector(
            torrent_hash=torrent_hash, select_all=select_all, filters=filters
        )
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
                    torrent_hash=torrent_hash,
                    select_all=select_all,
                    filters=filters,
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
        assume_yes=False,
        matched=plan.matched,
        has_changes=bool(plan.changes),
        apply_fn=_apply,
        summary_rows=lambda status: rendering.bulk_torrent_summary_rows(
            plan, status=status
        ),
    )

    if verbose:
        rendering.print_bulk_torrent_details(plan, applied=applied)
    exit_if_no_targeted_matches(plan.matched)


@torrents_app.command(name="list")
@error_boundary.catch_internal_errors
def list_qbit_torrents(
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
        typer.Option("--tracker", help=TRACKER_FILTER_HELP),
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
    if filters.requires_tracker_data:
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
            selection = select_torrents(client, filters, on_progress=advance)

    rendering.print_torrent_selection(selection, output_format)
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
        typer.Option("--category", help=CATEGORY_FILTER_HELP),
    ] = [],  # noqa: B006 - Typer requires a literal default to detect list options
    state: Annotated[
        list[str],
        typer.Option("--state", help=STATE_FILTER_HELP),
    ] = [],  # noqa: B006
    tracker: Annotated[
        str | None,
        typer.Option("--tracker", help=TRACKER_FILTER_HELP),
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
@error_boundary.catch_internal_errors
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
        typer.Option("--category", help=CATEGORY_FILTER_HELP),
    ] = [],  # noqa: B006 - Typer requires a literal default to detect list options
    state: Annotated[
        list[str],
        typer.Option("--state", help=STATE_FILTER_HELP),
    ] = [],  # noqa: B006
    tracker: Annotated[
        str | None,
        typer.Option("--tracker", help=TRACKER_FILTER_HELP),
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
@error_boundary.catch_internal_errors
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
        typer.Option("--category", help=CATEGORY_FILTER_HELP),
    ] = [],  # noqa: B006 - Typer requires a literal default to detect list options
    state: Annotated[
        list[str],
        typer.Option("--state", help=STATE_FILTER_HELP),
    ] = [],  # noqa: B006
    tracker: Annotated[
        str | None,
        typer.Option("--tracker", help=TRACKER_FILTER_HELP),
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
@error_boundary.catch_internal_errors
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
        typer.Option("--category", help=CATEGORY_FILTER_HELP),
    ] = [],  # noqa: B006 - Typer requires a literal default to detect list options
    state: Annotated[
        list[str],
        typer.Option("--state", help=STATE_FILTER_HELP),
    ] = [],  # noqa: B006
    tracker: Annotated[
        str | None,
        typer.Option("--tracker", help=TRACKER_FILTER_HELP),
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
