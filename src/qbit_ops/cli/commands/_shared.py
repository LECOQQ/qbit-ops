"""Mutation-flow orchestration shared across command groups."""

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

    `matched` and `has_changes` are separate: a plan can match targets
    without any actual change to apply, distinguishing `NO_MATCH` (the
    selector matched nothing) from `NO_CHANGES` (matched, but already
    satisfied) -- neither ever prompts or calls a mutation API. Returns
    whether the plan was applied; exits via `typer.Exit` on a
    non-interactive refusal or a declined confirmation.
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
    if match_count == 0:
        raise typer.Exit(code=ExitCode.NO_MATCH)
