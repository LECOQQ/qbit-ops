"""Hermeticity and bencode/torrent-corpus unit tests for the Docker harness.

These run in the ordinary test suite (no Docker required, no
`QBIT_OPS_DOCKER_MATRIX` needed) -- they exercise the harness's pure
guard logic and deterministic corpus generation directly, including
controlled sabotage of the guard functions themselves. Container
lifecycle tests that require a real Docker daemon live under
`tests/integration/` instead, gated by `QBIT_OPS_DOCKER_MATRIX=1`.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tests.integration._bencode import bencode
from tests.integration._harness import (
    HarnessError,
    HermeticEnv,
    assert_no_ambient_qbit_ops_config,
    assert_target_is_disposable,
    make_hermetic_env,
)
from tests.integration._matrix import load_matrix, load_matrix_entry
from tests.integration._qbit_conf_template import build_webui_password_hash
from tests.integration._torrent_corpus import (
    build_complete_torrent,
    build_incomplete_torrent,
    build_tracked_torrent,
)


@pytest.fixture()
def hermetic_env_for_test():
    """Build one `HermeticEnv` and remove its temp HOME afterward.

    `make_hermetic_env()` creates a real `/tmp/qbit-ops-it-home-*`
    directory via `tempfile.mkdtemp` -- calling it directly in a test,
    without cleanup, leaks that directory on every run. This fixture is
    the single, tracked cleanup point every test in this file must go
    through.
    """
    created: list[HermeticEnv] = []

    def _make(
        *, host: str, username: str = "admin", password: str = "x"
    ) -> HermeticEnv:
        env = make_hermetic_env(host=host, username=username, password=password)
        created.append(env)
        return env

    try:
        yield _make
    finally:
        for env in created:
            shutil.rmtree(env.home_dir, ignore_errors=True)


def test_hermetic_env_passes_its_own_guard(hermetic_env_for_test) -> None:
    env = hermetic_env_for_test(host="http://127.0.0.1:18112")
    assert_no_ambient_qbit_ops_config(env.as_environ())


def test_sabotage_missing_qbit_ops_env_file_fails_closed(
    hermetic_env_for_test,
) -> None:
    """The harness must fail if it could discover the repo `.env`."""
    env = hermetic_env_for_test(host="http://127.0.0.1:18112").as_environ()
    del env["QBIT_OPS_ENV_FILE"]

    with pytest.raises(HarnessError, match="QBIT_OPS_ENV_FILE"):
        assert_no_ambient_qbit_ops_config(env)


def test_sabotage_env_file_pointing_at_a_real_file_fails_closed(
    hermetic_env_for_test,
) -> None:
    """A `QBIT_OPS_ENV_FILE` that resolves to a real, existing file
    (standing in for the repository `.env`) must be rejected."""
    env = hermetic_env_for_test(host="http://127.0.0.1:18112").as_environ()
    env["QBIT_OPS_ENV_FILE"] = str(Path(__file__))  # any real, existing file

    with pytest.raises(HarnessError, match="unexpectedly exists"):
        assert_no_ambient_qbit_ops_config(env)


def test_sabotage_real_home_directory_fails_closed(
    hermetic_env_for_test,
) -> None:
    """The harness must fail if HOME could resolve to the user's
    real HOME config."""
    env = hermetic_env_for_test(host="http://127.0.0.1:18112").as_environ()
    env["HOME"] = os.path.expanduser("~")

    with pytest.raises(HarnessError, match="real user HOME"):
        assert_no_ambient_qbit_ops_config(env)


def test_sabotage_non_loopback_host_fails_closed() -> None:
    """A resolved host outside loopback must never be targeted."""
    with pytest.raises(HarnessError, match="disposable"):
        assert_target_is_disposable("http://192.168.1.50:8080")


def test_disposable_loopback_host_passes() -> None:
    assert_target_is_disposable("http://127.0.0.1:18112")


def test_sabotage_lookalike_loopback_hostname_fails_closed() -> None:
    """A substring check (`"127.0.0.1" in host`) would wrongly accept
    a hostname that merely *contains* the loopback address -- the guard
    must parse the URL and compare the exact hostname instead."""
    with pytest.raises(HarnessError, match="disposable"):
        assert_target_is_disposable("http://127.0.0.1.evil.example:18112")


def test_sabotage_loopback_as_userinfo_fails_closed() -> None:
    """Another substring-check bypass: embedding the loopback address as
    URL userinfo rather than the actual host."""
    with pytest.raises(HarnessError, match="disposable"):
        assert_target_is_disposable("http://127.0.0.1@evil.example:18112")


def test_webui_password_hash_matches_the_reverse_engineered_format() -> None:
    """Regression: this exact salt/password/hash triple was verified against
    a real qBittorrent 5.1.4 container's own generated `qBittorrent.conf` --
    if this ever stops matching, the harness can no longer log in without
    falling back to scraping a per-boot temporary password."""
    import base64

    salt = base64.b64decode("uST3wxhBEBMjC2hmKBlb2g==")
    expected = (
        "@ByteArray(uST3wxhBEBMjC2hmKBlb2g==:"
        "P2pJEFs9tYwYTAf0ER5m5M0w5L2qErupVXhEi5q7SktR3vQUPWQ9SMijpaFiOCMFaIN/LxsXJatRuj7SSThRLA==)"
    )
    assert (
        build_webui_password_hash("FixedTestPassword123!", salt=salt)
        == expected
    )


def test_bencode_matches_bep0003_reference_examples() -> None:
    assert bencode(3) == b"i3e"
    assert bencode("spam") == b"4:spam"
    assert bencode(["spam", "eggs"]) == b"l4:spam4:eggse"
    assert (
        bencode({"cow": "moo", "spam": "eggs"}) == b"d3:cow3:moo4:spam4:eggse"
    )


def test_synthetic_torrent_corpus_is_deterministic_and_distinct() -> None:
    complete_a = build_complete_torrent()
    complete_b = build_complete_torrent()
    incomplete = build_incomplete_torrent()
    tracked = build_tracked_torrent(
        announce="http://qbit-ops-tracker:6969/announce"
    )

    assert complete_a.info_hash_hex == complete_b.info_hash_hex
    assert (
        len(
            {
                complete_a.info_hash_hex,
                incomplete.info_hash_hex,
                tracked.info_hash_hex,
            }
        )
        == 3
    )
    for name in (
        "HOME",
        "/home/",
        "/Users/",
        os.environ.get("USER", "\0impossible"),
    ):
        assert name not in complete_a.torrent_bytes.decode("latin-1")


def test_matrix_manifest_loads_and_has_no_duplicate_ids() -> None:
    entries = load_matrix()
    assert len(entries) >= 3
    ids = [entry.id for entry in entries]
    assert len(ids) == len(set(ids))


def test_matrix_manifest_only_contains_verified_release_lines() -> None:
    """4.6.x, 5.0.x, and 5.1.x must each be represented, not an arbitrary
    set."""
    release_lines = {entry.release_line for entry in load_matrix()}
    assert {"4.6.x", "5.0.x", "5.1.x"} <= release_lines


def test_unknown_matrix_id_raises_instead_of_running_something_else() -> None:
    with pytest.raises(KeyError, match="unknown qBittorrent matrix id"):
        load_matrix_entry("qbit-9.9.9-does-not-exist")


def test_sabotage_empty_matrix_manifest_fails_closed(tmp_path) -> None:
    """A manifest with zero entries must never silently produce "success" --
    a CI job with nothing to run, or a local/freshness command reporting
    green having tested nothing."""
    empty_manifest = tmp_path / "empty-matrix.toml"
    empty_manifest.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="zero matrix entries"):
        load_matrix(empty_manifest)
