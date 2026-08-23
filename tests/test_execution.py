"""Test the shared mutation risk classification and execution policy."""

from pathlib import Path
from typing import Any

import pytest
from typer.main import get_command

from qbit_core.shared.execution import (
    MUTATION_RISK,
    ExecutionDecision,
    ExecutionPolicy,
    MutationOperation,
    MutationRisk,
)
from qbit_ops.cli.app import app
from qbit_ops.cli.commands._shared import CLI_COMMAND_PATH

COMMANDS_DOC = Path(__file__).resolve().parent.parent / "docs" / "COMMANDS.md"

LOW_RISK_OPERATIONS = [
    MutationOperation.TORRENTS_PAUSE,
    MutationOperation.TORRENTS_RESUME,
    MutationOperation.TORRENTS_START,
    MutationOperation.TORRENTS_REANNOUNCE,
    MutationOperation.TORRENTS_CATEGORY_SET,
    MutationOperation.TORRENTS_CATEGORY_CLEAR,
    MutationOperation.TORRENTS_TAG_ADD,
    MutationOperation.TORRENTS_TAG_REMOVE,
]
MEDIUM_RISK_OPERATIONS = [
    MutationOperation.TRACKERS_ADD_IF_PRESENT,
    MutationOperation.TORRENTS_IMPORT,
    MutationOperation.BACKUP_RESTORE,
]
HIGH_RISK_OPERATIONS = [
    MutationOperation.TRACKERS_REMOVE,
    MutationOperation.TRACKERS_REPLACE,
    MutationOperation.TRACKERS_REPLACE_PASSKEY,
    MutationOperation.TORRENTS_DELETE,
]


# --- Risk classification completeness ---------------------------------


def test_mutation_risk_covers_every_mutation_operation() -> None:
    assert set(MUTATION_RISK) == set(MutationOperation)


@pytest.mark.parametrize("operation", LOW_RISK_OPERATIONS)
def test_torrent_bulk_actions_are_low_risk(
    operation: MutationOperation,
) -> None:
    assert MUTATION_RISK[operation] is MutationRisk.LOW


@pytest.mark.parametrize("operation", MEDIUM_RISK_OPERATIONS)
def test_medium_risk_operations_are_classified_medium(
    operation: MutationOperation,
) -> None:
    assert MUTATION_RISK[operation] is MutationRisk.MEDIUM


@pytest.mark.parametrize("operation", HIGH_RISK_OPERATIONS)
def test_destructive_operations_are_high_risk(
    operation: MutationOperation,
) -> None:
    assert MUTATION_RISK[operation] is MutationRisk.HIGH


def test_mutation_risk_has_no_unclassified_extra_entries() -> None:
    assert len(MUTATION_RISK) == len(MutationOperation)


def test_cli_command_path_covers_every_mutation_operation() -> None:
    """`CLI_COMMAND_PATH` (the CLI's own spelling for each operation) must
    stay as complete as `MUTATION_RISK` -- a mutation classified for risk
    but missing its CLI path would crash `run_mutation`'s refusal path
    instead of failing here."""
    assert set(CLI_COMMAND_PATH) == set(MutationOperation)


def _collect_commands(
    typer_instance, prefix: str, registered: set[str]
) -> None:
    """Recurse into every nested sub-typer (e.g. `torrents category set`),
    not just direct subcommands -- a group can itself hold groups."""
    for command in typer_instance.registered_commands:
        assert command.callback is not None
        command_name = command.name or command.callback.__name__.replace(
            "_", "-"
        )
        registered.add(f"{prefix} {command_name}")
    for group in typer_instance.registered_groups:
        assert group.name is not None
        _collect_commands(
            group.typer_instance, f"{prefix} {group.name}", registered
        )


def test_every_mutation_operation_is_registered_as_a_cli_command() -> None:
    """Prevents documentation/CLI registration from silently omitting (or
    misnaming) a mutation command relative to the risk table.
    """
    registered: set[str] = set()
    for group_name in ("torrents", "trackers", "backup"):
        group = next(
            group.typer_instance
            for group in app.registered_groups
            if group.name == group_name
        )
        assert group is not None
        _collect_commands(group, group_name, registered)

    for operation in MutationOperation:
        assert CLI_COMMAND_PATH[operation] in registered, operation


