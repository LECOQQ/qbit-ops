"""Rich-based CLI output helpers."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.live import Live
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

from app.doctor import CheckStatus, DoctorReport
from app.execution import MutationStatus
from app.explain import Evidence, ExplanationReport, ExplanationSeverity
from app.selectors import ResolvedTorrent
from app.status import Health, StatusSnapshot
from app.trackers import sanitize_tracker_text

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

_CHECK_STATUS_STYLES: dict[CheckStatus, str] = {
    CheckStatus.PASS: "bold green",
    CheckStatus.WARNING: "bold yellow",
    CheckStatus.FAIL: "bold red",
    CheckStatus.SKIPPED: "dim",
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
    """Print an error message on stderr, styled consistently.

    The single rendering funnel for every command's error output, so it
    runs `sanitize_tracker_text` unconditionally: an upstream exception
    (from `qbittorrentapi`, an HTTP client, or a misconfigured
    `QBIT_HOST`) may embed a tracker announce URL, credentials, or
    userinfo, and callers should not each have to remember to sanitize
    before calling this.
    """
    err_console.print(
        f"[bold red]✗ ERROR[/bold red] {sanitize_tracker_text(message)}"
    )


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


def _build_summary_table(rows: dict[str, Any], title: str = "Summary") -> Table:
    """Build a two-column summary table, highlighting the mutation status.

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

    return table


def print_summary(rows: dict[str, Any], title: str = "Summary") -> None:
    """Print a summary table built by `_build_summary_table`."""
    console.print(_build_summary_table(rows, title))


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


@dataclass(frozen=True)
class WatchRenderContext:
    """Presentation-only context for one `status --watch` table refresh.

    Deliberately not part of `StatusSnapshot`: refresh interval and
    iteration count are watch-loop bookkeeping, not part of the
    one-shot status model. `StatusSnapshot.generated_at` already
    carries the last-refresh timestamp, so it is not duplicated here.
    """

    interval: float
    iteration: int


