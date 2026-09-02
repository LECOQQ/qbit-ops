"""Prove scripts/install.sh keeps its three non-negotiable guardrails.

Most checks run the real script under `sh`, with a sandboxed `PATH` and
`HOME` so nothing ever reaches a real network or a real `uv`/qBittorrent
-- fake `id`, `curl` and `uv` executables stand in, and every assertion
reads either the exit code or the transcript they produced. Nothing here
asserts on the script's prose (AGENTS.md, "asserter le comportement,
jamais la prose"): each check exercises a behaviour a broken script
would actually get wrong.

The one test that reaches the real network and a real disposable
container is marked `install`, excluded from `make check`/
`make check-fast`, and run by `make check-install-script`.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import textwrap
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install.sh"
CHECKSUM_FILE = REPO_ROOT / "scripts" / "install.sh.sha256"
README = REPO_ROOT / "README.md"

# The real Astral installer URL the script must call when uv is
# missing -- pinned so a silent switch to a different source is caught.
UV_INSTALL_URL = "https://astral.sh/uv/install.sh"
PACKAGE_SPEC = "qbit-ops[tui]"

FAKE_ID = """#!/bin/sh
echo "$FAKE_UID"
"""

# Refuses outright: any scenario expecting this fake to run proves the
# script tried to reach the network when it must not have.
POISON_CURL = """#!/bin/sh
printf '%s\\n' "$*" >> "$CURL_CALL_LOG"
exit 1
"""

# Simulates the real Astral installer's user-visible effect (a `uv`
# landing in `$HOME/.local/bin`, plus its `env` shim) without touching
# the network. Prints nothing to stdout: install.sh pipes this into
# `sh`, and real output there would be executed as a script.
FAKE_CURL_INSTALLING_UV = """#!/bin/sh
printf '%s\\n' "$*" >> "$CURL_CALL_LOG"
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/uv" <<'UV_EOF'
#!/bin/sh
printf '%s\\n' "$*" >> "$UV_CALL_LOG"
case "$1 $2" in
    "tool dir") echo "$HOME/.local/bin" ;;
    "tool install") echo "FAKE_UV_TOOL_INSTALL_RAN" ;;
esac
exit 0
UV_EOF
chmod +x "$HOME/.local/bin/uv"
printf 'export PATH="$HOME/.local/bin:$PATH"\\n' > "$HOME/.local/bin/env"
"""

FAKE_UV_ALREADY_PRESENT = """#!/bin/sh
printf '%s\\n' "$*" >> "$UV_CALL_LOG"
case "$1 $2" in
    "tool dir") echo "$FAKE_UV_BIN_DIR" ;;
    "tool install") echo "FAKE_UV_TOOL_INSTALL_RAN" ;;
