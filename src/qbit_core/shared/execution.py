"""Shared mutation risk classification and execution policy.

Every mutation this project can apply is classified into exactly one
`MutationRisk` tier and goes through the same `ExecutionPolicy.decide()`
logic to determine whether it previews, applies silently, prompts for
confirmation, or refuses. See `MutationOperation` for why the
classification stays CLI-agnostic.
"""

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "ExecutionDecision",
    "ExecutionPolicy",
    "MUTATION_RISK",
    "MutationOperation",
    "MutationStatus",
]


class MutationRisk(StrEnum):
    """How much damage a mutation can do, and how much friction it needs."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MutationOperation(StrEnum):
    """Every kind of mutation this project can apply to a qBittorrent
    instance -- one member per distinct blast radius, not per interface.
    "Mutating" means changing the *qBittorrent instance*; writing only
    qbit-ops' own configuration file (`init`) is out of scope.

    Values are plain symbolic identifiers, never a CLI invocation path
    -- this module stays usable by any surface (TUI, MCP, a future one)
    that only needs to ask "how risky is this?" without knowing how the
    CLI spells its commands. The CLI's own spelling for each of these
    (e.g. "torrents pause") lives in
    `qbit_ops.cli.commands._shared.CLI_COMMAND_PATH`, the one place that
    correspondence belongs.
    """

    TORRENTS_PAUSE = "torrents_pause"
    TORRENTS_RESUME = "torrents_resume"
    TORRENTS_START = "torrents_start"
    TORRENTS_REANNOUNCE = "torrents_reannounce"
    TORRENTS_DELETE = "torrents_delete"
    TORRENTS_IMPORT = "torrents_import"
    TORRENTS_CATEGORY_SET = "torrents_category_set"
    TORRENTS_CATEGORY_CLEAR = "torrents_category_clear"
    TORRENTS_TAG_ADD = "torrents_tag_add"
    TORRENTS_TAG_REMOVE = "torrents_tag_remove"
    TORRENTS_THROTTLE = "torrents_throttle"
    BACKUP_RESTORE = "backup_restore"
    TRACKERS_ADD_IF_PRESENT = "trackers_add_if_present"
    TRACKERS_REMOVE = "trackers_remove"
    TRACKERS_REPLACE = "trackers_replace"
    TRACKERS_REPLACE_PASSKEY = "trackers_replace_passkey"


# The single source of truth for risk classification. Tests assert this
# dict covers every mutating command registered on the Typer app, so a
# new mutation command cannot silently ship unclassified.
MUTATION_RISK: dict[MutationOperation, MutationRisk] = {
    MutationOperation.TORRENTS_PAUSE: MutationRisk.LOW,
    MutationOperation.TORRENTS_RESUME: MutationRisk.LOW,
    MutationOperation.TORRENTS_START: MutationRisk.LOW,
    MutationOperation.TORRENTS_REANNOUNCE: MutationRisk.LOW,
    MutationOperation.TORRENTS_DELETE: MutationRisk.HIGH,
    MutationOperation.TORRENTS_IMPORT: MutationRisk.MEDIUM,
    MutationOperation.TORRENTS_CATEGORY_SET: MutationRisk.LOW,
    MutationOperation.TORRENTS_CATEGORY_CLEAR: MutationRisk.LOW,
    MutationOperation.TORRENTS_TAG_ADD: MutationRisk.LOW,
    MutationOperation.TORRENTS_TAG_REMOVE: MutationRisk.LOW,
    MutationOperation.TORRENTS_THROTTLE: MutationRisk.LOW,
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

        Callers must check for an empty/no-match/invalid plan themselves --
        those never reach a decision (and never prompt) regardless of policy.
        """
        if self.dry_run:
            return ExecutionDecision.PREVIEW_ONLY

        if not self.requires_confirmation or self.assume_yes:
            return ExecutionDecision.APPLY_WITHOUT_PROMPT

        if self.interactive:
            return ExecutionDecision.APPLY_WITH_PROMPT

        return ExecutionDecision.REFUSE_NON_INTERACTIVE

    def refusal_message(self, operation_label: str) -> str:
        """Build the error shown when refusing a non-interactive run.

        `operation_label` is the caller's own display spelling for the
        operation (the CLI passes `CLI_COMMAND_PATH[operation]`); this
        module has no opinion on that spelling.
        """
        return (
            f"'{operation_label}' requires confirmation ({self.risk.value} "
            "risk) but stderr is not an interactive terminal. Re-run with "
            "--yes to confirm unattended execution."
        )
