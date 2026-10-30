"""The scan script gives the same findings with either engine."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "sast.sh"
FIXTURE = ROOT / "tests" / "fixtures" / "sast"
OPENGREP = os.environ.get("OPENGREP_BIN") or shutil.which("opengrep")
SEMGREP = os.environ.get("SEMGREP_BIN") or shutil.which("semgrep")

pytestmark = pytest.mark.skipif(
    not (OPENGREP and SEMGREP),
    reason="needs opengrep and semgrep (OPENGREP_BIN / SEMGREP_BIN or PATH)",
)


def _findings(
    engine: str, sarif: Path, home: Path, project: Path
) -> set[tuple[str, str, int]]:
    env = {
        **os.environ,
        "SAST_ENGINE": engine,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "OPENGREP_BIN": str(OPENGREP),
        "SEMGREP_BIN": str(SEMGREP),
        "SAST_SOURCES": str(FIXTURE / "sources"),
        "SAST_RULES": str(ROOT / ".sast" / "rules"),
        "SAST_SARIF": str(sarif),
    }
    completed = subprocess.run(
        [str(SCRIPT)],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    results = json.loads(sarif.read_text())["runs"][0]["results"]
    return {
        (
            result["ruleId"].rsplit(".", 1)[-1],
            result["locations"][0]["physicalLocation"]["artifactLocation"][
                "uri"
            ],
            result["locations"][0]["physicalLocation"]["region"]["startLine"],
        )
        for result in results
    }


def test_both_engines_report_the_same_findings_on_the_same_rules(
    tmp_path: Path,
) -> None:
    # Outside the repository: the engines skip `tests/` inside a git checkout.
    project = tmp_path / "project"
    project.mkdir()
    shutil.copy(FIXTURE / "sample.py", project / "sample.py")
    (tmp_path / ".config").mkdir()
    opengrep = _findings("opengrep", tmp_path / "o.sarif", tmp_path, project)
    semgrep = _findings("semgrep", tmp_path / "s.sarif", tmp_path, project)

    assert opengrep == {("urlopen-without-timeout", "sample.py", 5)}
    assert semgrep == opengrep


def test_unknown_engine_is_refused() -> None:
    completed = subprocess.run(
        [str(SCRIPT)],
        cwd=FIXTURE,
        env={**os.environ, "SAST_ENGINE": "grep"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "SAST_ENGINE" in completed.stderr
