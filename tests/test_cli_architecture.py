"""Enforce the `cli/` package's structural boundaries after the CLI
reorganization (see docs/ARCHITECTURE.md).

Covers what the pre-existing, per-concern test files
(`test_cli_help_snapshot.py`, `test_errors.py`, `test_layering.py`,
`test_tui_security.py`, `test_package_entrypoint.py`,
`test_qbit_architecture.py`) do not already characterize on their own:
exactly one canonical root Typer application, no dependency from
application/domain modules back into `cli/`, no stale executable import
of the removed `qbit_ops.main`/`qbit_ops.ui`, no duplicated rendering
implementation, and that the packaged wheel's `qbit-ops --help` works
with nothing else installed.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import typer

import qbit_ops
from qbit_ops.cli.app import app as canonical_app
from qbit_ops.cli.commands import (
    backup,
    connection,
    doctor,
    explain,
    status,
    torrents,
    trackers,
    tui,
)

PACKAGE_DIR = Path(qbit_ops.__file__).parent
CLI_DIR = PACKAGE_DIR / "cli"
REPO_ROOT = Path(__file__).resolve().parent.parent


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


# --- 1/17: exactly one canonical root Typer app -----------------------


def test_cli_dir_defines_exactly_one_typer_app_with_add_completion() -> None:
    """Only `cli/app.py` may construct the *root* Typer application
    (`typer.Typer(add_completion=..., help=...)`, the one registered as
    the console entrypoint). Every other `cli/**` module either defines
    a command *group* sub-app (`connection_app`, `backup_app`,
    `torrents_app`, `trackers_app`, `explain_app`) or no Typer app at
    all -- never a second root-shaped one."""
    root_app_files: list[str] = []
    for path in _production_python_files(CLI_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "Typer"
            ):
                continue
            has_add_completion = any(
                kw.arg == "add_completion" for kw in node.keywords
            )
            if has_add_completion:
                root_app_files.append(str(path))

    assert root_app_files == [str(CLI_DIR / "app.py")], root_app_files


def test_canonical_app_object_is_a_typer_instance() -> None:
    """`qbit_ops.cli.app.app` is a `typer.Typer` instance, and it is the
    exact object the console entrypoint resolves to (cross-checked
    against installed metadata in `test_package_entrypoint.py`)."""
    assert isinstance(canonical_app, typer.Typer)


# --- 2/3: every existing command group is registered, exactly once -----


def test_every_command_group_sub_app_is_registered_on_the_root_app() -> None:
    """Every `cli/commands/*.py` group sub-app must be registered on the
    canonical root app exactly once -- a group silently left
    unregistered, or registered twice, would both be caught here."""
    expected_sub_apps = {
        "connection": connection.connection_app,
        "backup": backup.backup_app,
        "torrents": torrents.torrents_app,
        "trackers": trackers.trackers_app,
        "explain": explain.explain_app,
    }

    registered_by_name: dict[str, list[typer.Typer]] = {}
    for group in canonical_app.registered_groups:
        assert group.name is not None
        assert group.typer_instance is not None
        registered_by_name.setdefault(group.name, []).append(
            group.typer_instance
        )

    for name, sub_app in expected_sub_apps.items():
        instances = registered_by_name.get(name, [])
        assert len(instances) == 1, (
            name,
            "expected exactly one registration",
            instances,
        )
        assert instances[0] is sub_app


def test_every_root_level_command_is_registered_exactly_once() -> None:
    """`status`, `doctor`, `tui` are root-level (non-grouped) commands,
    each registered via its module's `register(app)` function exactly
    once."""
    names = []
    for command in canonical_app.registered_commands:
        assert command.callback is not None
        names.append(
            command.name or command.callback.__name__.replace("_", "-")
        )
    for expected in ("status", "doctor", "tui"):
        assert names.count(expected) == 1, (expected, names)


def test_command_modules_expose_the_registration_pattern_advertised() -> None:
    """Root-level command modules expose a `register(app)` function;
    group command modules expose a module-level `*_app` Typer sub-app --
    the two composition patterns `cli/app.py` actually relies on."""
    assert callable(status.register)
    assert callable(doctor.register)
    assert callable(tui.register)
    assert isinstance(connection.connection_app, typer.Typer)
    assert isinstance(backup.backup_app, typer.Typer)
    assert isinstance(torrents.torrents_app, typer.Typer)
    assert isinstance(trackers.trackers_app, typer.Typer)
    assert isinstance(explain.explain_app, typer.Typer)


# --- 9/14: dependency direction -- application modules never import cli


def test_no_application_module_outside_cli_and_tui_imports_cli() -> None:
    """`cli` -> application modules is the only allowed direction.

    No file under `src/qbit_ops/` outside `qbit_ops/cli/**` may import
    `qbit_ops.cli` or any of its submodules. `qbit_ops/tui/**` is
    exempted from this *scan* only in the trivial sense that it is
    itself outside `cli/`; `test_tui_security.py` separately proves the
    TUI package never actually does so.
    """
    files = [
        path
        for path in _production_python_files(PACKAGE_DIR)
        if CLI_DIR not in path.parents and path.parent != CLI_DIR
    ]
    assert files
    assert any(path.name == "doctor.py" for path in files), (
        "expected qbit_ops/doctor.py to be part of the scanned set -- an "
        "empty or wrong scan would make this test vacuously pass"
    )

    offenders: dict[str, set[str]] = {}
    for path in files:
        modules = _all_imported_modules(path.read_text(encoding="utf-8"))
        leaked = {
            module
            for module in modules
            if module == "qbit_ops.cli" or module.startswith("qbit_ops.cli.")
        }
        if leaked:
            offenders[str(path)] = leaked

    assert not offenders, f"non-cli modules importing qbit_ops.cli: {offenders}"


def test_cli_app_module_never_imported_by_command_modules() -> None:
    """`cli/commands/*.py` must never import `cli/app.py` back -- `app.py`
    imports command modules to register them, so the reverse import
    would be circular. Root-level commands use a `register(app)`
    function precisely to avoid this."""
    offenders: dict[str, set[str]] = {}
    for path in _production_python_files(CLI_DIR / "commands"):
        modules = _all_imported_modules(path.read_text(encoding="utf-8"))
        leaked = {module for module in modules if module == "qbit_ops.cli.app"}
        if leaked:
            offenders[str(path)] = leaked

    assert not offenders, offenders


# --- Seam integrity: the shared client/config/progress toggles must stay
# reachable through exactly one module-attribute path each, never copied
# into a command module's own namespace by a `from ... import name`
# (a copy would silently stop responding to
# `monkeypatch.setattr(error_boundary, "create_qbit_client", ...)` /
# `monkeypatch.setattr(rendering, "is_interactive_terminal", ...)` etc.,
# even though the module itself is still patched correctly).

_ERROR_BOUNDARY_SEAM_NAMES = {"create_qbit_client", "load_qbit_config"}
_RENDERING_SEAM_NAMES = {
    "is_interactive_terminal",
    "transient_spinner",
    "transient_progress",
    "live_status_display",
    "progress_enabled",
}


def _from_imports_by_module(source: str) -> dict[str, set[str]]:
    tree = ast.parse(source)
    imports: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.setdefault(node.module, set()).update(
                alias.name for alias in node.names
            )
    return imports


def test_no_cli_module_copies_the_client_config_seam_by_bare_import() -> None:
    """No file under `qbit_ops/cli/**` may `from
    qbit_ops.cli.error_boundary import create_qbit_client` (or
    `load_qbit_config`) -- every consumer must call
    `error_boundary.create_qbit_client()` as a module-attribute access.
    `error_boundary.py` itself is exempt: it is the canonical owner and
    legitimately imports the real implementation from
    `qbit_ops.app_services` once."""
    offenders: dict[str, set[str]] = {}
    for path in _production_python_files(CLI_DIR):
        if path == CLI_DIR / "error_boundary.py":
            continue
        imports = _from_imports_by_module(path.read_text(encoding="utf-8"))
        leaked = imports.get("qbit_ops.cli.error_boundary", set()) & (
            _ERROR_BOUNDARY_SEAM_NAMES
        )
        if leaked:
            offenders[str(path)] = leaked

    assert not offenders, offenders


def test_no_cli_module_copies_the_progress_seam_by_bare_import() -> None:
    """No file under `qbit_ops/cli/**` may `from qbit_ops.cli.rendering
    import is_interactive_terminal` (or `transient_spinner`,
    `transient_progress`, `live_status_display`, `progress_enabled`) --
    every consumer must call `rendering.is_interactive_terminal()` etc.
    as a module-attribute access, so a single
    `monkeypatch.setattr(rendering, ...)` affects every command group
    uniformly."""
    offenders: dict[str, set[str]] = {}
    for path in _production_python_files(CLI_DIR):
        if path == CLI_DIR / "rendering.py":
            continue
        imports = _from_imports_by_module(path.read_text(encoding="utf-8"))
        leaked = imports.get("qbit_ops.cli.rendering", set()) & (
            _RENDERING_SEAM_NAMES
        )
        if leaked:
            offenders[str(path)] = leaked

    assert not offenders, offenders


def test_error_boundary_owns_create_qbit_client_by_a_single_import() -> None:
    """Positive companion: `error_boundary.py` itself must still import
    `create_qbit_client` from `qbit_ops.app_services` exactly once --
    an empty or missing origin would make the negative test above
    vacuously pass."""
    imports = _from_imports_by_module(
        (CLI_DIR / "error_boundary.py").read_text(encoding="utf-8")
    )
    assert "create_qbit_client" in imports.get("qbit_ops.app_services", set())


# --- 15/16: no stale reference to the removed main.py/ui.py ------------


def test_main_and_ui_modules_no_longer_exist() -> None:
    """`qbit_ops/main.py` and `qbit_ops/ui.py` were fully replaced by
    `cli/app.py`/`cli/commands/*.py` and `cli/rendering.py` -- neither
    file should still be on disk (a leftover would mean two competing
    rendering/composition implementations)."""
    assert not (PACKAGE_DIR / "main.py").exists()
    assert not (PACKAGE_DIR / "ui.py").exists()


def test_no_tracked_source_file_still_imports_qbit_ops_main_or_ui() -> None:
    """No file under `src/` or `tests/` may still import the removed
    `qbit_ops.main`/`qbit_ops.ui` modules -- a stale import would be a
    dead reference (both modules no longer exist) rather than a
    functioning duplicate, but is exactly the kind of leftover a
    mechanical rewrite could silently miss."""
    offenders: dict[str, set[str]] = {}
    for root in (PACKAGE_DIR, REPO_ROOT / "tests"):
        for path in _production_python_files(root):
            modules = _all_imported_modules(path.read_text(encoding="utf-8"))
            leaked = {
                module
                for module in modules
                if module in ("qbit_ops.main", "qbit_ops.ui")
            }
            if leaked:
                offenders[str(path)] = leaked

    assert not offenders, offenders


def test_rendering_module_is_the_sole_definition_of_print_table() -> None:
    """Positive companion: `print_table` (a representative rendering
    primitive) is defined exactly once, in `cli/rendering.py` -- proving
    no second rendering implementation was left behind anywhere in
    `src/qbit_ops/`."""
    defining_files: list[str] = []
    for path in _production_python_files(PACKAGE_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "print_table":
                defining_files.append(str(path))

    assert defining_files == [str(CLI_DIR / "rendering.py")]


# --- 18: isolated wheel installation runs `qbit-ops --help` ------------


def test_qbit_ops_help_works_from_a_built_wheel(tmp_path: Path) -> None:
    """Prove the packaged wheel's `qbit-ops --help` works with nothing
    else on disk: no repository checkout, no `tests/` directory, no
    Docker, no network beyond (here, absent) qBittorrent connectivity,
    and an arbitrary working directory. Complements
    `tests/test_doctor_isolated_wheel.py`'s doctor-specific isolated
    wheel test with the CLI-composition-root angle this phase adds.
    """
    if shutil.which("poetry") is None:
        pytest.skip("poetry CLI not on PATH")

    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    subprocess.run(
        ["poetry", "build", "--format", "wheel", "--output", str(wheel_dir)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    wheels = list(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1, wheels

    extracted = tmp_path / "site-packages"
    extracted.mkdir()
    with zipfile.ZipFile(wheels[0]) as archive:
        for name in archive.namelist():
            if name.startswith("qbit_ops/"):
                archive.extract(name, extracted)

    assert (extracted / "qbit_ops" / "cli" / "app.py").exists()
    assert not (extracted / "qbit_ops" / "main.py").exists()
    assert not (extracted / "qbit_ops" / "ui.py").exists()
    assert not (extracted / "tests").exists()

    outside_repo_cwd = tmp_path / "outside_repo_cwd"
    outside_repo_cwd.mkdir()

    env = dict(os.environ)
    env["PYTHONPATH"] = str(extracted)
    smoke_script = (
        "from typer.testing import CliRunner\n"
        "from qbit_ops.cli.app import app\n"
        "result = CliRunner().invoke(app, ['--help'])\n"
        "assert result.exit_code == 0, result.output\n"
        "assert 'Administer qBittorrent' in result.output\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", smoke_script],
        cwd=outside_repo_cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "OK" in result.stdout
