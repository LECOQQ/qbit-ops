"""Characterize `qbit_ops.__version__` resolution (constat A-2).

`qbit_ops/__init__.py` used to read the Poetry version from `pyproject.toml`
via a path relative to this file's own location
(`Path(__file__).resolve().parents[1]`) -- correct only for the old flat
`app/` layout. The move to `src/qbit_ops/` would have silently changed
that path's meaning without raising, since `_resolve_version()` fell
back to `importlib.metadata` (returning `0.0.0` outside an installation)
rather than failing loudly.

The resolver now reads installed distribution metadata first, with an
explicit, non-version-shaped development fallback -- see
`docs/audits/2026-07-package-refactor-plan.md` (constat A-2, phase 2a).
These tests call `qbit_ops._resolve_version()` directly (never reloading
the `qbit_ops` module or mutating its already-resolved `__version__`), so a
monkeypatched `importlib.metadata.version` cannot leak into any other
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
    """A normal installed distribution resolves straight from metadata."""
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda name: "9.9.9" if name == "qbit-ops" else "wrong-package",
    )

    assert qbit_ops._resolve_version() == "9.9.9"


def test_editable_install_metadata_follows_same_code_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An editable (`pip install -e` / Poetry dev) install uses the same path.

    `importlib.metadata.version()` does not distinguish an editable
    install from a regular one -- both resolve through the same
    distribution metadata lookup (a `.dist-info`/`.egg-info` directory),
    so there is no separate code path to characterize; this test proves
    the resolver takes no shortcut that would only work for one of them.
    """
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda name: "0.2.0.dev0+editable",
    )

    assert qbit_ops._resolve_version() == "0.2.0.dev0+editable"


def test_unavailable_distribution_metadata_uses_development_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No installed distribution falls back to an explicit dev marker.

    The fallback must not look like a real release version (constat
    A-2's requirement): `0+unknown` cannot be confused with a shipped
    tag the way the old `0.0.0` fallback could.
    """

    def _raise(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", _raise)

    resolved = qbit_ops._resolve_version()

    assert resolved == "0+unknown"
    assert resolved != "0.0.0"


def test_unrelated_metadata_exception_is_not_silently_converted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-`PackageNotFoundError` failure must propagate, not fall back.

    Only "no distribution installed" is a legitimate development case.
    Malformed metadata or any other unexpected exception must not be
    hidden behind the same fallback -- that would mask a real defect.
    """

    def _raise(name: str) -> str:
        raise ValueError("malformed distribution metadata")

    monkeypatch.setattr(importlib.metadata, "version", _raise)

    with pytest.raises(ValueError):
        qbit_ops._resolve_version()


def test_package_import_never_reads_repository_pyproject_toml() -> None:
    """`qbit_ops/__init__.py` must not open `pyproject.toml` at all.

    An AST-based check (not a plain substring search, so a mention in a
    docstring or comment cannot cause a false failure): the old resolver
    read the repository's `pyproject.toml` via a path relative to
    `__file__`. The replacement must not retain a second, hidden path
    that climbs the filesystem for the same file -- proven here by the
    absence of both a `tomllib` import and any reference to the `__file__`
    name, since a repository-relative read needs both.
    """
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
    """Resolution must not depend on how deep the package sits under the
    repo root.

    A fake, arbitrarily deep `__file__` proves nothing in the resolver's
    control flow consults the package's own path -- the defect the old
    `parents[1]` lookup had.
    """
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
    """Resolution must succeed from a cwd with no repository files at all.

    Runs in a fresh subprocess, from a directory nested several levels
    below `tmp_path` and containing no `pyproject.toml`, to prove the
    installed package resolves its version without any repository file
    being reachable from the current working directory.
    """
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
    """The CLI's own diagnostics still surface `qbit_ops.__version__`
    unchanged."""
    result = CliRunner().invoke(cli_app)

    assert result.exit_code == 0
    assert qbit_ops.__version__ in result.stdout
