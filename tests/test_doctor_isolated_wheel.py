"""Prove `doctor`'s matrix-aware compatibility checks work from a real,
built wheel: no repository checkout, no `tests/` directory, no Docker,
no network beyond the (here, faked) configured qBittorrent connection,
and an arbitrary working directory (item 15 of the phase spec).

Builds the wheel and extracts only its `qbit_ops/` package tree into a
scratch directory, then runs a subprocess with that directory as the
*sole* entry on `PYTHONPATH` -- proving the packaged module needs
nothing else on disk. Deliberately does not `pip install` into a fresh
venv: that would require reinstalling qbit-ops's runtime dependencies,
which is either a hidden network dependency (no local wheel cache) or
redundant with what this project's own CI already verified when it ran
`poetry install` -- see `tests/test_compatibility_manifest_source_of_truth.py`
for the analogous, already-hermetic "wheel contains the manifest" check
this test complements.
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

_DOCTOR_SMOKE_SCRIPT = """
import sys

import qbit_ops
assert qbit_ops.__file__.startswith(sys.argv[1]), qbit_ops.__file__

from qbit_ops.config import QbitConfig
from qbit_core.features.doctor import (
    CheckStatus,
    ConnectionOutcome,
    collect_doctor_report,
)


class FakeClient:
    def app_version(self):
        return "5.2.3"

    def app_web_api_version(self):
        return "2.15.1"

    def transfer_info(self):
        return {"dl_info_speed": 0, "up_info_speed": 0}

    def torrents_info(self):
        return []


config = QbitConfig(host="http://localhost:8080", username="u", password="p")
report = collect_doctor_report(
    config=config,
    config_error=None,
    connection_outcome=ConnectionOutcome.OK,
    connection_error=None,
    client=FakeClient(),
)
by_code = {c.code: c for c in report.checks}

assert by_code["COMPAT002"].status == CheckStatus.PASS, by_code["COMPAT002"]
message = by_code["COMPAT002"].message.lower()
assert "container integration tested" in message, message
assert by_code["COMPAT003"].status == CheckStatus.PASS, by_code["COMPAT003"]
assert "docker" not in sys.modules
assert "tests" not in sys.modules
print("OK")
"""


def test_doctor_compatibility_evidence_works_from_a_built_wheel(
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

    # Extract only the `qbit_ops/` package tree -- the wheel's dist-info
    # is irrelevant here, and this deliberately proves the runtime
    # module works from nothing but its own packaged files.
    extracted = tmp_path / "site-packages"
    extracted.mkdir()
    with zipfile.ZipFile(wheels[0]) as archive:
        for name in archive.namelist():
            if name.startswith("qbit_ops/") or name.startswith("qbit_core/"):
                archive.extract(name, extracted)

    assert (extracted / "qbit_core" / "features" / "doctor.py").exists()
    assert (
        extracted / "qbit_core" / "data" / "qbittorrent-matrix.toml"
    ).exists()
    assert not (extracted / "tests").exists()

    outside_repo_cwd = tmp_path / "outside_repo_cwd"
    outside_repo_cwd.mkdir()

    # Run with the current (dev) interpreter -- it already has
    # qbit-ops's runtime dependencies installed, so no network-dependent
    # `pip install` is needed -- but with the extracted wheel's
    # `qbit_ops/` prepended on `PYTHONPATH`, ahead of this repository's
    # editable `src/` install, so the module actually imported is the
    # packaged one, not the checkout.
    env = dict(os.environ)
    env["PYTHONPATH"] = str(extracted)
    result = subprocess.run(
        [sys.executable, "-c", _DOCTOR_SMOKE_SCRIPT, str(extracted)],
        cwd=outside_repo_cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "OK" in result.stdout
