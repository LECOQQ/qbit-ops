"""Mutation-flow orchestration shared across command groups."""

import sys
from collections.abc import Callable
from typing import Any

import typer

from qbit_core.errors import ErrorCategory, InvalidInputError, require_non_blank
from qbit_core.features.trackers import (
    PASSKEY_PLACEHOLDER,
    fill_passkey_template,
)
from qbit_core.shared.execution import (
    MUTATION_RISK,
    ExecutionDecision,
    ExecutionPolicy,
    MutationOperation,
    MutationStatus,
)
from qbit_ops.cli import error_boundary, rendering
from qbit_ops.cli.exit_codes import ExitCode

# The CLI's own spelling for each `MutationOperation` -- the literal
# Typer command path an operator types. `qbit_core.shared.execution`
# only names *what* mutates, never how the CLI invokes it; this
# correspondence is a CLI-layer concern and stays here. Tests assert
# this dict covers every `MutationOperation` and matches the app's
# actual registered commands, so it cannot drift from either side.
CLI_COMMAND_PATH: dict[MutationOperation, str] = {
    MutationOperation.TORRENTS_PAUSE: "torrents pause",
    MutationOperation.TORRENTS_RESUME: "torrents resume",
    MutationOperation.TORRENTS_START: "torrents start",
    MutationOperation.TORRENTS_REANNOUNCE: "torrents reannounce",
    MutationOperation.TORRENTS_DELETE: "torrents delete",
    MutationOperation.TORRENTS_IMPORT: "torrents import",
    MutationOperation.TORRENTS_CATEGORY_SET: "torrents category set",
    MutationOperation.TORRENTS_CATEGORY_CLEAR: "torrents category clear",
    MutationOperation.TORRENTS_TAG_ADD: "torrents tag add",
    MutationOperation.TORRENTS_TAG_REMOVE: "torrents tag remove",
    MutationOperation.TORRENTS_THROTTLE: "torrents throttle",
    MutationOperation.BACKUP_RESTORE: "backup restore",
    MutationOperation.TRACKERS_ADD_IF_PRESENT: "trackers add-if-present",
    MutationOperation.TRACKERS_REMOVE: "trackers remove",
    MutationOperation.TRACKERS_REPLACE: "trackers replace",
    MutationOperation.TRACKERS_REPLACE_PASSKEY: "trackers replace-passkey",
}


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
    quiet: bool = False,
) -> bool:
    """Run the shared confirm/apply/refuse flow for an already-built plan.

    `matched` and `has_changes` are separate: a plan can match targets
    without any actual change to apply, distinguishing `NO_MATCH` (the
    selector matched nothing) from `NO_CHANGES` (matched, but already
    satisfied) -- neither ever prompts or calls a mutation API. Returns
    whether the plan was applied; exits via `typer.Exit` on a
    non-interactive refusal or a declined confirmation. `quiet` skips
    the Rich summary table -- for a command that also offers `--format
    json`, so stdout carries only the JSON payload.
    """
    policy = ExecutionPolicy(
        dry_run=dry_run,
        assume_yes=assume_yes,
        interactive=rendering.is_interactive_terminal(),
        risk=MUTATION_RISK[operation],
    )

    def _print(status: MutationStatus) -> None:
        # `summary_rows` is called even when quiet, because a caller that
        # renders its own payload still needs to learn the status --
        # skipping the call left it reading the default and reporting
        # `preview` on a mutation that had actually been applied.
        rows = summary_rows(status)
        if not quiet:
            rendering.print_summary(rows)

    if matched == 0:
        _print(MutationStatus.NO_MATCH)
        return False

    if not has_changes:
        _print(MutationStatus.NO_CHANGES)
        return False

    decision = policy.decide()

    if decision is ExecutionDecision.REFUSE_NON_INTERACTIVE:
        error_boundary.fail(policy.refusal_message(CLI_COMMAND_PATH[operation]))

    if decision is ExecutionDecision.APPLY_WITHOUT_PROMPT:
        apply_fn()
        _print(MutationStatus.APPLIED)
        return True

    _print(MutationStatus.PREVIEW)

    if decision is ExecutionDecision.PREVIEW_ONLY:
        return False

    assert confirmation_message is not None
    if not rendering.confirm(confirmation_message):
        if not quiet:
            rendering.print_cancelled()
        raise typer.Exit(code=ExitCode.SUCCESS)

    apply_fn()
    if not quiet:
        rendering.print_applied()
    return True


def exit_if_no_targeted_matches(match_count: int) -> None:
    if match_count == 0:
        raise typer.Exit(code=ExitCode.NO_MATCH)


def resolve_tracker_target(
    target: str,
    *,
    passkey_stdin: bool,
    dry_run: bool,
    assume_yes: bool,
) -> str:
    """Resolve a `--target` tracker URL, filling in a '{passkey}' placeholder.

    Without a placeholder, `target` is returned unchanged and
    `--passkey-stdin` is refused as having nothing to fill -- it would
    otherwise silently read and discard a secret. With one, the passkey
    is obtained the same way `replace-passkey` obtains its own: piped
    via `--passkey-stdin` (one line), or a hidden interactive prompt.
    Combined with real execution (`not dry_run`), stdin also needs
    `--yes`: the confirmation prompt cannot read stdin a second time --
    refused here, before any network call.
    """
    if PASSKEY_PLACEHOLDER not in target:
        if passkey_stdin:
            error_boundary.fail(
                "--passkey-stdin has no effect: --target has no "
                "'{passkey}' placeholder to fill.",
                ErrorCategory.INVALID_INPUT,
            )
        return target

    if passkey_stdin:
        if not assume_yes and not dry_run:
            error_boundary.fail(
                "--passkey-stdin with --no-dry-run also needs --yes: "
                "the confirmation prompt cannot read stdin a second time.",
                ErrorCategory.INVALID_INPUT,
            )
        passkey = sys.stdin.readline()
    elif not rendering.is_interactive_terminal():
        error_boundary.fail(
            "No terminal to prompt for the target passkey. Pipe it "
            "instead with --passkey-stdin.",
            ErrorCategory.INVALID_INPUT,
        )
    else:
        passkey = rendering.prompt_secret("Target passkey")

    try:
        passkey = require_non_blank(passkey, field_name="the target passkey")
    except InvalidInputError as error:
        error_boundary.fail(str(error), ErrorCategory.INVALID_INPUT)

    try:
        return fill_passkey_template(target, passkey)
    except RuntimeError as error:
        error_boundary.fail(str(error))
