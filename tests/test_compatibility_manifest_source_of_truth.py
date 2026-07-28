"""Prove the qBittorrent compatibility evidence manifest has exactly one
canonical, packaged source, and that every real consumer reads it through
the package loader -- not a second parser, not a repository-relative path.

Closure-review §9 (S-5): a matrix-aware `doctor` cannot be built safely
until the manifest is packaged and every consumer agrees on the same
source. These are the guard tests for that guarantee.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import pytest

from qbit_ops.qbit.compatibility import load_compatibility_evidence

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _tracked_files_named(filename: str) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", f"*/{filename}", filename],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def test_exactly_one_canonical_matrix_file_is_tracked() -> None:
    tracked = _tracked_files_named("qbittorrent-matrix.toml")
    assert tracked == ["src/qbit_ops/data/qbittorrent-matrix.toml"], tracked


def test_canonical_matrix_lives_under_the_runtime_package() -> None:
    tracked = _tracked_files_named("qbittorrent-matrix.toml")
    assert all(path.startswith("src/qbit_ops/") for path in tracked)


def test_no_executable_consumer_references_the_old_test_path() -> None:
    """No `.py`/`.yml`/Makefile consumer may hardcode the manifest's old
    pre-packaging location any longer -- only historical audit prose
    under `docs/audits/` may still mention it. The old path is built
    from parts rather than written as a literal string in this file, so
    this guard (now itself a tracked, `git grep`-visible file) never
    flags its own source as an offender."""
    old_path = "/".join(["tests", "integration", "qbittorrent-matrix.toml"])
    result = subprocess.run(
        ["git", "grep", "-l", "--fixed-strings", old_path],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # git grep exits 1 when nothing matches -- that's the expected case
    # once every real reference has moved, so don't `check=True` here.
    matches = [line for line in result.stdout.splitlines() if line]
    offenders = [line for line in matches if "docs/audits/" not in line]
    assert offenders == [], (
        f"executable/documentary consumer still references the old "
        f"tests/integration path: {offenders}"
    )


def test_all_current_entries_load_through_the_package_loader() -> None:
    evidence = load_compatibility_evidence()
    assert {entry.id for entry in evidence.entries} == {
        "qbit-4.6.7",
        "qbit-5.0.0",
        "qbit-5.1.4",
        "qbit-5.2.3",
    }


def test_ci_matrix_generation_uses_the_package_loader() -> None:
    workflow = (
        _REPO_ROOT / ".github" / "workflows" / "qbittorrent-matrix.yml"
    ).read_text(encoding="utf-8")
    assert "qbit_ops.qbit.compatibility" in workflow
    assert "load_compatibility_evidence" in workflow
    assert "tests.integration._matrix" not in workflow


def test_freshness_checker_uses_the_package_loader() -> None:
    source = Path(
        _REPO_ROOT / "scripts" / "check_qbit_matrix_freshness.py"
    ).read_text(encoding="utf-8")
    assert "from qbit_ops.qbit.compatibility import" in source
    assert "tests.integration._matrix" not in source
    assert "sys.path.insert" not in source


def test_documentation_consistency_check_uses_the_package_loader() -> None:
    source = Path(
        _REPO_ROOT / "tests" / "test_compatibility_docs_consistency.py"
    ).read_text(encoding="utf-8")
    assert "qbit_ops.qbit.compatibility" in source
    assert "load_compatibility_evidence" in source


def test_makefile_help_reads_the_packaged_manifest_path() -> None:
    old_path = "/".join(["tests", "integration", "qbittorrent-matrix.toml"])
    makefile = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "src/qbit_ops/data/qbittorrent-matrix.toml" in makefile
    assert old_path not in makefile


def test_built_wheel_contains_the_packaged_manifest() -> None:
    """Builds a real wheel (poetry-core, no network) into a scratch
    directory and inspects its contents -- proves packaging config, not
    just source-tree layout, actually ships the manifest.

    Requires the `poetry` CLI on `PATH` -- true for every environment
    this repository's own tooling (`make check`/`make check-fast`) runs
    in, since they are themselves invoked via `poetry run`. Skips
    rather than failing where that assumption does not hold (e.g. a
    bare venv with only the built wheel's runtime deps installed).
    """
    if shutil.which("poetry") is None:
        pytest.skip("poetry CLI not on PATH")

    with tempfile.TemporaryDirectory() as tmp_dir:
        subprocess.run(
            ["poetry", "build", "--format", "wheel", "--output", tmp_dir],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        wheels = list(Path(tmp_dir).glob("*.whl"))
        assert len(wheels) == 1, wheels

        with zipfile.ZipFile(wheels[0]) as archive:
            names = archive.namelist()

        assert "qbit_ops/data/qbittorrent-matrix.toml" in names
        assert not any(name.startswith("tests/") for name in names)
