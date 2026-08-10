"""AST-based layering guarantees (R1, R6).

Compile-time guarantees, not just runtime tests -- mirrors
`tests/test_errors.py`'s and `tests/test_tui_security.py`'s AST
approach. This file covers two rules:

- R1: a module declared pure (no I/O, no client) never imports Typer,
  Rich, Textual, or `qbittorrentapi` at module level.
- R6: Textual stays optional -- no production module outside
  `qbit_ops/tui/**` imports it at module level. Only a *deferred* import
  (inside a function body, like `qbit_ops/cli/commands/tui.py`'s `tui`
  command) is allowed; that is the exact mechanism that keeps the `tui`
  extra optional.

Both scans use `Path.rglob()`, not a one-directory `glob()`, so a
future subpackage split (e.g. `qbit_ops/tui/screens/*.py`, or a later
`qbit_ops/domain/`) cannot silently fall outside the scan.
"""

from __future__ import annotations

import ast
from pathlib import Path

import qbit_core
import qbit_ops

APP_PACKAGE_DIR = Path(qbit_ops.__file__).parent
CORE_PACKAGE_DIR = Path(qbit_core.__file__).parent
TUI_PACKAGE_DIR = APP_PACKAGE_DIR / "tui"


def test_canonical_production_root_is_src_qbit_ops() -> None:
    """Guard the scan root itself: it must resolve under `src/qbit_ops`.

    If a future change reintroduced a flat `qbit_ops/` at the repo root
    (or any other layout), every scan above would silently start
    inspecting the wrong tree instead of failing loudly.
    """
    assert APP_PACKAGE_DIR.name == "qbit_ops"
    assert APP_PACKAGE_DIR.parent.name == "src"


def test_canonical_core_root_is_src_qbit_core() -> None:
    """Same guard as above, for the `qbit_core` package."""
    assert CORE_PACKAGE_DIR.name == "qbit_core"
    assert CORE_PACKAGE_DIR.parent.name == "src"


# R1: exactly the modules that must stay pure (no I/O, no client). All
# live under `qbit_core` now (moved out of `qbit_ops`). Not derived by
# scanning, because "pure" is a design claim about these specific
# modules, not a structural property `rglob` could detect on its own.
_PURE_DOMAIN_MODULE_NAMES = (
    "shared/torrent_states.py",
    "shared/selection.py",
    "shared/parsers.py",
    "errors.py",
    "shared/execution.py",
)

_R1_FORBIDDEN_ROOTS = {"typer", "rich", "textual", "qbittorrentapi"}


def _module_level_imported_modules(source: str) -> set[str]:
    """Return every dotted module path imported at *module top level*.

    Unlike `_module_level_imported_roots`, keeps the full dotted path
    (e.g. `qbit_ops.tui.app`, not just `qbit_ops`), so a caller can check
    for a specific submodule rather than only its root package.
    """
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _module_level_imported_roots(source: str) -> set[str]:
    """Return root module names imported at *module top level* only.

    Deliberately restricted to `tree.body` (the module's direct
    statements) rather than `ast.walk`, so an import deferred inside a
    function or method body -- e.g. `qbit_ops/main.py`'s `tui` command
    importing `qbit_ops.tui.app`, the sanctioned mechanism that keeps
    Textual optional -- is never mistaken for a module-level import.
    """
    tree = ast.parse(source)
    roots: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _production_python_files(root: Path) -> list[Path]:
    """Recursively list production `.py` files, excluding build noise."""
    return sorted(
        path for path in root.rglob("*.py") if "__pycache__" not in path.parts
    )


def test_pure_modules_never_import_typer_rich_textual_or_qbittorrentapi() -> (
    None
):
    """R1: rule modules stay importable without Typer, Rich, Textual, or
    qbittorrentapi."""
    checked = 0
    for name in _PURE_DOMAIN_MODULE_NAMES:
        path = CORE_PACKAGE_DIR / name
        assert path.is_file(), f"expected pure module {name} at {path}"
        roots = _module_level_imported_roots(path.read_text(encoding="utf-8"))
        leaked = roots & _R1_FORBIDDEN_ROOTS
        assert not leaked, (
            f"{path} imports {leaked} at module level -- a module "
            "declared pure must never depend on a presentation library "
            "or the qBittorrent client (R1)."
        )
        checked += 1

    assert checked == len(_PURE_DOMAIN_MODULE_NAMES)


