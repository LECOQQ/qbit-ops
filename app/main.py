"""Provide the command-line application."""

import json
import logging
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
)
from app.config import ConfigError, load_qbit_config
from app.torrents import (
    TorrentBulkAction,
    apply_bulk_torrent_action,
    inspect_torrent,
    list_category_usage,
    list_torrents,
    list_torrents_by_category,
    search_torrents_by_name,
    validate_bulk_torrent_selection,
)
from app.trackers import (
    add_tracker_if_source_present,
    analyze_tracker_health,
    export_tracker_state,
    inspect_tracker,
    list_tracker_usage,
    remove_tracker_from_all,
    replace_tracker_in_all,
    replace_tracker_passkey,
)
from app.ui import (
    print_error,
    print_summary,
    print_table,
    progress_bar,
    spinner,
)

PROJECT_NAME = "qbit-ops"

app = typer.Typer(add_completion=True, help="Administer qBittorrent.")
config_app = typer.Typer(help="Inspect qbit-ops configuration.")
connection_app = typer.Typer(help="Check qBittorrent connectivity.")
backup_app = typer.Typer(help="Export qBittorrent state.")
torrents_app = typer.Typer(help="Inspect qBittorrent torrents.")
trackers_app = typer.Typer(help="Manage qBittorrent trackers.")
app.add_typer(config_app, name="config")
app.add_typer(connection_app, name="connection")
app.add_typer(backup_app, name="backup")
app.add_typer(torrents_app, name="torrents")
app.add_typer(trackers_app, name="trackers")


class ExitCode(IntEnum):
    """Define explicit process exit codes."""

    SUCCESS = 0
    ERROR = 1
    NO_MATCH = 2


class TrackerMatchModeOption(StrEnum):
    """Expose tracker matching modes for Typer options."""

    exact = "exact"
    without_query = "without-query"


