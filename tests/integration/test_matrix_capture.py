"""Capture authentic `captured-container` payload fixtures for one matrix entry.

Run via `make capture-qbit-fixtures QBIT_MATRIX_ID=<id>` -- this test
both performs the capture and asserts it produced a security-clean,
non-empty fixture set, so a bad capture fails loudly instead of
silently committing something.
"""

from __future__ import annotations

from tests.compatibility._security_scan import scan_text
from tests.integration._capture import capture_matrix_fixtures


def test_capture_matrix_fixtures_for_this_entry(qbit_container, seeded_corpus):
    result = capture_matrix_fixtures(qbit_container, seeded_corpus)

    assert result.written_files, "capture produced no fixture files"
    for path in result.written_files:
        text = path.read_text(encoding="utf-8")
        assert not scan_text(
            text
        ), f"{path} failed the post-write security re-scan"
