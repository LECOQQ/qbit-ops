"""Expose package metadata for the application."""

import importlib.metadata
import tomllib
from pathlib import Path

_PACKAGE_NAME = "qbit-ops"


def _read_pyproject_version() -> str | None:
    """Read the Poetry version from the repository pyproject.toml."""
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    if not pyproject_path.is_file():
        return None

    with pyproject_path.open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)

    poetry = pyproject.get("tool", {}).get("poetry", {})
    version = poetry.get("version")
    if isinstance(version, str) and version:
        return version

    return None


def _resolve_version() -> str:
    """Resolve the package version from pyproject.toml or installed metadata."""
    pyproject_version = _read_pyproject_version()
    if pyproject_version is not None:
        return pyproject_version

    try:
        return importlib.metadata.version(_PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"


__version__ = _resolve_version()
