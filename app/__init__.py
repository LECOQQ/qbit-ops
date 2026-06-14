"""Expose package metadata for the application."""

import importlib.metadata

try:
    __version__ = importlib.metadata.version("qbit-ops")
except importlib.metadata.PackageNotFoundError:
    __version__ = "0.0.0"
