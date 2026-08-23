"""Characterize `qbit_ops._resolve_version()`.

These tests call `qbit_ops._resolve_version()` directly, never reloading
the `qbit_ops` module or mutating its already-resolved `__version__`, so
a monkeypatched `importlib.metadata.version` cannot leak into any other
test in the suite.
"""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

import qbit_ops
from qbit_ops.cli.app import app as cli_app


def test_installed_metadata_returns_expected_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda name: "9.9.9" if name == "qbit-ops" else "wrong-package",
    )

    assert qbit_ops._resolve_version() == "9.9.9"


def test_editable_install_metadata_follows_same_code_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda name: "0.2.0.dev0+editable",
    )

    assert qbit_ops._resolve_version() == "0.2.0.dev0+editable"


def test_unavailable_distribution_metadata_uses_development_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback string itself must not look like a real release
    version, so it can never be confused with a shipped tag."""

    def _raise(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", _raise)

    resolved = qbit_ops._resolve_version()

    assert resolved == "0+unknown"
    assert resolved != "0.0.0"


def test_unrelated_metadata_exception_is_not_silently_converted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only `PackageNotFoundError` triggers the fallback; any other
    exception must propagate unmasked."""

    def _raise(name: str) -> str:
        raise ValueError("malformed distribution metadata")

    monkeypatch.setattr(importlib.metadata, "version", _raise)

    with pytest.raises(ValueError):
        qbit_ops._resolve_version()


def test_package_import_never_reads_repository_pyproject_toml() -> None:
    """AST-based, not a substring search, so a mention in a docstring or
    comment cannot cause a false failure."""
    import ast

    source = Path(qbit_ops.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_modules: set[str] = set()
    name_references: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
        elif isinstance(node, ast.Name):
            name_references.add(node.id)

    assert "tomllib" not in imported_modules
    assert "__file__" not in name_references


def test_version_resolution_independent_of_package_directory_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        qbit_ops,
        "__file__",
        "/somewhere/very/deeply/nested/src/qbit_ops/__init__.py",
    )
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda name: "1.2.3",
    )

    assert qbit_ops._resolve_version() == "1.2.3"


def test_version_resolution_independent_of_current_working_directory(
    tmp_path: Path,
) -> None:
    nested_cwd = tmp_path / "a" / "b" / "c" / "d"
    nested_cwd.mkdir(parents=True)

    result = subprocess.run(
        [sys.executable, "-c", "import qbit_ops; print(qbit_ops.__version__)"],
        cwd=nested_cwd,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()
    assert result.stdout.strip() != "0.0.0"


def test_cli_root_invocation_exposes_the_resolved_version() -> None:
    result = CliRunner().invoke(cli_app)

    assert result.exit_code == 0
    assert qbit_ops.__version__ in result.stdout
