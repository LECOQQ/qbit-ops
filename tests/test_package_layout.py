"""Static guarantees for the `features/`/`shared/` package split.

Mirrors `tests/test_layering.py`'s and `tests/test_cli_architecture.py`'s
AST approach: compile-time guarantees, not just runtime tests, for the
claims the package-cleanup phase makes about dependency direction. See
docs/ARCHITECTURE.md for the strict meaning of `features/` and
`shared/`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import qbit_ops

PACKAGE_DIR = Path(qbit_ops.__file__).parent
FEATURES_DIR = PACKAGE_DIR / "features"
SHARED_DIR = PACKAGE_DIR / "shared"
QBIT_DIR = PACKAGE_DIR / "qbit"
CLI_DIR = PACKAGE_DIR / "cli"
TUI_DIR = PACKAGE_DIR / "tui"

# The exact modules this phase moved -- a fixed baseline (not derived
# from a scan), so a regression has something concrete to be caught
# against, mirroring `tests/test_tui_architecture.py`'s
# `_CANONICAL_HOME` pattern.
_FEATURE_MODULES = {
    "backup",
    "doctor",
    "explain",
    "status",
    "torrents",
    "trackers",
    "tracker_status",
}
_SHARED_MODULES = {"execution", "selectors", "torrent_states"}
_GENERIC_DUMPING_GROUND_NAMES = {"utils.py", "helpers.py", "common.py"}


def _production_python_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*.py") if "__pycache__" not in path.parts
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


def test_features_and_shared_directories_are_both_non_empty() -> None:
    feature_files = _production_python_files(FEATURES_DIR)
    shared_files = _production_python_files(SHARED_DIR)
    assert {p.stem for p in feature_files} >= _FEATURE_MODULES
    assert {p.stem for p in shared_files} >= _SHARED_MODULES


def test_each_moved_module_exists_in_exactly_one_canonical_location() -> None:
    """No module was left duplicated at the old root path after the move."""
    for name in _FEATURE_MODULES:
        assert (FEATURES_DIR / f"{name}.py").is_file(), name
        assert not (PACKAGE_DIR / f"{name}.py").exists(), (
            f"{name}.py still exists at the package root -- "
            "it should only exist under features/"
        )
    for name in _SHARED_MODULES:
        assert (SHARED_DIR / f"{name}.py").is_file(), name
        assert not (PACKAGE_DIR / f"{name}.py").exists(), (
            f"{name}.py still exists at the package root -- "
            "it should only exist under shared/"
        )


def test_no_generic_dumping_ground_module_exists() -> None:
    """`shared/` is not a generic dumping ground: no `utils.py`,
    `helpers.py`, or `common.py` anywhere under the package."""
    offenders = [
        path
        for path in _production_python_files(PACKAGE_DIR)
        if path.name in _GENERIC_DUMPING_GROUND_NAMES
    ]
    assert not offenders, f"generic dumping-ground modules found: {offenders}"


def test_cli_and_tui_may_import_features() -> None:
    """Positive companion: prove the scan below isn't vacuous by
    confirming at least one CLI module and (indirectly, via a feature
    import) the TUI actually depend on `features/`."""
    cli_files = _production_python_files(CLI_DIR)
    found_cli_feature_import = False
    for path in cli_files:
        modules = _all_imported_modules(path.read_text(encoding="utf-8"))
        if any(m.startswith("qbit_ops.features.") for m in modules):
            found_cli_feature_import = True
            break
    assert (
        found_cli_feature_import
    ), "expected at least one cli/ -> features/ import"

    tui_files = _production_python_files(TUI_DIR)
    found_tui_feature_import = False
    for path in tui_files:
        modules = _all_imported_modules(path.read_text(encoding="utf-8"))
        if any(m.startswith("qbit_ops.features.") for m in modules):
            found_tui_feature_import = True
            break
    assert (
        found_tui_feature_import
    ), "expected at least one tui/ -> features/ import"


def test_feature_modules_never_import_cli_or_tui() -> None:
    files = _production_python_files(FEATURES_DIR)
    assert files
    offenders: dict[str, set[str]] = {}
    for path in files:
        modules = _all_imported_modules(path.read_text(encoding="utf-8"))
        leaked = {
            m
            for m in modules
            if m == "qbit_ops.cli"
            or m.startswith("qbit_ops.cli.")
            or m == "qbit_ops.tui"
            or m.startswith("qbit_ops.tui.")
        }
        if leaked:
            offenders[str(path)] = leaked
    assert not offenders, f"features/ importing cli/tui: {offenders}"


def test_shared_modules_never_import_features_cli_or_tui() -> None:
    files = _production_python_files(SHARED_DIR)
    assert files
    offenders: dict[str, set[str]] = {}
    for path in files:
        modules = _all_imported_modules(path.read_text(encoding="utf-8"))
        leaked = {
            m
            for m in modules
            if m == "qbit_ops.features"
            or m.startswith("qbit_ops.features.")
            or m == "qbit_ops.cli"
            or m.startswith("qbit_ops.cli.")
            or m == "qbit_ops.tui"
            or m.startswith("qbit_ops.tui.")
        }
        if leaked:
            offenders[str(path)] = leaked
    assert not offenders, f"shared/ importing features/cli/tui: {offenders}"


def test_qbit_boundary_modules_never_import_features_cli_or_tui() -> None:
    files = _production_python_files(QBIT_DIR)
    assert files
    offenders: dict[str, set[str]] = {}
    for path in files:
        modules = _all_imported_modules(path.read_text(encoding="utf-8"))
        leaked = {
            m
            for m in modules
            if m == "qbit_ops.features"
            or m.startswith("qbit_ops.features.")
            or m == "qbit_ops.cli"
            or m.startswith("qbit_ops.cli.")
            or m == "qbit_ops.tui"
            or m.startswith("qbit_ops.tui.")
        }
        if leaked:
            offenders[str(path)] = leaked
    assert not offenders, f"qbit/ importing features/cli/tui: {offenders}"


def test_no_executable_import_references_the_old_root_feature_paths() -> None:
    """No production or test module imports a moved feature module from
    its old root path (`qbit_ops.torrents`, not
    `qbit_ops.features.torrents`) -- this scan is over executable
    imports only, via AST, never grep over prose."""
    files = _production_python_files(PACKAGE_DIR) + _production_python_files(
        Path(qbit_ops.__file__).parent.parent.parent / "tests"
    )
    assert files
    offenders: dict[str, set[str]] = {}
    for path in files:
        modules = _all_imported_modules(path.read_text(encoding="utf-8"))
        leaked = {
            m
            for m in modules
            if any(
                m == f"qbit_ops.{name}" or m.startswith(f"qbit_ops.{name}.")
                for name in _FEATURE_MODULES
            )
        }
        if leaked:
            offenders[str(path)] = leaked
    assert not offenders, f"old root feature-path imports found: {offenders}"


def test_no_executable_import_references_the_old_root_shared_paths() -> None:
    files = _production_python_files(PACKAGE_DIR) + _production_python_files(
        Path(qbit_ops.__file__).parent.parent.parent / "tests"
    )
    assert files
    offenders: dict[str, set[str]] = {}
    for path in files:
        modules = _all_imported_modules(path.read_text(encoding="utf-8"))
        leaked = {
            m
            for m in modules
            if any(
                m == f"qbit_ops.{name}" or m.startswith(f"qbit_ops.{name}.")
                for name in _SHARED_MODULES
            )
        }
        if leaked:
            offenders[str(path)] = leaked
    assert not offenders, f"old root shared-path imports found: {offenders}"


def test_app_services_remains_at_the_package_root_documented() -> None:
    """`app_services.py` was deliberately kept at the root (Outcome C:
    mixes a shared classifier with TUI-exclusive refresh orchestration
    -- see its module docstring), not moved into `features/` or
    `shared/`. Pin its location so a future change is deliberate."""
    assert (PACKAGE_DIR / "app_services.py").is_file()
    assert not (FEATURES_DIR / "app_services.py").exists()
    assert not (SHARED_DIR / "app_services.py").exists()
