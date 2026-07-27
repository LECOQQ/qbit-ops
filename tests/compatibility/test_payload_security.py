"""Automated security scans over every compatibility fixture.

No fixture may contain a real credential, real host, real torrent
name/infohash, a local filesystem path identifying the user, or an
`.env` fragment -- see `tests/compatibility/README.md`'s security
policy. These scans run over the raw fixture *files*, not just their
decoded payloads, so a leak hidden in `_meta` prose would still be
caught. Patterns live in `_security_scan.py`, shared with the
captured-container capture harness (`tests/integration/_capture.py`),
which runs the same scan *before* writing a fixture to disk.
"""

from __future__ import annotations

import json

from tests.compatibility._fixture_loader import FIXTURES_DIR, load_all_fixtures
from tests.compatibility._security_scan import (
    _ENV_FRAGMENT_PATTERN,
    _HOME_DIR_PATTERN,
    _PASSKEY_PARAM_PATTERN,
    _URL_HOST_PATTERN,
    _USERINFO_PATTERN,
    ALLOWED_HOSTS,
    ALLOWED_PLACEHOLDER_PASSKEY,
)


def _all_fixture_files() -> list:
    files = sorted(FIXTURES_DIR.rglob("*.json"))
    assert files, "expected at least one fixture file"
    return files


def test_fixture_discovery_is_non_empty() -> None:
    """Guard the scan itself: an empty fixture set would make every
    other test in this file vacuously pass.

    `_all_fixture_files()` recurses (`rglob`) so it also reaches
    `captured-container/<matrix-id>/*.json`, which
    `load_all_fixtures()` deliberately does not enumerate (see
    `tests/compatibility/_captured_loader.py`) -- so the two counts are
    only compared for the four flat `category/name.json` directories.
    """
    files = _all_fixture_files()
    assert len(files) >= 10
    category_json_files = [
        path for path in files if "captured-container" not in path.parts
    ]
    fixtures = load_all_fixtures()
    assert len(fixtures) == len(category_json_files)


def test_no_fixture_contains_userinfo_in_a_url() -> None:
    offenders = []
    for path in _all_fixture_files():
        text = path.read_text(encoding="utf-8")
        if _USERINFO_PATTERN.search(text):
            offenders.append(str(path))

    assert not offenders, f"userinfo found in: {offenders}"


def test_no_fixture_contains_a_non_placeholder_passkey_value() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _all_fixture_files():
        text = path.read_text(encoding="utf-8")
        matches = _PASSKEY_PARAM_PATTERN.findall(text)
        bad_values = [
            value
            for _param, value in matches
            if value != ALLOWED_PLACEHOLDER_PASSKEY
        ]
        if bad_values:
            offenders[str(path)] = bad_values

    assert not offenders, f"non-placeholder passkey-shaped values: {offenders}"


def test_no_fixture_uses_a_hostname_outside_the_allowlist() -> None:
    offenders: dict[str, set[str]] = {}
    for path in _all_fixture_files():
        text = path.read_text(encoding="utf-8")
        hosts = {
            host.split(":")[0]  # strip a port, if any
            for host in _URL_HOST_PATTERN.findall(text)
        }
        unexpected = hosts - ALLOWED_HOSTS
        if unexpected:
            offenders[str(path)] = unexpected

    assert not offenders, f"unexpected hostnames: {offenders}"


def test_no_fixture_contains_a_home_directory_path() -> None:
    offenders = []
    for path in _all_fixture_files():
        text = path.read_text(encoding="utf-8")
        if _HOME_DIR_PATTERN.search(text):
            offenders.append(str(path))

    assert not offenders, f"home-directory path found in: {offenders}"


def test_no_fixture_contains_an_env_file_fragment() -> None:
    offenders = []
    for path in _all_fixture_files():
        text = path.read_text(encoding="utf-8")
        if _ENV_FRAGMENT_PATTERN.search(text):
            offenders.append(str(path))

    assert not offenders, f".env-shaped fragment found in: {offenders}"


def test_no_fixture_json_is_malformed() -> None:
    for path in _all_fixture_files():
        json.loads(path.read_text(encoding="utf-8"))  # raises if malformed
