"""Enforce the TUI's security/layering boundary statically.

Mirrors `tests/test_errors.py`'s AST-based approach: a compile-time
guarantee, not just a runtime test, that TUI modules never import a
raw-secret-producing helper, an out-of-scope mutation function, or the
CLI module. The boundary is stated in `qbit_ops.tui.app`'s module docstring.

TUI 2 narrowed, not removed, this boundary: the TUI may import
exactly `qbit_core.features.torrents.apply_bulk_torrent_action`/
`build_bulk_action_plan_from_snapshot` -- frozen-plan-in, frozen-plan-
out, never a rescan, never `--all`. `tui-filters` widened the *actions*
those two functions cover from Pause/Resume/Reannounce to seven
(category/tags/throttle too, all still `MutationRisk.LOW`); the two
function names allowed through this boundary did not change.
`plan_bulk_torrent_action` (always rescans and accepts an unbounded
`--all` selector -- neither fits the frozen-plan model) and every
tracker mutation function remain fully forbidden, same as before.
"""

from __future__ import annotations

import ast
from pathlib import Path

import qbit_ops.tui
import qbit_ops.tui.app
import qbit_ops.tui.state

TUI_PACKAGE_DIR = Path(qbit_ops.tui.__file__).parent

_ALLOWED_TORRENTS_MUTATION_NAMES = {
    "apply_bulk_torrent_action",
    "build_bulk_action_plan_from_snapshot",
}
_FORBIDDEN_TORRENTS_NAMES = {
    "list_torrents_with_trackers",
    "_get_tracker_details",
    "plan_bulk_torrent_action",
}
_FORBIDDEN_TRACKERS_NAMES = {
    "plan_tracker_addition",
    "apply_tracker_addition",
    "plan_tracker_removal",
    "apply_tracker_removal",
    "plan_tracker_replacement",
    "apply_tracker_replacement",
    "plan_tracker_passkey_replacement",
    "apply_tracker_passkey_replacement",
}