class OutputFormatOption(StrEnum):
    """Expose generic output formats for Typer options."""

    text = "text"
    json = "json"


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Print the project name and version when no command is provided."""
    if ctx.invoked_subcommand is None:
        typer.echo(f"{PROJECT_NAME} {__version__}")
        raise typer.Exit(code=ExitCode.SUCCESS)


@connection_app.command()
def check(
    output_format: Annotated[
        OutputFormatOption,
        typer.Option(
            "--output",
            help="Output format.",
        ),
    ] = OutputFormatOption.text,
) -> None:
    """Check qBittorrent connectivity using `.env` settings."""
    try:
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

    if output_format == OutputFormatOption.json:
        _print_json_output(report)


@config_app.command()
def doctor(
    output_format: Annotated[
        OutputFormatOption,
        typer.Option(
            "--output",
            help="Output format.",
        ),
    ] = OutputFormatOption.text,
) -> None:
    """Check qbit-ops configuration and qBittorrent API access."""
    try:
        config = load_qbit_config()
        client = _create_qbit_client()
        qbit_version = _get_optional_client_value(client, "app_version")
        web_api_version = _get_optional_client_value(
            client,
            "app_web_api_version",
        )
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    report = {
        "config": "ok",
        "host": config.host,
        "authentication": "ok",
        "connection": "ok",
        "qbittorrent_version": qbit_version,
        "web_api_version": web_api_version,
    }

    if output_format == OutputFormatOption.json:
        _print_json_output(report)
        return

    print_summary(report, title="Config Doctor")


@torrents_app.command(name="list")
def list_qbit_torrents(
    tracker: Annotated[
        str | None,
        typer.Option(
            "--tracker",
            help="List torrents using a specific tracker.",
        ),
    ] = None,
    category: Annotated[
        str | None,
        typer.Option(
            "--category",
            help="List torrents in a specific category.",
        ),
    ] = None,
    match: Annotated[
        TrackerMatchModeOption,
        typer.Option(
            "--match",
            help="Tracker comparison mode when --tracker is used.",
        ),
    ] = TrackerMatchModeOption.exact,
    output_format: Annotated[
        OutputFormatOption,
        typer.Option(
            "--output",
            help="Output format.",
        ),
    ] = OutputFormatOption.text,
) -> None:
    """List torrents with useful audit fields."""
    if tracker is not None and category is not None:
        _fail("Use either --tracker or --category, not both.")

    try:
        client = _create_qbit_client()
        if tracker is not None:
            report = inspect_tracker(
                client=client,
                tracker=tracker,
                match_mode=match.value,
            )
            _print_torrents_for_tracker(report, output_format)
            _exit_if_no_targeted_matches(report["matched_tracker"])
            return

        if category is not None:
            report = list_torrents_by_category(client, category)
            _print_torrents_for_category(report, output_format)
            _exit_if_no_targeted_matches(report["matched"])
            return

        torrents = list_torrents(client)
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    if output_format == OutputFormatOption.json:
        _print_json_output(
            {
                "summary": {"torrents": len(torrents)},
                "torrents": torrents,
            }
        )
        return

    if not torrents:
        typer.echo("No torrents found.")
        return

    print_table(
        "Torrents",
        ["Name", "Hash", "Category", "State", "Progress", "Ratio", "Trackers"],
        [_torrent_audit_row(torrent) for torrent in torrents],
    )
    print_summary({"torrents": len(torrents)})


@torrents_app.command(name="categories")
def list_qbit_categories(
    output_format: Annotated[
        OutputFormatOption,
        typer.Option(
            "--output",
            help="Output format.",
        ),
    ] = OutputFormatOption.text,
) -> None:
    """List torrent categories and usage counts."""
    try:
        client = _create_qbit_client()
        category_usage = list_category_usage(client)
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    if output_format == OutputFormatOption.json:
        _print_json_output(
            {
                "summary": {"categories": len(category_usage)},
                "categories": category_usage,
            }
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
            help="Torrent hash to inspect.",
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
        OutputFormatOption,
        typer.Option(
            "--output",
            help="Output format.",
        ),
    ] = OutputFormatOption.text,
) -> None:
    """Inspect a torrent by hash or search torrents by name."""
    if (torrent_hash is None) == (name is None):
        _fail("Provide exactly one of --hash or --name.")

    try:
        client = _create_qbit_client()
        if name is not None:
            report = search_torrents_by_name(client, name, limit=limit)
            _print_torrent_name_search(report, output_format)
            _exit_if_no_targeted_matches(report["summary"]["matched"])
            return

        report = inspect_torrent(client, torrent_hash or "")
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    if report is None:
        if output_format == OutputFormatOption.json:
            _print_json_output({"torrent": None, "hash": torrent_hash})
        else:
            typer.echo(f"No torrent found for hash: {torrent_hash}")

        _exit_if_no_targeted_matches(0)
        return

    if output_format == OutputFormatOption.json:
        _print_json_output({"torrent": report})
        return

    _print_torrent_details(report)


@torrents_app.command()
def pause(
    category: Annotated[
        str | None,
        typer.Option(
            "--category",
            help="Pause torrents in a specific category.",
        ),
    ] = None,
    tracker: Annotated[
        str | None,
        typer.Option(
            "--tracker",
            help="Pause torrents using a specific tracker.",
        ),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option(
            "--name",
            help="Pause torrents matching a name query.",
        ),
    ] = None,
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
            help="Preview actions without modifying qBittorrent.",
        ),
    ] = True,
    match: Annotated[
        TrackerMatchModeOption,
        typer.Option(
            "--match",
            help="Tracker comparison mode when --tracker is used.",
        ),
    ] = TrackerMatchModeOption.exact,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Print impacted torrent details.",
        ),
    ] = False,
) -> None:
    """Pause torrents matching a category, tracker, name filter, or all."""
    _run_bulk_torrent_action(
        action="pause",
        category=category,
        tracker=tracker,
        name=name,
        select_all=select_all,
        match=match,
        dry_run=dry_run,
        verbose=verbose,
    )


@torrents_app.command()
def resume(
    category: Annotated[
        str | None,
        typer.Option(
            "--category",
            help="Resume torrents in a specific category.",
        ),
    ] = None,
    tracker: Annotated[
        str | None,
        typer.Option(
            "--tracker",
            help="Resume torrents using a specific tracker.",
        ),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option(
            "--name",
            help="Resume torrents matching a name query.",
        ),
    ] = None,
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
            help="Preview actions without modifying qBittorrent.",
        ),
    ] = True,
    match: Annotated[
        TrackerMatchModeOption,
        typer.Option(
            "--match",
            help="Tracker comparison mode when --tracker is used.",
        ),
    ] = TrackerMatchModeOption.exact,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Print impacted torrent details.",
        ),
    ] = False,
) -> None:
    """Resume torrents matching a category, tracker, name filter, or all."""
    _run_bulk_torrent_action(
        action="resume",
        category=category,
        tracker=tracker,
        name=name,
        select_all=select_all,
        match=match,
        dry_run=dry_run,
        verbose=verbose,
    )


@torrents_app.command()
def start(
    category: Annotated[
        str | None,
        typer.Option(
            "--category",
            help="Start stopped torrents in a specific category.",
        ),
    ] = None,
    tracker: Annotated[
        str | None,
        typer.Option(
            "--tracker",
            help="Start stopped torrents using a specific tracker.",
        ),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option(
            "--name",
            help="Start stopped torrents matching a name query.",
        ),
    ] = None,
    select_all: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Start all stopped torrents.",
        ),
    ] = False,
    completed_only: Annotated[
        bool,
        typer.Option(
            "--completed",
            help="Start stopped completed torrents (Web UI Start All).",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/--no-dry-run",
            help="Preview actions without modifying qBittorrent.",
        ),
    ] = True,
    match: Annotated[
        TrackerMatchModeOption,
        typer.Option(
            "--match",
            help="Tracker comparison mode when --tracker is used.",
        ),
    ] = TrackerMatchModeOption.exact,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Print impacted torrent details.",
        ),
    ] = False,
) -> None:
    """Start stopped torrents, including completed ones with --completed."""
    _run_bulk_torrent_action(
        action="start",
        category=category,
        tracker=tracker,
        name=name,
        select_all=select_all,
        completed_only=completed_only,
        match=match,
        dry_run=dry_run,
        verbose=verbose,
    )


@torrents_app.command()
def reannounce(
    category: Annotated[
        str | None,
        typer.Option(
            "--category",
            help="Reannounce torrents in a specific category.",
        ),
    ] = None,
    tracker: Annotated[
        str | None,
        typer.Option(
            "--tracker",
            help="Reannounce torrents using a specific tracker.",
        ),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option(
            "--name",
            help="Reannounce torrents matching a name query.",
        ),
    ] = None,
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
            help="Preview actions without modifying qBittorrent.",
        ),
    ] = True,
    match: Annotated[
        TrackerMatchModeOption,
        typer.Option(
            "--match",
            help="Tracker comparison mode when --tracker is used.",
        ),
    ] = TrackerMatchModeOption.exact,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Print impacted torrent details.",
        ),
    ] = False,
) -> None:
    """Reannounce torrents matching a category, tracker, name filter, or all."""
    _run_bulk_torrent_action(
        action="reannounce",
        category=category,
        tracker=tracker,
        name=name,
        select_all=select_all,
        match=match,
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
            help="Preview actions without modifying qBittorrent.",
        ),
    ] = True,
    match: Annotated[
        TrackerMatchModeOption,
        typer.Option(
            "--match",
            help="Tracker comparison mode.",
        ),
    ] = TrackerMatchModeOption.exact,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Print impacted torrent details.",
        ),
    ] = False,
) -> None:
    """Add a target tracker when a source tracker is already present."""
    _configure_logging()

    try:
        client = _create_qbit_client()
        with progress_bar("Scanning torrents...") as on_progress:
            summary = add_tracker_if_source_present(
                client=client,
                source_tracker=source,
                target_tracker=target,
                dry_run=dry_run,
                match_mode=match.value,
                verbose=verbose,
                on_progress=on_progress,
            )
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    _print_summary(summary)
    _print_details(summary)
    _exit_if_no_targeted_matches(summary["matched_source"])


@trackers_app.command(name="list")
def list_trackers(
    match: Annotated[
        TrackerMatchModeOption,
        typer.Option(
            "--match",
            help="Tracker grouping mode.",
        ),
    ] = TrackerMatchModeOption.exact,
    output_format: Annotated[
        OutputFormatOption,
        typer.Option(
            "--output",
            help="Output format.",
        ),
    ] = OutputFormatOption.text,
) -> None:
    """List trackers currently present on the qBittorrent instance."""
    try:
        client = _create_qbit_client()
        tracker_usage = list_tracker_usage(client, match_mode=match.value)
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    if output_format == OutputFormatOption.json:
        _print_json_output(
            {
                "match": match.value,
                "summary": {"trackers": len(tracker_usage)},
                "trackers": tracker_usage,
            }
        )
        return

    if not tracker_usage:
        typer.echo("No trackers found.")
        return

    print_table(
        "Trackers",
        ["Tracker", "Torrents"],
        [
            [tracker_url, str(torrent_count)]
            for tracker_url, torrent_count in tracker_usage.items()
        ],
    )
    print_summary({"trackers": len(tracker_usage)})


@trackers_app.command()
def health(
    output_format: Annotated[
        OutputFormatOption,
        typer.Option(
            "--output",
            help="Output format.",
        ),
    ] = OutputFormatOption.text,
) -> None:
    """Analyze tracker health across the qBittorrent instance."""
    try:
        client = _create_qbit_client()
        report = analyze_tracker_health(client)
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    if output_format == OutputFormatOption.json:
        _print_json_output(report)
        return

    print_summary(report["summary"], title="Tracker Health")

    if report["query_variant_groups"]:
        print_table(
            "Query Variant Groups",
            ["Tracker", "Variants"],
            [
                [group["tracker"], "\n".join(group["variants"])]
                for group in report["query_variant_groups"]
            ],
        )

    if report["disabled_trackers"]:
        print_table(
            "Disabled Trackers",
            ["Tracker"],
            [[tracker_url] for tracker_url in report["disabled_trackers"]],
        )


@trackers_app.command(name="inspect")
def inspect_tracker_usage(
    tracker: Annotated[
        str,
        typer.Option(
            "--tracker",
            help="Tracker used to find matching torrents.",
        ),
    ],
    match: Annotated[
        TrackerMatchModeOption,
        typer.Option(
            "--match",
            help="Tracker comparison mode.",
        ),
    ] = TrackerMatchModeOption.exact,
    output_format: Annotated[
        OutputFormatOption,
        typer.Option(
            "--output",
            help="Output format.",
        ),
    ] = OutputFormatOption.text,
) -> None:
    """Inspect torrents using a tracker."""
    try:
        client = _create_qbit_client()
        report = inspect_tracker(
            client=client,
            tracker=tracker,
            match_mode=match.value,
        )
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    if output_format == OutputFormatOption.json:
        _print_json_output(report)
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
                "Matching Tracker URLs",
            ],
            [
                [
                    *_torrent_audit_row(
                        torrent,
                        tracker_count_field="active_tracker_count",
                    ),
                    "\n".join(torrent["matching_tracker_urls"]),
                ]
                for torrent in report["torrents"]
            ],
        )

    print_summary(
        {
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
            help="Preview replacements without modifying qBittorrent.",
        ),
    ] = True,
    match: Annotated[
        TrackerMatchModeOption,
        typer.Option(
            "--match",
            help="Tracker comparison mode.",
        ),
    ] = TrackerMatchModeOption.exact,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Print impacted torrent details.",
        ),
    ] = False,
) -> None:
    """Replace a tracker on every torrent using it."""
    _configure_logging()

    try:
        client = _create_qbit_client()
        with progress_bar("Scanning torrents...") as on_progress:
            summary = replace_tracker_in_all(
                client=client,
                source_tracker=source,
                target_tracker=target,
                dry_run=dry_run,
                match_mode=match.value,
                verbose=verbose,
                on_progress=on_progress,
            )
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    _print_replace_summary(summary)
    _print_details(summary)
    _exit_if_no_targeted_matches(summary["matched_source"])


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
            help="Preview replacements without modifying qBittorrent.",
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
    """Replace a tracker's passkey on every torrent using that tracker."""
    _configure_logging()

    try:
        client = _create_qbit_client()
        with progress_bar("Scanning torrents...") as on_progress:
            summary = replace_tracker_passkey(
                client=client,
                tracker_template=tracker,
                new_passkey=new_passkey,
                dry_run=dry_run,
                verbose=verbose,
                on_progress=on_progress,
            )
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    _print_replace_summary(summary)
    _print_details(summary)
    _exit_if_no_targeted_matches(summary["matched_source"])


