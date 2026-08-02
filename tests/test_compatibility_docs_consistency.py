"""Prove `docs/COMPATIBILITY.md`'s version/Web API table cannot silently
drift from the executable matrix manifest.

Deliberately narrow: parses only the compact "| qBittorrent | Web API |
..." table row per entry -- never the surrounding prose, which would
make this a fragile whole-document text match instead of a structured-
fact check. A passing run here says nothing about whether the
documented range is "broad" or "narrow"; see
`test_docs_never_claim_a_broad_version_range` for that, kept as a
separate, explicit assertion.
"""

from __future__ import annotations

import re
from pathlib import Path

from qbit_ops.qbit.compatibility import load_compatibility_evidence

COMPATIBILITY_DOC = Path(__file__).parent.parent / "docs" / "COMPATIBILITY.md"

_TABLE_ROW_PATTERN = re.compile(
    r"^\| (?P<version>[\d.]+) \| (?P<web_api>[\d.]+) \|",
    re.MULTILINE,
)


def _parsed_doc_rows() -> dict[str, str]:
    text = COMPATIBILITY_DOC.read_text(encoding="utf-8")
    return {
        match.group("version"): match.group("web_api")
        for match in _TABLE_ROW_PATTERN.finditer(text)
    }


def test_evidence_table_discovery_is_non_empty() -> None:
    """Guard the parser itself: an empty result would make every other
    assertion in this file vacuously pass."""
    assert len(_parsed_doc_rows()) >= 4


def test_every_matrix_entry_appears_in_the_evidence_table() -> None:
    doc_versions = _parsed_doc_rows()
    manifest_versions = {
        entry.expected_version.removeprefix("v")
        for entry in load_compatibility_evidence().entries
    }
    assert manifest_versions <= set(doc_versions), (
        f"matrix versions missing from docs/COMPATIBILITY.md's evidence "
        f"table: {manifest_versions - set(doc_versions)}"
    )


def test_evidence_table_never_lists_a_version_absent_from_the_manifest() -> (
    None
):
    doc_versions = _parsed_doc_rows()
    manifest_versions = {
        entry.expected_version.removeprefix("v")
        for entry in load_compatibility_evidence().entries
    }
    assert set(doc_versions) <= manifest_versions, (
        f"docs/COMPATIBILITY.md documents a version the manifest no "
        f"longer has: {set(doc_versions) - manifest_versions}"
    )


def test_documented_web_api_matches_the_manifest() -> None:
    doc_versions = _parsed_doc_rows()
    for entry in load_compatibility_evidence().entries:
        version = entry.expected_version.removeprefix("v")
        assert doc_versions[version] == entry.expected_web_api_version, entry.id


def test_the_compatibility_claims_policy_is_tracked() -> None:
    """The exact-versions-only claims policy must live in this tracked
    file, not only in the gitignored `AGENTS.md`."""
    text = COMPATIBILITY_DOC.read_text(encoding="utf-8")
    assert "must always cite exact tested versions" in text
    assert "container-integration tested" in text.lower()


_FORBIDDEN_CONTEXT_MARKERS = ("jamais", "never", "interdit", "forbidden")
_CONTEXT_WINDOW_LINES = 6


def test_docs_never_claim_a_broad_version_range() -> None:
    """A broad range claim must never appear as an actual assertion in the
    tracked documentation -- only inside an explicit "never/forbidden
    say this" example. Looks at a small window of nearby lines (not
    just the exact matching line), since a "formulations interdites:"
    heading legitimately introduces a bullet list where the qualifier
    and the forbidden phrase are on different lines."""
    lines = COMPATIBILITY_DOC.read_text(encoding="utf-8").splitlines()
    broad_claims = [
        "compatible with qBittorrent 4.6",
        "supports qBittorrent 4.x and 5.x",
        "all qBittorrent 5.x versions are supported",
    ]
    for claim in broad_claims:
        for index, line in enumerate(lines):
            if claim.lower() not in line.lower():
                continue
            window_start = max(0, index - _CONTEXT_WINDOW_LINES)
            window = " ".join(lines[window_start : index + 1]).lower()
            assert any(
                marker in window for marker in _FORBIDDEN_CONTEXT_MARKERS
            ), (
                f"broad version-range claim found outside a "
                f"forbidden-example context: {line!r}"
            )


_TEST_READMES_WITH_HERMETICITY_WORDING = (
    Path(__file__).parent / "integration" / "README.md",
    Path(__file__).parent / "compatibility" / "README.md",
)
_UNQUALIFIED_ISOLATION_CLAIMS = (
    "network is isolated",
    "network is hermetic",
)


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_test_readmes_never_claim_network_egress_is_technically_blocked() -> (
    None
):
    """The disposable container's public network egress is NOT technically
    blocked (only application-level DHT/PeX/LSD/UPnP/tracker settings
    are disabled) -- `docs/COMPATIBILITY.md` §5.2 documents this
    precisely. Both test READMEs must carry the same qualification:
    every occurrence of "technically blocked" must be part of a
    "not ... technically blocked" qualification (whitespace/markdown-
    tolerant), and neither README may claim the Docker network itself
    is isolated/hermetic."""
    offenders = []
    for readme in _TEST_READMES_WITH_HERMETICITY_WORDING:
        normalized = _normalize_whitespace(readme.read_text(encoding="utf-8"))
        text_lower = normalized.lower()

        for claim in _UNQUALIFIED_ISOLATION_CLAIMS:
            if claim in text_lower:
                offenders.append(f"{readme.name}: {claim!r}")

        for match in re.finditer("technically blocked", text_lower):
            preceding = text_lower[max(0, match.start() - 30) : match.start()]
            if "not" not in preceding:
                offenders.append(
                    f"{readme.name}: unqualified 'technically blocked' "
                    f"near {preceding!r}"
                )

    assert not offenders, (
        f"test README(s) falsely claim network egress is blocked: "
        f"{offenders}"
    )


def test_test_readmes_qualify_hermetic_as_configuration_not_network() -> None:
    """The word "hermetic" may remain in these READMEs only when
    qualified as configuration/test-target hermeticity -- never used
    bare to describe the Docker network itself. Also guards discovery:
    both READMEs must actually mention the egress limitation at all."""
    for readme in _TEST_READMES_WITH_HERMETICITY_WORDING:
        text = _normalize_whitespace(readme.read_text(encoding="utf-8"))
        assert "not technically blocked" in text, (
            f"{readme.name} does not mention the network-egress "
            "limitation at all"
        )
