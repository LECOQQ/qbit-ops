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
)

PROJECT_NAME = "qbit-ops"

app = typer.Typer(add_completion=False, help="Administer qBittorrent.")
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
        return

    typer.echo(report["message"])


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

    typer.echo("Config doctor:")
    typer.echo(f"- config: {report['config']}")
    typer.echo(f"- host: {report['host']}")
    typer.echo(f"- authentication: {report['authentication']}")
    typer.echo(f"- connection: {report['connection']}")
    typer.echo(f"- qbittorrent_version: {report['qbittorrent_version']}")
    typer.echo(f"- web_api_version: {report['web_api_version']}")


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
    else:
        typer.echo("Torrents:")
        for torrent in torrents:
            _print_torrent_audit_line(torrent)

    typer.echo("Summary:")
    typer.echo(f"- torrents: {len(torrents)}")


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

    typer.echo("Categories:")
    for category_name, torrent_count in category_usage.items():
        typer.echo(f"- {category_name} ({torrent_count} torrent(s))")

    typer.echo("Summary:")
    typer.echo(f"- categories: {len(category_usage)}")


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
        summary = add_tracker_if_source_present(
            client=client,
            source_tracker=source,
            target_tracker=target,
            dry_run=dry_run,
            match_mode=match.value,
            verbose=verbose,
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

    typer.echo("Trackers:")
    for tracker_url, torrent_count in tracker_usage.items():
        typer.echo(f"- {tracker_url} ({torrent_count} torrent(s))")

    typer.echo("Summary:")
    typer.echo(f"- trackers: {len(tracker_usage)}")


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

    summary = report["summary"]
    typer.echo("Tracker health:")
    typer.echo(f"- scanned: {summary['scanned']}")
    typer.echo(
        f"- active_tracker_occurrences: {summary['active_tracker_occurrences']}"
    )
    typer.echo(
        "- disabled_tracker_occurrences: "
        f"{summary['disabled_tracker_occurrences']}"
    )
    typer.echo(f"- unique_exact_trackers: {summary['unique_exact_trackers']}")
    typer.echo(
        f"- unique_logical_trackers: {summary['unique_logical_trackers']}"
    )
    typer.echo(f"- query_variant_groups: {summary['query_variant_groups']}")

    if report["query_variant_groups"]:
        typer.echo("Query variant groups:")
        for group in report["query_variant_groups"]:
            typer.echo(f"- {group['tracker']}")
            for variant in group["variants"]:
                typer.echo(f"  - {variant}")

    if report["disabled_trackers"]:
        typer.echo("Disabled trackers:")
        for tracker_url in report["disabled_trackers"]:
            typer.echo(f"- {tracker_url}")


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
        typer.echo("Matching torrents:")
        for torrent in report["torrents"]:
            _print_torrent_audit_line(
                torrent,
                tracker_count_field="active_tracker_count",
            )
            for tracker_url in torrent["matching_tracker_urls"]:
                typer.echo(f"  - {tracker_url}")

    typer.echo("Summary:")
    typer.echo(f"- scanned: {report['scanned']}")
    typer.echo(f"- matched_tracker: {report['matched_tracker']}")
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
        summary = replace_tracker_in_all(
            client=client,
            source_tracker=source,
            target_tracker=target,
            dry_run=dry_run,
            match_mode=match.value,
            verbose=verbose,
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
    typer.echo("Backup export:")
    typer.echo(f"- exported_at: {metadata['exported_at']}")
    typer.echo(f"- torrents: {summary['torrents']}")
    typer.echo(f"- unique_trackers: {summary['unique_trackers']}")
    typer.echo(f"- tracker_match: {summary['tracker_match']}")
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

    summary = state["summary"]
    typer.echo("Tracker export:")
    typer.echo(f"- torrents: {summary['torrents']}")
    typer.echo(f"- match: {summary['match']}")
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
        summary = remove_tracker_from_all(
            client=client,
            tracker=tracker,
            dry_run=dry_run,
            match_mode=match.value,
            verbose=verbose,
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


def _print_torrent_audit_line(
    torrent: dict[str, Any],
    *,
    tracker_count_field: str = "tracker_count",
) -> None:
    """Print one torrent audit line."""
    progress = _format_percentage(torrent["progress"])
    tracker_count = torrent.get(
        tracker_count_field,
        torrent.get("tracker_count", 0),
    )
    typer.echo(
        "- "
        f"{torrent['name']} ({torrent['hash']}) "
        f"category={torrent.get('category', '(uncategorized)')} "
        f"state={torrent['state']} "
        f"progress={progress} "
        f"ratio={torrent['ratio']:.2f} "
        f"trackers={tracker_count}"
    )


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
        typer.echo("Torrents:")
        for torrent in report["torrents"]:
            _print_torrent_audit_line(torrent)

    typer.echo("Summary:")
    typer.echo(f"- scanned: {report['scanned']}")
    typer.echo(f"- matched: {report['matched']}")


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
        typer.echo("Torrents:")
        for torrent in report["torrents"]:
            _print_torrent_audit_line(
                torrent,
                tracker_count_field="active_tracker_count",
            )
            for tracker_url in torrent["matching_tracker_urls"]:
                typer.echo(f"  - {tracker_url}")

    typer.echo("Summary:")
    typer.echo(f"- scanned: {report['scanned']}")
    typer.echo(f"- matched: {report['matched_tracker']}")


def _print_torrent_details(report: dict[str, Any]) -> None:
    """Print detailed torrent inspection output."""
    progress = _format_percentage(report["progress"])
    typer.echo(f"Torrent: {report['name']} ({report['hash']})")
    typer.echo(f"- state: {report['state']}")
    typer.echo(f"- progress: {progress}")
    typer.echo(f"- ratio: {report['ratio']:.2f}")
    typer.echo(f"- size: {report['size']}")
    typer.echo(f"- save_path: {report['save_path']}")
    typer.echo(f"- category: {report['category']}")
    typer.echo(f"- added_on: {report['added_on']}")
    typer.echo(f"- active_trackers: {report['active_tracker_count']}")

    if report["trackers"]:
        typer.echo("Trackers:")
        for tracker in report["trackers"]:
            status_label = "disabled" if tracker["disabled"] else "active"
            typer.echo(
                f"- {tracker['url']} "
                f"status={tracker['status']} ({status_label})"
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
    typer.echo("Summary:")
    typer.echo(f"- matched: {summary['matched']}")
    typer.echo(f"- limit: {summary['limit']}")

    if not report["matches"]:
        typer.echo("No matching torrents found.")
        return

    typer.echo("Matches:")
    for match in report["matches"]:
        progress = _format_percentage(match["progress"])
        typer.echo(
            "- "
            f"{match['name']} ({match['hash']}) "
            f"score={match['match_score']:.2f} "
            f"state={match['state']} "
            f"progress={progress} "
            f"ratio={match['ratio']:.2f}"
        )


def _print_backup_diff(report: dict[str, Any]) -> None:
    """Print a human-readable backup diff report."""
    summary = report["summary"]

    if summary["identical"]:
        typer.echo("Backup diff: exports are identical.")
        return

    typer.echo("Backup diff:")
    typer.echo(f"- baseline: {summary['baseline']['source']}")
    typer.echo(f"- target: {summary['target']['source']}")
    typer.echo(f"- added_torrents: {summary['added_torrents']}")
    typer.echo(f"- removed_torrents: {summary['removed_torrents']}")
    typer.echo(f"- changed_torrents: {summary['changed_torrents']}")
    typer.echo("- tracker_usage_added: " f"{summary['tracker_usage_added']}")
    typer.echo(
        "- tracker_usage_removed: " f"{summary['tracker_usage_removed']}"
    )
    typer.echo(
        "- tracker_usage_changed: " f"{summary['tracker_usage_changed']}"
    )

    if report["added_torrents"]:
        typer.echo("Added torrents:")
        for torrent in report["added_torrents"]:
            typer.echo(f"- {torrent['name']} ({torrent['hash']})")

    if report["removed_torrents"]:
        typer.echo("Removed torrents:")
        for torrent in report["removed_torrents"]:
            typer.echo(f"- {torrent['name']} ({torrent['hash']})")

    if report["changed_torrents"]:
        typer.echo("Changed torrents:")
        for torrent in report["changed_torrents"]:
            typer.echo(f"- {torrent['name']} ({torrent['hash']})")
            tracker_changes = torrent["normalized_trackers"]
            for tracker_url in tracker_changes["added"]:
                typer.echo(f"  added: {tracker_url}")
            for tracker_url in tracker_changes["removed"]:
                typer.echo(f"  removed: {tracker_url}")

    tracker_usage = report["tracker_usage"]
    if tracker_usage["added"]:
        typer.echo("Tracker usage added:")
        for tracker_url, torrent_count in tracker_usage["added"].items():
            typer.echo(f"- {tracker_url} ({torrent_count} torrent(s))")

    if tracker_usage["removed"]:
        typer.echo("Tracker usage removed:")
        for tracker_url, torrent_count in tracker_usage["removed"].items():
            typer.echo(f"- {tracker_url} ({torrent_count} torrent(s))")

    if tracker_usage["changed"]:
        typer.echo("Tracker usage changed:")
        for item in tracker_usage["changed"]:
            typer.echo(
                "- "
                f"{item['tracker']} "
                f"baseline={item['baseline']} "
                f"target={item['target']}"
            )


def _configure_logging() -> None:
    """Configure readable logs for CLI commands."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def _fail(message: str) -> NoReturn:
    """Print an actionable error and exit with a failure code."""
    typer.secho(f"ERROR: {message}", err=True, fg=typer.colors.RED)
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
    typer.echo("Summary:")
    typer.echo(f"- scanned: {summary['scanned']}")
    typer.echo(f"- matched_source: {summary['matched_source']}")
    typer.echo(f"- already_had_target: {summary['already_had_target']}")
    typer.echo(f"- modified: {summary['modified']}")
    typer.echo(f"- dry_run: {str(summary['dry_run']).lower()}")


def _print_remove_summary(summary: dict[str, int | bool]) -> None:
    """Print the final tracker removal summary."""
    typer.echo("Summary:")
    typer.echo(f"- scanned: {summary['scanned']}")
    typer.echo(f"- matched_tracker: {summary['matched_tracker']}")
    typer.echo(f"- modified: {summary['modified']}")
    typer.echo(f"- removed_urls: {summary['removed_urls']}")
    typer.echo(f"- dry_run: {str(summary['dry_run']).lower()}")


def _print_replace_summary(summary: dict[str, int | bool]) -> None:
    """Print the final tracker replacement summary."""
    typer.echo("Summary:")
    typer.echo(f"- scanned: {summary['scanned']}")
    typer.echo(f"- matched_source: {summary['matched_source']}")
    typer.echo(f"- already_had_target: {summary['already_had_target']}")
    typer.echo(f"- modified: {summary['modified']}")
    typer.echo(f"- replaced_urls: {summary['replaced_urls']}")
    typer.echo(f"- removed_urls: {summary['removed_urls']}")
    typer.echo(f"- dry_run: {str(summary['dry_run']).lower()}")


def _print_bulk_torrent_summary(summary: dict[str, Any]) -> None:
    """Print the final bulk torrent action summary."""
    typer.echo("Summary:")
    typer.echo(f"- action: {summary['action']}")
    typer.echo(f"- filter: {summary['selection']['filter']}")
    typer.echo(f"- value: {summary['selection']['value']}")
    if summary["selection"].get("match") is not None:
        typer.echo(f"- match: {summary['selection']['match']}")
    typer.echo(f"- scanned: {summary['scanned']}")
    typer.echo(f"- matched: {summary['matched']}")
    typer.echo(f"- modified: {summary['modified']}")
    typer.echo(f"- skipped: {summary['skipped']}")
    typer.echo(f"- dry_run: {str(summary['dry_run']).lower()}")


def _print_bulk_torrent_details(summary: dict[str, Any]) -> None:
    """Print verbose bulk torrent action details when available."""
    details = summary.get("details")
    if not details:
        return

    typer.echo("Details:")
    for item in details:
        typer.echo(f"- {item['action']}: {item['name']} ({item['hash']})")


def _print_details(summary: dict[str, Any]) -> None:
    """Print verbose operation details when available."""
    details = summary.get("details")
    if not details:
        return

    typer.echo("Details:")
    for item in details:
        typer.echo(f"- {item['action']}: {item['name']} ({item['hash']})")
        if item.get("replaced_tracker_url"):
            typer.echo(f"  replaced: {item['replaced_tracker_url']}")
        for tracker_url in item.get("matching_tracker_urls", []):
            typer.echo(f"  - {tracker_url}")
        for tracker_url in item.get("removed_tracker_urls", []):
            typer.echo(f"  removed: {tracker_url}")


if __name__ == "__main__":
    app()