def build_status_renderable(
    snapshot: StatusSnapshot,
    *,
    watch: WatchRenderContext | None = None,
) -> RenderableType:
    """Build the status view as a single Rich renderable.

    Shared by `render_status_table()` (one-shot, prints once) and
    `live_status_display()` (`status --watch`, updates a persistent
    `Live` region in place) so both render identically from the same
    `StatusSnapshot` and never duplicate the health calculation.
    """
    style = _HEALTH_STYLES[snapshot.health]
    health_label = f"[{style}]{snapshot.health.value}[/{style}]"
    parts: list[RenderableType] = [f"[bold]qbit-ops[/bold] · {health_label}"]

    if watch is not None:
        refreshed_at = snapshot.generated_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        parts.append(
            f"[dim]watching · refresh every {watch.interval:g}s · "
            f"iteration {watch.iteration} · last refresh {refreshed_at}"
            "[/dim]"
        )

    parts.append("")
    parts.append(
        _build_summary_table(
            {
                "Version": snapshot.qbittorrent_version or "unknown",
                "API": snapshot.api_version or "unknown",
                "Connected": "yes" if snapshot.connected else "no",
            },
            title="qBittorrent",
        )
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
    parts.append(_build_summary_table(transfer_rows, title="Transfers"))

    parts.append(
        _build_summary_table(
            {
                "Download": format_byte_rate(
                    snapshot.rates.download_bytes_per_second
                ),
                "Upload": format_byte_rate(
                    snapshot.rates.upload_bytes_per_second
                ),
            },
            title="Rates",
        )
    )

    if snapshot.alerts:
        parts.append("")
        parts.append("[bold]Alerts[/bold]")
        for alert in snapshot.alerts:
            parts.append(f"  {alert.message}")

    return Group(*parts)


def render_status_table(snapshot: StatusSnapshot) -> None:
    """Render a status snapshot as a concise Rich table view."""
    console.print(build_status_renderable(snapshot))


@contextmanager
def live_status_display() -> (
    Generator[Callable[[StatusSnapshot, WatchRenderContext], None]]
):
    """Show a persistent, in-place status display for `status --watch`.

    Uses Rich `Live` with `screen=False` — no full-screen alternate
    buffer — so the terminal stays scrollable and remains usable after
    exit; each refresh replaces the previous render in place instead of
    appending a new table to scrollback. Distinct from
    `transient_spinner`/`transient_progress`: this display is
    intentionally persistent for the lifetime of the watch loop rather
    than a transient "while working" indicator, and is never used
    together with them. `Live.__exit__` always restores the cursor and
    terminal state, on normal completion, an exception, or
    `KeyboardInterrupt`.
    """
    with Live(
        console=console,
        screen=False,
        transient=False,
        auto_refresh=False,
    ) as live:

        def _update(
            snapshot: StatusSnapshot,
            watch: WatchRenderContext,
        ) -> None:
            live.update(build_status_renderable(snapshot, watch=watch))
            live.refresh()

        yield _update


def render_doctor_table(report: DoctorReport) -> None:
    """Render a doctor report as one table per section, grouped in order.

    Sections are grouped in the order their checks first appear in
    `report.checks` (already deterministic by construction in
    `app.doctor.collect_doctor_report`) rather than by iterating a set or
    dict, so table order never depends on hashing.
    """
    style = _CHECK_STATUS_STYLES[report.overall_status]
    console.print(
        f"[bold]qbit-ops doctor[/bold] · "
        f"[{style}]{report.overall_status.value}[/{style}]"
    )
    console.print()

    sections: list[str] = []
    for check in report.checks:
        if check.section not in sections:
            sections.append(check.section)

    for section in sections:
        table = Table(title=section.capitalize(), show_lines=False)
        table.add_column("Code")
        table.add_column("Status")
        table.add_column("Message")
        table.add_column("Remediation")

        for check in report.checks:
            if check.section != section:
                continue
            check_style = _CHECK_STATUS_STYLES[check.status]
            table.add_row(
                check.code,
                f"[{check_style}]{check.status.value}[/{check_style}]",
                check.message,
                check.remediation or "",
            )

        console.print(table)


_EXPLANATION_SEVERITY_STYLES: dict[ExplanationSeverity, str] = {
    ExplanationSeverity.INFO: "bold green",
    ExplanationSeverity.WARNING: "bold yellow",
    ExplanationSeverity.CRITICAL: "bold red",
    ExplanationSeverity.UNKNOWN: "dim",
}

_BYTE_RATE_EVIDENCE_CODES = {"download_rate", "upload_rate"}


def _format_evidence_value(item: Evidence) -> str:
    """Format one evidence value for table display.

    JSON/JSONL keep `Evidence.value` raw (an int byte rate, a float
    progress ratio, ...); only the table renderer applies a per-code
    human formatting -- a narrow, explicit lookup, not a generic
    formatting DSL.
    """
    if item.code in _BYTE_RATE_EVIDENCE_CODES and isinstance(item.value, int):
        return format_byte_rate(item.value)
    if item.code == "progress" and isinstance(item.value, float):
        return f"{item.value * 100:.1f}%"
    if item.value is None:
        return ""
    return str(item.value)


def render_explanation(report: ExplanationReport) -> None:
    """Render an explanation report as a concise, terminal-friendly narrative.

    Deliberately not a table (`print_table`): a narrative report has no
    stable tabular shape (see docs/COMMANDS.md, "Format Support
    Matrix"), so this builds its own structured text layout instead.
    Evidence/limitation/next-command text is already secret-free by
    construction (`app.explain` only ever derives it from
    `app.trackers`/`app.tracker_status`'s sanitized structural data), so
    nothing here re-sanitizes it.
    """
    style = _EXPLANATION_SEVERITY_STYLES[report.overall_severity]
    console.print(
        f"[bold]{report.target_type.capitalize()}[/bold] · "
        f"{report.target_identity}"
    )
    console.print(
        f"Severity · [{style}]{report.overall_severity.value}[/{style}]"
    )
    console.print()
    console.print("[bold]Summary[/bold]")
    console.print(report.summary)

    for finding in report.findings:
        console.print()
        finding_style = _EXPLANATION_SEVERITY_STYLES[finding.severity]
        console.print(
            f"[{finding_style}]{finding.severity.value.upper()}"
            f"[/{finding_style}] · [bold]{finding.title}[/bold]"
        )
        console.print(f"  {finding.explanation}")

        if finding.evidence:
            console.print("  [bold]Evidence[/bold]")
            for item in finding.evidence:
                console.print(
                    f"    {item.label:<20} {_format_evidence_value(item)}"
                )

        if finding.limitations:
            console.print("  [bold]Limitations[/bold]")
            for limitation in finding.limitations:
                console.print(f"    {limitation}")

        if finding.next_commands:
            console.print("  [bold]Consider[/bold]")
            for command in finding.next_commands:
                console.print(f"    {command}")
