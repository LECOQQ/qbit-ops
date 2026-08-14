"""Own every CLI presentation concern: Rich output, JSON/JSONL/CSV.

`cli/commands/*.py` must import this module and call its symbols as
module attributes (`rendering.print_table(...)`, not a local `from
import`): every command group shares the same interactive-terminal/
quiet/progress-feedback policy, and tests patch a single seam (e.g.
`rendering.is_interactive_terminal`) expecting it to affect every
command uniformly. Do not move Textual/TUI rendering into this module.
"""

import csv
import io
import json
import sys
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

import typer
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

from qbit_core.features.backup import BackupRestorePlan, BackupRestoreResult
from qbit_core.features.doctor import (
    CheckStatus,
    DoctorReport,
    doctor_report_to_csv_rows,
    doctor_report_to_json_dict,
)
from qbit_core.features.explain import (
    Evidence,
    ExplanationReport,
    ExplanationSeverity,
    explanation_report_to_dict,
)
from qbit_core.features.stats import (
    LibraryStats,
    TorrentStatsReport,
    stats_report_to_csv_rows,
    stats_report_to_dict,
)
from qbit_core.features.status import (
    Health,
    StatusSnapshot,
    snapshot_to_csv_rows,
    snapshot_to_json_dict,
)
from qbit_core.features.torrent_import import (
    TorrentImportPlan,
    TorrentImportResult,
)
from qbit_core.features.torrents import (
    BulkTorrentActionPlan,
)
from qbit_core.features.tracker_status import (
    TRACKER_STATUS_CSV_FIELDNAMES,
    TrackerStatusReport,
    tracker_status_report_to_csv_rows,
    tracker_status_report_to_dict,
)
from qbit_core.features.trackers import (
    PasskeyReplacementPlan,
    TrackerAdditionPlan,
    TrackerRemovalPlan,
    TrackerReplacementPlan,
    redact_tracker_identity,
    sanitize_tracker_text,
)
from qbit_core.features.version import (
    VersionReport,
    version_report_to_json_dict,
)
from qbit_core.shared.execution import MutationStatus
from qbit_core.shared.selection import (
    ResolvedTorrent,
    Selection,
    describe_torrent_filter,
    format_category_label,
    torrent_filter_to_dict,
)
from qbit_core.shared.torrent_states import TorrentSnapshot

console = Console()
err_console = Console(stderr=True)

ProgressCallback = Callable[[int, int], None]

MAX_DISPLAYED_HASH_CANDIDATES = 10


def is_interactive_terminal() -> bool:
    """Report whether stderr is an interactive TTY.

    A thin seam around `err_console.is_terminal` so tests can
    monkeypatch it without depending on Rich's own detection.
    """
    return err_console.is_terminal


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

    Takes `interactive` as a parameter so callers/tests can simulate a
    TTY or a piped stream without depending on the real console.
    `output_format=None` means "not applicable" and is treated like
    `table`. Enabled only when not `--quiet`, format is `table` or N/A,
    and stderr is an interactive terminal.
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

    No-ops when `enabled` is False. Rich's `Console.status()` clears its
    live line on any exit path (completion, exception, `KeyboardInterrupt`),
    so nothing is left in scrollback.
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

    Yields a callback usable as `advance(completed, total)`. No-ops when
    `enabled` is False. `transient=True` guarantees the bar is cleared on
    completion, an exception, or `KeyboardInterrupt`.
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

    Only meant to be called once the caller has already established the
    context is an interactive terminal; this does not check
    `err_console.is_terminal` itself.
    """
    return Confirm.ask(prompt, console=err_console, default=False)


def print_cancelled() -> None:
    console.print("Operation cancelled.")


def print_applied() -> None:
    console.print("[bold green]Applied.[/bold green]")


def print_error(message: str) -> None:
    """Print an error message on stderr, styled consistently.

    The single rendering funnel for every command's error output, so it
    runs `sanitize_tracker_text` unconditionally: an upstream exception
    may embed a tracker announce URL or credentials, and callers should
    not each have to remember to sanitize before calling this.
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

    A `"status"` key, if present, must be a `MutationStatus`; it is
    rendered as a distinct final row instead of a raw field so
    `NO_MATCH`/`NO_CHANGES` can never be confused with `APPLIED`.
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


def format_bytes(byte_count: int) -> str:
    """Format a byte count using binary units, e.g. '12.4 MiB'."""
    value = float(max(byte_count, 0))
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1

    unit = units[unit_index]
    if unit == "B":
        return f"{int(value)} {unit}"

    return f"{value:.1f} {unit}"


def format_byte_rate(bytes_per_second: int) -> str:
    """Format a byte rate using binary units, e.g. '12.4 MiB/s'."""
    return f"{format_bytes(bytes_per_second)}/s"


_DURATION_UNITS: tuple[tuple[str, int], ...] = (
    ("d", 86400),
    ("h", 3600),
    ("m", 60),
    ("s", 1),
)


def format_duration(seconds: int) -> str:
    """Format a duration with the two most significant units, e.g. '47d 6h'.

    Deliberately limited to the `d`/`h`/`m`/`s` vocabulary
    `qbit_core.shared.parsers.parse_duration` accepts: a rendered `47d`
    can be typed straight back as `--seeded-for 47d`, while a calendar
    unit could not -- months and years have no fixed length, which is
    why the parser refuses them.
    """
    remaining = max(seconds, 0)
    parts: list[str] = []

    for label, unit_seconds in _DURATION_UNITS:
        amount, remaining = divmod(remaining, unit_seconds)
        if amount:
            parts.append(f"{amount}{label}")
        if len(parts) == 2:
            break

    return " ".join(parts) if parts else "0s"


