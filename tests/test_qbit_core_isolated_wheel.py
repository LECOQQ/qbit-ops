"""Prove `qbit_core`'s Waitarr-facing surface is importable from a real,
built wheel, without qbit-ops's `src/` checkout, `tests/`, or any
network connection to a qBittorrent instance.

Builds the wheel and extracts only `qbit_core/` (never `qbit_ops/`)
into a scratch directory, then runs a subprocess with that directory as
the *sole* entry on `PYTHONPATH` -- proving `qbit_ops` need not be on
disk at all for the exact-hash pause/resume API to import and work.
Mirrors `tests/test_doctor_isolated_wheel.py`'s pattern for `qbit_ops`.
`qbit_core`'s own code never importing Typer/Rich/Textual is a separate,
already-covered claim -- see `tests/test_layering.py`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

_CONSUMER_SMOKE_SCRIPT = """
import sys

import qbit_core
assert qbit_core.__file__.startswith(sys.argv[1]), qbit_core.__file__

from qbit_core import (
    QbitAuthenticationError,
    QbitConfig,
    QbitConnectionError,
    QbitCoreError,
    create_qbit_client,
)
from qbit_core.features.torrents import (
    BulkHashActionResult,
    HashActionOutcome,
    HashActionStatus,
    pause_torrents_by_hash,
    resume_torrents_by_hash,
)

assert issubclass(QbitConnectionError, QbitCoreError)
assert issubclass(QbitAuthenticationError, QbitCoreError)

config = QbitConfig(host="http://localhost:8080", username="u", password="p")
assert config.host == "http://localhost:8080"

assert callable(create_qbit_client)
assert callable(pause_torrents_by_hash)
assert callable(resume_torrents_by_hash)
assert HashActionStatus.CHANGED.value == "changed"

# No live qBittorrent connection -- only prove the public shapes exist
# and hold together, e.g. an outcome/result can be constructed and its
# aggregate properties queried without a network call.
outcome = HashActionOutcome(
    torrent_hash="a" * 40, status=HashActionStatus.CHANGED
)
result = BulkHashActionResult(action="pause", outcomes=(outcome,))
assert result.changed_hashes == ("a" * 40,)
assert result.unchanged_hashes == ()

# `qbit_ops` must never be reachable here -- the wheel extraction below
# only ships qbit_core/. (Typer/Rich/Textual are not asserted absent:
# this subprocess reuses the dev interpreter, which has qbit-ops's own
# runtime dependencies installed; whether qbit_core's *code* avoids
# importing them is already enforced by tests/test_layering.py.)
assert "qbit_ops" not in sys.modules

print("OK")
"""


def test_qbit_core_pause_resume_surface_works_from_a_built_wheel(
    tmp_path: Path,
) -> None:
    if shutil.which("poetry") is None:
        pytest.skip("poetry CLI not on PATH")

    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    subprocess.run(
        ["poetry", "build", "--format", "wheel", "--output", str(wheel_dir)],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    wheels = list(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1, wheels

    # Extract only `qbit_core/` -- deliberately never `qbit_ops/`, so a
    # stray import of the CLI package would fail loudly instead of
    # silently succeeding because it happened to be on disk anyway.
    extracted = tmp_path / "site-packages"
    extracted.mkdir()
    with zipfile.ZipFile(wheels[0]) as archive:
        for name in archive.namelist():
            if name.startswith("qbit_core/"):
                archive.extract(name, extracted)

    assert (extracted / "qbit_core" / "__init__.py").exists()
    assert (extracted / "qbit_core" / "features" / "torrents.py").exists()
    assert not (extracted / "qbit_ops").exists()
    assert not (extracted / "tests").exists()

    outside_repo_cwd = tmp_path / "outside_repo_cwd"
    outside_repo_cwd.mkdir()

    # Run with the current (dev) interpreter -- it already has
    # qbittorrent-api/python-dotenv/packaging installed, so no
    # network-dependent `pip install` is needed -- but with the
    # extracted wheel's `qbit_core/` prepended on `PYTHONPATH`, ahead of
    # this repository's editable `src/` install, so the module actually
    # imported is the packaged one, not the checkout.
    env = dict(os.environ)
    env["PYTHONPATH"] = str(extracted)
    result = subprocess.run(
        [sys.executable, "-c", _CONSUMER_SMOKE_SCRIPT, str(extracted)],
        cwd=outside_repo_cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "OK" in result.stdout
