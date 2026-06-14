"""Provide the command-line application."""

import json
import logging
from enum import IntEnum, StrEnum
from typing import Annotated, Any, NoReturn

import qbittorrentapi
import typer

from app import __version__
from app.config import ConfigError, load_qbit_config
from app.trackers import (
    add_tracker_if_source_present,
    export_tracker_state,
    inspect_tracker,
    list_tracker_usage,
    remove_tracker_from_all,
)

PROJECT_NAME = "qbit-ops"

app = typer.Typer(add_completion=False, help="Administer qBittorrent.")
connection_app = typer.Typer(help="Check qBittorrent connectivity.")
trackers_app = typer.Typer(help="Manage qBittorrent trackers.")
app.add_typer(connection_app, name="connection")
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


def _print_details(summary: dict[str, Any]) -> None:
    """Print verbose operation details when available."""
    details = summary.get("details")
    if not details:
        return

    typer.echo("Details:")
    for item in details:
        typer.echo(f"- {item['action']}: {item['name']} ({item['hash']})")
        for tracker_url in item.get("matching_tracker_urls", []):
            typer.echo(f"  - {tracker_url}")


if __name__ == "__main__":
    app()