# Conventional display lengths, fixed here so the rendering never
# implies a calendar. A cumulative total spans no real months, so no
# real month length applies to it.
_YEAR_SECONDS = 365 * 86400
_MONTH_SECONDS = 30 * 86400

_CUMULATIVE_UNITS: tuple[tuple[str, int], ...] = (
    ("y", _YEAR_SECONDS),
    ("mo", _MONTH_SECONDS),
    ("d", 86400),
    ("h", 3600),
)


def format_cumulative_duration(seconds: int) -> str:
    """Format a summed duration compactly, e.g. '429y 5mo'.

    Reserved for totals, which no operator retypes: `156764d` names no
    torrent and answers no question. Values a filter could accept keep
    `format_duration` and its `d`/`h`/`m`/`s` vocabulary instead, so the
    round-trip into `--seeded-for` is never broken.

    `1y` is 365 days and `1mo` is 30 days by convention -- documented in
    `docs/COMMANDS.md`, and deliberately not fed back into
    `parse_duration`, which refuses calendar units precisely because
    they have no fixed length.
    """
    remaining = max(seconds, 0)
    parts: list[str] = []

    for label, unit_seconds in _CUMULATIVE_UNITS:
        amount, remaining = divmod(remaining, unit_seconds)
        if amount:
            parts.append(f"{amount}{label}")
        if len(parts) == 2:
            break

    return " ".join(parts) if parts else format_duration(seconds)


def _format_percentage(value: float) -> str:
    return f"{value * 100:.1f}%"


@dataclass(frozen=True)
class WatchRenderContext:
    """Presentation-only context for one `status --watch` table refresh.

    Deliberately not part of `StatusSnapshot`: refresh interval and
    iteration count are watch-loop bookkeeping, not part of the
    one-shot status model.
    """

    interval: float
    iteration: int


def build_status_renderable(
    snapshot: StatusSnapshot,
    *,
    watch: WatchRenderContext | None = None,
) -> RenderableType:
    """Build the status view as a single Rich renderable.

    Shared by `render_status_table()` and `live_status_display()` so
    both render identically from the same `StatusSnapshot`.
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
    console.print(build_status_renderable(snapshot))


@contextmanager
def live_status_display() -> (
    Generator[Callable[[StatusSnapshot, WatchRenderContext], None]]
):
    """Show a persistent, in-place status display for `status --watch`.

    Uses Rich `Live` with `screen=False` -- no full-screen alternate
    buffer -- so the terminal stays scrollable; each refresh replaces
    the previous render in place instead of appending to scrollback.
    `Live.__exit__` always restores cursor/terminal state, on normal
    completion, an exception, or `KeyboardInterrupt`.
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


def render_status(
    snapshot: StatusSnapshot,
    output_format: OutputFormat,
) -> None:
    if output_format == OutputFormat.table:
        render_status_table(snapshot)
        return

    if output_format == OutputFormat.json:
        print_json_output(snapshot_to_json_dict(snapshot))
        return

    if output_format == OutputFormat.jsonl:
        typer.echo(json.dumps(snapshot_to_json_dict(snapshot), sort_keys=True))
        return

    print_csv_rows(["section", "key", "value"], snapshot_to_csv_rows(snapshot))


def emit_status_jsonl(snapshot: StatusSnapshot) -> None:
    """Emit one status snapshot as a single, immediately flushed JSONL line."""
    typer.echo(json.dumps(snapshot_to_json_dict(snapshot), sort_keys=True))
    sys.stdout.flush()


def render_doctor_table(report: DoctorReport) -> None:
    """Render a doctor report as one table per section, grouped in order.

    Sections are grouped in the order their checks first appear in
    `report.checks` rather than by iterating a set or dict, so table
    order never depends on hashing.
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


def render_doctor_report(
    report: DoctorReport,
    output_format: OutputFormat,
) -> None:
    if output_format == OutputFormat.table:
        render_doctor_table(report)
        return

    if output_format == OutputFormat.json:
        print_json_output(doctor_report_to_json_dict(report))
        return

    if output_format == OutputFormat.jsonl:
        # One document per invocation, like every other command's jsonl,
        # not one line per check.
        typer.echo(
            json.dumps(doctor_report_to_json_dict(report), sort_keys=True)
        )
        return

    print_csv_rows(
        ["section", "code", "status", "message", "detail", "remediation"],
        doctor_report_to_csv_rows(report),
    )


UNAVAILABLE_VERSION_LABEL = "unavailable"

_VERSION_ROW_LABELS: tuple[tuple[str, str], ...] = (
    ("qbit-ops", "qbit_ops"),
    ("Python", "python"),
    ("qBittorrent", "qbittorrent"),
    ("Web API", "web_api"),
)


def version_summary_rows(report: VersionReport) -> dict[str, str]:
    """Build the table rows, `unavailable` standing for a `null` version.

    Tests `is None`, not truthiness, so the table and json/jsonl always
    agree on which versions are unavailable.
    """
    payload = version_report_to_json_dict(report)
    return {
        label: (
            UNAVAILABLE_VERSION_LABEL
            if payload[key] is None
            else str(payload[key])
        )
        for label, key in _VERSION_ROW_LABELS
    }


def render_version_report(
    report: VersionReport,
    output_format: OutputFormat,
) -> None:
    """Render a version report in the requested format.

    `csv` never reaches here: `validate_format_support("version", ...)`
    rejects it first (see `qbit_ops.cli.validation.FORMAT_SUPPORT`).
    """
    if output_format == OutputFormat.json:
        print_json_output(version_report_to_json_dict(report))
        return

    if output_format == OutputFormat.jsonl:
        print_jsonl_output(version_report_to_json_dict(report))
        return

    print_summary(version_summary_rows(report), title="Versions")


_EXPLANATION_SEVERITY_STYLES: dict[ExplanationSeverity, str] = {
    ExplanationSeverity.INFO: "bold green",
    ExplanationSeverity.WARNING: "bold yellow",
    ExplanationSeverity.CRITICAL: "bold red",
    ExplanationSeverity.UNKNOWN: "dim",
}

_BYTE_RATE_EVIDENCE_CODES = {"download_rate", "upload_rate"}


def _format_evidence_value(item: Evidence) -> str:
    """Format one evidence value for table display.

    JSON/JSONL keep `Evidence.value` raw; only the table renderer applies
    a per-code human formatting.
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

    Deliberately not a table: a narrative report has no stable tabular
    shape, so this builds its own structured text layout instead.
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


