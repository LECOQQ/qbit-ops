"""Tests for scripts/check_version_sync.py.

Every case operates on temporary files (never the repository's own
`pyproject.toml` or `.release-please-manifest.json`) so this suite cannot
mutate real release state.
"""

from __future__ import annotations

from pathlib import Path

import check_version_sync as cvs
import pytest

POETRY_TOML = """\
[tool.poetry]
name = "qbit-ops"
version = "{version}"

[tool.commitizen]
name = "cz_conventional_commits"
version = "{cz_version}"
"""

PEP621_TOML = """\
[project]
name = "qbit-ops"
version = "{version}"
"""

CONFLICTING_TOML = """\
[project]
name = "qbit-ops"
version = "{project_version}"

[tool.poetry]
name = "qbit-ops"
version = "{poetry_version}"
"""

NO_VERSION_TOML = """\
[tool.poetry]
name = "qbit-ops"
"""


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def _manifest(tmp_path: Path, version: str | None, key: str = ".") -> Path:
    if version is None:
        body = "{}"
    else:
        body = f'{{"{key}": "{version}"}}'
    return _write(tmp_path, ".release-please-manifest.json", body)


def test_matching_poetry_version_and_manifest(tmp_path: Path) -> None:
    pyproject = _write(
        tmp_path,
        "pyproject.toml",
        POETRY_TOML.format(version="0.3.0", cz_version="0.3.0"),
    )
    manifest = _manifest(tmp_path, "0.3.0")

    message = cvs.check(pyproject, manifest, tag=None)

    assert "0.3.0" in message


def test_matching_pep621_version_and_manifest(tmp_path: Path) -> None:
    pyproject = _write(
        tmp_path, "pyproject.toml", PEP621_TOML.format(version="1.2.3")
    )
    manifest = _manifest(tmp_path, "1.2.3")

    message = cvs.check(pyproject, manifest, tag=None)

    assert "1.2.3" in message


def test_mismatching_pyproject_and_manifest(tmp_path: Path) -> None:
    pyproject = _write(
        tmp_path,
        "pyproject.toml",
        POETRY_TOML.format(version="0.3.0", cz_version="0.3.0"),
    )
    manifest = _manifest(tmp_path, "0.2.0")

    with pytest.raises(cvs.VersionSyncError, match="does not match"):
        cvs.check(pyproject, manifest, tag=None)


def test_missing_manifest_root_entry(tmp_path: Path) -> None:
    pyproject = _write(
        tmp_path,
        "pyproject.toml",
        POETRY_TOML.format(version="0.3.0", cz_version="0.3.0"),
    )
    manifest = _manifest(tmp_path, "0.3.0", key="some-other-package")

    with pytest.raises(cvs.VersionSyncError, match="root package entry"):
        cvs.check(pyproject, manifest, tag=None)


def test_missing_toml_version(tmp_path: Path) -> None:
    pyproject = _write(tmp_path, "pyproject.toml", NO_VERSION_TOML)
    manifest = _manifest(tmp_path, "0.3.0")

    with pytest.raises(cvs.VersionSyncError, match="declares no version"):
        cvs.check(pyproject, manifest, tag=None)


def test_conflicting_project_and_poetry_version(tmp_path: Path) -> None:
    pyproject = _write(
        tmp_path,
        "pyproject.toml",
        CONFLICTING_TOML.format(
            project_version="0.3.0", poetry_version="0.2.0"
        ),
    )
    manifest = _manifest(tmp_path, "0.3.0")

    with pytest.raises(
        cvs.VersionSyncError, match="Conflicting version declarations"
    ):
        cvs.check(pyproject, manifest, tag=None)


def test_conflicting_commitizen_version(tmp_path: Path) -> None:
    pyproject = _write(
        tmp_path,
        "pyproject.toml",
        POETRY_TOML.format(version="0.3.0", cz_version="0.2.0"),
    )
    manifest = _manifest(tmp_path, "0.3.0")

    with pytest.raises(cvs.VersionSyncError, match="tool.commitizen"):
        cvs.check(pyproject, manifest, tag=None)


