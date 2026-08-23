"""Unit tests for `qbit_core.qbit.compatibility`, the package-owned loader.

Covers the packaged manifest's happy path plus every fail-closed case
required by the phase spec: missing package data, empty manifest,
malformed TOML, duplicate entry id, duplicate exact application/Web API
evidence, and an unparseable version string. Missing/malformed/empty
cases exercise `parse_manifest_text()` directly against in-memory text
rather than ever touching the real packaged file on disk.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from qbit_core.qbit import compatibility as compat_module
from qbit_core.qbit.compatibility import (
    CompatibilityManifestError,
    load_compatibility_evidence,
    parse_manifest_text,
)

_VALID_ENTRY = """
[[entry]]
id = "qbit-1.0.0"
image_repository = "linuxserver/qbittorrent"
image_tag = "1.0.0"
image_digest = "sha256:{digest}"
expected_architecture = "amd64"
release_line = "1.0.x"
expected_version = "v1.0.0"
expected_web_api_version = "2.0.0"
webui_port = 18100
capabilities = ["read_only"]
limitations = "none"
provenance = "test fixture"
"""


def _entry(entry_id: str = "qbit-1.0.0", digest: str = "abc123") -> str:
    return _VALID_ENTRY.replace('"qbit-1.0.0"', f'"{entry_id}"').replace(
        "{digest}", digest
    )


# --- Real packaged manifest -----------------------------------------------


def test_loads_all_four_real_entries_with_expected_fields() -> None:
    evidence = load_compatibility_evidence()
    assert evidence.ids == (
        "qbit-4.6.7",
        "qbit-5.0.0",
        "qbit-5.1.4",
        "qbit-5.2.3",
    )
    entry = evidence.entry("qbit-5.2.3")
    assert entry.expected_version == "v5.2.3"
    assert entry.expected_web_api_version == "2.15.1"
    assert entry.expected_architecture == "amd64"
    assert entry.image_reference == "linuxserver/qbittorrent:5.2.3"
    assert entry.image_reference_with_digest.startswith(
        "linuxserver/qbittorrent@sha256:"
    )
    assert entry.has_capability("read_only")
    assert not entry.has_capability("not_a_real_capability")


def test_latest_returns_the_highest_expected_version() -> None:
    evidence = load_compatibility_evidence()
    assert evidence.latest().id == "qbit-5.2.3"


def test_oldest_returns_the_lowest_expected_version() -> None:
    evidence = load_compatibility_evidence()
    assert evidence.oldest().id == "qbit-4.6.7"


def test_entry_raises_key_error_on_unknown_id() -> None:
    evidence = load_compatibility_evidence()
    with pytest.raises(KeyError, match="unknown qBittorrent matrix id"):
        evidence.entry("qbit-9.9.9-does-not-exist")


def test_entry_for_application_version_finds_exact_match() -> None:
    evidence = load_compatibility_evidence()
    match = evidence.entry_for_application_version("5.2.3")
    assert match is not None
    assert match.id == "qbit-5.2.3"
    match_with_v_prefix = evidence.entry_for_application_version("v5.2.3")
    assert match_with_v_prefix is not None
    assert match_with_v_prefix.id == "qbit-5.2.3"


def test_entry_for_application_version_returns_none_for_untested_version() -> (
    None
):
    evidence = load_compatibility_evidence()
    assert evidence.entry_for_application_version("5.0.1") is None
    assert evidence.entry_for_application_version("9.9.9") is None


def test_entry_for_application_version_does_not_match_a_patch_sibling() -> None:
    """A patch release adjacent to an exact tested entry must not
    match: one patch release is never evidence for its whole line."""
    evidence = load_compatibility_evidence()
    assert evidence.entry_for_application_version("5.2.2") is None
    assert evidence.entry_for_application_version("5.2.4") is None


def test_importing_the_module_does_not_parse_the_manifest() -> None:
    """Importing `qbit_core.qbit.compatibility` must never read/parse the
    manifest -- only calling `load_compatibility_evidence()` does.

    Verified statically (not by reloading the module, which would mint a
    second, distinct `CompatibilityManifestError` class and break every
    `pytest.raises` identity check elsewhere in this process): every
    top-level module statement other than an import/class/function
    definition must contain no function call at all.
    """
    tree = ast.parse(inspect.getsource(compat_module))
    for node in tree.body:
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
                ast.Import,
                ast.ImportFrom,
            ),
        ):
            continue
        for sub_node in ast.walk(node):
            assert not isinstance(sub_node, ast.Call), (
                f"module-level call found outside a function/class body: "
                f"{ast.dump(sub_node)}"
            )


# --- Fail-closed parsing/validation ----------------------------------------


def test_empty_manifest_fails_closed() -> None:
    with pytest.raises(CompatibilityManifestError, match="zero matrix entries"):
        parse_manifest_text("")


def test_malformed_toml_fails_closed() -> None:
    with pytest.raises(CompatibilityManifestError, match="malformed"):
        parse_manifest_text("this is not [valid toml")


def test_duplicate_matrix_id_fails_closed() -> None:
    text = _entry("qbit-dup", digest="a") + _entry("qbit-dup", digest="b")
    with pytest.raises(
        CompatibilityManifestError, match="duplicate matrix entry id"
    ):
        parse_manifest_text(text)


def test_duplicate_exact_application_and_web_api_evidence_fails_closed() -> (
    None
):
    """Two entries claiming the identical (expected_version,
    expected_web_api_version) pair is redundant/contradictory evidence
    and must be rejected explicitly, not silently accepted."""
    text = _entry("qbit-a", digest="a") + _entry("qbit-b", digest="b")
    with pytest.raises(
        CompatibilityManifestError,
        match="duplicate exact application/Web API evidence",
    ):
        parse_manifest_text(text)


def test_unparseable_version_string_fails_closed() -> None:
    text = _entry().replace(
        'expected_version = "v1.0.0"', 'expected_version = "not-a-version"'
    )
    with pytest.raises(CompatibilityManifestError, match="unparseable"):
        parse_manifest_text(text)


def test_unparseable_web_api_version_string_fails_closed() -> None:
    text = _entry().replace(
        'expected_web_api_version = "2.0.0"',
        'expected_web_api_version = "not-a-version"',
    )
    with pytest.raises(CompatibilityManifestError, match="unparseable"):
        parse_manifest_text(text)


def test_missing_required_field_fails_closed() -> None:
    text = _entry().replace(
        'image_repository = "linuxserver/qbittorrent"\n', ""
    )
    with pytest.raises(
        CompatibilityManifestError, match="missing required field"
    ):
        parse_manifest_text(text)


def test_missing_package_data_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_missing() -> str:
        raise CompatibilityManifestError("simulated missing package data")

    monkeypatch.setattr(
        compat_module, "_read_packaged_manifest_text", _raise_missing
    )
    with pytest.raises(
        CompatibilityManifestError, match="missing package data"
    ):
        compat_module.load_compatibility_evidence()
