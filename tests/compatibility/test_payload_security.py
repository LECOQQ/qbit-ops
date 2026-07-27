"""Automated security scans over every compatibility fixture.

No fixture may contain a real credential, real host, real torrent
name/infohash, a local filesystem path identifying the user, or an
`.env` fragment -- see `tests/compatibility/README.md`'s security
policy. These scans run over the raw fixture *files*, not just their
decoded payloads, so a leak hidden in `_meta` prose would still be
caught.
"""

from __future__ import annotations

import json
import re

from tests.compatibility._fixture_loader import FIXTURES_DIR, load_all_fixtures

# The one sanctioned placeholder passkey value fixtures may use to test
# sanitization itself (see message_with_secret_like_material.json).
_ALLOWED_PLACEHOLDER_PASSKEY = "FAKE_PLACEHOLDER_PASSKEY_0000000000000000"

# Hostnames a fixture may legitimately use -- RFC 2606 reserved example
# domains, plus qbit-ops's own established placeholder convention.
_ALLOWED_HOSTS = {
    "tracker.example",
    "other.example",
    "example.com",
    "example.org",
    "example.net",
    "localhost",
}

_USERINFO_PATTERN = re.compile(r"://[^/@\s]+:[^/@\s]+@")
_PASSKEY_PARAM_PATTERN = re.compile(
    r"[?&](passkey|auth|token|secret|key)=([^&\s\"']+)", re.IGNORECASE
)
_HOME_DIR_PATTERN = re.compile(r"(/home/|/Users/|C:\\\\Users\\\\)[^/\\\"']+")
_ENV_FRAGMENT_PATTERN = re.compile(r"\bQBIT_(HOST|USER|PASSWORD)\s*=")
_URL_HOST_PATTERN = re.compile(r"://([^/:@\s\"']+)")


def _all_fixture_files() -> list:
    files = sorted(FIXTURES_DIR.rglob("*.json"))
    assert files, "expected at least one fixture file"
    return files


def test_fixture_discovery_is_non_empty() -> None:
    """Guard the scan itself: an empty fixture set would make every
    other test in this file vacuously pass."""
    files = _all_fixture_files()
    assert len(files) >= 10
    fixtures = load_all_fixtures()
    assert len(fixtures) == len(files)


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
            if value != _ALLOWED_PLACEHOLDER_PASSKEY
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
        unexpected = hosts - _ALLOWED_HOSTS
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
