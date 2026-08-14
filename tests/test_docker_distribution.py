"""Prove the Docker distribution is publishable, safe and correctly tagged.

Most checks read the Dockerfile, the workflow and the Compose example --
they are hermetic and run in `make check`. The one test that actually
builds the image is marked `image`, excluded from `make check` and
`make check-fast`, and run by `make check-image`.

Nothing here pushes anything: GHCR is never contacted.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from tests.support import without_comments

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-container.yml"
COMPOSE = REPO_ROOT / "docs" / "examples" / "docker-compose.yml"

IMAGE_NAME = "ghcr.io/lecoqq/qbit-ops"

REQUIRED_LABELS = (
    "org.opencontainers.image.title",
    "org.opencontainers.image.description",
    "org.opencontainers.image.source",
    "org.opencontainers.image.url",
    "org.opencontainers.image.documentation",
    "org.opencontainers.image.version",
    "org.opencontainers.image.revision",
    "org.opencontainers.image.created",
    "org.opencontainers.image.licenses",
)


def _declared_version() -> str:
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    return pyproject["tool"]["poetry"]["version"]


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return without_comments(DOCKERFILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def workflow() -> str:
    return without_comments(WORKFLOW.read_text(encoding="utf-8"))


# --- Dockerfile --------------------------------------------------------


def test_entrypoint_is_the_cli_in_exec_form(dockerfile: str) -> None:
    """Exec form, so signals reach qbit-ops and arguments append to it
    instead of being re-parsed by a shell."""
    assert 'ENTRYPOINT ["qbit-ops"]' in dockerfile


def test_the_image_runs_as_a_non_root_user(dockerfile: str) -> None:
    assert re.search(r"^USER 1000:1000$", dockerfile, re.MULTILINE)


def test_data_is_the_conventional_mount_point(dockerfile: str) -> None:
    assert 'VOLUME ["/data"]' in dockerfile
    assert re.search(r"^WORKDIR /data$", dockerfile, re.MULTILINE)


def test_the_build_is_multi_stage_so_poetry_never_ships(
    dockerfile: str,
) -> None:
    stages = re.findall(r"^FROM .+ AS (\w+)$", dockerfile, re.MULTILINE)

    assert stages == ["builder", "runtime"]


def test_no_secret_or_control_plane_path_is_ever_copied(
    dockerfile: str,
) -> None:
    """A blanket `COPY . .` would drag `.env` and `.agents/` in whenever
    `.dockerignore` regressed. The Dockerfile copies named paths only."""
    copies = re.findall(r"^COPY\s+(.+)$", dockerfile, re.MULTILINE)

    assert copies, "expected the Dockerfile to copy something"
    for copy in copies:
        sources = copy.split()[:-1]
        assert "." not in sources, f"blanket COPY: {copy!r}"
        for source in sources:
            assert not source.startswith(".env")
            assert not source.startswith(".agents")


@pytest.mark.parametrize(
    "excluded",
    [".env", ".agents", "AGENTS.md", "tests", ".git", ".venv", "dist"],
)
def test_dockerignore_excludes_irrelevant_repository_files(
    excluded: str,
) -> None:
    entries = {
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }

    assert excluded in entries


def test_dockerignore_keeps_what_the_wheel_build_needs() -> None:
    """`README.md` is the wheel's long description; excluding it fails
    the build rather than merely shrinking the context."""
    entries = {
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }

    for required in ("README.md", "LICENSE", "pyproject.toml", "src"):
        assert required not in entries


# --- Tag derivation ----------------------------------------------------


def test_the_exact_version_tag_is_always_published(workflow: str) -> None:
    """Unconditional on purpose: a valid stable release always gets its
    X.Y.Z tag, whatever the floating-tag ranking says."""
    assert "type=raw,value=${{ steps.plan.outputs.version }}\n" in workflow


@pytest.mark.parametrize(
    ("tag_expression", "gate"),
    [
        ("${{ steps.plan.outputs.minor }}", "is_newest_in_minor"),
        ("latest", "is_newest"),
    ],
)
def test_each_floating_tag_is_gated_on_its_own_ranking(
    workflow: str, tag_expression: str, gate: str
) -> None:
    """Neither floating tag may be emitted unconditionally: republishing
    an older release must not drag them backwards."""
    expected = (
        f"type=raw,value={tag_expression},"
        f"enable=${{{{ steps.plan.outputs.{gate} }}}}"
    )

    assert expected in workflow


def test_the_ranking_reads_git_tags_and_never_a_registry(
    workflow: str,
) -> None:
    assert "scripts/docker_tags.py --tag" in workflow
    for registry_probe in ("imagetools inspect", "docker manifest", "skopeo"):
        assert registry_probe not in workflow


def test_metadata_action_cannot_add_latest_by_itself(workflow: str) -> None:
    """`latest=auto` looks only at the tag being built, so it would
    happily re-point `latest` at an older release."""
    assert "flavor: latest=false" in workflow


def test_no_floating_major_only_tag_is_published(workflow: str) -> None:
    assert "{{major}}" not in workflow


def test_latest_is_unreachable_from_a_normal_push(workflow: str) -> None:
    """Structural, not conventional: the workflow has no push trigger at
    all, so no commit to main can move a release tag."""
    trigger_keys = {
        line.strip().rstrip(":")
        for line in workflow.splitlines()
        if line.startswith("  ") and line.strip().endswith(":")
    }

    assert "push" not in trigger_keys
    assert "workflow_dispatch" in trigger_keys


def test_the_image_version_is_cross_checked_against_the_tag(
    workflow: str,
) -> None:
    assert "scripts/check_version_sync.py --tag" in workflow


# --- Publishing configuration ------------------------------------------


def test_the_workflow_publishes_both_supported_architectures(
    workflow: str,
) -> None:
    assert "platforms: linux/amd64,linux/arm64" in workflow


def test_ghcr_login_uses_the_builtin_token_not_a_pat(workflow: str) -> None:
    assert "registry: ghcr.io" in workflow
    assert "password: ${{ secrets.GITHUB_TOKEN }}" in workflow
    for forbidden in ("GHCR_PAT", "CR_PAT", "DOCKERHUB", "PERSONAL_ACCESS"):
        assert forbidden not in workflow


def test_the_workflow_requests_only_the_permissions_it_needs(
    workflow: str,
) -> None:
    assert "packages: write" in workflow
    assert "contents: read" in workflow
    assert "contents: write" not in workflow


def test_labels_come_from_dockers_metadata_tooling(workflow: str) -> None:
    assert "docker/metadata-action" in workflow
    assert "labels: ${{ steps.meta.outputs.labels }}" in workflow


def test_release_please_dispatches_the_container_publish() -> None:
    """One release mechanism: the container hangs off Release Please's
    own output, exactly like the PyPI upload."""
    release_workflow = without_comments(
        (REPO_ROOT / ".github" / "workflows" / "release-please.yml").read_text(
            encoding="utf-8"
        )
    )

    assert "gh workflow run publish-container.yml" in release_workflow
    assert "release_created" in release_workflow


# --- Compose example ---------------------------------------------------


@pytest.fixture(scope="module")
def compose() -> str:
    return without_comments(COMPOSE.read_text(encoding="utf-8"))


def test_the_compose_example_uses_the_canonical_image(compose: str) -> None:
    assert f"image: {IMAGE_NAME}:latest" in compose


def test_the_compose_example_mounts_data_and_read_only_input(
    compose: str,
) -> None:
    assert "./qbit-data:/data" in compose
    assert ":/input:ro" in compose


def test_the_compose_example_never_embeds_credentials(compose: str) -> None:
    """Only interpolation references may appear -- never a literal."""
    for variable in ("QBIT_HOST", "QBIT_USER", "QBIT_PASSWORD"):
        for line in compose.splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{variable}:"):
                assert "${" in stripped, stripped


def test_the_compose_example_does_not_fake_a_daemon(compose: str) -> None:
    """qbit-ops is an on-demand CLI; a container kept alive to look like
    a service would be a lie about the product.

    Comments are stripped first: this file *explains* which tricks it
    avoids, and matching prose instead of configuration would make the
    check pass or fail on the wording.
    """
    for trick in ("sleep infinity", "tail -f /dev/null", "restart:"):
        assert trick not in compose
    assert 'profiles: ["cli"]' in compose


# --- Real image build --------------------------------------------------


@pytest.mark.image
def test_the_built_image_runs_the_cli_and_reports_the_project_version() -> None:
    """Builds the real image and exercises the entrypoint. Never pushes."""
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not on PATH")

    tag = "qbit-ops:pytest-image-check"
    build = subprocess.run(
        [
            "docker",
            "build",
            "--build-arg",
            f"VERSION={_declared_version()}",
            "--build-arg",
            "REVISION=pytest",
            "--build-arg",
            "CREATED=1970-01-01T00:00:00Z",
            "-t",
            tag,
            str(REPO_ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    assert build.returncode == 0, build.stderr[-4000:]

    try:
        version = subprocess.run(
            ["docker", "run", "--rm", tag, "--version"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert version.returncode == 0, version.stderr
        assert version.stdout.strip() == f"qbit-ops {_declared_version()}"

        help_result = subprocess.run(
            ["docker", "run", "--rm", tag, "--help"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert help_result.returncode == 0, help_result.stderr
        assert "torrents" in help_result.stdout

        inspected = subprocess.run(
            ["docker", "inspect", tag, "--format", "{{json .Config}}"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        config = json.loads(inspected.stdout)

        for label in REQUIRED_LABELS:
            assert label in config["Labels"], label
        assert config["Labels"]["org.opencontainers.image.licenses"] == "MIT"
        assert (
            config["Labels"]["org.opencontainers.image.source"]
            == "https://github.com/LECOQQ/qbit-ops"
        )
        assert config["Labels"]["org.opencontainers.image.version"] == (
            _declared_version()
        )
        assert config["User"] == "1000:1000"
        assert config["Entrypoint"] == ["qbit-ops"]
        assert config["WorkingDir"] == "/data"

        # Nothing private may have travelled into the image.
        leaked = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "sh",
                tag,
                "-c",
                "ls -a / /data 2>/dev/null; "
                "find / -name '.env' -not -path '/proc/*' 2>/dev/null",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert ".env" not in leaked.stdout
        assert "AGENTS.md" not in leaked.stdout
        assert "tests" not in leaked.stdout
    finally:
        subprocess.run(
            ["docker", "image", "rm", "-f", tag],
            capture_output=True,
            timeout=120,
        )
