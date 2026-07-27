"""Shared fixture-security regex scans.

Extracted from `test_payload_security.py` so the same rules can be
applied *before* a captured-container fixture is ever written to disk
(`tests/integration/_capture.py`), not only checked after the fact by
the test suite. Both call sites must use exactly these patterns --
duplicating them would let the two silently drift apart.
"""

from __future__ import annotations

import re

# The one sanctioned placeholder passkey value fixtures may use to test
# sanitization itself (see message_with_secret_like_material.json).
ALLOWED_PLACEHOLDER_PASSKEY = "FAKE_PLACEHOLDER_PASSKEY_0000000000000000"

# Hostnames a fixture may legitimately use -- RFC 2606 reserved example
# domains, plus qbit-ops's own established placeholder convention.
ALLOWED_HOSTS = {
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


def scan_text(text: str) -> list[str]:
    """Return a list of human-readable violations found in `text`.

    Empty list means clean. Runs over raw text (not a decoded JSON
    structure), so a leak hidden anywhere -- including free-text
    provenance fields -- is still caught.
    """
    violations: list[str] = []

    if _USERINFO_PATTERN.search(text):
        violations.append("userinfo embedded in a URL")

    bad_passkeys = [
        value
        for _param, value in _PASSKEY_PARAM_PATTERN.findall(text)
        if value != ALLOWED_PLACEHOLDER_PASSKEY
    ]
    if bad_passkeys:
        violations.append(
            f"non-placeholder passkey-shaped value(s): {bad_passkeys}"
        )

    hosts = {host.split(":")[0] for host in _URL_HOST_PATTERN.findall(text)}
    unexpected_hosts = hosts - ALLOWED_HOSTS
    if unexpected_hosts:
        violations.append(
            f"hostname(s) outside the allowlist: {sorted(unexpected_hosts)}"
        )

    if _HOME_DIR_PATTERN.search(text):
        violations.append("home-directory-shaped path")

    if _ENV_FRAGMENT_PATTERN.search(text):
        violations.append(
            ".env-shaped fragment (QBIT_HOST/QBIT_USER/QBIT_PASSWORD=)"
        )

    return violations
