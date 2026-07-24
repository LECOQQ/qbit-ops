"""Rich-based CLI output helpers."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
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
from rich.table import Table

console = Console()
err_console = Console(stderr=True)

ProgressCallback = Callable[[int, int], None]


@contextmanager
def spinner(message: str, done: str | None = None) -> Generator[None]:
    """Show a spinner on stderr while a task runs, then leave a static line.

    No-ops outside an interactive terminal so piped/JSON output and
    non-TTY logs (CI, cron) stay clean. The spinner line is replaced by a
    "done" line only on success; an exception raised inside the block
    leaves no trailing checkmark.
    """
    if not err_console.is_terminal:
        yield
        return

    with err_console.status(message, spinner="dots"):
        yield

    err_console.print(f"[green]:heavy_check_mark:[/green] {done or message}")


@contextmanager
def progress_bar(
    message: str,
    done: str | None = None,
) -> Generator[ProgressCallback]:
    """Show a Rich progress bar on stderr while a per-item task runs.

    Yields a callback usable as `on_progress(completed, total)`. No-ops
    outside an interactive terminal, same rationale as `spinner`.
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

    err_console.print(f"[green]:heavy_check_mark:[/green] {done or message}")


def print_error(message: str) -> None:
    """Print an error message on stderr, styled consistently."""
    err_console.print(f"[bold red]✗ ERROR[/bold red] {message}")


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
) -> None:
    """Print a simple Rich table."""
    table = Table(title=title, show_lines=False)
    for column in columns:
        table.add_column(column)
    for row in rows:
        table.add_row(*row)

    console.print(table)
