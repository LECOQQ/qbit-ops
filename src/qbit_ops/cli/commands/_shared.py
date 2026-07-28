"""Share mutation-flow orchestration across command groups.

`run_mutation`/`exit_if_no_targeted_matches` are used by both
`cli/commands/torrents.py` and `cli/commands/trackers.py` (and the
no-match exit by `explain.py`/`backup.py` too); kept in one place so
confirmation policy, dry-run behavior, and the no-match exit code are
never duplicated between command modules (forbidden by
docs/ARCHITECTURE.md's CLI boundary). Moved verbatim from
`qbit_ops.main`.
"""

from collections.abc import Callable
from typing import Any

import typer

from qbit_ops.cli import error_boundary, rendering
from qbit_ops.cli.exit_codes import ExitCode
from qbit_ops.shared.execution import (
    MUTATION_RISK,
    ExecutionDecision,
    ExecutionPolicy,
    MutationOperation,
    MutationStatus,
)


def run_mutation(
    *,
    operation: MutationOperation,
    dry_run: bool,
    assume_yes: bool,
    matched: int,
    has_changes: bool,
    apply_fn: Callable[[], None],
    summary_rows: Callable[[MutationStatus], dict[str, Any]],
    confirmation_message: str | None = None,
) -> bool:
    """Run the shared confirm/apply/refuse flow for an already-built plan.

    Centralizes `qbit_ops.shared.execution.ExecutionPolicy` decisions for every
    mutation command: low-risk commands apply real changes immediately
    (no `confirmation_message` needed); medium/high-risk commands show
    the plan, then confirm, then apply exactly that plan (no rescan).

    `matched` and `has_changes` are deliberately separate: a plan can
    match targets without having any actual change to apply (e.g. every
    torrent already paused, or a passkey already up to date). This
    distinguishes `NO_MATCH` (the selector matched nothing) from
    `NO_CHANGES` (matched, but already satisfied the requested state) —
    neither ever prompts or calls a mutation API, and neither is ever
    reported as `APPLIED`.

    `summary_rows(status)` must build the `rendering.print_summary` rows
    dict with its `status` key set to the given `MutationStatus`, so the
    same plan-derived counts can be shown under every terminal status.

    Returns whether the plan was applied. Exits via `typer.Exit` on a
    non-interactive refusal (`ExitCode.ERROR`) or a declined
    confirmation (`ExitCode.SUCCESS`, per the cancellation contract).
    """
    policy = ExecutionPolicy(
        dry_run=dry_run,
        assume_yes=assume_yes,
        interactive=rendering.is_interactive_terminal(),
        risk=MUTATION_RISK[operation],
    )

    if matched == 0:
        rendering.print_summary(summary_rows(MutationStatus.NO_MATCH))
        return False

    if not has_changes:
        rendering.print_summary(summary_rows(MutationStatus.NO_CHANGES))
        return False

    decision = policy.decide()

    if decision is ExecutionDecision.REFUSE_NON_INTERACTIVE:
        error_boundary.fail(policy.refusal_message(operation))

    if decision is ExecutionDecision.APPLY_WITHOUT_PROMPT:
        apply_fn()
        rendering.print_summary(summary_rows(MutationStatus.APPLIED))
        return True

    rendering.print_summary(summary_rows(MutationStatus.PREVIEW))

    if decision is ExecutionDecision.PREVIEW_ONLY:
        return False

    assert confirmation_message is not None
    if not rendering.confirm(confirmation_message):
        rendering.print_cancelled()
        raise typer.Exit(code=ExitCode.SUCCESS)

    apply_fn()
    rendering.print_applied()
    return True


def exit_if_no_targeted_matches(match_count: int) -> None:
    """Exit explicitly when a targeted command does not match any torrent."""
    if match_count == 0:
        raise typer.Exit(code=ExitCode.NO_MATCH)
