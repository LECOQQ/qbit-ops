"""Pure, presentation-only helpers shared by `qbit_ops.tui` widgets/modals.

Moved out of `qbit_ops.tui.app` (see docs/DECISIONS.md, TUI reorg
phase). Every function here takes already-safe structured domain data
(`TuiState`, `SelectedTorrent`, `BulkTorrentActionPlan`,
`MutationUiResult`, `ExplanationReport`/`Evidence`) and renders it as a
string/`Text` -- never a qBittorrent call, never a widget mount, never
a mutation. No behavior change from the pre-split module.
"""

from __future__ import annotations

from datetime import datetime, tzinfo
from typing import Any

from rich.text import Text

from qbit_ops.errors import ErrorCategory
from qbit_ops.features.explain import (
    Evidence,
    ExplanationFinding,
    ExplanationReport,
)
from qbit_ops.features.explain import ExplanationSeverity as Severity
from qbit_ops.features.torrents import BulkTorrentActionPlan, SelectedTorrent
from qbit_ops.shared.execution import MutationStatus
from qbit_ops.tui.state import MutationUiResult, TuiState, _split_skips

NARROW_WIDTH_THRESHOLD = 100
WIDE_WIDTH_THRESHOLD = 130

_SEVERITY_STYLES: dict[Severity, str] = {
    Severity.INFO: "bold green",
    Severity.WARNING: "bold yellow",
    Severity.CRITICAL: "bold red",
    Severity.UNKNOWN: "bold magenta",
}


def _format_local_time(moment: datetime, *, tz: tzinfo | None = None) -> str:
    """Format a timestamp in the local system timezone.

    Refresh times default to local time, not UTC, with the timezone
    label always shown so a UTC-configured host is not silently
    ambiguous. `moment` is always timezone-aware (`datetime.now(UTC)`
    upstream, see `qbit_ops.features.status`), so `.astimezone()` with no `tz`
    converts it to the system's local timezone -- exactly what
    `datetime.astimezone(tz=None)` already means. `tz` exists purely
    for deterministic tests: passing a fixed `tzinfo` (e.g. a
    `zoneinfo.ZoneInfo` or a fixed-offset `timezone`) verifies the
    conversion/label logic without depending on the CI machine's own
    system timezone (which may legitimately be UTC, making a bug and
    a UTC host indistinguishable by output alone).
    """
    local = moment.astimezone(tz)
    tz_label = local.tzname() or "local"
    return f"{local:%H:%M:%S} {tz_label}"


def _shorten_hash(full_hash: str) -> str:
    """Shorten a 40-character infohash for display, e.g. '8ac34f89…f95704b8'.

    Display-only: `c` (`action_copy_hash`) always copies the untouched
    `full_hash`, never this shortened form.
    """
    if len(full_hash) <= 20:
        return full_hash
    return f"{full_hash[:8]}…{full_hash[-8:]}"


def _format_byte_rate(bytes_per_second: int) -> str:
    """Format a byte rate using binary units, e.g. '12.4 MiB/s'.

    A deliberate small duplicate of `qbit_ops.ui.format_byte_rate`: TUI
    modules must never import from `qbit_ops.ui` (a CLI/Rich rendering
    module, see the security boundary in `qbit_ops.tui.app`'s module
    docstring), and this pure formatting function is too small to
    justify promoting it to a third, shared module.
    """
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


# Column display order -- "a user-oriented column order", per the
# visual-polish phase: Name first (always shown, gets the remaining
# width), then operational columns, Category last (shown only once
# width permits). Row *identity* (the DataTable row `key=`) is always
# the full torrent hash regardless of which columns are visible.
_ALL_COLUMNS: tuple[str, ...] = (
    "Name",
    "State",
    "Progress",
    "Down",
    "Up",
    "Ratio",
    "Category",
)

