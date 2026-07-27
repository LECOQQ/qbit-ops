"""Unit tests for `scripts/check_qbit_matrix_freshness.py`.

All network access is mocked -- these run in the ordinary offline
`make check` suite. The freshness checker itself is never invoked with
real network access here; see the Docker matrix implementation note
for the live smoke check performed manually against the real GitHub
API.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import check_qbit_matrix_freshness as freshness
import pytest


def _fake_response(payload: dict) -> FakeHTTPResponse:
    return FakeHTTPResponse(payload)


class FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeHTTPResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def test_fetch_latest_stable_version_parses_the_release_tag() -> None:
    with patch(
        "urllib.request.urlopen",
        return_value=_fake_response({"tag_name": "release-5.2.3"}),
    ):
        assert freshness.fetch_latest_stable_version() == "5.2.3"


def test_fetch_latest_stable_version_raises_on_network_error() -> None:
    import urllib.error

    with patch(
        "urllib.request.urlopen", side_effect=urllib.error.URLError("boom")
    ):
        with pytest.raises(freshness.FreshnessCheckError):
            freshness.fetch_latest_stable_version()


def test_fetch_latest_stable_version_raises_on_missing_tag_name() -> None:
    with patch("urllib.request.urlopen", return_value=_fake_response({})):
        with pytest.raises(freshness.FreshnessCheckError):
            freshness.fetch_latest_stable_version()


def test_fetch_latest_stable_version_raises_on_unparsable_tag() -> None:
    with patch(
        "urllib.request.urlopen",
        return_value=_fake_response({"tag_name": "release-not-a-version"}),
    ):
        with pytest.raises(freshness.FreshnessCheckError):
            freshness.fetch_latest_stable_version()


def test_newest_matrix_version_picks_the_highest_expected_version() -> None:
    assert freshness.newest_matrix_version() == "5.2.3"


def test_main_reports_fresh_when_matrix_matches_latest(capsys) -> None:
    with patch.object(
        freshness, "fetch_latest_stable_version", return_value="5.2.3"
    ):
        exit_code = freshness.main()
    assert exit_code == 0
    assert "FRESH" in capsys.readouterr().out


def test_sabotage_main_reports_stale_when_a_newer_release_exists(
    capsys,
) -> None:
    """Item 10 sabotage: prove the checker actually notices staleness --
    it must never consider an older matrix entry current."""
    with patch.object(
        freshness, "fetch_latest_stable_version", return_value="9.9.9"
    ):
        exit_code = freshness.main()
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "STALE" in out
    assert "MATRIX REVIEW REQUIRED" in out
    # Must never claim qbit-ops itself is broken.
    assert "incompatible" not in out.lower() or "does not mean" in out


def test_main_distinguishes_network_failure_from_staleness(capsys) -> None:
    with patch.object(
        freshness,
        "fetch_latest_stable_version",
        side_effect=freshness.FreshnessCheckError("network down"),
    ):
        exit_code = freshness.main()
    out = capsys.readouterr().out
    assert exit_code == 2
    assert "UNKNOWN" in out
    assert "STALE" not in out


def test_freshness_check_never_writes_to_the_matrix_manifest(tmp_path) -> None:
    """Item 9: the checker must never modify the matrix file."""
    from tests.integration._matrix import MATRIX_MANIFEST_PATH

    before = MATRIX_MANIFEST_PATH.read_text(encoding="utf-8")
    with patch.object(
        freshness, "fetch_latest_stable_version", return_value="9.9.9"
    ):
        freshness.main()
    after = MATRIX_MANIFEST_PATH.read_text(encoding="utf-8")
    assert before == after