@backup_app.command(name="export")
def export_backup(
    output_format: Annotated[
        OutputFormatOption,
        typer.Option(
            "--output",
            help="Output format.",
        ),
    ] = OutputFormatOption.text,
    match: Annotated[
        TrackerMatchModeOption,
        typer.Option(
            "--match",
            help="Tracker normalization mode for exported identities.",
        ),
    ] = TrackerMatchModeOption.exact,
) -> None:
    """Export torrents, trackers and metadata for backup or audit."""
    try:
        config = load_qbit_config()
        client = _create_qbit_client()
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

    if output_format == OutputFormatOption.json:
        _print_json_output(state)
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
    typer.echo("Use --output json for the full backup payload.")


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
        OutputFormatOption,
        typer.Option(
            "--output",
            help="Output format.",
        ),
    ] = OutputFormatOption.text,
) -> None:
    """Compare two backup or tracker export JSON files."""
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

    if output_format == OutputFormatOption.json:
        _print_json_output(report)
    else:
        _print_backup_diff(report)

    _exit_if_backup_diff(report)


@trackers_app.command(name="export")
def export_trackers(
    output_format: Annotated[
        OutputFormatOption,
        typer.Option(
            "--output",
            help="Output format.",
        ),
    ] = OutputFormatOption.text,
    match: Annotated[
        TrackerMatchModeOption,
        typer.Option(
            "--match",
            help="Tracker normalization mode for exported identities.",
        ),
    ] = TrackerMatchModeOption.exact,
) -> None:
    """Export active tracker state."""
    try:
        client = _create_qbit_client()
        state = export_tracker_state(client=client, match_mode=match.value)
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    if output_format == OutputFormatOption.json:
        _print_json_output(state)
        return

    print_summary(state["summary"], title="Tracker Export")
    typer.echo("Use --output json for the full export payload.")


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
            help="Preview removals without modifying qBittorrent.",
        ),
    ] = True,
    match: Annotated[
        TrackerMatchModeOption,
        typer.Option(
            "--match",
            help="Tracker comparison mode.",
        ),
    ] = TrackerMatchModeOption.exact,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Print impacted torrent details.",
        ),
    ] = False,
) -> None:
    """Remove a tracker from every torrent using it."""
    _configure_logging()

    try:
        client = _create_qbit_client()
        with progress_bar("Scanning torrents...") as on_progress:
            summary = remove_tracker_from_all(
                client=client,
                tracker=tracker,
                dry_run=dry_run,
                match_mode=match.value,
                verbose=verbose,
                on_progress=on_progress,
            )
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    _print_remove_summary(summary)
    _print_details(summary)
    _exit_if_no_targeted_matches(summary["matched_tracker"])


