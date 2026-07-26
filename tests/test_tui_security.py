"""Enforce the TUI's security/layering boundary statically.

Mirrors `tests/test_errors.py`'s AST-based approach: a compile-time
guarantee, not just a runtime test, that TUI modules never import a
raw-secret-producing helper, an out-of-scope mutation function, or the
CLI module -- see docs/TUI_ARCHITECTURE_REVIEW.md §10/§12.

TUI 2 (see docs/DECISIONS.md, "multi-selection + LOW-risk bulk
actions") narrowed, not removed, this boundary: the TUI may now import
exactly `app.torrents.apply_bulk_torrent_action`/
`build_bulk_action_plan_from_snapshot` (Pause/Resume/Reannounce only,
frozen-plan-in, frozen-plan-out, never a rescan, never `--all`).
`plan_bulk_torrent_action` (always rescans and accepts an unbounded
`--all` selector -- neither fits the frozen-plan model) and every
tracker mutation function remain fully forbidden, same as before.
"""

from __future__ import annotations

import ast
from pathlib import Path

import app.tui
import app.tui.app
import app.tui.state

TUI_PACKAGE_DIR = Path(app.tui.__file__).parent

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


def test_tui_never_imports_raw_tracker_helpers_or_unbounded_planner() -> None:
    for path in _tui_module_files():
        imports = _imported_names_by_module(path.read_text(encoding="utf-8"))
        torrents_names = imports.get("app.torrents", set())
        leaked = torrents_names & _FORBIDDEN_TORRENTS_NAMES
        assert not leaked, (
            f"{path} imports forbidden app.torrents names: {leaked} -- "
            "raw tracker details and the always-rescanning/`--all` "
            "planner must never reach the TUI."
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
        found |= imports.get("app.torrents", set()) & (
            _ALLOWED_TORRENTS_MUTATION_NAMES | _FORBIDDEN_TORRENTS_NAMES
        )
    assert found <= _ALLOWED_TORRENTS_MUTATION_NAMES
    assert "apply_bulk_torrent_action" in found
    assert "build_bulk_action_plan_from_snapshot" in found


def test_tui_modules_never_import_tracker_mutation_functions() -> None:
    """Tracker add/remove/replace/passkey-replace remain fully out of
    scope for the TUI -- TUI 2 only ever reaches LOW-risk *torrent*
    bulk actions (Pause/Resume/Reannounce), never a tracker mutation.
    """
    for path in _tui_module_files():
        imports = _imported_names_by_module(path.read_text(encoding="utf-8"))
        trackers_names = imports.get("app.trackers", set())
        leaked = trackers_names & _FORBIDDEN_TRACKERS_NAMES
        assert not leaked, (
            f"{path} imports forbidden app.trackers mutation names: "
            f"{leaked} -- no tracker mutation is reachable from the TUI."
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
