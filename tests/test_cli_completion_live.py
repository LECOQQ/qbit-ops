"""Test shell completion for options whose values live on the instance.

Covers `.agents/specs/completion-cache.md` end to end, through the real
Typer app the way a shell actually invokes it (`_complete`, imported
from `tests.test_cli_completion` -- see that module for how it drives
Click's shell-completion protocol). `tests/test_completion_cache.py`
covers the storage layer in isolation; this file covers what decides
*when* to read the cache, probe, or skip.

Deliberately does **not** share `test_cli_completion.py`'s autouse
`_forbid_qbit_client` fixture: that fixture protects a different,
stronger guarantee (a closed enum can never build a client, full stop)
which nothing here touches. The guarantee this file protects is
weaker and is asserted directly, by counting `create_qbit_client`
calls through `configure_qbit_backend` -- see
"cache-hit-never-touches-network" and "one-probe-per-empty-cache"
below.
"""

import re
import time
import tomllib
from pathlib import Path

import pytest

from qbit_core.errors import QbitConnectionError
from qbit_ops.cli import error_boundary
from qbit_ops.cli.app import app
from qbit_ops.cli.completion_cache import (
    mark_cold_start_probe_failed,
    merge_instance_vocabulary,
    read_instance_vocabulary,
)
from qbit_ops.config import APP_ENV_FILE_VARIABLE
from tests.support import FakeQbitClient, make_config, make_torrent
from tests.test_cli_completion import _complete

HOST = "http://localhost:8080"
TORRENT_A = "a" * 40
TORRENT_B = "b" * 40


@pytest.fixture(autouse=True)
def _isolated_cache_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Give every test in this file its own cache file.

    Without this, all tests would share the one cache file the
    session-wide isolation fixture points `QBIT_OPS_ENV_FILE` at for
    the whole suite.
    """
    monkeypatch.setenv(APP_ENV_FILE_VARIABLE, str(tmp_path / ".env"))


# --- acceptance: cache hit never touches the network --------------------------


def test_a_populated_cache_never_touches_the_network(
    configure_qbit_backend,
) -> None:
    merge_instance_vocabulary(
        HOST,
        categories=["Movies"],
        tags=["archived"],
        tracker_hosts=["tracker.example"],
    )
    calls = configure_qbit_backend(config=make_config(host=HOST))

    assert _complete("qbit-ops torrents list --category ", 4) == ["Movies"]
    assert _complete("qbit-ops torrents list --tag ", 4) == ["archived"]
    assert _complete("qbit-ops torrents list --tracker ", 4) == [
        "tracker.example"
    ]
    assert calls == []


# --- acceptance criterion 1: fills by usage -----------------------------------


def test_after_a_successful_torrents_list_category_offers_real_values(
    runner, configure_qbit_backend
) -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A, category="Movies"),
            make_torrent(hash=TORRENT_B, category="TV", tags="archived"),
        ]
    )
    configure_qbit_backend(config=make_config(host=HOST), client=client)

    result = runner.invoke(app, ["torrents", "list"])
    assert result.exit_code == 0

    assert _complete("qbit-ops torrents list --category ", 4) == [
        "Movies",
        "TV",
    ]
    assert _complete("qbit-ops torrents list --tag ", 4) == ["archived"]


def test_after_a_successful_trackers_list_tracker_offers_real_hosts(
    runner, configure_qbit_backend
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A)],
        trackers_by_hash={
            TORRENT_A: [{"url": "https://tracker.example/announce"}]
        },
    )
    configure_qbit_backend(config=make_config(host=HOST), client=client)

    result = runner.invoke(app, ["trackers", "list"])
    assert result.exit_code == 0

    assert _complete("qbit-ops torrents list --tracker ", 4) == [
        "tracker.example"
    ]


def test_a_filtered_torrents_list_never_shrinks_the_category_cache(
    runner, configure_qbit_backend
) -> None:
    """Regression the merge-write design exists to prevent."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A, category="Movies"),
            make_torrent(hash=TORRENT_B, category="TV"),
        ]
    )
    configure_qbit_backend(config=make_config(host=HOST), client=client)
    runner.invoke(app, ["torrents", "list"])

    result = runner.invoke(app, ["torrents", "list", "--category", "Movies"])
    assert result.exit_code == 0

    assert _complete("qbit-ops torrents list --category ", 4) == [
        "Movies",
        "TV",
    ]


# --- acceptance criteria 2/3: the cold-start exception -----------------------


def test_cold_start_on_a_reachable_instance_probes_once(
    configure_qbit_backend,
) -> None:
    calls = configure_qbit_backend(
        config=make_config(host=HOST),
        client=FakeQbitClient(categories={"Movies": {}}, tags=["archived"]),
    )

    first = _complete("qbit-ops torrents list --category ", 4)
    second = _complete("qbit-ops torrents list --category ", 4)

    assert first == ["Movies"]
    assert second == ["Movies"]
    assert len(calls) == 1


def test_cold_start_populates_tags_alongside_categories_in_one_probe(
    configure_qbit_backend,
) -> None:
    """One login pays for both: a `--tag` Tab right after doesn't
    re-probe just because `--category` was the one that triggered it."""
    calls = configure_qbit_backend(
        config=make_config(host=HOST),
        client=FakeQbitClient(categories={"Movies": {}}, tags=["archived"]),
    )

    _complete("qbit-ops torrents list --category ", 4)
    tags = _complete("qbit-ops torrents list --tag ", 4)

    assert tags == ["archived"]
    assert len(calls) == 1


