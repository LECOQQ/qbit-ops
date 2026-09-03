"""Test the completion cache's storage layer in isolation.

No Typer, no qBittorrent client -- just the file: where it lives, what
a merge-write does to it, and how a read degrades on damage. The
Typer-facing behavior (`--category`/`--tag`/`--tracker` completion
itself, the cold-start probe, the guard that a closed enum still never
touches the network) is `tests/test_cli_completion_live.py`.
"""

import json
from pathlib import Path

import pytest

from qbit_core.features.status import redact_host
from qbit_ops.cli.completion_cache import (
    CACHE_FILENAME,
    InstanceVocabulary,
    cache_file_path,
    mark_cold_start_probe_failed,
    merge_instance_vocabulary,
    read_instance_vocabulary,
)
from qbit_ops.config import APP_ENV_FILE_VARIABLE

HOST = "http://localhost:8080"
CREDENTIALED_HOST = "http://admin:super-secret-password@localhost:8080"


@pytest.fixture(autouse=True)
def _isolated_cache_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point the cache at a private directory for every test in this file.

    The session-wide isolation fixture already points
    `QBIT_OPS_ENV_FILE` at one fixed path for the whole suite; without
    this override, every test here would share (and race on) the same
    on-disk cache file.
    """
    env_file = tmp_path / ".env"
    monkeypatch.setenv(APP_ENV_FILE_VARIABLE, str(env_file))
    return tmp_path


# --- path resolution ---------------------------------------------------------


def test_cache_file_path_sits_next_to_the_active_env_file(
    tmp_path: Path,
) -> None:
    assert cache_file_path() == tmp_path / CACHE_FILENAME


# --- reading an absent or damaged cache never raises -------------------------


def test_read_on_a_missing_cache_file_is_empty() -> None:
    assert read_instance_vocabulary(HOST) == InstanceVocabulary()


def test_read_on_truncated_json_is_empty_not_raising(tmp_path: Path) -> None:
    (tmp_path / CACHE_FILENAME).write_text('{"instances": {"localhost:80')

    assert read_instance_vocabulary(HOST) == InstanceVocabulary()


def test_read_on_a_non_utf8_file_is_empty_not_raising(tmp_path: Path) -> None:
    (tmp_path / CACHE_FILENAME).write_bytes(b"\xff\xfe\x00\x01")

    assert read_instance_vocabulary(HOST) == InstanceVocabulary()


def test_read_on_an_unexpectedly_shaped_payload_is_empty(
    tmp_path: Path,
) -> None:
    (tmp_path / CACHE_FILENAME).write_text(json.dumps(["not", "a", "dict"]))

    assert read_instance_vocabulary(HOST) == InstanceVocabulary()


def test_read_tolerates_a_non_list_categories_field(tmp_path: Path) -> None:
    document = {"instances": {redact_host(HOST): {"categories": "not-a-list"}}}
    (tmp_path / CACHE_FILENAME).write_text(json.dumps(document))

    assert read_instance_vocabulary(HOST).categories == ()


# --- writing merges rather than replaces --------------------------------------


def test_merge_populates_a_fresh_entry() -> None:
    merge_instance_vocabulary(HOST, categories=["Movies", "TV"], tags=["a"])

    entry = read_instance_vocabulary(HOST)
    assert entry.categories == ("Movies", "TV")
    assert entry.tags == ("a",)
    assert entry.tracker_hosts == ()
    assert entry.cold_start_probe_failed is False


def test_merge_unions_new_values_into_an_existing_entry() -> None:
    merge_instance_vocabulary(HOST, categories=["Movies"])
    merge_instance_vocabulary(HOST, categories=["TV"], tags=["archived"])

    entry = read_instance_vocabulary(HOST)
    assert entry.categories == ("Movies", "TV")
    assert entry.tags == ("archived",)


def test_a_filtered_run_never_erases_a_broader_run_s_categories() -> None:
    """The regression this design exists to prevent: `torrents list
    --category Movies` must not shrink what a prior unfiltered run
    already learned."""
    merge_instance_vocabulary(HOST, categories=["Movies", "TV", "Anime"])

    merge_instance_vocabulary(HOST, categories=["Movies"])

    assert read_instance_vocabulary(HOST).categories == (
        "Anime",
        "Movies",
        "TV",
    )


def test_merge_deduplicates_repeated_values() -> None:
    merge_instance_vocabulary(HOST, categories=["Movies", "Movies"])

    assert read_instance_vocabulary(HOST).categories == ("Movies",)


def test_two_hosts_get_independent_cache_entries() -> None:
    merge_instance_vocabulary(HOST, categories=["Movies"])
    merge_instance_vocabulary(
        "http://seedbox.example:8080", categories=["Linux"]
    )

    assert read_instance_vocabulary(HOST).categories == ("Movies",)
    assert read_instance_vocabulary(
        "http://seedbox.example:8080"
    ).categories == ("Linux",)


# --- the redacted host indexes the cache, never the raw one ------------------


def test_the_cache_key_and_file_content_never_carry_credentials(
    tmp_path: Path,
) -> None:
    merge_instance_vocabulary(CREDENTIALED_HOST, categories=["Movies"])

    raw = (tmp_path / CACHE_FILENAME).read_text(encoding="utf-8")
    assert "super-secret-password" not in raw
    assert redact_host(CREDENTIALED_HOST) in raw


def test_lookup_by_the_raw_and_redacted_host_agree(tmp_path: Path) -> None:
    merge_instance_vocabulary(CREDENTIALED_HOST, categories=["Movies"])

    assert read_instance_vocabulary(CREDENTIALED_HOST).categories == ("Movies",)
    assert read_instance_vocabulary(
        redact_host(CREDENTIALED_HOST)
    ).categories == ("Movies",)


# --- the cold-start witness ---------------------------------------------------


def test_mark_cold_start_probe_failed_sets_the_witness() -> None:
    mark_cold_start_probe_failed(HOST)

    assert read_instance_vocabulary(HOST).cold_start_probe_failed is True


def test_mark_cold_start_probe_failed_does_not_erase_existing_values() -> None:
    merge_instance_vocabulary(HOST, categories=["Movies"])

    mark_cold_start_probe_failed(HOST)

    entry = read_instance_vocabulary(HOST)
    assert entry.categories == ("Movies",)
    assert entry.cold_start_probe_failed is True


def test_a_successful_merge_clears_a_prior_witness() -> None:
    mark_cold_start_probe_failed(HOST)

    merge_instance_vocabulary(HOST, categories=["Movies"])

    assert read_instance_vocabulary(HOST).cold_start_probe_failed is False


# --- atomic write ---------------------------------------------------------


def test_write_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    merge_instance_vocabulary(HOST, categories=["Movies"])

    assert [p.name for p in tmp_path.iterdir()] == [CACHE_FILENAME]


def test_write_survives_a_read_only_cache_directory(
    tmp_path: Path,
) -> None:
    """A read-only directory must degrade the write, not the command
    that triggered it."""
    tmp_path.chmod(0o500)
    try:
        merge_instance_vocabulary(HOST, categories=["Movies"])
    finally:
        tmp_path.chmod(0o700)