def _run_bulk_torrent_action(
    *,
    action: TorrentBulkAction,
    category: str | None,
    tracker: str | None,
    name: str | None,
    select_all: bool,
    match: TrackerMatchModeOption,
    dry_run: bool,
    verbose: bool,
    completed_only: bool = False,
) -> None:
    """Execute a bulk torrent action with shared validation and output."""
    try:
        validate_bulk_torrent_selection(
            category=category,
            tracker=tracker,
            name=name,
            select_all=select_all,
            completed_only=completed_only,
        )
    except ValueError as error:
        _fail(str(error))

    _configure_logging()

    try:
        client = _create_qbit_client()
        with progress_bar(f"Scanning torrents to {action}...") as on_progress:
            summary = apply_bulk_torrent_action(
                client=client,
                action=action,
                category=category,
                tracker=tracker,
                match_mode=match.value,
                name=name,
                select_all=select_all,
                completed_only=completed_only,
                dry_run=dry_run,
                verbose=verbose,
                on_progress=on_progress,
            )
    except ValueError as error:
        _fail(str(error))
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    _print_bulk_torrent_summary(summary)
    _print_bulk_torrent_details(summary)
    _exit_if_no_targeted_matches(summary["matched"])


def _create_qbit_client() -> Any:
    """Create and authenticate a qBittorrent API client."""
    config = load_qbit_config()
    client = qbittorrentapi.Client(
        host=config.host,
        username=config.username,
        password=config.password,
    )

    try:
        with spinner(
            "Connecting to qBittorrent...",
            done="Connected to qBittorrent",
        ):
            client.auth_log_in()
    except Exception as error:
        if _is_qbit_error(error, {"LoginFailed"}):
            raise RuntimeError(
                "Authentication to qBittorrent failed. Check QBIT_USER and "
                "QBIT_PASSWORD."
            ) from error
        if _is_qbit_error(error, {"APIConnectionError"}):
            raise RuntimeError(
                f"Unable to connect to qBittorrent at {config.host}."
            ) from error

        raise RuntimeError(
            f"Unable to initialize qBittorrent client: {error}"
        ) from error

    return client


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