def test_cold_start_on_an_unreachable_instance_returns_promptly(
    configure_qbit_backend,
) -> None:
    calls = configure_qbit_backend(
        config=make_config(host=HOST),
        client_error=QbitConnectionError("unreachable"),
    )

    started = time.perf_counter()
    result = _complete("qbit-ops torrents list --category ", 4)
    elapsed = time.perf_counter() - started

    assert result == []
    assert elapsed < 0.5
    assert len(calls) == 1


def test_cold_start_failure_is_never_retried(configure_qbit_backend) -> None:
    calls = configure_qbit_backend(
        config=make_config(host=HOST),
        client_error=QbitConnectionError("unreachable"),
    )

    first = _complete("qbit-ops torrents list --category ", 4)
    second = _complete("qbit-ops torrents list --category ", 4)

    assert first == []
    assert second == []
    assert len(calls) == 1


def test_cold_start_abandons_a_hung_connection_within_the_budget(
    monkeypatch: pytest.MonkeyPatch, configure_qbit_backend
) -> None:
    """The budget is enforced even when the instance never answers at
    all -- not merely when it answers with an error quickly."""
    configure_qbit_backend(config=make_config(host=HOST))

    def _hang(**kwargs: object) -> None:
        time.sleep(2.0)
        raise QbitConnectionError("never got here")

    monkeypatch.setattr(error_boundary, "create_qbit_client", _hang)

    started = time.perf_counter()
    result = _complete("qbit-ops torrents list --category ", 4)
    elapsed = time.perf_counter() - started

    assert result == []
    assert elapsed < 0.5


# --- acceptance criterion 4: the witness disappears on success ---------------


def test_a_successful_command_clears_a_prior_probe_witness(
    runner, configure_qbit_backend
) -> None:
    mark_cold_start_probe_failed(HOST)
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, category="Movies")]
    )
    configure_qbit_backend(config=make_config(host=HOST), client=client)

    result = runner.invoke(app, ["torrents", "list"])

    assert result.exit_code == 0
    assert read_instance_vocabulary(HOST).cold_start_probe_failed is False


# --- acceptance criterion 5: per-instance isolation ---------------------------


def test_two_env_files_never_share_completions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configure_qbit_backend
) -> None:
    seedbox_env = tmp_path / "seedbox" / ".env"
    nas_env = tmp_path / "nas" / ".env"

    monkeypatch.setenv(APP_ENV_FILE_VARIABLE, str(seedbox_env))
    merge_instance_vocabulary(
        "http://seedbox.example:8080", categories=["Linux"]
    )

    monkeypatch.setenv(APP_ENV_FILE_VARIABLE, str(nas_env))
    merge_instance_vocabulary("http://nas.example:8080", categories=["Movies"])

    configure_qbit_backend(config=make_config(host="http://nas.example:8080"))
    assert _complete("qbit-ops torrents list --category ", 4) == ["Movies"]

    monkeypatch.setenv(APP_ENV_FILE_VARIABLE, str(seedbox_env))
    configure_qbit_backend(
        config=make_config(host="http://seedbox.example:8080")
    )
    assert _complete("qbit-ops torrents list --category ", 4) == ["Linux"]


# --- acceptance criterion 6: a damaged cache degrades to empty ---------------


def test_a_corrupted_cache_file_completes_as_if_empty(
    tmp_path: Path, configure_qbit_backend
) -> None:
    (tmp_path / ".qbit-ops-completion-cache.json").write_text("{not json")
    calls = configure_qbit_backend(
        config=make_config(host=HOST),
        client_error=QbitConnectionError("unreachable"),
    )

    result = _complete("qbit-ops torrents list --category ", 4)

    assert result == []
    assert len(calls) == 1  # treated as empty, so the cold-start path ran


# --- acceptance criterion 8: no passkey ever reaches the cache file ----------


def test_trackers_list_cache_never_carries_a_full_announce_url(
    tmp_path: Path, runner, configure_qbit_backend
) -> None:
    passkey_url = "https://tracker.example/announce/UNMISTAKABLE-PASSKEY123"
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A)],
        trackers_by_hash={TORRENT_A: [{"url": passkey_url}]},
    )
    configure_qbit_backend(config=make_config(host=HOST), client=client)

    result = runner.invoke(app, ["trackers", "list"])
    assert result.exit_code == 0

    raw = (tmp_path / ".qbit-ops-completion-cache.json").read_text(
        encoding="utf-8"
    )
    assert "UNMISTAKABLE-PASSKEY123" not in raw
    for pattern in _gitleaks_tracker_passkey_patterns():
        assert not pattern.search(raw)


def _gitleaks_tracker_passkey_patterns() -> list[re.Pattern[str]]:
    """Load this repo's own two passkey-detecting gitleaks rules.

    Read from `.gitleaks.toml` rather than re-typed here, so this test
    can never silently drift from what `make secrets` actually checks.
    """
    config_path = Path(__file__).resolve().parents[1] / ".gitleaks.toml"
    document = tomllib.loads(config_path.read_text(encoding="utf-8"))
    ids = {"qbit-tracker-passkey", "qbit-tracker-passkey-path"}
    return [
        re.compile(rule["regex"])
        for rule in document["rules"]
        if rule["id"] in ids
    ]


# --- tracker_hosts never probes: N+1 cost cannot fit the budget --------------


def test_tracker_completion_never_probes_even_on_an_empty_cache(
    configure_qbit_backend,
) -> None:
    calls = configure_qbit_backend(config=make_config(host=HOST))

    started = time.perf_counter()
    result = _complete("qbit-ops torrents list --tracker ", 4)
    elapsed = time.perf_counter() - started

    assert result == []
    assert calls == []
    assert elapsed < 0.1