esac
exit 0
"""


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run_script(
    tmp_path: Path,
    *,
    uid: int,
    fake_bin_files: dict[str, str],
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "id", FAKE_ID)
    for name, content in fake_bin_files.items():
        _write_executable(fake_bin / name, content)

    home = tmp_path / "home"
    home.mkdir()

    env = {
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "HOME": str(home),
        "FAKE_UID": str(uid),
        "CURL_CALL_LOG": str(tmp_path / "curl.log"),
        "UV_CALL_LOG": str(tmp_path / "uv.log"),
    }
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        ["sh", str(INSTALL_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


# --- static shape --------------------------------------------------------


def test_the_script_has_a_posix_sh_shebang() -> None:
    first_line = INSTALL_SCRIPT.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "#!/bin/sh"


def test_the_script_is_executable() -> None:
    assert os.access(INSTALL_SCRIPT, os.X_OK)


def test_the_readme_pins_install_sh_to_the_current_release() -> None:
    """A `main`-relative URL would change under a reader's feet -- the
    spec requires a tag-pinned raw.githubusercontent.com path. Pinning
    it means every release must update it, so this stays a hard
    failure rather than a doc that quietly drifts (AGENTS.md,
    "affirmations chiffrées")."""
    declared_version = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["poetry"]["version"]

    urls = re.findall(
        r"https://raw\.githubusercontent\.com/LECOQQ/qbit-ops/"
        r"([^/]+)/scripts/install\.sh(?:\.sha256)?",
        README.read_text(encoding="utf-8"),
    )

    assert urls, "README.md has no raw.githubusercontent.com install.sh link"
    for ref in urls:
        assert ref != "main", "install.sh must be served from a tag, never main"
        assert ref == f"v{declared_version}", (
            f"README.md pins scripts/install.sh to {ref!r}, but the "
            f"declared version is {declared_version!r}"
        )


def test_the_published_checksum_matches_the_script() -> None:
    digest = hashlib.sha256(INSTALL_SCRIPT.read_bytes()).hexdigest()
    recorded = CHECKSUM_FILE.read_text(encoding="utf-8").split()[0]

    assert digest == recorded, (
        "scripts/install.sh.sha256 is stale. Regenerate it with: "
        "sha256sum scripts/install.sh > scripts/install.sh.sha256"
    )


# --- refusing root ---------------------------------------------------------


def test_refuses_to_run_as_root(tmp_path: Path) -> None:
    result = _run_script(
        tmp_path,
        uid=0,
        fake_bin_files={"curl": POISON_CURL},
    )

    assert result.returncode == 1
    assert "root" in result.stderr.lower()


def test_root_refusal_happens_before_any_network_call(tmp_path: Path) -> None:
    _run_script(tmp_path, uid=0, fake_bin_files={"curl": POISON_CURL})

    assert not (tmp_path / "curl.log").exists()


# --- announcing before acting ----------------------------------------------


def test_announces_the_plan_before_installing_uv(tmp_path: Path) -> None:
    result = _run_script(
        tmp_path,
        uid=1000,
        fake_bin_files={"curl": FAKE_CURL_INSTALLING_UV},
    )

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()

    def index_containing(needle: str) -> int:
        return next(i for i, line in enumerate(lines) if needle in line)

    announce_uv = index_containing("uv not found. About to install it")
    uv_bin = tmp_path / "home" / ".local" / "bin" / "uv"
    announce_install = index_containing(
        f'About to run: {uv_bin} tool install "{PACKAGE_SPEC}"'
    )
    mutation = index_containing("FAKE_UV_TOOL_INSTALL_RAN")

    assert announce_uv < announce_install < mutation


def test_installs_uv_via_the_pinned_astral_url_when_missing(
    tmp_path: Path,
) -> None:
    _run_script(
        tmp_path,
        uid=1000,
        fake_bin_files={"curl": FAKE_CURL_INSTALLING_UV},
    )

    curl_log = (tmp_path / "curl.log").read_text(encoding="utf-8")
    assert UV_INSTALL_URL in curl_log


def test_skips_installing_uv_when_already_present(tmp_path: Path) -> None:
    fake_uv_dir = tmp_path / "home" / ".local" / "bin"
    result = _run_script(
        tmp_path,
        uid=1000,
        fake_bin_files={
            "uv": FAKE_UV_ALREADY_PRESENT,
            "curl": POISON_CURL,
        },
        extra_env={"FAKE_UV_BIN_DIR": str(fake_uv_dir)},
    )

    assert result.returncode == 0, result.stderr
    assert "uv is already installed" in result.stdout
    assert not (tmp_path / "curl.log").exists()


def test_reports_the_package_spec_and_install_location(tmp_path: Path) -> None:
    fake_uv_dir = tmp_path / "home" / ".local" / "bin"
    result = _run_script(
        tmp_path,
        uid=1000,
        fake_bin_files={
            "uv": FAKE_UV_ALREADY_PRESENT,
            "curl": POISON_CURL,
        },
        extra_env={"FAKE_UV_BIN_DIR": str(fake_uv_dir)},
    )

    assert result.returncode == 0, result.stderr
    assert PACKAGE_SPEC in result.stdout
    assert str(fake_uv_dir) in result.stdout


# --- real end-to-end, inside a disposable container ------------------------


@pytest.mark.install
def test_the_real_script_installs_qbit_ops_in_a_debian_12_container() -> None:
    """Debian 12 ships no Python at all -- the exact case the whole
    "wrap uv" decision rests on. Proves uv downloads its own managed
    Python and the script completes without root and without touching
    a system Python."""
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not on PATH")

    # Bind-mounted read-only rather than embedded in a heredoc: the
    # exact bytes shellcheck and the checksum test already validated,
    # with no re-quoting step of our own that could drift from them.
    container_script = textwrap.dedent("""
        set -e
        apt-get update -qq >/dev/null
        apt-get install -y -qq curl ca-certificates >/dev/null

        if sh /mnt/install.sh; then
            echo "UNEXPECTED: root run did not fail"
            exit 1
        fi

        useradd -m testuser
        cp /mnt/install.sh /home/testuser/install.sh
        chown testuser:testuser /home/testuser/install.sh
        su - testuser -c "sh /home/testuser/install.sh"

        su - testuser -c \
            'export PATH="$HOME/.local/bin:$PATH"; qbit-ops --version'
        """)

    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{INSTALL_SCRIPT}:/mnt/install.sh:ro",
            "debian:12",
            "bash",
            "-c",
            container_script,
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "qbit-ops " in result.stdout
