"""Enforce `search`'s cul-de-sac boundary statically (AST-based).

Mirrors `tests/test_tui_security.py`'s and `tests/test_package_layout.py`'s
approach: compile-time guarantees, not just runtime tests, that a search
result can never reach a mutation. See `.agents/features/search/SPEC.md`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import qbit_core
import qbit_ops
from qbit_ops.cli.app import app
from qbit_ops.cli.commands._shared import CLI_COMMAND_PATH

CORE_DIR = Path(qbit_core.__file__).parent
PACKAGE_DIR = Path(qbit_ops.__file__).parent
TORRENTS_COMMANDS_FILE = PACKAGE_DIR / "cli" / "commands" / "torrents.py"
FEATURES_TORRENTS_FILE = CORE_DIR / "features" / "torrents.py"

# Beyond whatever a module imports directly from `shared.search`, these
# two names are the ones the contract names explicitly -- catches a
# `qbit_core.shared.search.SearchHit` qualified reference too.
_FORBIDDEN_SEARCH_REFERENCES = {"SearchHit", "SearchResults"}

_SEARCH_CALL_PATTERN = re.compile(r"^search_")


def _production_python_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*.py") if "__pycache__" not in path.parts
    )


def _all_imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _names_imported_from(tree: ast.Module, module_name: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module_name:
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def _referenced_names(node: ast.AST) -> set[str]:
    """Every `Name`/`Attribute` identifier reachable from `node`."""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
    return names


# --- Test 1: no plan_*/apply_* function reaches the search engine ---------
#
# The contract states this at module granularity ("aucun module
# définissant un plan_*/apply_* n'importe shared.search ni ne référence
# SearchHit/SearchResults"). That form is unsatisfiable: S5 mandates
# `search_torrents` in `qbit_core.features.torrents`, the same module
# that already defines `plan_bulk_torrent_action`/
# `apply_bulk_torrent_action`, and no `features/search.py` split is
# permitted. A module-granular test would go green through S4 and red
# the moment S5 lands, and the "fix" would be excluding the one module
# where this boundary actually matters. Implemented function-
# granular instead, which preserves the real intent: the mutation
# planning/application functions themselves never reach the search
# engine, even while their sibling `search_torrents` legitimately does.


def test_no_plan_or_apply_function_reaches_the_search_engine() -> None:
    offenders: dict[str, set[str]] = {}
    found_plan_or_apply = False

    # The contract states this unqualified ("aucun module") -- scan both
    # qbit_core and qbit_ops, not just the package that happens to hold
    # today's plan_*/apply_* functions.
    scanned_files = _production_python_files(
        CORE_DIR
    ) + _production_python_files(PACKAGE_DIR)
    for path in scanned_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        forbidden = (
            _names_imported_from(tree, "qbit_core.shared.search")
            | _FORBIDDEN_SEARCH_REFERENCES
        )
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not (
                node.name.startswith("plan_") or node.name.startswith("apply_")
            ):
                continue
            found_plan_or_apply = True
            leaked = _referenced_names(node) & forbidden
            if leaked:
                offenders[f"{path}::{node.name}"] = leaked

    assert found_plan_or_apply, (
        "expected at least one plan_*/apply_* function under qbit_core -- "
        "the scan below is vacuous otherwise"
    )
    assert (
        not offenders
    ), f"plan_*/apply_* functions referencing the search engine: {offenders}"


# --- Test 2: shared/search.py never imports shared/selection.py -----------


def test_shared_search_never_imports_shared_selection() -> None:
    path = CORE_DIR / "shared" / "search.py"
    assert path.is_file()
    modules = _all_imported_modules(path.read_text(encoding="utf-8"))
    leaked = {
        module
        for module in modules
        if module == "qbit_core.shared.selection"
        or module.startswith("qbit_core.shared.selection.")
    }
    assert (
        not leaked
    ), f"shared/search.py must never import shared/selection.py: {leaked}"


# --- Test 3: no mutating torrents command calls a search_* function -------


def _collect_mutating_names(
    typer_instance, prefix: str, mutation_values: set[str], names: set[str]
) -> None:
    """Recurse into every nested sub-typer (e.g. `torrents category set`),
    not just direct subcommands -- a group can itself hold groups (mirrors
    `test_execution.py`'s and `test_errors.py`'s own registry walks)."""
    for command in typer_instance.registered_commands:
        assert command.callback is not None
        command_name = command.name or command.callback.__name__.replace(
            "_", "-"
        )
        if f"{prefix} {command_name}" in mutation_values:
            names.add(command.callback.__name__)
    for group in typer_instance.registered_groups:
        assert group.name is not None
        _collect_mutating_names(
            group.typer_instance,
            f"{prefix} {group.name}",
            mutation_values,
            names,
        )


def _mutating_torrents_command_function_names() -> set[str]:
    """Callback `__name__`s for every registered `torrents` command that
    is a `MutationOperation` -- read from `CLI_COMMAND_PATH`, never a
    hand-copied command list (same source `test_execution.py` uses for
    its own CLI-registration completeness check).
    """
    torrents_group = next(
        group.typer_instance
        for group in app.registered_groups
        if group.name == "torrents"
    )
    assert torrents_group is not None

    mutation_values = set(CLI_COMMAND_PATH.values())
    names: set[str] = set()
    _collect_mutating_names(torrents_group, "torrents", mutation_values, names)
    return names


def _function_defs_by_name(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }


def _search_call_names(func: ast.FunctionDef) -> list[str]:
    """Every `ast.Call` target inside `func` whose name matches `search_*`."""
    found: list[str] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name: str | None = None
        if isinstance(target, ast.Name):
            name = target.id
        elif isinstance(target, ast.Attribute):
            name = target.attr
        if name is not None and _SEARCH_CALL_PATTERN.match(name):
            found.append(name)
    return found


def test_mutating_torrents_commands_never_call_a_search_function() -> None:
    tree = ast.parse(TORRENTS_COMMANDS_FILE.read_text(encoding="utf-8"))
    functions = _function_defs_by_name(tree)

    offenders: dict[str, list[str]] = {}
    for name in _mutating_torrents_command_function_names():
        calls = _search_call_names(functions[name])
        if calls:
            offenders[name] = calls

    assert (
        not offenders
    ), f"mutating torrents commands calling a search_* function: {offenders}"


def test_mutating_torrents_command_discovery_is_non_vacuous() -> None:
    """Non-vacuity companion (a): without this, the test above would
    stay green even if `MutationOperation`-based discovery silently
    stopped finding any command to scan -- the exact failure mode the
    contract names ("si la détection des commandes mutantes se
    cassait"). Pinned to every `torrents` command registered as a
    `MutationOperation` today.
    """
    assert _mutating_torrents_command_function_names() == {
        "pause",
        "resume",
        "start",
        "reannounce",
        "delete",
        "import_torrents",
        "category_set",
        "category_clear",
        "tag_add",
        "tag_remove",
        "throttle",
    }


def test_search_torrents_actually_calls_a_search_function() -> None:
    """Non-vacuity companion (b): proves the `search_*`-call detection
    `test_mutating_torrents_commands_never_call_a_search_function` relies
    on actually fires on real code, not only on a name that happens
    never to occur."""
    tree = ast.parse(TORRENTS_COMMANDS_FILE.read_text(encoding="utf-8"))
    functions = _function_defs_by_name(tree)
    calls = _search_call_names(functions["search_qbit_torrents"])
    assert "search_torrents" in calls


def _called_function_names(func: ast.FunctionDef) -> set[str]:
    """Every callee name in `func`, bare or qualified.

    `ast.Attribute` is included so a qualified call
    (`torrents.select_torrents(...)`) cannot slip past a check written
    against the bare name.
    """
    called: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            called.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
    return called


def test_search_torrents_uses_the_client_free_selection_helper() -> None:
    """Piège 5, statically: `search_torrents` must call
    `select_torrents_from_items`, never `select_torrents`.

    A runtime call-count test cannot tell these two apart here:
    `select_torrents`'s own no-inspection branch degrades to
    `select_torrents_from_items` internally after one `torrents_info()`,
    so swapping the call alone (with `search_torrents`'s own
    `requires_inspection` guard left in place) produces an identical
    `torrents_info`/`torrents_trackers` call count. This is a static,
    name-level pin instead.
    """
    tree = ast.parse(FEATURES_TORRENTS_FILE.read_text(encoding="utf-8"))
    functions = _function_defs_by_name(tree)
    called = _called_function_names(functions["search_torrents"])

    assert "select_torrents_from_items" in called
    assert "select_torrents" not in called


# --- Test 4: mutating surfaces derived from the client protocols ----------
#
# Tests 1 and 3 identify mutating surfaces by name convention
# (`plan_*`/`apply_*`) and by the CLI registry. Neither reaches a domain
# function that mutates without carrying the prefix --
# `pause_torrents_by_hash` and `_apply_bulk_action_to_hashes` both do.
#
# `qbit_core.qbit.protocols` is the business source of truth for what
# mutation *is*: three Protocols declare every client method that
# changes qBittorrent state. A function calling one of them mutates, by
# definition rather than by naming.
#
# What this proves: no production function that calls a mutating client
# method also references the search engine.
#
# What it does NOT prove: that a search result cannot reach a mutation
# through an intermediate variable, a helper, or a second function --
# that is data-flow analysis, deliberately out of scope. This is a
# reference-level guard, not a taint tracker.


def _mutating_client_methods() -> set[str]:
    """Every client method declared by a mutator Protocol."""
    from qbit_core.qbit import protocols

    methods: set[str] = set()
    for name in dir(protocols):
        if not name.endswith("Mutator"):
            continue
        protocol = getattr(protocols, name)
        methods.update(
            attribute
            for attribute in vars(protocol)
            if attribute.startswith("torrents_")
        )
    return methods


def test_the_mutator_protocols_are_a_usable_source_of_truth() -> None:
    """Non-vacuity: if the Protocols were renamed or emptied, the guard
    below would scan for nothing and stay green."""
    methods = _mutating_client_methods()

    assert "torrents_pause" in methods
    assert "torrents_delete" in methods
    assert len(methods) >= 5


def test_no_function_that_mutates_references_the_search_engine() -> None:
    mutating_methods = _mutating_client_methods()
    offenders: dict[str, set[str]] = {}
    scanned = 0

    files = _production_python_files(CORE_DIR) + _production_python_files(
        PACKAGE_DIR
    )
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        forbidden = (
            _names_imported_from(tree, "qbit_core.shared.search")
            | _FORBIDDEN_SEARCH_REFERENCES
        )
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not _called_function_names(node) & mutating_methods:
                continue
            scanned += 1
            leaked = _referenced_names(node) & forbidden
            if leaked:
                offenders[f"{path.name}::{node.name}"] = leaked

    assert scanned, "no mutating function found -- the scan is vacuous"
    assert not offenders, (
        f"functions calling a mutating client method and referencing the "
        f"search engine: {offenders}"
    )
