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

from app.execution import MutationStatus
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


def progress_enabled(
    *,
    output_format: OutputFormat | None = None,
    quiet: bool = False,
    interactive: bool | None = None,
) -> bool:
    """Decide whether transient progress feedback should be shown.

    Pure and Rich-free (besides reading `err_console.is_terminal` as a
    default): takes `interactive` as a parameter so callers, and tests,
    can simulate a TTY or a piped/non-TTY stream without depending on the
    real console. `output_format=None` means "not applicable" (mutation
    commands have no `--format`) and is treated like `table`.

    Progress is enabled only when every condition holds: not `--quiet`,
    output format is `table` or not applicable, and stderr is an
    interactive terminal. This is the single source of truth for the
    policy described in `docs/COMMANDS.md`
    ("Progress & Spinner Behavior") — command bodies must not
    re-implement this check inline.
    """
    if quiet:
        return False

    if output_format is not None and output_format is not OutputFormat.table:
        return False

    if interactive is None:
        interactive = err_console.is_terminal

    return interactive


@contextmanager
def transient_spinner(message: str, *, enabled: bool) -> Generator[None]:
    """Show a transient spinner on stderr for one pending operation.

    Use for a single remote request or a bounded collection call whose
    size isn't known up front (`status`, `connection check`, a single
    `torrents_info()` fetch). No-ops when `enabled` is False — callers
    decide that via `progress_enabled()`. Rich's `Console.status()`
    clears its live line on any exit path (normal completion, exception,
    `KeyboardInterrupt`), so nothing is left in scrollback.
    """
    if not enabled:
        yield
        return

    with err_console.status(message, spinner="dots"):
        yield


@contextmanager
def transient_progress(
    message: str,
    *,
    total: int | None = None,
    enabled: bool,
) -> Generator[ProgressCallback]:
    """Show a transient Rich progress bar on stderr for a per-item task.

    Use once a collection has already been fetched and items are
    processed one by one with a real, known total (e.g. one
    `torrents_trackers()` call per torrent). Yields a callback usable as
    `advance(completed, total)`. No-ops when `enabled` is False.
    `transient=True` plus the `with Progress(...)` block guarantee the
    bar is cleared on normal completion, an exception, or a
    `KeyboardInterrupt` — nothing is left behind in scrollback.
    """
    if not enabled:

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
        task_id = progress.add_task(message, total=total)

        def _advance(completed: int, total: int) -> None:
            progress.update(task_id, completed=completed, total=total)

        yield _advance


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


_MUTATION_STATUS_LABELS: dict[MutationStatus, str] = {
    MutationStatus.PREVIEW: "[yellow]PREVIEW (dry-run)[/yellow]",
    MutationStatus.APPLIED: "[bold green]APPLIED[/bold green]",
    MutationStatus.CANCELLED: "[bold red]CANCELLED[/bold red]",
    MutationStatus.NO_MATCH: "[yellow]NO_MATCH[/yellow]",
    MutationStatus.NO_CHANGES: "[cyan]NO_CHANGES[/cyan]",
}


def print_summary(rows: dict[str, Any], title: str = "Summary") -> None:
    """Print a modification summary, highlighting the mutation status.

    A `"status"` key, if present, must be a `MutationStatus` — see
    `docs/COMMANDS.md#mutation-risk--confirmation-policy` for what each
    value means. It is rendered as a distinct final row instead of a raw
    field so `NO_MATCH`/`NO_CHANGES` can never be confused with `APPLIED`.
    """
    table = Table(
        title=title, show_header=False, box=None, padding=(0, 1, 0, 0)
    )
    table.add_column(style="bold cyan")
    table.add_column()

    status = rows.get("status")
    for key, value in rows.items():
        if key == "status":
            continue
        table.add_row(key, str(value))

    if status is not None:
        table.add_row("status", _MUTATION_STATUS_LABELS[status])

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
