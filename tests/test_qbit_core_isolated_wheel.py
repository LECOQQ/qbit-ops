"""Prove `qbit_core`'s Waitarr-facing surface imports and works from a
built wheel with only `qbit_core/` on disk -- never `qbit_ops/`.
Mirrors `tests/test_doctor_isolated_wheel.py`'s pattern.
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

# No qBittorrent connection -- just prove the shapes hold together.
outcome = HashActionOutcome(
    torrent_hash="a" * 40, status=HashActionStatus.CHANGED
)
result = BulkHashActionResult(action="pause", outcomes=(outcome,))
assert result.changed_hashes == ("a" * 40,)
assert result.unchanged_hashes == ()

# Typer/Rich/Textual aren't asserted absent here: the dev interpreter
# has them installed regardless -- see tests/test_layering.py for that.
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

    # Extract only qbit_core/ -- a stray qbit_ops import must fail loudly.
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

    # Dev interpreter (deps already installed), but PYTHONPATH points at
    # the extracted wheel, not this repo's editable src/ checkout.
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
