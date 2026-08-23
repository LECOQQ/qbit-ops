"""`.gitleaks.toml`'s allowlist must actually reach what it claims to.

`make secrets` runs gitleaks in `dir` mode by default: it walks the raw
filesystem and does not consult `.gitignore`. A root-anchored allowlist
entry (`^\\.venv/`) therefore only excludes a top-level `.venv/` -- it
misses the identical, per-worktree one that `scripts/worktree_new.py`
creates at `.worktrees/<feature>/.venv/`, which is exactly where the
vendored stub false positives came from (pywin32, cryptography, jwt,
paramiko typeshed stubs).

`FIXTURE_LINES` is copied verbatim from one of those real false
positives (a `cryptography` typeshed stub's type annotation) because it
is already proven to trip gitleaks' default `generic-api-key` rule --
guessing a synthetic pattern that matches a ruleset this file does not
own would be the same mistake in miniature.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GITLEAKS_CONFIG = ROOT / ".gitleaks.toml"

# Two real lines from a vendored `cryptography` typeshed stub -- not a
# secret, but proven to trip gitleaks' default `generic-api-key` rule
# (the annotation's high-entropy class name reads as a plausible key).
FIXTURE_LINES = (
    "        private_key: x25519.X25519PrivateKey\n"
    "        | ec.EllipticCurvePrivateKey\n"
)


def _skip_without_gitleaks() -> None:
    if shutil.which("gitleaks") is None:
        pytest.skip("gitleaks CLI not on PATH")


def _run_gitleaks(source: Path, report: Path) -> list[dict]:
    subprocess.run(
        [
            "gitleaks",
            "detect",
            "--source",
            str(source),
            "--no-git",
            "-c",
            str(GITLEAKS_CONFIG),
            "--no-banner",
            "--redact",
            "--report-format",
            "json",
            "--report-path",
            str(report),
            "--exit-code",
            "0",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    if not report.exists() or report.stat().st_size == 0:
        return []
    return json.loads(report.read_text(encoding="utf-8"))


def test_a_nested_worktree_venv_is_allowlisted(tmp_path: Path) -> None:
    """The false-positive shape `worktree_new.py` actually produces."""
    _skip_without_gitleaks()

    stub = (
        tmp_path
        / ".worktrees"
        / "some-feature"
        / ".venv"
        / "lib"
        / "python3.12"
        / "site-packages"
        / "cryptography"
        / "hpke.pyi"
    )
    stub.parent.mkdir(parents=True)
    stub.write_text(FIXTURE_LINES, encoding="utf-8")

    leaks = _run_gitleaks(tmp_path, tmp_path / "report.json")

    assert leaks == []


def test_the_same_line_outside_any_venv_still_gets_caught(
    tmp_path: Path,
) -> None:
    """Proves the allowlist is path-scoped, not a rule the scanner
    dropped -- a false-negative allowlist would pass the test above by
    also silencing genuine findings."""
    _skip_without_gitleaks()

    # Named to avoid gitleaks' own default stopword allowlist (paths
    # containing "vendor" are silenced by the base ruleset this file
    # extends) -- that would confound the exact thing under test here.
    exposed = tmp_path / "src" / "exposed_secret.py"
    exposed.parent.mkdir(parents=True)
    exposed.write_text(FIXTURE_LINES, encoding="utf-8")

    leaks = _run_gitleaks(tmp_path, tmp_path / "report.json")

    assert len(leaks) == 1
    assert leaks[0]["File"] == str(exposed)


def test_make_secrets_reports_no_leak_on_this_repository() -> None:
    """End-to-end: the working tree this repository actually ships,
    scanned exactly as `make secrets` scans it."""
    _skip_without_gitleaks()

    result = subprocess.run(
        [
            "gitleaks",
            "detect",
            "--source",
            ".",
            "--no-git",
            "-c",
            str(GITLEAKS_CONFIG),
            "--no-banner",
            "--redact",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
