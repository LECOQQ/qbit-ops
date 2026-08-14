"""Prove the built distribution is publishable and actually usable.

Everything here operates on artifacts built into `tmp_path`, never on a
committed `dist/`. The hermetic checks read the wheel and sdist
directly; the one test that installs the wheel needs PyPI to resolve
dependencies and is therefore marked `network`, excluded from
`make check`/`make check-fast` and run by `make check-dist`.
"""

from __future__ import annotations

import configparser
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

from tests.support import without_comments

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _declared_version() -> str:
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    return pyproject["tool"]["poetry"]["version"]


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Build wheel + sdist once, and return them."""
    if shutil.which("poetry") is None:
        pytest.skip("poetry CLI not on PATH")

    out = tmp_path_factory.mktemp("dist")
    subprocess.run(
        ["poetry", "build", "--output", str(out)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    (wheel,) = out.glob("*.whl")
    (sdist,) = out.glob("*.tar.gz")
    return wheel, sdist


@pytest.fixture(scope="module")
def wheel_metadata(built: tuple[Path, Path]) -> str:
    wheel, _ = built
    with zipfile.ZipFile(wheel) as archive:
        name = next(n for n in archive.namelist() if n.endswith("METADATA"))
        return archive.read(name).decode("utf-8")


def test_building_produces_both_a_wheel_and_an_sdist(
    built: tuple[Path, Path],
) -> None:
    wheel, sdist = built

    assert wheel.name.endswith("-py3-none-any.whl")
    assert sdist.name == f"qbit_ops-{_declared_version()}.tar.gz"


def test_the_built_version_is_the_single_declared_version(
    wheel_metadata: str,
) -> None:
    """No second source of truth: the artifact carries exactly the
    version `pyproject.toml` declares and Release Please maintains."""
    assert f"\nVersion: {_declared_version()}\n" in wheel_metadata


@pytest.mark.parametrize(
    "field",
    [
        "Name: qbit-ops",
        "License: MIT",
        "Requires-Python: >=3.12",
        "Description-Content-Type: text/markdown",
        "Classifier: Environment :: Console",
        "Project-URL: Repository, https://github.com/LECOQQ/qbit-ops",
    ],
)
def test_metadata_carries_the_fields_pypi_renders(
    wheel_metadata: str, field: str
) -> None:
    assert field in wheel_metadata


def test_the_long_description_has_no_repository_relative_assets(
    wheel_metadata: str,
) -> None:
    """PyPI renders the README without repository context, so a relative
    image or document path resolves to nothing there."""
    assert 'src="docs/' not in wheel_metadata
    assert "](docs/" not in wheel_metadata


def test_the_wheel_ships_both_packages_and_no_tests(
    built: tuple[Path, Path],
) -> None:
    wheel, _ = built
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()

    assert any(n.startswith("qbit_ops/") for n in names)
    assert any(n.startswith("qbit_core/") for n in names)
    assert not any(n.startswith("tests/") for n in names)
    assert not any(n.startswith("scripts/") for n in names)


def test_the_wheel_ships_the_packaged_compatibility_matrix(
    built: tuple[Path, Path],
) -> None:
    """`doctor` reads this data file at runtime; losing it from the wheel
    would only surface after install."""
    wheel, _ = built
    with zipfile.ZipFile(wheel) as archive:
        assert "qbit_core/data/qbittorrent-matrix.toml" in archive.namelist()


def test_the_wheel_declares_the_console_script(
    built: tuple[Path, Path],
) -> None:
    wheel, _ = built
    with zipfile.ZipFile(wheel) as archive:
        name = next(
            n for n in archive.namelist() if n.endswith("entry_points.txt")
        )
        parser = configparser.ConfigParser()
        parser.read_string(archive.read(name).decode("utf-8"))

    assert parser["console_scripts"]["qbit-ops"] == "qbit_ops.cli.app:app"


def test_the_wheel_declares_the_optional_tui_extra(
    wheel_metadata: str,
) -> None:
    assert "Provides-Extra: tui" in wheel_metadata
    assert 'extra == "tui"' in wheel_metadata


def test_the_sdist_carries_what_a_rebuild_needs(
    built: tuple[Path, Path],
) -> None:
    _, sdist = built
    with tarfile.open(sdist) as archive:
        names = {
            Path(*Path(n).parts[1:]).as_posix() for n in archive.getnames()
        }

    assert "pyproject.toml" in names
    assert "README.md" in names
    assert "LICENSE" in names
    assert "src/qbit_ops/cli/app.py" in names


def test_twine_accepts_both_artifacts(built: tuple[Path, Path]) -> None:
    """The same gate the publish workflow runs before uploading."""
    wheel, sdist = built
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "twine",
            "check",
            "--strict",
            str(wheel),
            str(sdist),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.network
def test_the_entrypoint_works_after_installing_the_built_wheel(
    built: tuple[Path, Path], tmp_path: Path
) -> None:
    """The end-to-end shape of `uv tool install` / `pipx install`: a fresh
    interpreter, the real wheel, real dependency resolution, and the
    console script actually invoked."""
    wheel, _ = built
    venv = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv)], check=True, timeout=120
    )
    subprocess.run(
        [
            str(venv / "bin" / "python"),
            "-m",
            "pip",
            "install",
            "-q",
            str(wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )

    result = subprocess.run(
        [str(venv / "bin" / "qbit-ops"), "--version"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"qbit-ops {_declared_version()}"


# PyPI proxies every long-description image through its camo service
# (`img-src 'self' https://pypi-camo.freetls.fastly.net/ ...`). An asset
# that is megabytes large is a bad bet through any proxy, and a bad
# experience on a slow connection -- link to it instead of inlining it.
MAX_INLINE_ASSET_BYTES = 5 * 1024 * 1024


def test_readme_never_inlines_a_heavyweight_asset() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    inlined = re.findall(
        r"!\[[^\]]*\]\("
        r"https://raw\.githubusercontent\.com/LECOQQ/qbit-ops/main/([^)]+)\)",
        readme,
    )

    assert inlined, "expected the README to inline at least one asset"

    oversized = {
        name: (REPO_ROOT / name).stat().st_size
        for name in inlined
        if (REPO_ROOT / name).exists()
        and (REPO_ROOT / name).stat().st_size > MAX_INLINE_ASSET_BYTES
    }

    assert not oversized, (
        f"inlined README assets above {MAX_INLINE_ASSET_BYTES} bytes: "
        f"{oversized}. Link to them behind a lightweight poster instead."
    )


# --- Publishing configuration guardrails -------------------------------


@pytest.fixture(scope="module")
def publish_workflow() -> str:
    return without_comments(
        (WORKFLOWS / "publish.yml").read_text(encoding="utf-8")
    )


def test_publishing_uses_oidc_and_never_a_stored_token(
    publish_workflow: str,
) -> None:
    assert "id-token: write" in publish_workflow
    assert "pypa/gh-action-pypi-publish" in publish_workflow
    assert "PYPI_API_TOKEN" not in publish_workflow
    assert "password:" not in publish_workflow


def test_publishing_runs_in_the_pypi_environment(publish_workflow: str) -> None:
    """PyPI's Trusted Publisher is bound to this environment name."""
    assert "name: pypi" in publish_workflow


