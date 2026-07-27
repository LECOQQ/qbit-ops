"""Test the shared mutation risk classification and execution policy."""

from pathlib import Path

import pytest

from qbit_ops.execution import (
    MUTATION_RISK,
    ExecutionDecision,
    ExecutionPolicy,
    MutationOperation,
    MutationRisk,
)
from qbit_ops.main import app

COMMANDS_DOC = Path(__file__).resolve().parent.parent / "docs" / "COMMANDS.md"

LOW_RISK_OPERATIONS = [
    MutationOperation.TORRENTS_PAUSE,
    MutationOperation.TORRENTS_RESUME,
    MutationOperation.TORRENTS_START,
    MutationOperation.TORRENTS_REANNOUNCE,
]
MEDIUM_RISK_OPERATIONS = [MutationOperation.TRACKERS_ADD_IF_PRESENT]
HIGH_RISK_OPERATIONS = [
    MutationOperation.TRACKERS_REMOVE,
    MutationOperation.TRACKERS_REPLACE,
    MutationOperation.TRACKERS_REPLACE_PASSKEY,
]


# --- Risk classification completeness ---------------------------------


def test_mutation_risk_covers_every_mutation_operation() -> None:
    """Ensure the risk table classifies every declared mutation operation."""
    assert set(MUTATION_RISK) == set(MutationOperation)


@pytest.mark.parametrize("operation", LOW_RISK_OPERATIONS)
def test_torrent_bulk_actions_are_low_risk(
    operation: MutationOperation,
) -> None:
    """Ensure bulk torrent actions are classified as low risk."""
    assert MUTATION_RISK[operation] is MutationRisk.LOW


@pytest.mark.parametrize("operation", MEDIUM_RISK_OPERATIONS)
def test_add_if_present_is_medium_risk(operation: MutationOperation) -> None:
    """Ensure conditional tracker addition is classified as medium risk."""
    assert MUTATION_RISK[operation] is MutationRisk.MEDIUM


@pytest.mark.parametrize("operation", HIGH_RISK_OPERATIONS)
def test_destructive_tracker_operations_are_high_risk(
    operation: MutationOperation,
) -> None:
    """Ensure remove/replace/replace-passkey are classified as high risk."""
    assert MUTATION_RISK[operation] is MutationRisk.HIGH


def test_mutation_risk_has_no_unclassified_extra_entries() -> None:
    """Ensure the risk table does not silently carry stale entries."""
    assert len(MUTATION_RISK) == len(MutationOperation)


def test_every_mutation_operation_is_registered_as_a_cli_command() -> None:
    """Ensure every classified operation maps to a real Typer command.

    Prevents documentation/CLI registration from silently omitting (or
    misnaming) a mutation command relative to the risk table.
    """
    registered: set[str] = set()
    for group_name in ("torrents", "trackers"):
        group = next(
            group.typer_instance
            for group in app.registered_groups
            if group.name == group_name
        )
        assert group is not None
        for command in group.registered_commands:
            assert command.callback is not None
            command_name = command.name or command.callback.__name__.replace(
                "_", "-"
            )
            registered.add(f"{group_name} {command_name}")

    for operation in MutationOperation:
        assert operation.value in registered, operation.value


def test_docs_commands_reference_mentions_every_risk_tier() -> None:
    """Ensure docs/COMMANDS.md documents all three risk tiers by name."""
    text = COMMANDS_DOC.read_text(encoding="utf-8")
    for risk in MutationRisk:
        assert risk.value in text.lower()


# --- ExecutionPolicy decision table -------------------------------------


def _policy(
    *,
    dry_run: bool,
    assume_yes: bool = False,
    interactive: bool = False,
    risk: MutationRisk,
) -> ExecutionPolicy:
    return ExecutionPolicy(
        dry_run=dry_run,
        assume_yes=assume_yes,
        interactive=interactive,
        risk=risk,
    )


@pytest.mark.parametrize("risk", list(MutationRisk))
def test_dry_run_always_previews_regardless_of_risk(risk: MutationRisk) -> None:
    """Ensure --dry-run (the default) never mutates or prompts, any risk."""
    policy = _policy(dry_run=True, risk=risk)
    assert policy.decide() is ExecutionDecision.PREVIEW_ONLY
    assert policy.mutation_requested is False


def test_low_risk_never_requires_confirmation() -> None:
    """Ensure low risk applies immediately without a prompt."""
    policy = _policy(dry_run=False, interactive=True, risk=MutationRisk.LOW)
    assert policy.requires_confirmation is False
    assert policy.decide() is ExecutionDecision.APPLY_WITHOUT_PROMPT


def test_low_risk_applies_without_prompt_even_non_interactive() -> None:
    """Ensure low risk never blocks on non-interactive execution."""
    policy = _policy(dry_run=False, interactive=False, risk=MutationRisk.LOW)
    assert policy.decide() is ExecutionDecision.APPLY_WITHOUT_PROMPT


@pytest.mark.parametrize("risk", [MutationRisk.MEDIUM, MutationRisk.HIGH])
def test_medium_and_high_risk_prompt_when_interactive(
    risk: MutationRisk,
) -> None:
    """Ensure medium/high risk require a prompt in an interactive terminal."""
    policy = _policy(dry_run=False, interactive=True, risk=risk)
    assert policy.requires_confirmation is True
    assert policy.decide() is ExecutionDecision.APPLY_WITH_PROMPT


@pytest.mark.parametrize("risk", [MutationRisk.MEDIUM, MutationRisk.HIGH])
def test_medium_and_high_risk_refuse_when_non_interactive(
    risk: MutationRisk,
) -> None:
    """Ensure medium/high risk refuse instead of hanging non-interactively."""
    policy = _policy(dry_run=False, interactive=False, risk=risk)
    assert policy.decide() is ExecutionDecision.REFUSE_NON_INTERACTIVE


@pytest.mark.parametrize("risk", [MutationRisk.MEDIUM, MutationRisk.HIGH])
@pytest.mark.parametrize("interactive", [True, False])
def test_assume_yes_always_skips_confirmation(
    risk: MutationRisk,
    interactive: bool,
) -> None:
    """Ensure --yes applies without a prompt, interactive or not."""
    policy = _policy(
        dry_run=False, assume_yes=True, interactive=interactive, risk=risk
    )
    assert policy.decide() is ExecutionDecision.APPLY_WITHOUT_PROMPT


def test_assume_yes_never_implies_no_dry_run() -> None:
    """Ensure --yes alone (still dry-run) never triggers a mutation."""
    policy = _policy(dry_run=True, assume_yes=True, risk=MutationRisk.HIGH)
    assert policy.decide() is ExecutionDecision.PREVIEW_ONLY
    assert policy.mutation_requested is False


def test_refusal_message_never_leaks_and_names_the_operation() -> None:
    """Ensure the refusal message names the operation and suggests --yes."""
    policy = _policy(dry_run=False, interactive=False, risk=MutationRisk.HIGH)
    message = policy.refusal_message(MutationOperation.TRACKERS_REMOVE)
    assert "trackers remove" in message
    assert "--yes" in message
