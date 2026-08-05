"""Characterize `qbit_core.qbit.client.create_qbit_client`, the single place
that constructs a `qbittorrentapi.Client` and classifies its login
exceptions. It takes an explicit `QbitConfig`; loading configuration
from the environment is `qbit_ops.app_services.create_qbit_client`'s job
(see `tests/test_app_services.py`), never this boundary's.
"""

from __future__ import annotations

from typing import Any

import pytest
import qbittorrentapi

from qbit_core.config import QbitConfig
from qbit_core.errors import QbitAuthenticationError, QbitConnectionError
from qbit_core.qbit.client import create_qbit_client, is_qbit_error

_CONFIG = QbitConfig(
    host="http://qbit.example:8080", username="someone", password="s3cret"
)


class _FakeClient:
    """Minimal stand-in for `qbittorrentapi.Client` construction only."""

    def __init__(self, *, host: str, username: str, password: str) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.auth_log_in_calls = 0

    def auth_log_in(self) -> None:
        self.auth_log_in_calls += 1


def test_create_qbit_client_passes_configuration_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configuration values reach `qbittorrentapi.Client` unmodified."""
    calls: list[dict[str, Any]] = []

    def _fake_client(*, host: str, username: str, password: str) -> _FakeClient:
        calls.append({"host": host, "username": username, "password": password})
        return _FakeClient(host=host, username=username, password=password)

    monkeypatch.setattr("qbittorrentapi.Client", _fake_client)

    client = create_qbit_client(_CONFIG)

    assert calls == [
        {
            "host": "http://qbit.example:8080",
            "username": "someone",
            "password": "s3cret",
        }
    ]
    assert client.auth_log_in_calls == 1


def test_create_qbit_client_classifies_login_failed_as_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`qbittorrentapi.LoginFailed` becomes `QbitAuthenticationError`."""

    class _RaisingClient(_FakeClient):
        def auth_log_in(self) -> None:
            raise qbittorrentapi.LoginFailed("bad credentials")

    monkeypatch.setattr("qbittorrentapi.Client", _RaisingClient)

    with pytest.raises(QbitAuthenticationError):
        create_qbit_client(_CONFIG)


def test_create_qbit_client_classifies_connection_error_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`qbittorrentapi.APIConnectionError` becomes `QbitConnectionError`."""

    class _RaisingClient(_FakeClient):
        def auth_log_in(self) -> None:
            raise qbittorrentapi.APIConnectionError("unreachable")

    monkeypatch.setattr("qbittorrentapi.Client", _RaisingClient)

    with pytest.raises(QbitConnectionError):
        create_qbit_client(_CONFIG)


def test_create_qbit_client_classifies_unexpected_exception_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any other login-time exception still degrades to `QbitConnectionError`,
    never left unclassified or reraised as-is."""

    class _RaisingClient(_FakeClient):
        def auth_log_in(self) -> None:
            raise RuntimeError("something else went wrong")

    monkeypatch.setattr("qbittorrentapi.Client", _RaisingClient)

    with pytest.raises(QbitConnectionError):
        create_qbit_client(_CONFIG)


def test_importing_qbit_client_module_creates_no_client() -> None:
    """Importing the boundary module must never construct or connect a client.

    Runs in a fresh subprocess so no other test's import of
    `qbit_core.qbit.client` (already cached in this process's
    `sys.modules`) can hide a real side effect.
    """
    import subprocess
    import sys

    script = (
        "import qbittorrentapi\n"
        "calls = []\n"
        "qbittorrentapi.Client = lambda *a, **k: calls.append((a, k))\n"
        "import qbit_core.qbit.client\n"
        "assert calls == [], calls\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_create_qbit_client_makes_no_extra_api_call_beyond_auth_log_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Client construction performs exactly one call: `auth_log_in()`.
    No version/API-version request is made during construction."""
    calls: list[str] = []

    class _TrackingClient(_FakeClient):
        def auth_log_in(self) -> None:
            calls.append("auth_log_in")

        def __getattr__(self, name: str) -> Any:
            calls.append(name)
            raise AssertionError(f"unexpected call to {name!r}")

    monkeypatch.setattr("qbittorrentapi.Client", _TrackingClient)

    create_qbit_client(_CONFIG)

    assert calls == ["auth_log_in"]


# --- is_qbit_error: the boundary-owned predicate error_boundary delegates to


def test_is_qbit_error_true_for_api_error() -> None:
    assert is_qbit_error(qbittorrentapi.APIError("boom")) is True


def test_is_qbit_error_true_for_api_error_subclass() -> None:
    """`LoginFailed`/`APIConnectionError` are themselves `APIError`
    subclasses."""
    assert is_qbit_error(qbittorrentapi.LoginFailed("boom")) is True
    assert is_qbit_error(qbittorrentapi.APIConnectionError("boom")) is True


def test_is_qbit_error_false_for_unrelated_exceptions() -> None:
    """An internal programming defect must never be mistaken for a
    qBittorrent error."""
    assert is_qbit_error(RuntimeError("bug")) is False
    assert is_qbit_error(TypeError("bug")) is False
    assert is_qbit_error(ValueError("bug")) is False


def test_is_qbit_error_false_for_bare_os_error() -> None:
    """`OSError` is handled by its own `except` clause in `error_boundary`,
    not this predicate."""
    assert is_qbit_error(OSError("connection reset")) is False


def test_error_boundary_module_no_longer_imports_qbittorrentapi_directly() -> (
    None
):
    """`qbit_ops.cli.error_boundary` must not import `qbittorrentapi` at all --
    it reaches its exception type only indirectly, through
    `qbit_core.qbit.client.is_qbit_error`."""
    import ast
    from pathlib import Path

    import qbit_ops.cli.error_boundary

    source = Path(qbit_ops.cli.error_boundary.__file__).read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert not any(
        module == "qbittorrentapi" or module.startswith("qbittorrentapi.")
        for module in imported_modules
    )