def _dry_run_capable_commands() -> set[str]:
    """Every registered command id whose CLI offers `--dry-run`."""
    found: set[str] = set()

    def _walk(command: Any, prefix: str) -> None:
        children = getattr(command, "commands", None)
        if children:
            for name, child in children.items():
                _walk(child, f"{prefix} {name}".strip())
            return
        options: set[str] = set()
        for parameter in command.params:
            options.update(parameter.opts)
        if "--dry-run" in options:
            found.add(prefix)

    _walk(get_command(app), "")
    return found


def test_only_classified_mutations_offer_the_dry_run_control() -> None:
    """The membership rule for `MUTATION_RISK`, checked against the real
    CLI rather than restated.

    A command that can preview before applying is a mutation and must
    carry a risk tier; `init` offers no `--dry-run` because it changes
    nothing on the instance, and is therefore absent from both sides.
    """
    offering = _dry_run_capable_commands()

    assert offering, "expected at least one command offering --dry-run"
    assert offering == set(CLI_COMMAND_PATH.values())
    assert "init" not in offering


def test_docs_commands_reference_mentions_every_risk_tier() -> None:
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
    policy = _policy(dry_run=True, risk=risk)
    assert policy.decide() is ExecutionDecision.PREVIEW_ONLY
    assert policy.mutation_requested is False


def test_low_risk_never_requires_confirmation() -> None:
    policy = _policy(dry_run=False, interactive=True, risk=MutationRisk.LOW)
    assert policy.requires_confirmation is False
    assert policy.decide() is ExecutionDecision.APPLY_WITHOUT_PROMPT


def test_low_risk_applies_without_prompt_even_non_interactive() -> None:
    policy = _policy(dry_run=False, interactive=False, risk=MutationRisk.LOW)
    assert policy.decide() is ExecutionDecision.APPLY_WITHOUT_PROMPT


@pytest.mark.parametrize("risk", [MutationRisk.MEDIUM, MutationRisk.HIGH])
def test_medium_and_high_risk_prompt_when_interactive(
    risk: MutationRisk,
) -> None:
    policy = _policy(dry_run=False, interactive=True, risk=risk)
    assert policy.requires_confirmation is True
    assert policy.decide() is ExecutionDecision.APPLY_WITH_PROMPT


@pytest.mark.parametrize("risk", [MutationRisk.MEDIUM, MutationRisk.HIGH])
def test_medium_and_high_risk_refuse_when_non_interactive(
    risk: MutationRisk,
) -> None:
    policy = _policy(dry_run=False, interactive=False, risk=risk)
    assert policy.decide() is ExecutionDecision.REFUSE_NON_INTERACTIVE


@pytest.mark.parametrize("risk", [MutationRisk.MEDIUM, MutationRisk.HIGH])
@pytest.mark.parametrize("interactive", [True, False])
def test_assume_yes_always_skips_confirmation(
    risk: MutationRisk,
    interactive: bool,
) -> None:
    policy = _policy(
        dry_run=False, assume_yes=True, interactive=interactive, risk=risk
    )
    assert policy.decide() is ExecutionDecision.APPLY_WITHOUT_PROMPT


def test_assume_yes_never_implies_no_dry_run() -> None:
    policy = _policy(dry_run=True, assume_yes=True, risk=MutationRisk.HIGH)
    assert policy.decide() is ExecutionDecision.PREVIEW_ONLY
    assert policy.mutation_requested is False


def test_refusal_message_never_leaks_and_names_the_operation() -> None:
    policy = _policy(dry_run=False, interactive=False, risk=MutationRisk.HIGH)
    message = policy.refusal_message(
        CLI_COMMAND_PATH[MutationOperation.TRACKERS_REMOVE]
    )
    assert "trackers remove" in message
    assert "--yes" in message