def _tui_module_files() -> list[Path]:
    """List every TUI production module, including future subdirectories.

    Uses `rglob`, not a one-directory `glob`, so a future split of
    `qbit_ops/tui/app.py` into `qbit_ops/tui/widgets/*.py` and
    `qbit_ops/tui/screens/*.py` cannot make
    this security boundary silently vacuous by leaving new files
    unscanned.
    """
    files = sorted(
        path
        for path in TUI_PACKAGE_DIR.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    assert files, f"expected at least one .py file under {TUI_PACKAGE_DIR}"
    return files


def _imported_names_by_module(source: str) -> dict[str, set[str]]:
    """Map each imported module to the names imported from it."""
    tree = ast.parse(source)
    imports: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.setdefault(alias.name, set())
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.setdefault(node.module, set()).update(
                alias.name for alias in node.names
            )
    return imports


def test_tui_modules_never_import_the_cli_package() -> None:
    """A TUI module must never depend on the CLI layer.

    Updated for the CLI reorganization (see docs/ARCHITECTURE.md):
    `qbit_ops.main` no longer exists -- the equivalent, and now broader,
    boundary is that no TUI module may import `qbit_ops.cli` or any of
    its submodules (`cli.app`, `cli.commands.*`, `cli.rendering`,
    `cli.error_boundary`, `cli.validation`) -- see docs/ARCHITECTURE.md
    §4.
    """
    for path in _tui_module_files():
        imports = _imported_names_by_module(path.read_text(encoding="utf-8"))
        leaked = {
            module
            for module in imports
            if module == "qbit_ops.cli" or module.startswith("qbit_ops.cli.")
        }
        assert not leaked, (
            f"{path} must never import the CLI package ({leaked}) -- a TUI "
            "must not depend on the CLI layer "
            "(see `qbit_ops.tui.app`'s module docstring)."
        )


def test_tui_never_imports_raw_tracker_helpers_or_unbounded_planner() -> None:
    for path in _tui_module_files():
        imports = _imported_names_by_module(path.read_text(encoding="utf-8"))
        torrents_names = imports.get("qbit_core.features.torrents", set())
        leaked = torrents_names & _FORBIDDEN_TORRENTS_NAMES
        assert not leaked, (
            f"{path} imports forbidden qbit_core.features.torrents names: "
            f"{leaked} -- raw tracker details and the "
            "always-rescanning/`--all` planner must never reach the TUI."
        )


def test_tui_may_only_import_the_two_low_risk_bulk_mutation_functions() -> None:
    """Positive companion to the negative test above: confirm the TUI's
    only reachable torrent-mutation surface is exactly the two LOW-risk,
    frozen-plan functions TUI 2 needs -- not zero (TUI 1's invariant),
    and not more than these two.
    """
    found: set[str] = set()
    for path in _tui_module_files():
        imports = _imported_names_by_module(path.read_text(encoding="utf-8"))
        found |= imports.get("qbit_core.features.torrents", set()) & (
            _ALLOWED_TORRENTS_MUTATION_NAMES | _FORBIDDEN_TORRENTS_NAMES
        )
    assert found <= _ALLOWED_TORRENTS_MUTATION_NAMES
    assert "apply_bulk_torrent_action" in found
    assert "build_bulk_action_plan_from_snapshot" in found


def test_tui_modules_never_import_tracker_mutation_functions() -> None:
    """Tracker add/remove/replace/passkey-replace remain fully out of
    scope for the TUI -- it only ever reaches LOW-risk *torrent* bulk
    actions (pause/resume/reannounce/category/tags/throttle), never a
    tracker mutation.
    """
    for path in _tui_module_files():
        imports = _imported_names_by_module(path.read_text(encoding="utf-8"))
        trackers_names = imports.get("qbit_core.features.trackers", set())
        leaked = trackers_names & _FORBIDDEN_TRACKERS_NAMES
        assert not leaked, (
            f"{path} imports forbidden qbit_core.features.trackers "
            f"mutation names: {leaked} -- no tracker mutation is "
            "reachable from the TUI."
        )


def test_tui_modules_never_import_backup_module() -> None:
    for path in _tui_module_files():
        imports = _imported_names_by_module(path.read_text(encoding="utf-8"))
        assert "qbit_core.features.backup" not in imports, (
            f"{path} must never import qbit_core.features.backup -- "
            "backup raw content is out of scope for TUI 1."
        )


def test_tui_modules_never_import_ui_module() -> None:
    """TUI widgets must never import the CLI's Rich rendering helpers.

    `qbit_ops.cli.rendering` (formerly `qbit_ops.ui`) renders for a Rich
    `Console`/CLI confirmation prompts, not a Textual widget tree -- a
    TUI consuming it would be "scraping Rich-rendered output" by
    another name. Pure formatting duplicated locally instead (see
    `qbit_ops.tui.app._format_byte_rate`). Subsumed by, but kept
    alongside, the broader `test_tui_modules_never_import_the_cli_package`
    check above for an explicit, positive pin on this specific module.
    """
    for path in _tui_module_files():
        imports = _imported_names_by_module(path.read_text(encoding="utf-8"))
        assert "qbit_ops.cli.rendering" not in imports, (
            f"{path} must never import qbit_ops.cli.rendering -- see "
            "qbit_ops.tui.app._format_byte_rate for the sanctioned duplicate."
        )


_CONNECTION_SETUP_MODULE = "qbit_core.features.connection_setup"
_DOMAIN_WRITER = "write_connection_env_file"
# Every way a module can put bytes on disk itself. A TUI module that
# called one of these would be a second implementation of the file
# mode and of the precedence warning -- the two things a divergence
# would land on.
_FILE_WRITING_CALLS = {"open", "write_text", "write_bytes", "mkdir"}


def _called_names(source: str) -> set[str]:
    """Every called function/method name, at any nesting."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def test_the_tui_writes_its_configuration_only_through_the_shared_domain() -> (
    None
):
    """The TUI's one on-disk write goes through `qbit_core`, never
    through a file call of its own.

    Non-vacuous by construction: the same scan must also *find* the
    domain writer imported, so an empty or mis-rooted scan fails here
    instead of passing quietly.
    """
    importers: list[Path] = []
    offenders: dict[str, set[str]] = {}
    for path in _tui_module_files():
        source = path.read_text(encoding="utf-8")
        imports = _imported_names_by_module(source)
        if _DOMAIN_WRITER in imports.get(_CONNECTION_SETUP_MODULE, set()):
            importers.append(path)
        leaked = _called_names(source) & _FILE_WRITING_CALLS
        if leaked:
            offenders[str(path)] = leaked

    assert len(importers) == 1, (
        f"expected exactly one TUI module to import {_DOMAIN_WRITER}, "
        f"found {[str(path) for path in importers]}"
    )
    assert not offenders, (
        f"TUI modules writing files themselves: {offenders} -- the file "
        f"mode and the precedence warning live in {_CONNECTION_SETUP_MODULE}, "
        "and a second writer would diverge from them."
    )


def test_tui_module_discovery_is_recursive(tmp_path: Path) -> None:
    """Prove a nested file survives the same discovery pattern
    `_tui_module_files` uses.

    Builds a synthetic `tui/` tree with a `screens/` subdirectory --
    a plausible future shape for `qbit_ops/tui/app.py`'s modal screens.
    A one-directory
    `glob("*.py")` would miss `screens/help.py` here and make every
    security assertion above silently vacuous once that split happens;
    `rglob` must not.
    """
    root = tmp_path / "tui"
    (root / "screens").mkdir(parents=True)
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    (root / "screens" / "help.py").write_text("y = 2\n", encoding="utf-8")

    discovered = sorted(
        path for path in root.rglob("*.py") if "__pycache__" not in path.parts
    )

    assert root / "screens" / "help.py" in discovered
    assert len(discovered) == 2


def test_tui_state_module_never_imports_textual() -> None:
    """The pure state/controller layer must stay independently testable.

    `qbit_ops/tui/state.py` (unlike `qbit_ops/tui/app.py`) has no
    Textual-specific concern and must remain importable/testable
    without a terminal.
    """
    source = Path(qbit_ops.tui.state.__file__).read_text(encoding="utf-8")
    imports = _imported_names_by_module(source)
    assert not any(
        module == "textual" or module.startswith("textual.")
        for module in imports
    )
