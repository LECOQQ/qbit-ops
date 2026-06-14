"""Provide the command-line application."""

import json
import logging
from enum import IntEnum, StrEnum
from typing import Annotated, Any, NoReturn

import qbittorrentapi
import typer

from app import __version__
from app.config import ConfigError, load_qbit_config
from app.torrents import list_torrents
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
torrents_app = typer.Typer(help="Inspect qBittorrent torrents.")
trackers_app = typer.Typer(help="Manage qBittorrent trackers.")
app.add_typer(config_app, name="config")
app.add_typer(connection_app, name="connection")
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


class ExportFormatOption(StrEnum):
    """Expose tracker export formats for Typer options."""

    json = "json"


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
def check() -> None:
    """Check qBittorrent connectivity using `.env` settings."""
    try:
        _create_qbit_client()
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    typer.echo("Connection OK: qBittorrent is reachable with .env settings.")


@config_app.command()
def doctor() -> None:
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

    typer.echo("Config doctor:")
    typer.echo("- config: ok")
    typer.echo(f"- host: {config.host}")
    typer.echo("- authentication: ok")
    typer.echo("- connection: ok")
    typer.echo(f"- qbittorrent_version: {qbit_version}")
    typer.echo(f"- web_api_version: {web_api_version}")


@torrents_app.command(name="list")
def list_qbit_torrents(
    output_format: Annotated[
        OutputFormatOption,
        typer.Option(
            "--output",
            help="Output format.",
        ),
    ] = OutputFormatOption.text,
) -> None:
    """List torrents with useful audit fields."""
    try:
        client = _create_qbit_client()
        torrents = list_torrents(client)
    except ConfigError as error:
        _fail(f"Configuration error: {error}")
    except RuntimeError as error:
        _fail(str(error))
    except Exception as error:
        _fail(f"qBittorrent API error: {error}")

    if output_format == OutputFormatOption.json:
        typer.echo(json.dumps({"torrents": torrents}, indent=2, sort_keys=True))
        return

    if not torrents:
        typer.echo("No torrents found.")
    else:
        typer.echo("Torrents:")
        for torrent in torrents:
            progress = _format_percentage(torrent["progress"])
            typer.echo(
                "- "
                f"{torrent['name']} ({torrent['hash']}) "
                f"state={torrent['state']} "
                f"progress={progress} "
                f"ratio={torrent['ratio']:.2f} "
                f"trackers={torrent['tracker_count']}"
            )

    typer.echo("Summary:")
    typer.echo(f"- torrents: {len(torrents)}")


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
        typer.echo(json.dumps(report, indent=2, sort_keys=True))
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


@trackers_app.command()
def inspect(
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

    if not report["torrents"]:
        typer.echo("No matching torrents found.")
    else:
        typer.echo("Matching torrents:")
        for torrent in report["torrents"]:
            typer.echo(f"- {torrent['name']} ({torrent['hash']})")
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


@trackers_app.command(name="export")
def export_trackers(
    export_format: Annotated[
        ExportFormatOption,
        typer.Option(
            "--format",
            help="Export output format.",
        ),
    ] = ExportFormatOption.json,
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

    if export_format == ExportFormatOption.json:
        typer.echo(json.dumps(state, indent=2, sort_keys=True))


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
