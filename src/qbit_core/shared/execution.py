"""Shared mutation risk classification and execution policy.

Every mutating command in `qbit_ops`'s CLI is classified into exactly one
`MutationRisk` tier and goes through the same `ExecutionPolicy.decide()`
logic to determine whether it previews, applies silently, prompts for
confirmation, or refuses. This keeps the safety model in one place
instead of re-implemented per command.
"""

from dataclasses import dataclass
from enum import StrEnum


class MutationRisk(StrEnum):
    """How much damage a mutation can do, and how much friction it needs."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MutationOperation(StrEnum):
    """Every mutating CLI command, named after its exact invocation."""

    TORRENTS_PAUSE = "torrents pause"
    TORRENTS_RESUME = "torrents resume"
    TORRENTS_START = "torrents start"
    TORRENTS_REANNOUNCE = "torrents reannounce"
    TORRENTS_IMPORT = "torrents import"
    BACKUP_RESTORE = "backup restore"
    TRACKERS_ADD_IF_PRESENT = "trackers add-if-present"
    TRACKERS_REMOVE = "trackers remove"
    TRACKERS_REPLACE = "trackers replace"
    TRACKERS_REPLACE_PASSKEY = "trackers replace-passkey"


# The single source of truth for risk classification. Tests assert this
# dict covers every mutating command registered on the Typer app, so a
# new mutation command cannot silently ship unclassified.
MUTATION_RISK: dict[MutationOperation, MutationRisk] = {
    MutationOperation.TORRENTS_PAUSE: MutationRisk.LOW,
    MutationOperation.TORRENTS_RESUME: MutationRisk.LOW,
    MutationOperation.TORRENTS_START: MutationRisk.LOW,
    MutationOperation.TORRENTS_REANNOUNCE: MutationRisk.LOW,
    MutationOperation.TORRENTS_IMPORT: MutationRisk.MEDIUM,
    MutationOperation.BACKUP_RESTORE: MutationRisk.MEDIUM,
    MutationOperation.TRACKERS_ADD_IF_PRESENT: MutationRisk.MEDIUM,
    MutationOperation.TRACKERS_REMOVE: MutationRisk.HIGH,
    MutationOperation.TRACKERS_REPLACE: MutationRisk.HIGH,
    MutationOperation.TRACKERS_REPLACE_PASSKEY: MutationRisk.HIGH,
}


class ExecutionDecision(StrEnum):
    """What the mutation runner should do once a plan has real changes."""

    PREVIEW_ONLY = "preview_only"
    APPLY_WITHOUT_PROMPT = "apply_without_prompt"
    APPLY_WITH_PROMPT = "apply_with_prompt"
    REFUSE_NON_INTERACTIVE = "refuse_non_interactive"


class MutationStatus(StrEnum):
    """The terminal, user-facing outcome shown in a mutation summary.

    `NO_MATCH` and `NO_CHANGES` are reported before any `ExecutionDecision`
    is made -- an empty or already-satisfied plan is never shown as `APPLIED`.
    """

    PREVIEW = "preview"
    APPLIED = "applied"
    CANCELLED = "cancelled"
    NO_MATCH = "no_match"
    NO_CHANGES = "no_changes"


@dataclass(frozen=True)
class ExecutionPolicy:
    """Pure decision object: no I/O, no Typer, no Rich.

    `interactive` must reflect stderr's TTY-ness (`is_interactive_terminal()`),
    not stdout, since confirmation prompts and progress feedback both live
    on stderr alongside the rest of qbit-ops' diagnostic output.
    """

    dry_run: bool
    assume_yes: bool
    interactive: bool
    risk: MutationRisk

    @property
    def mutation_requested(self) -> bool:
        """Whether `--no-dry-run` was passed."""
        return not self.dry_run

    @property
    def requires_confirmation(self) -> bool:
        """Whether this risk tier ever needs a yes/no gate to execute."""
        return self.risk is not MutationRisk.LOW

    def decide(self) -> ExecutionDecision:
        """Decide what to do, assuming the plan has at least one real change.

        Callers must check for an empty/no-match/invalid plan themselves —
        those never reach a decision (and never prompt) regardless of policy.
        """
        if self.dry_run:
            return ExecutionDecision.PREVIEW_ONLY

        if not self.requires_confirmation or self.assume_yes:
            return ExecutionDecision.APPLY_WITHOUT_PROMPT

        if self.interactive:
            return ExecutionDecision.APPLY_WITH_PROMPT

        return ExecutionDecision.REFUSE_NON_INTERACTIVE

    def refusal_message(self, operation: MutationOperation) -> str:
        """Build the error shown when refusing a non-interactive run."""
        return (
            f"'{operation.value}' requires confirmation ({self.risk.value} "
            "risk) but stderr is not an interactive terminal. Re-run with "
            "--yes to confirm unattended execution."
        )