def _torrent_audit_row(
    torrent: dict[str, Any],
    *,
    tracker_count_field: str = "tracker_count",
) -> list[str]:
    """Build one torrent audit table row."""
    tracker_count = torrent.get(
        tracker_count_field,
        torrent.get("tracker_count", 0),
    )
    return [
        torrent["name"],
        torrent["hash"],
        torrent.get("category", "(uncategorized)"),
        torrent["state"],
        _format_percentage(torrent["progress"]),
        f"{torrent['ratio']:.2f}",
        str(tracker_count),
    ]


def _print_torrents_for_category(
    report: dict[str, Any],
    output_format: OutputFormatOption,
) -> None:
    """Print torrents filtered by category."""
    if output_format == OutputFormatOption.json:
        _print_json_output(
            {
                "category": report["category"],
                "summary": {
                    "scanned": report["scanned"],
                    "matched": report["matched"],
                },
                "torrents": report["torrents"],
            }
        )
        return

    typer.echo(f"Category filter: {report['category']}")

    if not report["torrents"]:
        typer.echo("No matching torrents found.")
    else:
        print_table(
            "Torrents",
            [
                "Name",
                "Hash",
                "Category",
                "State",
                "Progress",
                "Ratio",
                "Trackers",
            ],
            [_torrent_audit_row(torrent) for torrent in report["torrents"]],
        )

    print_summary({"scanned": report["scanned"], "matched": report["matched"]})


