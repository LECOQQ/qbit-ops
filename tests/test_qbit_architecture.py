"""Boundary-wide architecture guarantees for the qBittorrent extraction phase.

Mirrors `tests/test_layering.py`'s and `tests/test_qbit_boundary.py`'s
AST approach: compile-time guarantees, not just runtime tests, for the
claims this phase and the prior extraction phase make about
`qbit_ops.qbit`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import qbit_ops

PACKAGE_DIR = Path(qbit_ops.__file__).parent
BOUNDARY_DIR = PACKAGE_DIR / "qbit"


def _production_python_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*.py") if "__pycache__" not in path.parts
    )


def _module_level_imported_roots(source: str) -> set[str]:
    """Return root module names imported at *module top level* only."""
    tree = ast.parse(source)
    roots: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _all_imported_modules(source: str) -> set[str]:
    """Return every imported module/name, at any nesting (module or
    deferred, inside functions), for a broader "ever imports" check."""
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_qbittorrentapi_import_is_confined_to_the_qbit_boundary() -> None:
    """`qbittorrentapi` must never be imported (module-level or deferred)
    outside `qbit_ops/qbit/**` -- client construction and third-party
    exception knowledge live entirely in the boundary (see
    `docs/audits/2026-07-package-refactor-plan.md` Phase 3
    continuation)."""
    files = [
        path
        for path in _production_python_files(PACKAGE_DIR)
        if BOUNDARY_DIR not in path.parents
    ]
    assert (
        files
    ), "expected at least one production module outside qbit_ops/qbit/"
    assert any(
        path.name == "app.py" and path.parent.name == "cli" for path in files
    ), (
        "expected qbit_ops/cli/app.py to be part of the scanned set -- an "
        "empty or wrong scan would make this test vacuously pass"
    )

    offenders: dict[str, set[str]] = {}
    for path in files:
        modules = _all_imported_modules(path.read_text(encoding="utf-8"))
        leaked = {
            module
            for module in modules
            if module == "qbittorrentapi"
            or module.startswith("qbittorrentapi.")
        }
        if leaked:
            offenders[str(path)] = leaked

    assert (
        not offenders
    ), f"qbittorrentapi imported outside qbit_ops/qbit/: {offenders}"


def test_qbit_boundary_files_are_the_only_ones_importing_qbittorrentapi() -> (
    None
):
    """Positive companion: confirm the boundary itself still does the
    import it is supposed to own (an empty allowlist would make the
    negative test above meaningless)."""
    client_source = (BOUNDARY_DIR / "client.py").read_text(encoding="utf-8")
    roots = _module_level_imported_roots(client_source)
    assert "qbittorrentapi" in roots


def test_no_production_module_imports_the_tests_package() -> None:
    """No file under `src/qbit_ops/` may import `tests` or `tests.support`
    -- a production module depending on a test fixture would be
    inverted (tests depend on production, never the other way)."""
    files = _production_python_files(PACKAGE_DIR)
    assert files

    offenders: list[str] = []
    for path in files:
        modules = _all_imported_modules(path.read_text(encoding="utf-8"))
        if any(
            module == "tests" or module.startswith("tests.")
            for module in modules
        ):
            offenders.append(str(path))

    assert not offenders, f"production modules importing tests/: {offenders}"


def test_client_construction_exists_in_exactly_one_canonical_location() -> None:
    """`create_qbit_client` must be *defined* only in
    `qbit_ops/qbit/client.py` -- `qbit_ops.app_services` and
    `qbit_ops.cli.error_boundary` only re-export the same function
    object (proven elsewhere, `tests/test_qbit_client.py`), never
    redefine it."""
    files = _production_python_files(PACKAGE_DIR)

    defining_files: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "create_qbit_client"
            ):
                defining_files.append(str(path))

    assert defining_files == [str(BOUNDARY_DIR / "client.py")]
