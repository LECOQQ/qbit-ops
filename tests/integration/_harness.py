"""Hermetic Docker lifecycle for one qBittorrent matrix entry.

Every function here shells out to the `docker` CLI via `subprocess`
(no `docker-py` dependency) and only ever touches resources it created
itself: a dedicated bridge network, a uniquely named container, and
two temporary directories. Nothing here reads the repository `.env`,
`~/.config/qbit-ops/.env`, or the real user `HOME` -- see
`HermeticEnv` and `assert_no_ambient_qbit_ops_config` below, and
`tests/test_integration_harness_units.py` for the sabotage proof.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from tests.integration._matrix import MatrixEntry
from tests.integration._qbit_conf_template import (
    build_hermetic_qbittorrent_conf,
)

HARNESS_LABEL = "qbit-ops.harness"
MATRIX_ID_LABEL = "qbit-ops.matrix-id"
FIXED_WEBUI_USERNAME = "admin"

# Only ever bind published ports on loopback -- never 0.0.0.0, never a
# LAN-reachable interface. See item 2 of the phase spec.
LOOPBACK_HOST = "127.0.0.1"


class HarnessError(RuntimeError):
    """Report a harness setup, teardown, or guard failure."""


@dataclass(frozen=True)
class HermeticEnv:
    """The exact environment overrides used for every qbit-ops invocation.

    `QBIT_OPS_ENV_FILE` is pointed at a path that is guaranteed not to
    exist, inside a throwaway temporary directory -- per
    `qbit_ops.config._load_env_files`, an explicit
    `QBIT_OPS_ENV_FILE` short-circuits *all* default `.env` discovery
    (project `.env`, `~/.config/qbit-ops/.env`), so this alone is
    sufficient to make ordinary config discovery unreachable. `HOME`
    and `XDG_CONFIG_HOME` are additionally pointed at the same
    temporary directory as defense in depth, per AGENTS.md's smoke-test
    isolation rule.
    """

    home_dir: Path
    host: str
    username: str
    password: str

    def as_environ(self) -> dict[str, str]:
        """Return the env override dict for `CliRunner.invoke(env=...)`."""
        nonexistent_env_file = self.home_dir / "unreachable.env"
        return {
            "HOME": str(self.home_dir),
            "XDG_CONFIG_HOME": str(self.home_dir / ".config"),
            "QBIT_OPS_ENV_FILE": str(nonexistent_env_file),
            "QBIT_HOST": self.host,
            "QBIT_USER": self.username,
            "QBIT_PASSWORD": self.password,
        }


def assert_no_ambient_qbit_ops_config(env: dict[str, str]) -> None:
    """Fail closed if `env` could still discover a real `.env` file.

    Guards against the exact incident class AGENTS.md documents: a test
    silently falling back to `qbit_ops.config`'s ordinary discovery
    order. Every key this function requires must be present and must
    point at a path that does not exist yet -- an absent
    `QBIT_OPS_ENV_FILE` (falling back to default discovery) is a hard
    failure, not a warning.
    """
    required = (
        "HOME",
        "XDG_CONFIG_HOME",
        "QBIT_OPS_ENV_FILE",
        "QBIT_HOST",
        "QBIT_USER",
        "QBIT_PASSWORD",
    )
    missing = [key for key in required if not env.get(key)]
    if missing:
        raise HarnessError(
            f"hermetic environment is missing required variable(s): {missing}; "
            "refusing to run against ambient qbit-ops configuration discovery"
        )

    env_file = Path(env["QBIT_OPS_ENV_FILE"])
    if env_file.exists():
        raise HarnessError(
            f"QBIT_OPS_ENV_FILE {env_file} unexpectedly exists; "
            "a hermetic run must point at a path that is guaranteed absent"
        )

    real_home = Path(os.path.expanduser("~"))
    if Path(env["HOME"]) == real_home:
        raise HarnessError("hermetic HOME must not equal the real user HOME")


def assert_target_is_disposable(
    host: str, *, expected_loopback_host: str = LOOPBACK_HOST
) -> None:
    """Fail closed unless `host` is a loopback address the harness controls.

    `host` is the exact string qbit-ops's `QBIT_HOST` would receive --
    this must never be a LAN address, a real hostname, or anything
    other than the loopback interface a disposable container was
    published to.
    """
    if expected_loopback_host not in host:
        raise HarnessError(
            f"refusing to target {host!r}: not a loopback/disposable address "
            f"(expected {expected_loopback_host!r} in the host string)"
        )


def make_hermetic_env(
    *, host: str, username: str, password: str
) -> HermeticEnv:
    """Build a `HermeticEnv` rooted in a fresh temporary directory."""
    home_dir = Path(tempfile.mkdtemp(prefix="qbit-ops-it-home-"))
    (home_dir / ".config").mkdir(parents=True, exist_ok=True)
    return HermeticEnv(
        home_dir=home_dir, host=host, username=username, password=password
    )


def _run_docker(
    args: list[str], *, check: bool = True
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        check=check,
        timeout=120,
    )


def docker_is_available() -> bool:
    """Return whether the `docker` CLI is installed and the daemon responds."""
    if shutil.which("docker") is None:
        return False
    try:
        _run_docker(["version", "--format", "{{.Server.Version}}"])
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return False
    return True


@dataclass
class RunningQbitContainer:
    """Handle to one running, disposable qBittorrent container."""

    matrix_entry: MatrixEntry
    run_id: str
    container_name: str
    network_name: str
    config_dir: Path
    downloads_dir: Path
    host: str
    username: str
    password: str
    observed_version: str
    observed_web_api_version: str


def _wait_for_webui(host: str, timeout_seconds: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"{host}/api/v2/app/webapiVersion", timeout=2
            ) as resp:
                if resp.status == 200:
                    return
        except urllib.error.HTTPError as error:
            if error.code == 403:  # server is up, just not logged in yet
                return
            last_error = error
        except (urllib.error.URLError, OSError) as error:
            last_error = error
        time.sleep(1)
    raise HarnessError(
        f"qBittorrent WebUI at {host} did not become ready: {last_error}"
    )


def start_matrix_container(
    entry: MatrixEntry, *, run_id: str
) -> RunningQbitContainer:
    """Start one disposable, hermetic qBittorrent container for `entry`.

    Fails closed (raises `HarnessError`) if the observed `app_version()`
    does not match `entry.expected_version` -- a mismatched image tag
    must never be silently accepted as "close enough".
    """
    if not docker_is_available():
        raise HarnessError(
            "docker is not available; cannot start the matrix container"
        )

    network_name = f"qbit-ops-it-{run_id}-{entry.id}"
    container_name = f"qbit-ops-it-{run_id}-{entry.id}"
    config_root = Path(
        tempfile.mkdtemp(prefix=f"qbit-ops-it-config-{entry.id}-")
    )
    config_dir = config_root / "qBittorrent"
    config_dir.mkdir(parents=True)
    downloads_dir = Path(
        tempfile.mkdtemp(prefix=f"qbit-ops-it-downloads-{entry.id}-")
    )

    password = f"Test-{secrets.token_urlsafe(18)}"
    conf_text = build_hermetic_qbittorrent_conf(
        webui_port=entry.webui_port,
        webui_username=FIXED_WEBUI_USERNAME,
        webui_password=password,
    )
    (config_dir / "qBittorrent.conf").write_text(conf_text, encoding="utf-8")

    _run_docker(
        [
            "network",
            "create",
            "--label",
            f"{HARNESS_LABEL}={run_id}",
            network_name,
        ]
    )

    try:
        _run_docker(
            [
                "run",
                "-d",
                "--name",
                container_name,
                "--network",
                network_name,
                "--label",
                f"{HARNESS_LABEL}={run_id}",
                "--label",
                f"{MATRIX_ID_LABEL}={entry.id}",
                "-e",
                "PUID=1000",
                "-e",
                "PGID=1000",
                "-e",
                "TZ=Etc/UTC",
                "-e",
                f"WEBUI_PORT={entry.webui_port}",
                "-p",
                f"{LOOPBACK_HOST}:{entry.webui_port}:{entry.webui_port}",
                "-v",
                f"{config_dir}:/config/qBittorrent",
                "-v",
                f"{downloads_dir}:/downloads",
                entry.image_reference_with_digest,
            ]
        )
    except subprocess.CalledProcessError as error:
        # `docker run` can leave a non-running "Created" container
        # behind even when it ultimately fails (e.g. network setup
        # fails *after* the container object is created) -- always
        # attempt removal too, not just the network. Found via a real
        # port collision during the Docker matrix phase (see the
        # implementation note); `_force_remove_container` is a no-op
        # (check=False) when nothing was actually created.
        _cleanup_failed_start(
            container_name, network_name, config_root, downloads_dir
        )
        raise HarnessError(
            f"failed to start container {container_name}: {error.stderr}"
        ) from error

    host = f"http://{LOOPBACK_HOST}:{entry.webui_port}"
    try:
        _wait_for_webui(host)
        observed_version, observed_web_api_version = _read_versions(
            host, username=FIXED_WEBUI_USERNAME, password=password
        )
    except Exception:
        _cleanup_failed_start(
            container_name, network_name, config_root, downloads_dir
        )
        raise

    if observed_version != entry.expected_version:
        _cleanup_failed_start(
            container_name, network_name, config_root, downloads_dir
        )
        raise HarnessError(
            f"matrix entry {entry.id!r} expected qBittorrent "
            f"{entry.expected_version!r}, container reported "
            f"{observed_version!r}: refusing to run the matrix against "
            "an unverified version"
        )

    return RunningQbitContainer(
        matrix_entry=entry,
        run_id=run_id,
        container_name=container_name,
        network_name=network_name,
        config_dir=config_dir,
        downloads_dir=downloads_dir,
        host=host,
        username=FIXED_WEBUI_USERNAME,
        password=password,
        observed_version=observed_version,
        observed_web_api_version=observed_web_api_version,
    )


def _read_versions(
    host: str, *, username: str, password: str
) -> tuple[str, str]:
    import qbittorrentapi

    client = qbittorrentapi.Client(
        host=host, username=username, password=password
    )
    client.auth_log_in()
    return str(client.app_version()), str(client.app_web_api_version())


def _force_remove_container(name: str) -> None:
    _run_docker(["rm", "--force", "--volumes", name], check=False)


def _force_remove_network(name: str) -> None:
    _run_docker(["network", "rm", name], check=False)


def _cleanup_failed_start(
    container_name: str,
    network_name: str,
    config_root: Path,
    downloads_dir: Path,
) -> None:
    """Best-effort cleanup for every `start_matrix_container` failure path.

    `docker run` can leave a non-running "Created" container behind
    even when it ultimately fails (e.g. network setup fails *after*
    the container object is created) -- found via a real port
    collision during the Docker matrix phase (see the implementation
    note). Always attempt every resource's removal, never just the one
    that "should" need it.
    """
    _force_remove_container(container_name)
    _force_remove_network(network_name)
    shutil.rmtree(config_root, ignore_errors=True)
    shutil.rmtree(downloads_dir, ignore_errors=True)


def teardown_matrix_container(container: RunningQbitContainer) -> None:
    """Tear down a container's container, network, and temp directories.

    Always runs to completion (best-effort on each step) so a failure
    partway through does not skip the remaining cleanup steps, then
    raises if anything was left behind.
    """
    errors: list[str] = []

    _force_remove_container(container.container_name)
    _force_remove_network(container.network_name)

    for path in (container.config_dir.parent, container.downloads_dir):
        try:
            shutil.rmtree(path, ignore_errors=False)
        except OSError as error:
            errors.append(f"failed to remove {path}: {error}")

    leaked = _find_leaked_resources(container.run_id)
    if leaked:
        errors.append(f"leaked docker resources after teardown: {leaked}")

    if errors:
        raise HarnessError("; ".join(errors))


def _find_leaked_resources(run_id: str) -> list[str]:
    leaked: list[str] = []

    containers = _run_docker(
        ["ps", "-aq", "--filter", f"label={HARNESS_LABEL}={run_id}"],
        check=False,
    )
    if containers.stdout.strip():
        leaked.extend(f"container:{line}" for line in containers.stdout.split())

    networks = _run_docker(
        ["network", "ls", "-q", "--filter", f"label={HARNESS_LABEL}={run_id}"],
        check=False,
    )
    if networks.stdout.strip():
        leaked.extend(f"network:{line}" for line in networks.stdout.split())

    return leaked


def start_tracker_container(
    *, run_id: str, network_name: str, tracker_alias: str, port: int
) -> str:
    """Start the disposable tracker service as its own container.

    Runs `_tracker_service.py` (bind-mounted alongside `_bencode.py`)
    inside a pinned `python:3.12-alpine` image, on the same disposable
    network as the qBittorrent container under test -- so
    `http://<tracker_alias>:<port>/announce` resolves only inside that
    network, never on the host or a public network.
    """
    integration_dir = Path(__file__).parent
    container_name = f"qbit-ops-it-{run_id}-tracker"

    _run_docker(
        [
            "run",
            "-d",
            "--name",
            container_name,
            "--network",
            network_name,
            "--network-alias",
            tracker_alias,
            "--label",
            f"{HARNESS_LABEL}={run_id}",
            "-v",
            f"{integration_dir / '_tracker_service.py'}"
            ":/harness/_tracker_service.py:ro",
            "-v",
            f"{integration_dir / '_bencode.py'}:/harness/_bencode.py:ro",
            "-w",
            "/harness",
            "python:3.12-alpine",
            "python3",
            "_tracker_service.py",
            "0.0.0.0",
            str(port),
        ]
    )
    return container_name


def read_tracker_announce_log(container_name: str) -> list[dict]:
    """Parse the tracker container's stdout log into announce records."""
    result = _run_docker(["logs", container_name], check=False)
    records = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        records.append(json.loads(line))
    return records