def _print_torrents_for_tracker(
    report: dict[str, Any],
    output_format: OutputFormatOption,
) -> None:
    """Print torrents filtered by tracker."""
    if output_format == OutputFormatOption.json:
        _print_json_output(
            {
                "tracker": report["tracker"],
                "match": report["match"],
                "summary": {
                    "scanned": report["scanned"],
                    "matched": report["matched_tracker"],
                },
                "torrents": report["torrents"],
            }
        )
        return

    typer.echo(f"Tracker filter: {report['tracker']}")
    typer.echo(f"- match: {report['match']}")

    if not report["torrents"]:
        typer.echo("No matching torrents found.")
    else:
        print_table(
            "Torrents",
            [
                "Name",
                "Hash",
                "Category",
                "State",
                "Progress",
                "Ratio",
                "Trackers",
                "Matching Tracker URLs",
            ],
            [
                [
                    *_torrent_audit_row(
                        torrent,
                        tracker_count_field="active_tracker_count",
                    ),
                    "\n".join(torrent["matching_tracker_urls"]),
                ]
                for torrent in report["torrents"]
            ],
        )

    print_summary(
        {"scanned": report["scanned"], "matched": report["matched_tracker"]}
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
            ["URL", "Status"],
            [
                [
                    tracker["url"],
                    f"{tracker['status']} "
                    f"({'disabled' if tracker['disabled'] else 'active'})",
                ]
                for tracker in report["trackers"]
            ],
        )
    else:
        typer.echo("Trackers: none")


