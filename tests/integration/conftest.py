"""pytest wiring for the hermetic Docker qBittorrent version matrix.

Every test under `tests/integration/` is skipped unless
`QBIT_OPS_DOCKER_MATRIX=1` is set in the environment -- these tests
start real, disposable Docker containers and must never run as part
of an ordinary `pytest`/`make check` invocation (no Docker requirement
leaks into the default test suite). `make test-qbit-matrix` and
`make test-qbit-version` are the supported entry points; see the
Makefile.
"""

from __future__ import annotations

import uuid

import pytest

from tests.integration._harness import docker_is_available
from tests.integration._matrix import MatrixEntry, load_matrix

OPT_IN_VARIABLE = "QBIT_OPS_DOCKER_MATRIX"


def _opted_in() -> bool:
    import os

    return os.getenv(OPT_IN_VARIABLE) == "1"


@pytest.fixture(autouse=True, scope="session")
def _require_opt_in() -> None:
    if not _opted_in():
        pytest.skip(
            f"Docker matrix tests require {OPT_IN_VARIABLE}=1 "
            "(use `make test-qbit-matrix` / `make test-qbit-version`)"
        )
    if not docker_is_available():
        pytest.fail(
            "QBIT_OPS_DOCKER_MATRIX=1 but the docker CLI/daemon is not "
            "available"
        )


@pytest.fixture(scope="session")
def matrix_run_id() -> str:
    """A short id shared by every container/network this session creates."""
    return uuid.uuid4().hex[:10]


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "matrix_entry" in metafunc.fixturenames:
        import os

        only_id = os.getenv("QBIT_MATRIX_ID")
        entries = load_matrix()
        if only_id:
            entries = [entry for entry in entries if entry.id == only_id]
            if not entries:
                raise pytest.UsageError(f"unknown QBIT_MATRIX_ID={only_id!r}")
        metafunc.parametrize(
            "matrix_entry", entries, ids=[entry.id for entry in entries]
        )


@pytest.fixture(scope="function")
def qbit_container(matrix_entry: MatrixEntry, matrix_run_id: str):
    """Start one disposable, verified qBittorrent container for the entry."""
    from tests.integration._harness import (
        start_matrix_container,
        teardown_matrix_container,
    )

    container = start_matrix_container(matrix_entry, run_id=matrix_run_id)
    try:
        yield container
    finally:
        teardown_matrix_container(container)


@pytest.fixture(scope="function")
def tracker_service(qbit_container, matrix_run_id: str):
    """Start the disposable in-network tracker alongside `qbit_container`."""
    from tests.integration._harness import (
        _force_remove_container,
        read_tracker_announce_log,
        start_tracker_container,
    )

    alias = "qbit-ops-tracker"
    port = 6969
    container_name = start_tracker_container(
        run_id=matrix_run_id,
        network_name=qbit_container.network_name,
        tracker_alias=alias,
        port=port,
    )
    try:
        announce_url = f"http://{alias}:{port}/announce"
        yield announce_url, container_name, read_tracker_announce_log
    finally:
        _force_remove_container(container_name)


@pytest.fixture(scope="function")
def seeded_corpus(qbit_container, tracker_service):
    """Seed the deterministic 3-torrent corpus into `qbit_container`."""
    from tests.integration._seed import seed_torrent_corpus

    announce_url, _tracker_container, _log_reader = tracker_service
    return seed_torrent_corpus(
        qbit_container, tracker_announce_url=announce_url
    )


@pytest.fixture()
def hermetic_env(qbit_container):
    """Return the exact `env=` dict every CLI invocation must use here."""
    import shutil

    from tests.integration._harness import (
        assert_no_ambient_qbit_ops_config,
        assert_target_is_disposable,
        make_hermetic_env,
    )

    assert_target_is_disposable(qbit_container.host)
    env = make_hermetic_env(
        host=qbit_container.host,
        username=qbit_container.username,
        password=qbit_container.password,
    )
    environ = env.as_environ()
    assert_no_ambient_qbit_ops_config(environ)
    try:
        yield environ
    finally:
        shutil.rmtree(env.home_dir, ignore_errors=True)
