"""Compatibility contract tests: application/version payload fixtures.

Exercises `qbit_ops.features.doctor._version_parsable_check` -- the exact
function `doctor`'s COMPAT001 check uses to parse `app_version()`'s
raw string.
"""

from __future__ import annotations

from qbit_ops.features.doctor import CheckStatus, _version_parsable_check
from tests.compatibility._fixture_loader import load_fixture


def test_well_formed_5x_version_string_parses() -> None:
    fixture = load_fixture("application", "version_5x")

    parsed, check = _version_parsable_check(fixture.payload)

    assert parsed == (5, 0, 1)
    assert check.status is CheckStatus.PASS


def test_web_api_version_string_at_the_stop_start_threshold() -> None:
    """2.11.0 is the threshold qbittorrent-api uses to switch
    pause/resume to stop/start; this fixture's value (2.11.4) is just
    past it."""
    fixture = load_fixture("application", "web_api_version_2_11")

    assert fixture.payload == "2.11.4"
    assert fixture.web_api_version == "2.11.4"


def test_prerelease_version_string_is_still_parsable() -> None:
    fixture = load_fixture("application", "version_prerelease")

    parsed, check = _version_parsable_check(fixture.payload)

    assert parsed == (5, 1, 0)
    assert check.status is CheckStatus.PASS


def test_malformed_version_string_produces_a_warning_not_a_crash() -> None:
    fixture = load_fixture("application", "version_malformed")

    parsed, check = _version_parsable_check(fixture.payload)

    assert parsed is None
    assert check.status is CheckStatus.WARNING


def test_missing_version_response_is_skipped_not_a_crash() -> None:
    """`None` is the fallback for a failed `app_version()` call."""
    parsed, check = _version_parsable_check(None)

    assert parsed is None
    assert check.status is CheckStatus.SKIPPED