# Compact, predictable widths for operational columns -- `Name` is
# deliberately left unset so it absorbs the remaining width instead of
# being squeezed to its content's natural size.
_COLUMN_WIDTHS: dict[str, int] = {
    "Sel": 4,
    "State": 12,
    "Progress": 7,
    "Down": 10,
    "Up": 10,
    "Ratio": 6,
    "Category": 14,
}

# A heavier glyph (U+2714, "heavy check mark") than a plain "✓", plus
# bold+color, so a selected row's marker reads clearly at a glance
# instead of blending into the row -- requested after dogfooding found
# the previous plain "✓" too easy to miss.
_SELECTED_MARK = "✔"
_UNSELECTED_MARK = " "


def _selection_cell(selected: bool) -> Text:
    if not selected:
        return Text(_UNSELECTED_MARK)
    return Text(_SELECTED_MARK, style="bold green")


def _columns_for_width(width: int) -> tuple[str, ...]:
    """Pick which table columns to show, in order, for a given App width.

    `Sel` (the selection marker, TUI 2) is always shown at every width
    -- multi-selection must never lose its only visual indicator just
    because the terminal is narrow. Progressive disclosure otherwise:
    Name/State/Progress are always shown; Down/Up appear at normal
    width; Ratio/Category only once the terminal is comfortably wide.
    Details, Filters, Search, Copy, and Explain never depend on which
    columns happen to be visible.
    """
    if width < NARROW_WIDTH_THRESHOLD:
        base = ("Name", "State", "Progress")
    elif width < WIDE_WIDTH_THRESHOLD:
        base = ("Name", "State", "Progress", "Down", "Up")
    else:
        base = _ALL_COLUMNS
    return ("Sel", *base)


def _torrent_row_values(
    torrent: SelectedTorrent, selected: bool
) -> dict[str, Any]:
    return {
        "Sel": _selection_cell(selected),
        "Name": torrent.name,
        "State": torrent.state,
        "Progress": f"{torrent.progress * 100:.0f}%",
        "Down": _format_byte_rate(torrent.download_rate),
        "Up": _format_byte_rate(torrent.upload_rate),
        "Ratio": f"{torrent.ratio:.2f}",
        "Category": torrent.category,
    }


def _format_endpoint(endpoint: dict[str, Any]) -> str:
    """Render one safe, structural tracker endpoint as one aligned line.

    Never a raw URL, path, query value, userinfo, or passkey. Shows
    identity and health/status as separate, clearly labeled columns,
    and a sanitized message only when one is present -- never a bare
    status word with nothing identifying which tracker it belongs to.
    Deliberately does *not* also append a synthetic "disabled" suffix
    from `endpoint["enabled"]`: `health` is already `"disabled"`
    whenever `enabled` is `False` for a classifiable status
    (`qbit_ops.features.trackers`'s single status->health mapping), so
    doing both previously produced a duplicated "disabled disabled".
    """
    identity = str(endpoint["tracker"])
    health = str(endpoint["health"])
    line = f"{identity:<10} {health}"
    message = endpoint.get("message")
    if message and health != "disabled":
        line += f"  -- {message}"
    return line


def _format_explain_text(
    torrent_name: str,
    report: ExplanationReport | None,
    state: TuiState,
) -> str:
    """Render an `ExplanationReport` as one scrollable, structured block.

    Display-only: never invents a recommendation, a confidence score,
    or hidden reasoning beyond what `report` itself carries. `report`
    being `None` means tracker data is still being fetched in the
    background -- shown as a concise loading line, not a blank modal.
    """
    freshness_lines = []
    if state.last_successful_refresh is not None:
        freshness_lines.append(
            f"Torrent snapshot refreshed "
            f"{_format_local_time(state.last_successful_refresh)}"
        )
    if state.focused_details_fetched_at is not None:
        freshness_lines.append(
            f"Tracker details fetched "
            f"{_format_local_time(state.focused_details_fetched_at)}"
        )
    if state.stale:
        freshness_lines.append(
            "[bold yellow]STALE[/bold yellow] -- qBittorrent is currently "
            "unreachable; this explanation uses last-known data."
        )

    header = [f"[bold]Explain[/bold] · {_truncate(torrent_name, 60)}"]
    header.extend(freshness_lines)

    if report is None:
        header.append("")
        header.append("Fetching tracker data...")
        return "\n".join(header)

    style = _SEVERITY_STYLES[report.overall_severity]
    header.append(f"[{style}]{report.overall_severity.value.title()}[/{style}]")

    # A single-finding report's summary is, by construction
    # (`qbit_ops.features.explain.build_torrent_explanation`), always
    # the finding's own `explanation` -- printing both would show the
    # same sentence twice. Only show the summary here when it says something the
    # first finding block does not already say.
    if not report.findings or report.summary != report.findings[0].explanation:
        header.append("")
        header.append(report.summary)

    blocks = ["\n".join(header)]

    for finding in report.findings:
        blocks.append(_format_finding(finding))

    return "\n\n".join(blocks)


