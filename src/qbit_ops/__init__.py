"""Expose package metadata for the application."""

import importlib.metadata

_PACKAGE_NAME = "qbit-ops"

# Marker for running from source with no package installed.
_DEVELOPMENT_FALLBACK_VERSION = "0+unknown"


def _resolve_version() -> str:
    """Resolve the package version from installed distribution metadata.

    Only falls back on `PackageNotFoundError`; other exceptions propagate.
    """
    try:
        return importlib.metadata.version(_PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        return _DEVELOPMENT_FALLBACK_VERSION


__version__ = _resolve_version()
