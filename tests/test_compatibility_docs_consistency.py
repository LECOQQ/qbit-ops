"""Prove `docs/COMPATIBILITY.md`'s structured evidence table cannot
silently drift from the executable matrix manifest.

Deliberately narrow: parses only the one compact "| `qbit-X` | ... |"
table row per entry (id, image:tag, digest prefix, version, Web API
version) -- never the surrounding prose, which would make this a
fragile whole-document text match instead of a structured-fact check.
A passing run here says nothing about whether the documented range is
"broad" or "narrow"; see `test_docs_never_claim_a_broad_version_range`
for that, kept as a separate, explicit assertion.
"""

from __future__ import annotations

import re
from pathlib import Path

from qbit_ops.qbit.compatibility import load_compatibility_evidence

COMPATIBILITY_DOC = Path(__file__).parent.parent / "docs" / "COMPATIBILITY.md"

_TABLE_ROW_PATTERN = re.compile(
    r"^\| `(?P<id>qbit-[\d.]+)` \| `[^:]+:[^`]+` "
    r"\(`(?P<digest_prefix>sha256:[0-9a-f]+)\.\.\.`\) "
    r"\| `v(?P<version>[\d.]+)` \| `(?P<web_api>[\d.]+)` \|",
    re.MULTILINE,
)


def _parsed_doc_rows() -> dict[str, dict[str, str]]:
    text = COMPATIBILITY_DOC.read_text(encoding="utf-8")
    rows = {}
    for match in _TABLE_ROW_PATTERN.finditer(text):
        rows[match.group("id")] = {
            "digest_prefix": match.group("digest_prefix"),
            "version": match.group("version"),
            "web_api": match.group("web_api"),
        }
    return rows


def test_evidence_table_discovery_is_non_empty() -> None:
    """Guard the parser itself: an empty result would make every other
    assertion in this file vacuously pass."""
    assert len(_parsed_doc_rows()) >= 4


def test_every_matrix_entry_appears_in_the_evidence_table() -> None:
    doc_rows = _parsed_doc_rows()
    manifest_ids = {entry.id for entry in load_compatibility_evidence().entries}
    assert manifest_ids <= set(doc_rows), (
        f"matrix entries missing from docs/COMPATIBILITY.md's evidence "
        f"table: {manifest_ids - set(doc_rows)}"
    )


def test_evidence_table_never_lists_an_id_absent_from_the_manifest() -> None:
    doc_rows = _parsed_doc_rows()
    manifest_ids = {entry.id for entry in load_compatibility_evidence().entries}
    assert set(doc_rows) <= manifest_ids, (
        f"docs/COMPATIBILITY.md documents an id the manifest no longer "
        f"has: {set(doc_rows) - manifest_ids}"
    )


def test_documented_version_and_web_api_match_the_manifest() -> None:
    doc_rows = _parsed_doc_rows()
    for entry in load_compatibility_evidence().entries:
        row = doc_rows[entry.id]
        assert row["version"] == entry.expected_version.removeprefix(
            "v"
        ), entry.id
        assert row["web_api"] == entry.expected_web_api_version, entry.id
        assert entry.image_digest.startswith(row["digest_prefix"]), entry.id


def test_the_normative_compatibility_claims_policy_is_tracked() -> None:
    """The eight-rule compatibility-claims policy must live in this
    tracked file (§10), not only in the gitignored `AGENTS.md`."""
    text = COMPATIBILITY_DOC.read_text(encoding="utf-8")
    assert "Politique normative de revendication de compatibilité" in text
    assert "container integration tested" in text.lower()


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