def _print_torrent_name_search(
    report: dict[str, Any],
    output_format: OutputFormatOption,
) -> None:
    """Print torrent name search results."""
    if output_format == OutputFormatOption.json:
        _print_json_output(report)
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


def _configure_logging() -> None:
    """Configure readable logs for CLI commands."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def _fail(message: str) -> NoReturn:
    """Print an actionable error and exit with a failure code."""
    print_error(message)
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


def _print_summary(summary: dict[str, int | bool]) -> None:
    """Print the final command summary."""
    print_summary(
        {
            "scanned": summary["scanned"],
            "matched_source": summary["matched_source"],
            "already_had_target": summary["already_had_target"],
            "modified": summary["modified"],
            "dry_run": summary["dry_run"],
        }
    )


def _print_remove_summary(summary: dict[str, int | bool]) -> None:
    """Print the final tracker removal summary."""
    print_summary(
        {
            "scanned": summary["scanned"],
            "matched_tracker": summary["matched_tracker"],
            "modified": summary["modified"],
            "removed_urls": summary["removed_urls"],
            "dry_run": summary["dry_run"],
        }
    )


def _print_replace_summary(summary: dict[str, int | bool]) -> None:
    """Print the final tracker replacement summary."""
    print_summary(
        {
            "scanned": summary["scanned"],
            "matched_source": summary["matched_source"],
            "already_had_target": summary["already_had_target"],
            "modified": summary["modified"],
            "replaced_urls": summary["replaced_urls"],
            "removed_urls": summary["removed_urls"],
            "dry_run": summary["dry_run"],
        }
    )


def _print_bulk_torrent_summary(summary: dict[str, Any]) -> None:
    """Print the final bulk torrent action summary."""
    rows: dict[str, Any] = {
        "action": summary["action"],
        "filter": summary["selection"]["filter"],
        "value": summary["selection"]["value"],
    }
    if summary["selection"].get("match") is not None:
        rows["match"] = summary["selection"]["match"]
    rows["scanned"] = summary["scanned"]
    rows["matched"] = summary["matched"]
    rows["modified"] = summary["modified"]
    rows["skipped"] = summary["skipped"]
    rows["dry_run"] = summary["dry_run"]

    print_summary(rows)


def _print_bulk_torrent_details(summary: dict[str, Any]) -> None:
    """Print verbose bulk torrent action details when available."""
    details = summary.get("details")
    if not details:
        return

    print_table(
        "Details",
        ["Action", "Name", "Hash"],
        [[item["action"], item["name"], item["hash"]] for item in details],
    )


def _print_details(summary: dict[str, Any]) -> None:
    """Print verbose operation details when available."""
    details = summary.get("details")
    if not details:
        return

    rows: list[list[str]] = []
    for item in details:
        info_lines: list[str] = []
        if item.get("replaced_tracker_url"):
            info_lines.append(f"replaced: {item['replaced_tracker_url']}")
        for source_url, target_url in item.get(
            "replaced_tracker_urls", {}
        ).items():
            info_lines.append(f"replaced: {source_url} -> {target_url}")
        info_lines.extend(item.get("matching_tracker_urls", []))
        for tracker_url in item.get("removed_tracker_urls", []):
            info_lines.append(f"removed: {tracker_url}")

        rows.append(
            [
                item["action"],
                item["name"],
                item["hash"],
                "\n".join(info_lines),
            ]
        )

    print_table("Details", ["Action", "Name", "Hash", "Info"], rows)


if __name__ == "__main__":
    app()