def test_textual_import_stays_confined_to_the_tui_package() -> None:
    """R6: no production module outside qbit_ops/tui/** imports Textual at
    module level."""
    files = [
        path
        for path in _production_python_files(APP_PACKAGE_DIR)
        if TUI_PACKAGE_DIR not in path.parents
    ]

    assert (
        files
    ), "expected at least one production module outside qbit_ops/tui/"
    assert any(
        path.name == "app.py" and path.parent.name == "cli" for path in files
    ), (
        "expected qbit_ops/cli/app.py to be part of the scanned set -- an "
        "empty or wrong scan would make this test vacuously pass"
    )

    for path in files:
        roots = _module_level_imported_roots(path.read_text(encoding="utf-8"))
        assert "textual" not in roots, (
            f"{path} imports textual at module level -- Textual must "
            "stay optional; only qbit_ops/tui/** may import it, and only "
            "ever as a deferred import elsewhere (R6)."
        )


def test_deferred_tui_import_in_main_is_not_flagged_as_module_level() -> None:
    """Positive companion: `qbit_ops/cli/commands/tui.py`'s deferred
    Textual import is allowed.

    Proves the module-level/deferred distinction is real:
    `qbit_ops/cli/commands/tui.py` does reference `qbit_ops.tui.app`
    (inside the `tui` command body), yet the recursive scan above does
    not flag it.
    """
    main_source = (APP_PACKAGE_DIR / "cli" / "commands" / "tui.py").read_text(
        encoding="utf-8"
    )

    assert "qbit_ops.tui.app" in main_source
    roots = _module_level_imported_roots(main_source)
    assert "textual" not in roots

    modules = _module_level_imported_modules(main_source)
    assert not any(
        module == "qbit_ops.tui" or module.startswith("qbit_ops.tui.")
        for module in modules
    )


def _all_imported_modules(source: str) -> set[str]:
    """Every imported module at any nesting (module-level or deferred)."""
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_qbit_core_never_imports_qbit_ops_typer_rich_or_textual() -> None:
    files = _production_python_files(CORE_PACKAGE_DIR)
    assert files, "expected at least one production .py file under qbit_core"

    forbidden_roots = {"typer", "rich", "textual"}
    offenders: dict[str, set[str]] = {}
    for path in files:
        modules = _all_imported_modules(path.read_text(encoding="utf-8"))
        leaked = {
            module
            for module in modules
            if module == "qbit_ops"
            or module.startswith("qbit_ops.")
            or module.split(".")[0] in forbidden_roots
        }
        if leaked:
            offenders[str(path)] = leaked

    assert not offenders, f"qbit_core depends on the CLI layer: {offenders}"


def test_production_file_discovery_is_non_empty_and_recursive() -> None:
    """Guard the scan itself: it must actually inspect nested files.

    A `glob("*.py")` that silently stopped descending into `qbit_ops/tui/`
    would make `test_textual_import_stays_confined_to_the_tui_package`
    vacuously pass once a future split adds a subdirectory. This test
    fails loudly instead: it asserts the *full* recursive scan (i.e.
    including `qbit_ops/tui/**`) finds files nested at least one directory
    below `qbit_ops/`.
    """
    all_files = _production_python_files(APP_PACKAGE_DIR)
    nested_files = [
        path for path in all_files if path.parent != APP_PACKAGE_DIR
    ]

    assert all_files, "expected at least one production .py file"
    assert nested_files, (
        "expected at least one production .py file nested below "
        "qbit_ops/ (e.g. qbit_ops/tui/*.py) -- a non-recursive scan "
        "would miss these"
    )