def test_publishing_validates_artifacts_before_uploading(
    publish_workflow: str,
) -> None:
    upload_at = publish_workflow.index("pypa/gh-action-pypi-publish")

    for gate in ("twine check --strict", "qbit-ops --version"):
        assert gate in publish_workflow
        assert (
            publish_workflow.index(gate) < upload_at
        ), f"{gate!r} must run before the upload step"


def test_publish_workflow_is_never_reusable(publish_workflow: str) -> None:
    """PyPI matches the `job_workflow_ref` OIDC claim against the declared
    workflow filename and explicitly rejects reusable workflows, so
    calling this one via `workflow_call` would fail `invalid-publisher`.
    The publish job must stay defined in this file.

    https://docs.pypi.org/trusted-publishers/troubleshooting/
    """
    trigger_keys = {
        line.strip().rstrip(":")
        for line in publish_workflow.splitlines()
        if line.startswith("  ") and line.strip().endswith(":")
    }

    assert "workflow_call" not in trigger_keys
    assert "workflow_dispatch" in trigger_keys


@pytest.fixture(scope="module")
def release_workflow() -> str:
    return without_comments(
        (WORKFLOWS / "release-please.yml").read_text(encoding="utf-8")
    )


def test_release_please_triggers_publishing_on_a_created_release(
    release_workflow: str,
) -> None:
    """The release mechanism stays single: publishing hangs off Release
    Please's own output, never a second versioning scheme."""
    assert "release_created" in release_workflow
    assert "gh workflow run publish.yml" in release_workflow
    assert "uses: ./.github/workflows/publish.yml" not in release_workflow


def test_release_please_can_dispatch_the_publish_workflow(
    release_workflow: str,
) -> None:
    """`gh workflow run` needs `actions: write`; without it the dispatch
    fails after the release is already cut."""
    assert "actions: write" in release_workflow
