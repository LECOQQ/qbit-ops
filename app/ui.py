"""Rich-based CLI output helpers."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from enum import StrEnum
from typing import Any

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.prompt import Confirm
from rich.table import Table

from app.selectors import ResolvedTorrent
from app.status import Health, StatusSnapshot

console = Console()
err_console = Console(stderr=True)

ProgressCallback = Callable[[int, int], None]

MAX_DISPLAYED_HASH_CANDIDATES = 10


class OutputFormat(StrEnum):
    """Expose the shared machine/human output formats for read commands."""

    table = "table"
    json = "json"
    jsonl = "jsonl"
    csv = "csv"


_HEALTH_STYLES: dict[Health, str] = {
    Health.HEALTHY: "bold green",
    Health.WARNING: "bold yellow",
    Health.CRITICAL: "bold red",
    Health.UNAVAILABLE: "bold red",
}


@contextmanager
def progress_bar(message: str) -> Generator[ProgressCallback]:
    """Show a Rich progress bar on stderr while a per-item task runs.

    Yields a callback usable as `on_progress(completed, total)`. No-ops
    outside an interactive terminal so piped/JSON output and non-TTY logs
    (CI, cron) stay clean. Fully transient: nothing is left behind in
    scrollback once the block exits, so the caller's next output (a
    preview, a confirmation prompt, a summary) starts on a clean line
    instead of trailing a leftover "done" line.
    """
    if not err_console.is_terminal:

        def _noop(_completed: int, _total: int) -> None:
            return

        yield _noop
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=err_console,
        transient=True,
    ) as progress:
        task_id = progress.add_task(message, total=None)

        def _on_progress(completed: int, total: int) -> None:
            progress.update(task_id, completed=completed, total=total)

        yield _on_progress


def confirm(prompt: str) -> bool:
    """Ask a yes/no question on stderr, defaulting to No.

    Only meant to be called when the caller has already established the
    context is an interactive terminal (see `app.execution.ExecutionPolicy`);
    this does not check `err_console.is_terminal` itself.
    """
    return Confirm.ask(prompt, console=err_console, default=False)


def print_cancelled() -> None:
    """Print the standard cancellation message for a declined mutation."""
    console.print("Operation cancelled.")


def print_applied() -> None:
    """Print the standard confirmation-flow success message after a mutation."""
    console.print("[bold green]Applied.[/bold green]")


def print_error(message: str) -> None:
    """Print an error message on stderr, styled consistently."""
    err_console.print(f"[bold red]✗ ERROR[/bold red] {message}")


def print_ambiguous_hash_error(
    value: str,
    candidates: tuple[ResolvedTorrent, ...],
) -> None:
    """Print an actionable ambiguous-hash-prefix error on stderr."""
    err_console.print(
        f"[bold red]✗ ERROR[/bold red] Hash prefix '{value}' matches "
        "multiple torrents."
    )
    err_console.print()

    displayed = candidates[:MAX_DISPLAYED_HASH_CANDIDATES]
    for candidate in displayed:
        short_hash = candidate.hash[:12]
        err_console.print(f"  {short_hash}…  {candidate.name}")

    remaining = len(candidates) - len(displayed)
    if remaining > 0:
        err_console.print(f"  … and {remaining} more.")

    err_console.print()
    err_console.print("Use a longer prefix.")


def print_summary(rows: dict[str, Any], title: str = "Summary") -> None:
    """Print a modification summary, highlighting dry-run status."""
    table = Table(
        title=title, show_header=False, box=None, padding=(0, 1, 0, 0)
    )
    table.add_column(style="bold cyan")
    table.add_column()

    dry_run = rows.get("dry_run")
    for key, value in rows.items():
        if key == "dry_run":
            continue
        table.add_row(key, str(value))

    if dry_run is not None:
        status = (
            "[yellow]PREVIEW (dry-run)[/yellow]"
            if dry_run
            else "[bold green]APPLIED[/bold green]"
        )
        table.add_row("status", status)

    console.print(table)


def print_table(
    title: str,
    columns: list[str],
    rows: list[list[str]],
    *,
    fold_columns: set[str] | None = None,
) -> None:
    """Print a simple Rich table.

    `fold_columns` names columns (e.g. "Hash") that must never be
    truncated with an ellipsis: they wrap onto extra lines instead, so
    a full torrent hash stays copyable even on a narrow terminal.
    """
    table = Table(title=title, show_lines=False)
    for column in columns:
        if fold_columns and column in fold_columns:
            table.add_column(column, overflow="fold")
        else:
            table.add_column(column)
    for row in rows:
        table.add_row(*row)

    console.print(table)


def format_byte_rate(bytes_per_second: int) -> str:
    """Format a byte rate using binary units, e.g. '12.4 MiB/s'."""
    value = float(max(bytes_per_second, 0))
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1

    unit = units[unit_index]
    if unit == "B":
        return f"{int(value)} {unit}/s"

    return f"{value:.1f} {unit}/s"


def render_status_table(snapshot: StatusSnapshot) -> None:
    """Render a status snapshot as a concise Rich table view."""
    style = _HEALTH_STYLES[snapshot.health]
    health_label = f"[{style}]{snapshot.health.value}[/{style}]"
    console.print(f"[bold]qbit-ops[/bold] · {health_label}")
    console.print()

    print_summary(
        {
            "Version": snapshot.qbittorrent_version or "unknown",
            "API": snapshot.api_version or "unknown",
            "Connected": "yes" if snapshot.connected else "no",
        },
        title="qBittorrent",
    )

    transfer_rows: dict[str, Any] = {
        "Total": snapshot.counts.total,
        "Downloading": snapshot.counts.downloading,
        "Seeding": snapshot.counts.seeding,
        "Completed": snapshot.counts.completed,
        "Stalled": snapshot.counts.stalled,
        "Checking": snapshot.counts.checking,
        "Errors": snapshot.counts.errored,
    }
    if snapshot.counts.unknown:
        transfer_rows["Unknown"] = snapshot.counts.unknown
    print_summary(transfer_rows, title="Transfers")

    print_summary(
        {
            "Download": format_byte_rate(
                snapshot.rates.download_bytes_per_second
            ),
            "Upload": format_byte_rate(snapshot.rates.upload_bytes_per_second),
        },
        title="Rates",
    )

    if snapshot.alerts:
        console.print()
        console.print("[bold]Alerts[/bold]")
        for alert in snapshot.alerts:
            console.print(f"  {alert.message}")