def _truncate(text: str, limit: int) -> str:
    """Truncate a display string safely, e.g. for a long torrent title."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


_MAX_PREVIEW_ROWS = 50
_MAX_SKIPPED_ROWS = 20


def _format_preview_text(
    plan: BulkTorrentActionPlan,
    snapshot_at: datetime | None,
    *,
    stale: bool = False,
) -> str:
    """Render a frozen `BulkTorrentActionPlan` for the Preview modal.

    Display-only: reads `plan`'s already-computed counts/changes/
    skips, never recomputes or rescans anything. `snapshot_at` is the
    periodic refresh the plan's torrent data came from -- shown so an
    operator can judge freshness, never "now" (no clock is read here).
    `stale` renders the same warning wording `_format_explain_text`
    already uses, plus the explicit rebuild instruction (audit finding
    R-2) -- the preview stays fully readable, only Apply is withdrawn.
    """
    action_label = plan.action.title()
    satisfied, not_found = _split_skips(plan)
    lines = [
        f"[bold]{action_label} · Preview[/bold]",
        "",
        f"Selected             {plan.matched}",
        f"Will {plan.action:<10}     {len(plan.changes)}",
        f"Already satisfied    {len(satisfied)}",
        f"Not found            {len(not_found)}",
    ]
    if snapshot_at is not None:
        lines.append(f"Snapshot             {_format_local_time(snapshot_at)}")
    if stale:
        lines.append("")
        lines.append(
            "[bold yellow]Snapshot stale[/bold yellow] -- qBittorrent is "
            "currently unreachable; this preview uses last-known data."
        )
        lines.append("Apply disabled — rebuild the preview after reconnection.")
    lines.append("")

    lines.append("[bold]Affected torrents[/bold]")
    if not plan.changes:
        lines.append("  (none)")
    else:
        for change in plan.changes[:_MAX_PREVIEW_ROWS]:
            lines.append(
                f"  {_SELECTED_MARK} {_truncate(change.name, 40):<40} "
                f"{_shorten_hash(change.hash)}"
            )
        remaining = len(plan.changes) - _MAX_PREVIEW_ROWS
        if remaining > 0:
            lines.append(f"  … and {remaining} more")

    if plan.skipped:
        lines.append("")
        lines.append("[bold]Skipped[/bold]")
        for skip in plan.skipped[:_MAX_SKIPPED_ROWS]:
            lines.append(f"  - {_truncate(skip.name, 40):<40} ({skip.reason})")
        remaining_skips = len(plan.skipped) - _MAX_SKIPPED_ROWS
        if remaining_skips > 0:
            lines.append(f"  … and {remaining_skips} more")

    return "\n".join(lines)


_ERROR_HEADINGS: dict[ErrorCategory, tuple[str, str]] = {
    ErrorCategory.CONFIGURATION: (
        "[bold red]Configuration invalid[/bold red]",
        "Fix .env and restart qbit-ops. This is a local configuration "
        "problem -- neither a software defect nor a remote failure.",
    ),
    ErrorCategory.AUTHENTICATION: (
        "[bold red]Authentication failed[/bold red]",
        "Check QBIT_USER/QBIT_PASSWORD. Nothing was submitted.",
    ),
    ErrorCategory.UNAVAILABLE: (
        "[bold yellow]Unavailable[/bold yellow]",
        "qBittorrent could not be reached, so nothing was confirmed "
        "submitted.",
    ),
    ErrorCategory.INTERNAL: (
        "[bold red]Internal error[/bold red]",
        "This is a qbit-ops defect, not a remote failure. Nothing was "
        "confirmed submitted.",
    ),
}


def _format_result_text(outcome: MutationUiResult) -> str:
    """Render one truthful `MutationUiResult`.

    Never claims more certainty than qBittorrent's bulk endpoints can
    provide: APPLIED says the request was *submitted* for exactly the
    planned hashes and that a refresh will show the observable state --
    it is not a per-hash confirmation (documented accepted limitation).
    NO_MATCH and NO_CHANGES are kept strictly distinct (audit finding
    F-3): "we could not find them" is not "they were already fine".
    """
    if outcome.error_category is not None:
        heading, explanation = _ERROR_HEADINGS[outcome.error_category]
        message = outcome.error_message or ""
        planned = len(outcome.planned_hashes)
        return (
            f"{heading}\n\n{message}\n\n{explanation}\n\n"
            f"The frozen plan ({planned} torrent(s)) is unchanged and "
            "remains inspectable. Rebuild the preview before retrying: "
            "it is now grounded in a stale snapshot."
        )

    if outcome.status is MutationStatus.NO_MATCH:
        return (
            "[bold]Nothing to do[/bold]\n\n"
            "No selected torrents were found in the current snapshot.\n"
            f"{len(outcome.not_found_hashes)} selected torrent(s) had "
            "disappeared before the plan was built."
        )

    if outcome.status is MutationStatus.NO_CHANGES:
        lines = [
            "[bold]No changes[/bold]",
            "",
            f"{len(outcome.satisfied_hashes)} selected torrent(s) already "
            f"satisfied '{outcome.action}'.",
        ]
        if outcome.not_found_hashes:
            lines.append(
                f"{len(outcome.not_found_hashes)} were not found in the "
                "current snapshot."
            )
        return "\n".join(lines)

    if outcome.cancelled_before_dispatch:
        return (
            "[bold]Cancelled before dispatch[/bold]\n\n"
            "qbit-ops shut down while this action was queued behind "
            "another qBittorrent operation, so it was abandoned before "
            "being sent.\n\n"
            f"No request reached qBittorrent: 0 of "
            f"{len(outcome.planned_hashes)} planned torrent(s) were "
            "submitted, and nothing was changed."
        )

    if outcome.status is MutationStatus.CANCELLED:
        return "[bold]Cancelled[/bold]\n\nNothing was submitted."

    # APPLIED
    lines = [
        "[bold green]Submitted[/bold green]",
        "",
        f"Action submitted for {len(outcome.submitted_hashes)} torrent(s).",
        "A refresh will show the latest observable state.",
    ]
    if outcome.satisfied_hashes:
        lines.append("")
        lines.append(
            f"{len(outcome.satisfied_hashes)} already satisfied "
            f"'{outcome.action}'."
        )
    if outcome.not_found_hashes:
        lines.append(
            f"{len(outcome.not_found_hashes)} were not found in the "
            "current snapshot."
        )
    return "\n".join(lines)


def _format_last_action_line(outcome: MutationUiResult) -> str:
    """One compact, persistent line summarising the latest mutation.

    Reuses the same truthful vocabulary as the Result modal -- notably
    "submitted", never "applied to each torrent" -- and distinguishes
    every outcome the TUI can produce: submitted, no-change, no-match,
    cancelled-before-dispatch, and each error category. Safe structured
    data only; short enough to stay readable at 80 columns.
    """
    when = _format_local_time(outcome.completed_at)
    action = outcome.action.title()

    if outcome.cancelled_before_dispatch:
        summary = "cancelled before dispatch (nothing sent)"
    elif outcome.error_category is not None:
        summary = f"failed · {outcome.error_category.value}"
    elif outcome.status is MutationStatus.APPLIED:
        summary = f"submitted for {len(outcome.submitted_hashes)} torrent(s)"
    elif outcome.status is MutationStatus.NO_MATCH:
        summary = "no selected torrents found"
    elif outcome.status is MutationStatus.NO_CHANGES:
        summary = (
            f"no change needed ({len(outcome.satisfied_hashes)} already "
            "satisfied)"
        )
    else:
        summary = "cancelled"

    return f"[dim]Last action ·[/dim] {action} {summary} [dim]· {when}[/dim]"


def _format_result_notification(outcome: MutationUiResult) -> str:
    """One-line fallback shown when the Result modal cannot be (audit
    finding R-3) -- a submitted mutation must never vanish silently."""
    if outcome.cancelled_before_dispatch:
        return (
            f"{outcome.action.title()} cancelled before dispatch -- "
            "nothing was sent to qBittorrent."
        )
    if outcome.error_category is not None:
        return (
            f"{outcome.action.title()} failed "
            f"({outcome.error_category.value}): "
            f"{outcome.error_message or 'no detail available'}"
        )
    if outcome.status is MutationStatus.APPLIED:
        return (
            f"{outcome.action.title()} submitted for "
            f"{len(outcome.submitted_hashes)} torrent(s)."
        )
    if outcome.status is MutationStatus.NO_MATCH:
        return (
            f"{outcome.action.title()}: no selected torrents were found "
            "in the current snapshot."
        )
    return (
        f"{outcome.action.title()}: no changes needed "
        f"({len(outcome.satisfied_hashes)} already satisfied)."
    )


def _format_finding(finding: ExplanationFinding) -> str:
    style = _SEVERITY_STYLES[finding.severity]
    lines = [
        f"[{style}]{finding.severity.value.upper()}[/{style}] "
        f"[bold]{finding.title}[/bold]",
        finding.explanation,
    ]

    if finding.evidence:
        lines.append("")
        lines.append("[bold]Evidence[/bold]")
        lines.extend(_format_evidence(item) for item in finding.evidence)

    if finding.limitations:
        lines.append("")
        lines.append("[bold]Limitations[/bold]")
        lines.extend(f"  - {item}" for item in finding.limitations)

    if finding.next_commands:
        lines.append("")
        lines.append("[bold]Consider[/bold]")
        lines.extend(f"  $ {command}" for command in finding.next_commands)

    return "\n".join(lines)


# Evidence codes that carry a raw byte-per-second rate, per
# `qbit_ops.features.explain._build_torrent_finding`'s `common_evidence` tuple.
_RATE_EVIDENCE_CODES = frozenset({"download_rate", "upload_rate"})


def _format_evidence(evidence: Evidence) -> str:
    """Render one evidence row with a humanized value where possible.

    Never changes the underlying `Evidence`/JSON model
    (`qbit_ops.features.explain`'s `evidence_to_dict` is untouched) and
    never invents a value this formatting doesn't already have --
    purely cosmetic, keyed off `evidence.code` (a stable identifier
    `qbit_ops.features.explain` already assigns, not inferred from the
    label text).
    """
    label = f"{evidence.label}:"
    return f"  {label:<15} {_format_evidence_value(evidence)}"


def _format_evidence_value(evidence: Evidence) -> str:
    value = evidence.value
    if evidence.code == "progress" and isinstance(value, int | float):
        return f"{value * 100:.1f}%"
    if evidence.code in _RATE_EVIDENCE_CODES and isinstance(value, int | float):
        return _format_byte_rate(int(value))
    if evidence.code == "tracker_health" and isinstance(value, str):
        return value.title()
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None:
        return "(none)"
    return str(value)
