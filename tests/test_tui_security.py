"""Enforce the TUI's security/layering boundary statically.

Mirrors `tests/test_errors.py`'s AST-based approach: a compile-time
guarantee, not just a runtime test, that TUI modules never import a
raw-secret-producing helper, a mutation function, or the CLI module --
see docs/TUI_ARCHITECTURE_REVIEW.md §10/§12.
"""

from __future__ import annotations

import ast
from pathlib import Path

import app.tui
import app.tui.app
import app.tui.state

TUI_PACKAGE_DIR = Path(app.tui.__file__).parent

_FORBIDDEN_TORRENTS_NAMES = {
    "list_torrents_with_trackers",
    "_get_tracker_details",
    "plan_bulk_torrent_action",
    "apply_bulk_torrent_action",
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
    return sorted(TUI_PACKAGE_DIR.glob("*.py"))


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


def test_tui_modules_never_import_app_main() -> None:
    for path in _tui_module_files():
        imports = _imported_names_by_module(path.read_text(encoding="utf-8"))
        assert "app.main" not in imports, (
            f"{path} must never import app.main -- a TUI must not depend "
            "on the CLI module (docs/TUI_ARCHITECTURE_REVIEW.md §12)."
        )


def test_tui_modules_never_import_raw_tracker_helpers() -> None:
    for path in _tui_module_files():
        imports = _imported_names_by_module(path.read_text(encoding="utf-8"))
        torrents_names = imports.get("app.torrents", set())
        leaked = torrents_names & _FORBIDDEN_TORRENTS_NAMES
        assert not leaked, (
            f"{path} imports forbidden app.torrents names: {leaked} -- "
            "raw tracker details and mutation functions must never reach "
            "the TUI."
        )


def test_tui_modules_never_import_mutation_functions() -> None:
    for path in _tui_module_files():
        imports = _imported_names_by_module(path.read_text(encoding="utf-8"))
        trackers_names = imports.get("app.trackers", set())
        leaked = trackers_names & _FORBIDDEN_TRACKERS_NAMES
        assert not leaked, (
            f"{path} imports forbidden app.trackers mutation names: "
            f"{leaked} -- TUI 1 is read-only, no mutation is reachable."
        )


def test_tui_modules_never_import_backup_module() -> None:
    for path in _tui_module_files():
        imports = _imported_names_by_module(path.read_text(encoding="utf-8"))
        assert "app.backup" not in imports, (
            f"{path} must never import app.backup -- backup raw content "
            "is out of scope for TUI 1."
        )


def test_tui_modules_never_import_ui_module() -> None:
    """TUI widgets must never import the CLI's Rich rendering helpers.

    `app.ui` renders for a Rich `Console`/CLI confirmation prompts, not
    a Textual widget tree -- a TUI consuming it would be "scraping
    Rich-rendered output" by another name. Pure formatting duplicated
    locally instead (see `app.tui.app._format_byte_rate`).
    """
    for path in _tui_module_files():
        imports = _imported_names_by_module(path.read_text(encoding="utf-8"))
        assert "app.ui" not in imports, (
            f"{path} must never import app.ui -- see "
            "app.tui.app._format_byte_rate for the sanctioned duplicate."
        )


def test_tui_state_module_never_imports_textual() -> None:
    """The pure state/controller layer must stay independently testable.

    `app/tui/state.py` (unlike `app/tui/app.py`) has no Textual-specific
    concern and must remain importable/testable without a terminal.
    """
    source = Path(app.tui.state.__file__).read_text(encoding="utf-8")
    imports = _imported_names_by_module(source)
    assert not any(
        module == "textual" or module.startswith("textual.")
        for module in imports
    )
