"""Boundary-wide architecture guarantees for the qBittorrent extraction phase.

Mirrors `tests/test_layering.py`'s and `tests/test_qbit_boundary.py`'s
AST approach: compile-time guarantees, not just runtime tests, for the
claims this phase and the prior extraction phase make about
`qbit_core.qbit`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import qbit_core
import qbit_ops

PACKAGE_DIR = Path(qbit_ops.__file__).parent
CORE_DIR = Path(qbit_core.__file__).parent
BOUNDARY_DIR = CORE_DIR / "qbit"


def _all_production_files() -> list[Path]:
    """Every production file across both `qbit_ops` and `qbit_core`."""
    return _production_python_files(PACKAGE_DIR) + _production_python_files(
        CORE_DIR
    )


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
    outside `qbit_core/qbit/**` -- client construction and third-party
    exception knowledge live entirely in the boundary."""
    files = [
        path
        for path in _all_production_files()
        if BOUNDARY_DIR not in path.parents
    ]
    assert (
        files
    ), "expected at least one production module outside qbit_core/qbit/"
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
    ), f"qbittorrentapi imported outside qbit_core/qbit/: {offenders}"


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
    """No file under `src/qbit_ops/` or `src/qbit_core/` may import `tests`
    or `tests.support` -- a production module depending on a test
    fixture would be inverted (tests depend on production, never the
    other way)."""
    files = _all_production_files()
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


def test_client_construction_exists_in_exactly_two_documented_locations() -> (
    None
):
    """`create_qbit_client` is defined in exactly two places, each with a
    distinct, documented responsibility:

    - `qbit_core/qbit/client.py`: takes an explicit `QbitConfig`, builds
      and authenticates the `qbittorrentapi.Client` -- no environment
      or `.env` knowledge.
    - `qbit_ops/app_services.py`: the zero-argument CLI/TUI entry point
      that loads `.env`/environment configuration, then delegates to
      the one above.

    `qbit_ops.cli.error_boundary` only re-exports the `app_services`
    function object (proven in `tests/test_qbit_client.py`), never
    redefines it."""
    defining_files: list[str] = []
    for path in _all_production_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "create_qbit_client"
            ):
                defining_files.append(str(path))

    assert sorted(defining_files) == sorted(
        [
            str(BOUNDARY_DIR / "client.py"),
            str(PACKAGE_DIR / "app_services.py"),
        ]
    )
