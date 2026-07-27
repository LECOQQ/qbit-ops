"""Expose package metadata for the application."""

import importlib.metadata

_PACKAGE_NAME = "qbit-ops"

# Not a real release version: an explicit, deterministic marker for the
# one case installed distribution metadata cannot exist -- running from
# a source tree with no package installed (not even in editable mode).
_DEVELOPMENT_FALLBACK_VERSION = "0+unknown"


def _resolve_version() -> str:
    """Resolve the package version from installed distribution metadata.

    Independent of the source layout or the package directory's depth
    under the repository root -- unlike reading `pyproject.toml` via a
    path relative to this file, this works identically for a wheel, a
    pipx install, or an editable install, and never opens a repository
    file.

    Only `PackageNotFoundError` (no matching distribution installed)
    falls back to `_DEVELOPMENT_FALLBACK_VERSION`. Any other exception
    (e.g. malformed metadata) propagates -- it must never be silently
    relabelled as "not installed".
    """
    try:
        return importlib.metadata.version(_PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        return _DEVELOPMENT_FALLBACK_VERSION


__version__ = _resolve_version()