def render_explanation_report(
    report: ExplanationReport,
    output_format: OutputFormat,
) -> None:
    """Render an explanation report in the requested format."""
    if output_format == OutputFormat.json:
        print_json_output(explanation_report_to_dict(report))
        return

    if output_format == OutputFormat.jsonl:
        print_jsonl_output(explanation_report_to_dict(report))
        return

    render_explanation(report)


def print_csv_rows(fieldnames: list[str], rows: list[tuple[str, ...]]) -> None:
    """Print rows as CSV with a stable header, free of Rich formatting."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(fieldnames)
    writer.writerows(rows)
    typer.echo(buffer.getvalue(), nl=False)


def get_optional_client_value(client: Any, method_name: str) -> str:
    method = getattr(client, method_name, None)
    if method is None:
        return "unknown"

    try:
        value = method()
    except Exception:
        return "unknown"

    return str(value)


def print_json_output(payload: Any) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


def print_jsonl_output(payload: Any) -> None:
    """Print exactly one compact JSON document, followed by a newline."""
    typer.echo(json.dumps(payload, sort_keys=True))


def _torrent_audit_row(
    torrent: dict[str, Any],
    *,
    tracker_count_field: str = "tracker_count",
    include_tracker_count: bool = True,
) -> list[str]:
    """Build one torrent audit table row.

    `include_tracker_count=False` omits the trailing column entirely
    rather than rendering a placeholder: "not collected" is a property
    of the whole table, not of one cell, so a bare `-` would read too
    easily as "zero trackers" instead of "not measured".
    """
    row = [
        torrent["name"],
        torrent["hash"],
        torrent.get("category", "(uncategorized)"),
        torrent["state"],
        _format_percentage(torrent["progress"]),
        f"{torrent['ratio']:.2f}",
    ]
    if include_tracker_count:
        row.append(str(torrent[tracker_count_field]))
    return row


TORRENT_CSV_FIELDNAMES = [
    "name",
    "hash",
    "category",
    "state",
    "progress",
    "ratio",
    "tracker_count",
]


def _torrent_csv_row(
    torrent: dict[str, Any],
    *,
    tracker_count_field: str = "tracker_count",
) -> tuple[str, ...]:
    """Build one torrent CSV row with numeric fields left unformatted."""
    tracker_count = torrent.get(tracker_count_field)
    return (
        torrent["name"],
        torrent["hash"],
        torrent.get("category", "(uncategorized)"),
        torrent["state"],
        str(torrent["progress"]),
        str(torrent["ratio"]),
        "" if tracker_count is None else str(tracker_count),
    )


def format_endpoint_summary(endpoint: dict[str, Any]) -> str:
    """Render one `trackers inspect` matching endpoint as a safe summary line.

    Built entirely from `SafeTrackerIdentity`/`TrackerHealth` fields --
    never a full announce URL, path value, or query value.
    """
    parts = [endpoint["health"]]
    if not endpoint["enabled"]:
        parts.append("disabled")
    if endpoint["scheme"]:
        parts.append(endpoint["scheme"])
    if endpoint["path_shape"]:
        parts.append(f"path={endpoint['path_shape']}")
    if endpoint["query_keys"]:
        parts.append(f"query={','.join(endpoint['query_keys'])}")
    if endpoint["message"]:
        parts.append(f"msg={endpoint['message']}")
    return " ".join(parts)


def torrent_csv_row(
    torrent: dict[str, Any],
    *,
    tracker_count_field: str = "tracker_count",
) -> tuple[str, ...]:
    return _torrent_csv_row(torrent, tracker_count_field=tracker_count_field)


def torrent_audit_row(
    torrent: dict[str, Any],
    *,
    tracker_count_field: str = "tracker_count",
    include_tracker_count: bool = True,
) -> list[str]:
    return _torrent_audit_row(
        torrent,
        tracker_count_field=tracker_count_field,
        include_tracker_count=include_tracker_count,
    )


def torrent_snapshot_to_dict(
    snapshot: TorrentSnapshot,
    *,
    tracker_count: int | None,
) -> dict[str, Any]:
    """Convert a `TorrentSnapshot` into a JSON/CSV/table-ready dict.

    Applies the `(uncategorized)` display label -- the domain model
    carries the raw category. `tracker_count` is `None` when no tracker
    inspection ran, which is distinct from `0` (inspected, no active
    tracker). `download_rate`/`upload_rate` (bytes/second) are
    deliberately not added to the `table`/`csv` renderers -- only to
    `json`/`jsonl`.
    """
    return {
        "hash": snapshot.hash,
        "name": snapshot.name,
        "category": format_category_label(snapshot.category),
        "state": snapshot.state,
        "size": snapshot.size,
        "progress": snapshot.progress,
        "ratio": snapshot.ratio,
        "tracker_count": tracker_count,
        "download_rate": snapshot.download_rate,
        "upload_rate": snapshot.upload_rate,
    }


def print_torrent_selection(
    selection: Selection,
    output_format: OutputFormat,
    *,
    tracker_counts: Mapping[str, int] | None = None,
) -> None:
    """Print a torrent selection, one shared shape for every filter.

    JSON/JSONL always include a normalized `filters` representation
    (`qbit_core.shared.selection.torrent_filter_to_dict`) alongside the
    usual `summary`/`torrents`.

    `tracker_counts` being `None` means no tracker inspection ran: every
    `tracker_count` renders as `null` and the `Trackers` column is
    omitted entirely, because "not collected" is a property of the whole
    listing rather than of any single row.
    """
    inspected = tracker_counts is not None
    torrents = [
        torrent_snapshot_to_dict(
            snapshot,
            tracker_count=(
                tracker_counts.get(snapshot.hash, 0)
                if tracker_counts is not None
                else None
            ),
        )
        for snapshot in selection.matched
    ]
    filters = selection.request.filters
    payload = {
        "filters": torrent_filter_to_dict(filters),
        "summary": {
            "scanned": selection.scanned,
            "matched": len(selection.matched),
        },
        "torrents": torrents,
    }

    if output_format == OutputFormat.json:
        print_json_output(payload)
        return

    if output_format == OutputFormat.jsonl:
        print_jsonl_output(payload)
        return

    if output_format == OutputFormat.csv:
        print_csv_rows(
            TORRENT_CSV_FIELDNAMES,
            [_torrent_csv_row(torrent) for torrent in torrents],
        )
        return

    if not filters.is_empty:
        typer.echo(f"Filter: {describe_torrent_filter(filters)}")

    if not torrents:
        typer.echo("No torrents found.")
    else:
        columns = ["Name", "Hash", "Category", "State", "Progress", "Ratio"]
        if inspected:
            columns.append("Trackers")

        print_table(
            "Torrents",
            columns,
            [
                _torrent_audit_row(torrent, include_tracker_count=inspected)
                for torrent in torrents
            ],
            fold_columns={"Hash"},
        )

    print_summary(
        {"scanned": selection.scanned, "matched": len(selection.matched)}
    )


UNKNOWN_METRIC_LABEL = "unknown"


def _format_optional_bytes(byte_count: int | None) -> str:
    return (
        UNKNOWN_METRIC_LABEL if byte_count is None else format_bytes(byte_count)
    )


def _format_optional_ratio(ratio: float | None) -> str:
    return UNKNOWN_METRIC_LABEL if ratio is None else f"{ratio:.2f}"


def _format_optional_date(moment: datetime | None) -> str:
    """Render one aggregate date as a labelled UTC calendar day.

    Day precision on purpose: `oldest_added_at` answers "how far back
    does this library go", a question no hour of the day refines. The
    `UTC` label is not decoration -- east of UTC+12 the bare day is off
    by one, and nothing else on the line would say so.
    """
    return (
        UNKNOWN_METRIC_LABEL
        if moment is None
        else moment.strftime("%Y-%m-%d UTC")
    )


def _format_seeding_time(library: LibraryStats) -> str:
    """Render the seeded total and its median on one line.

    The two halves use different vocabularies on purpose: the median is
    a value an operator retypes into `--seeded-for`, so it keeps the
    parser-compatible units, while the total is retyped by nobody and
    reads better in conventional ones.
    """
    median_seconds = library.seeding_time_median_seconds
    median_label = (
        UNKNOWN_METRIC_LABEL
        if median_seconds is None
        else format_duration(median_seconds)
    )
    total_label = format_cumulative_duration(library.seeding_time_total_seconds)
    return f"total {total_label} · median {median_label}"


def build_torrent_stats_renderable(
    report: TorrentStatsReport,
) -> RenderableType:
    """Build the `torrents stats` view as a single Rich renderable.

    The two ratios are always labelled apart -- `Selection ratio`
    describes the torrents present and retained, `Instance ratio` the
    whole instance since always, deleted torrents included. Rendering
    both as "Ratio" would rebuild exactly the confusion the two-block
    split exists to remove.
    """
    library = report.library
    groups: list[RenderableType] = [
        _build_summary_table(
            {
                "Torrents": library.torrents,
                "Scanned": library.scanned,
                "Total size": format_bytes(library.total_size_bytes),
                "Average size": _format_optional_bytes(
                    library.average_size_bytes
                ),
                "Largest": _format_optional_bytes(library.largest_size_bytes),
            },
            title="Library",
        ),
        _build_summary_table(
            {
                "Downloaded": format_bytes(library.downloaded_bytes),
                "Uploaded": format_bytes(library.uploaded_bytes),
                "Selection ratio": _format_optional_ratio(
                    library.aggregate_ratio
                ),
            },
            title="Transfer",
        ),
        _build_summary_table(
            {
                "Seeding time": _format_seeding_time(library),
                "Oldest added": _format_optional_date(library.oldest_added_at),
                "Newest added": _format_optional_date(library.newest_added_at),
            },
            title="Time",
        ),
    ]

    if report.instance is not None:
        groups.append(
            _build_summary_table(
                {
                    "Downloaded": format_bytes(
                        report.instance.all_time_downloaded_bytes
                    ),
                    "Uploaded": format_bytes(
                        report.instance.all_time_uploaded_bytes
                    ),
                    "Instance ratio": _format_optional_ratio(
                        report.instance.all_time_ratio
                    ),
                },
                title="Instance (all time)",
            )
        )

    parts: list[RenderableType] = []
    for index, group in enumerate(groups):
        if index > 0:
            parts.append("")
        parts.append(group)

    return Group(*parts)


def render_torrent_stats(
    report: TorrentStatsReport,
    output_format: OutputFormat,
) -> None:
    """Render a `torrents stats` report in the requested format."""
    if output_format == OutputFormat.json:
        print_json_output(stats_report_to_dict(report))
        return

    if output_format == OutputFormat.jsonl:
        print_jsonl_output(stats_report_to_dict(report))
        return

    if output_format == OutputFormat.csv:
        print_csv_rows(
            ["section", "key", "value"], stats_report_to_csv_rows(report)
        )
        return

    filters = report.request.filters
    if not filters.is_empty:
        typer.echo(f"Filter: {describe_torrent_filter(filters)}")

    console.print(build_torrent_stats_renderable(report))


TRACKER_INVENTORY_CSV_FIELDNAMES = [
    "tracker",
    "torrents",
    "exclusive_torrents",
    "endpoints",
    "size_bytes",
    "downloaded_bytes",
    "uploaded_bytes",
    "ratio",
    "seeding_time_seconds",
]


def _tracker_inventory_payload(report: TrackerStatusReport) -> dict[str, Any]:
    """Build the `trackers list` payload every machine format renders.

    Deliberately not `tracker_aggregate_to_dict`: that one is the
    `trackers status` contract, a health report. This one is an
    inventory, and carries volume instead of endpoint health counts.
    """
    return {
        "schema_version": report.schema_version,
        "summary": {
            "trackers": len(report.trackers),
            "torrents_scanned": report.scanned_torrents,
            "torrents_matched": report.matched_torrents,
            # Without this, an inventory that read nothing is
            # indistinguishable from an instance with no tracker: both
            # render zero rows. It never changes the exit code.
            "collection_errors": report.collection_errors,
        },
        "trackers": [
            {
                "identity": aggregate.identity,
                "torrent_count": aggregate.torrent_count,
                "exclusive_torrent_count": aggregate.exclusive_torrent_count,
                "endpoint_count": aggregate.endpoint_count,
                "total_size_bytes": aggregate.total_size_bytes,
                "downloaded_bytes": aggregate.downloaded_bytes,
                "uploaded_bytes": aggregate.uploaded_bytes,
                "aggregate_ratio": aggregate.aggregate_ratio,
                "seeding_time_total_seconds": (
                    aggregate.seeding_time_total_seconds
                ),
            }
            for aggregate in report.trackers
        ],
    }


def render_tracker_inventory(
    report: TrackerStatusReport,
    output_format: OutputFormat,
) -> None:
    """Render the `trackers list` inventory in the requested format.

    Never reads `report.overall_health`: tracker health does not drive
    this command's exit code.
    """
    payload = _tracker_inventory_payload(report)

    if output_format == OutputFormat.json:
        print_json_output(payload)
        return

    if output_format == OutputFormat.jsonl:
        print_jsonl_output(payload)
        return

    if output_format == OutputFormat.csv:
        print_csv_rows(
            TRACKER_INVENTORY_CSV_FIELDNAMES,
            [
                (
                    aggregate.identity,
                    str(aggregate.torrent_count),
                    str(aggregate.exclusive_torrent_count),
                    str(aggregate.endpoint_count),
                    str(aggregate.total_size_bytes),
                    str(aggregate.downloaded_bytes),
                    str(aggregate.uploaded_bytes),
                    (
                        ""
                        if aggregate.aggregate_ratio is None
                        else str(aggregate.aggregate_ratio)
                    ),
                    str(aggregate.seeding_time_total_seconds),
                )
                for aggregate in report.trackers
            ],
        )
        return

    if not report.filters.is_empty:
        typer.echo(f"Filter: {describe_torrent_filter(report.filters)}")

    if not report.trackers:
        typer.echo("No trackers found.")
    else:
        print_table(
            "Trackers",
            [
                "Tracker",
                "Torrents",
                "Excl",
                "Endpoints",
                "Size",
                "Downloaded",
                "Uploaded",
                "Ratio",
                "Seed Time",
            ],
            [
                [
                    aggregate.identity,
                    str(aggregate.torrent_count),
                    str(aggregate.exclusive_torrent_count),
                    str(aggregate.endpoint_count),
                    format_bytes(aggregate.total_size_bytes),
                    format_bytes(aggregate.downloaded_bytes),
                    format_bytes(aggregate.uploaded_bytes),
                    _format_optional_ratio(aggregate.aggregate_ratio),
                    # A cumulative total nobody retypes, so it reads in
                    # conventional units rather than the filter's.
                    format_cumulative_duration(
                        aggregate.seeding_time_total_seconds
                    ),
                ]
                for aggregate in report.trackers
            ],
            fold_columns={"Tracker"},
        )

    print_summary(payload["summary"])


def render_tracker_status(
    report: TrackerStatusReport,
    output_format: OutputFormat,
    *,
    verbose: bool,
) -> None:
    """Render a tracker status report in the requested format."""
    if output_format == OutputFormat.json:
        print_json_output(tracker_status_report_to_dict(report))
        return

    if output_format == OutputFormat.jsonl:
        print_jsonl_output(tracker_status_report_to_dict(report))
        return

    if output_format == OutputFormat.csv:
        print_csv_rows(
            TRACKER_STATUS_CSV_FIELDNAMES,
            tracker_status_report_to_csv_rows(report),
        )
        return

    if not report.filters.is_empty:
        typer.echo(f"Filter: {describe_torrent_filter(report.filters)}")

    if not report.trackers:
        typer.echo("No trackers found.")
    else:
        print_table(
            "Trackers",
            [
                "Tracker",
                "Health",
                "Torrents",
                "Endpts",
                "Healthy",
                "Warn",
                "Crit",
                "Disab",
                "Unk",
            ],
            [
                [
                    aggregate.identity,
                    aggregate.health.value,
                    str(aggregate.torrent_count),
                    str(aggregate.endpoint_count),
                    str(aggregate.healthy_count),
                    str(aggregate.warning_count),
                    str(aggregate.critical_count),
                    str(aggregate.disabled_count),
                    str(aggregate.unknown_count),
                ]
                for aggregate in report.trackers
            ],
            fold_columns={"Tracker"},
        )

        if verbose:
            messages = [
                [aggregate.identity, aggregate.representative_message or ""]
                for aggregate in report.trackers
                if aggregate.representative_message
            ]
            if messages:
                print_table(
                    "Tracker Messages", ["Tracker", "Message"], messages
                )

    print_summary(
        {
            "scanned_torrents": report.scanned_torrents,
            "matched_torrents": report.matched_torrents,
            "trackers": len(report.trackers),
            "collection_errors": report.collection_errors,
            "overall_health": report.overall_health.value,
        }
    )


def print_torrent_details(report: dict[str, Any]) -> None:
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
            ["Tracker", "Details"],
            [
                [tracker["tracker"], format_endpoint_summary(tracker)]
                for tracker in report["trackers"]
            ],
        )
    else:
        typer.echo("Trackers: none")

    # DHT/PeX/LSD are counted nowhere above -- they are peer-discovery
    # mechanisms, not announce endpoints -- but "DHT is off" is still
    # something an operator reads here rather than in the WebUI.
    if report["peer_discovery"]:
        typer.echo(
            "Peer discovery: "
            + ", ".join(
                f"{entry['mechanism']} {entry['health']}"
                for entry in report["peer_discovery"]
            )
        )


def print_filtered_torrent_inspection(
    report: dict[str, Any],
    output_format: OutputFormat,
    *,
    description: str = "",
) -> None:
    """Print `torrents inspect`'s filtered mode.

    Same per-torrent shape as the single-hash mode, wrapped in the
    `filters`/`summary`/`torrents` envelope `torrents list` already
    uses, so one payload shape covers both read commands.
    """
    if output_format == OutputFormat.json:
        print_json_output(report)
        return

    if output_format == OutputFormat.jsonl:
        print_jsonl_output(report)
        return

    if description:
        typer.echo(f"Filter: {description}")

    if not report["torrents"]:
        typer.echo("No torrents found.")
    else:
        for torrent in report["torrents"]:
            print_torrent_details(torrent)

    print_summary(report["summary"])


def print_torrent_name_search(
    report: dict[str, Any],
    output_format: OutputFormat,
) -> None:
    """Print torrent name search results.

    `--format csv` is intentionally not offered here: this helper also
    renders `torrents inspect --hash`'s single-torrent result elsewhere
    (nested tracker details, no stable tabular shape), and `torrents
    inspect` uses one format-support rule for both modes — see
    `qbit_ops.cli.validation.FORMAT_SUPPORT["torrents_inspect"]`.
    `validate_format_support` already rejected `csv` before any API
    call was made.
    """
    if output_format == OutputFormat.json:
        print_json_output(report)
        return

    if output_format == OutputFormat.jsonl:
        print_jsonl_output(report)
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
        fold_columns={"Hash"},
    )


def print_backup_diff(report: dict[str, Any]) -> None:
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


def bulk_torrent_summary_rows(
    plan: BulkTorrentActionPlan,
    *,
    status: MutationStatus,
) -> dict[str, Any]:
    """Build `print_summary` rows for a bulk torrent action plan.

    `filter`/`value` describe whichever selector was actually used
    (`--hash`, `--all`, or one or more combined filters, rendered by
    `describe_torrent_filter` -- never a raw tracker URL, so a passkey
    can never reach this summary).
    """
    if plan.torrent_hash is not None:
        selector_kind, selector_value = "hash", plan.torrent_hash
    elif plan.select_all:
        selector_kind, selector_value = "all", "*"
    else:
        selector_kind, selector_value = (
            "filter",
            describe_torrent_filter(plan.filters),
        )

    rows: dict[str, Any] = {
        "action": plan.action,
        "filter": selector_kind,
        "value": selector_value,
        "scanned": plan.scanned,
        "matched": plan.matched,
        "modified": len(plan.changes),
        "skipped": len(plan.skipped),
    }
    if plan.action == "delete":
        rows["with_data"] = plan.delete_files
    rows["status"] = status
    return rows


def bulk_delete_confirmation_message(plan: BulkTorrentActionPlan) -> str:
    scope = (
        "and their downloaded data"
        if plan.delete_files
        else "(downloaded data is kept on disk)"
    )
    return (
        f"Permanently delete {len(plan.changes)} torrent(s) {scope}? "
        "This cannot be undone."
    )


def print_bulk_torrent_details(
    plan: BulkTorrentActionPlan,
    *,
    applied: bool,
) -> None:
    if not plan.changes and not plan.skipped:
        return

    change_label = plan.action if applied else f"would_{plan.action}"
    rows = [[change_label, change.name, change.hash] for change in plan.changes]
    rows += [[skip.reason, skip.name, skip.hash] for skip in plan.skipped]

    print_table("Details", ["Action", "Name", "Hash"], rows)


def addition_summary_rows(
    plan: TrackerAdditionPlan,
    *,
    status: MutationStatus,
) -> dict[str, Any]:
    return {
        "scanned": plan.scanned,
        "matched": plan.matched,
        "matched_source": plan.matched_source,
        "already_had_target": len(plan.already_had_target),
        "modified": len(plan.changes),
        "status": status,
    }


def print_addition_details(plan: TrackerAdditionPlan, *, applied: bool) -> None:
    if not plan.changes and not plan.already_had_target:
        return

    change_label = "added" if applied else "would_add"
    rows = [[change_label, c.name, c.hash] for c in plan.changes]
    rows += [
        ["already_had_target", c.name, c.hash] for c in plan.already_had_target
    ]
    print_table("Details", ["Action", "Name", "Hash"], rows)


def addition_confirmation_message(plan: TrackerAdditionPlan) -> str:
    return (
        f"Add tracker {redact_tracker_identity(plan.target_tracker)} to "
        f"{len(plan.changes)} torrent(s) already using "
        f"{redact_tracker_identity(plan.source_tracker)}?"
    )


def removal_summary_rows(
    plan: TrackerRemovalPlan,
    *,
    status: MutationStatus,
) -> dict[str, Any]:
    return {
        "scanned": plan.scanned,
        "matched": plan.matched,
        "matched_tracker": plan.matched_tracker,
        "modified": len(plan.changes),
        "removed_urls": plan.removed_url_count,
        "status": status,
    }


def print_removal_details(plan: TrackerRemovalPlan, *, applied: bool) -> None:
    if not plan.changes:
        return

    change_label = "removed" if applied else "would_remove"
    print_table(
        "Details",
        ["Action", "Name", "Hash", "URLs"],
        [
            [change_label, c.name, c.hash, str(len(c.urls))]
            for c in plan.changes
        ],
    )


def removal_confirmation_message(plan: TrackerRemovalPlan) -> str:
    return (
        f"Remove tracker {redact_tracker_identity(plan.tracker)} from "
        f"{plan.matched_tracker} torrent(s) "
        f"({plan.removed_url_count} URL(s) total)? Private torrents relying "
        "solely on this tracker will lose their announce."
    )


def replacement_summary_rows(
    plan: TrackerReplacementPlan,
    *,
    status: MutationStatus,
) -> dict[str, Any]:
    already_had_target = sum(
        1 for change in plan.changes if change.already_had_target
    )
    return {
        "scanned": plan.scanned,
        "matched": plan.matched,
        "matched_source": plan.matched_source,
        "already_had_target": already_had_target,
        "modified": len(plan.changes),
        "replaced_urls": plan.replaced_url_count,
        "removed_urls": plan.removed_url_count,
        "status": status,
    }


def print_replacement_details(
    plan: TrackerReplacementPlan,
    *,
    applied: bool,
) -> None:
    if not plan.changes:
        return

    rows: list[list[str]] = []
    for change in plan.changes:
        if change.already_had_target:
            label = "removed_source" if applied else "would_remove_source"
        else:
            label = "replaced" if applied else "would_replace"
        info = f"{len(change.remove_urls)} removed"
        if change.replace_url is not None:
            info = f"1 replaced, {info}"
        rows.append([label, change.name, change.hash, info])

    print_table("Details", ["Action", "Name", "Hash", "Info"], rows)


def replacement_confirmation_message(plan: TrackerReplacementPlan) -> str:
    return (
        f"Replace {redact_tracker_identity(plan.source_tracker)} with "
        f"{redact_tracker_identity(plan.target_tracker)} on "
        f"{plan.matched_source} torrent(s) "
        f"({plan.replaced_url_count} replaced, {plan.removed_url_count} "
        "removed)? Private torrents relying solely on the source tracker "
        "will lose their announce until the target tracker takes over."
    )


def passkey_summary_rows(
    plan: PasskeyReplacementPlan,
    *,
    status: MutationStatus,
) -> dict[str, Any]:
    """Build `print_summary` rows for a passkey replacement plan.

    Deliberately excludes every field derived from `change.stale_urls`:
    only counts ever reach this summary.
    """
    return {
        "scanned": plan.scanned,
        "matched": plan.matched,
        "matched_source": plan.matched_source,
        "already_up_to_date": plan.already_up_to_date,
        "modified": len(plan.changes),
        "replaced_urls": plan.replaced_url_count,
        "status": status,
    }


def print_passkey_details(
    plan: PasskeyReplacementPlan,
    *,
    applied: bool,
) -> None:
    """Print verbose passkey replacement details, if any.

    Only ever prints `change.hash` / `change.name` / `change.stale_url_count`
    — never `change.stale_urls`, which carries the new passkey.
    """
    if not plan.changes:
        return

    change_label = "replaced" if applied else "would_replace"
    print_table(
        "Details",
        ["Action", "Name", "Hash", "URLs"],
        [
            [change_label, c.name, c.hash, str(c.stale_url_count)]
            for c in plan.changes
        ],
    )


def passkey_confirmation_message(plan: PasskeyReplacementPlan) -> str:
    """Build the confirmation prompt for `trackers replace-passkey`.

    Never includes the old or new passkey, or any tracker URL.
    """
    return (
        f"Update the passkey on {len(plan.changes)} torrent(s) "
        f"({plan.replaced_url_count} tracker URL(s) total)? An incorrect "
        "passkey will break every affected torrent's announce until "
        "corrected."
    )


def import_summary_rows(
    plan: TorrentImportPlan,
    *,
    status: MutationStatus,
    save_path: str | None,
    category: str | None,
    tags: list[str],
    paused: bool,
) -> dict[str, Any]:
    """Build `print_summary` rows for a torrent import plan.

    Never includes `display_name` or any torrent-derived text -- only
    counts and the caller-supplied placement options.
    """
    return {
        "discovered": plan.discovered,
        "valid": len(plan.candidates),
        "existing": len(plan.existing_hashes),
        "duplicates": len(plan.duplicate_hashes),
        "invalid": len(plan.invalid_entries),
        "ready": len(plan.ready),
        "state": "paused" if paused else "started",
        "save_path": save_path or "(qBittorrent default)",
        "category": category or "(none)",
        "tags": ", ".join(tags) if tags else "(none)",
        "status": status,
    }


def import_confirmation_message(plan: TorrentImportPlan) -> str:
    return f"Import {len(plan.ready)} torrent(s)?"


def print_existing_notice(count: int) -> None:
    err_console.print(
        f"[yellow]{count} torrent(s) already present, skipped.[/yellow]"
    )


def import_result_to_dict(
    plan: TorrentImportPlan,
    result: TorrentImportResult | None,
    *,
    dry_run: bool,
    source: str,
) -> dict[str, Any]:
    """Build the `torrents import --format json` payload.

    `results` stays empty in dry-run (nothing was attempted); on a real
    import it lists one entry per attempted torrent.
    """
    results: list[dict[str, Any]] = []
    if result is not None:
        results = [
            {"hash": info_hash, "status": "imported"}
            for info_hash in result.imported_hashes
        ] + [
            {
                "hash": failure.info_hash,
                "status": "failed",
                "message": failure.message,
            }
            for failure in result.failed
        ]

    return {
        "dry_run": dry_run,
        "source": source,
        "discovered": plan.discovered,
        "valid": len(plan.candidates),
        "ready": len(plan.ready),
        "existing": len(plan.existing_hashes),
        "duplicates": len(plan.duplicate_hashes),
        "invalid": len(plan.invalid_entries),
        "results": results,
    }


def backup_restore_summary_rows(
    plan: BackupRestorePlan,
    *,
    status: MutationStatus,
) -> dict[str, Any]:
    return {
        "matched": plan.matched,
        "unmatched": len(plan.unmatched_hashes),
        "categories_to_create": len(plan.categories_to_create),
        "category_changes": len(plan.category_changes),
        "tag_changes": len(plan.tag_changes),
        "tracker_changes": len(plan.tracker_changes),
        "status": status,
    }


def backup_restore_confirmation_message(plan: BackupRestorePlan) -> str:
    return (
        f"Restore category/tags/trackers on {plan.matched} matched "
        f"torrent(s) ({len(plan.category_changes)} category, "
        f"{len(plan.tag_changes)} tag, {len(plan.tracker_changes)} "
        "tracker change(s))?"
    )


def print_backup_restore_details(
    plan: BackupRestorePlan,
    *,
    applied: bool,
) -> None:
    """Print per-torrent restore details.

    Tracker changes only ever show a count -- `added_trackers` carries
    raw announce URLs (possible passkeys), never rendered here.
    """
    if not plan.has_changes:
        return

    label = "restored" if applied else "would_restore"
    rows: list[list[str]] = []
    for change in plan.category_changes:
        rows.append(
            [label, "category", change.name, change.hash, change.category]
        )
    for change in plan.tag_changes:
        rows.append(
            [
                label,
                "tags",
                change.name,
                change.hash,
                ", ".join(change.added_tags),
            ]
        )
    for change in plan.tracker_changes:
        rows.append(
            [
                label,
                "trackers",
                change.name,
                change.hash,
                f"{len(change.added_trackers)} url(s)",
            ]
        )

    print_table("Details", ["Action", "Field", "Name", "Hash", "Detail"], rows)


def backup_restore_result_to_dict(
    plan: BackupRestorePlan,
    result: BackupRestoreResult | None,
    *,
    dry_run: bool,
    source: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "dry_run": dry_run,
        "source": source,
        "matched": plan.matched,
        "unmatched": len(plan.unmatched_hashes),
        "categories_to_create": list(plan.categories_to_create),
        "category_changes": len(plan.category_changes),
        "tag_changes": len(plan.tag_changes),
        "tracker_changes": len(plan.tracker_changes),
        "result": None,
    }
    if result is not None:
        payload["result"] = {
            "categories_created": list(result.categories_created),
            "categories_restored": result.categories_restored,
            "tags_restored": result.tags_restored,
            "trackers_restored": result.trackers_restored,
            "failures": [
                {
                    "hash": failure.hash,
                    "name": failure.name,
                    "action": failure.action,
                    "message": failure.message,
                }
                for failure in result.failures
            ],
        }

    return payload
