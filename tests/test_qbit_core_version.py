from __future__ import annotations

import importlib.metadata

import pytest

import qbit_core


def test_version_is_the_installed_distribution_version() -> None:
    assert qbit_core.__version__ == importlib.metadata.version("qbit-ops")
    assert "__version__" in qbit_core.__all__


def test_version_falls_back_when_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _missing(_distribution: str) -> str:
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(importlib.metadata, "version", _missing)

    assert qbit_core._installed_version() == "0+unknown"