def test_valid_tag_matches(tmp_path: Path) -> None:
    pyproject = _write(
        tmp_path,
        "pyproject.toml",
        POETRY_TOML.format(version="0.3.0", cz_version="0.3.0"),
    )
    manifest = _manifest(tmp_path, "0.3.0")

    message = cvs.check(pyproject, manifest, tag="v0.3.0")

    assert "matches" in message


def test_mismatching_tag(tmp_path: Path) -> None:
    pyproject = _write(
        tmp_path,
        "pyproject.toml",
        POETRY_TOML.format(version="0.3.0", cz_version="0.3.0"),
    )
    manifest = _manifest(tmp_path, "0.3.0")

    with pytest.raises(
        cvs.VersionSyncError, match="does not match the declared version"
    ):
        cvs.check(pyproject, manifest, tag="v0.4.0")


@pytest.mark.parametrize(
    "bad_tag", ["0.3.0", "vX.Y.Z", "v0.3", "qbit-ops-v0.3.0"]
)
def test_malformed_or_unsupported_tag_prefix(
    tmp_path: Path, bad_tag: str
) -> None:
    pyproject = _write(
        tmp_path,
        "pyproject.toml",
        POETRY_TOML.format(version="0.3.0", cz_version="0.3.0"),
    )
    manifest = _manifest(tmp_path, "0.3.0")

    with pytest.raises(cvs.VersionSyncError, match="expected vX.Y.Z format"):
        cvs.check(pyproject, manifest, tag=bad_tag)


def test_malformed_json_manifest(tmp_path: Path) -> None:
    pyproject = _write(
        tmp_path,
        "pyproject.toml",
        POETRY_TOML.format(version="0.3.0", cz_version="0.3.0"),
    )
    manifest = _write(
        tmp_path, ".release-please-manifest.json", "{not valid json"
    )

    with pytest.raises(cvs.VersionSyncError, match="not valid JSON"):
        cvs.check(pyproject, manifest, tag=None)


def test_malformed_toml_pyproject(tmp_path: Path) -> None:
    pyproject = _write(tmp_path, "pyproject.toml", "[tool.poetry\nversion = ")
    manifest = _manifest(tmp_path, "0.3.0")

    with pytest.raises(cvs.VersionSyncError, match="not valid TOML"):
        cvs.check(pyproject, manifest, tag=None)


def test_missing_pyproject_file(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    manifest = _manifest(tmp_path, "0.3.0")

    with pytest.raises(cvs.VersionSyncError, match="not found"):
        cvs.check(pyproject, manifest, tag=None)


def test_missing_manifest_file(tmp_path: Path) -> None:
    pyproject = _write(
        tmp_path,
        "pyproject.toml",
        POETRY_TOML.format(version="0.3.0", cz_version="0.3.0"),
    )
    manifest = tmp_path / ".release-please-manifest.json"

    with pytest.raises(cvs.VersionSyncError, match="not found"):
        cvs.check(pyproject, manifest, tag=None)


def test_cli_failure_returns_nonzero_and_prints_to_stderr(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pyproject = _write(
        tmp_path,
        "pyproject.toml",
        POETRY_TOML.format(version="0.3.0", cz_version="0.3.0"),
    )
    manifest = _manifest(tmp_path, "0.2.0")
    monkeypatch.setattr(cvs, "PYPROJECT_PATH", pyproject)
    monkeypatch.setattr(cvs, "MANIFEST_PATH", manifest)

    exit_code = cvs.main([])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "ERROR" in captured.err


def test_cli_success_returns_zero_and_prints_to_stdout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pyproject = _write(
        tmp_path,
        "pyproject.toml",
        POETRY_TOML.format(version="0.3.0", cz_version="0.3.0"),
    )
    manifest = _manifest(tmp_path, "0.3.0")
    monkeypatch.setattr(cvs, "PYPROJECT_PATH", pyproject)
    monkeypatch.setattr(cvs, "MANIFEST_PATH", manifest)

    exit_code = cvs.main([])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "OK" in captured.out
