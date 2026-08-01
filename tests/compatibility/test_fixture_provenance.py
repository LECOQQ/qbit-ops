"""Prove every fixture declares honest, checkable provenance.

A fixture may only claim a qBittorrent/Web API version label when its
trust level actually supports that claim (see `docs/COMPATIBILITY.md`).
"""

from __future__ import annotations

from tests.compatibility._fixture_loader import (
    _VALID_TRUST_LEVELS,
    load_all_fixtures,
)

# Trust levels honest enough to name a specific qBittorrent/Web API
# version as observed (not merely "documented" or "constructed").
_TRUST_LEVELS_THAT_MAY_CLAIM_A_CAPTURED_VERSION = frozenset(
    {"captured-container", "captured-instance"}
)


def test_every_fixture_declares_a_valid_trust_level() -> None:
    fixtures = load_all_fixtures()
    assert fixtures

    for fixture in fixtures:
        assert fixture.trust in _VALID_TRUST_LEVELS, (
            f"{fixture.category}/{fixture.name}: invalid trust level "
            f"{fixture.trust!r}"
        )


def test_synthetic_fixtures_never_claim_a_captured_qbittorrent_version() -> (
    None
):
    offenders = []
    for fixture in load_all_fixtures():
        if fixture.trust == "synthetic" and fixture.qbittorrent_version:
            offenders.append(f"{fixture.category}/{fixture.name}")

    assert (
        not offenders
    ), f"synthetic fixtures falsely claiming a captured version: {offenders}"


def test_only_captured_trust_levels_may_declare_a_qbittorrent_version() -> None:
    """Even `official-example` (documented convention, not an actual
    capture) must not assert a specific captured version."""
    offenders = []
    for fixture in load_all_fixtures():
        claims_version = bool(fixture.qbittorrent_version)
        allowed = (
            fixture.trust in _TRUST_LEVELS_THAT_MAY_CLAIM_A_CAPTURED_VERSION
        )
        if claims_version and not allowed:
            offenders.append(
                f"{fixture.category}/{fixture.name} (trust={fixture.trust})"
            )

    assert not offenders, (
        f"fixtures claiming a version their trust level does not support: "
        f"{offenders}"
    )


def test_no_fixture_is_currently_labelled_as_a_real_capture() -> None:
    """`load_all_fixtures()` only enumerates flat `category/name.json`
    files, so `captured-container` fixtures (covered separately by
    `test_captured_container_payloads.py`) never appear here."""
    captured = [
        f"{fixture.category}/{fixture.name}"
        for fixture in load_all_fixtures()
        if fixture.trust in _TRUST_LEVELS_THAT_MAY_CLAIM_A_CAPTURED_VERSION
    ]

    assert captured == []


def test_every_fixture_has_a_non_empty_description_and_limitations() -> None:
    offenders = []
    for fixture in load_all_fixtures():
        if not fixture.description.strip() or not fixture.limitations.strip():
            offenders.append(f"{fixture.category}/{fixture.name}")

    assert not offenders, f"missing description/limitations: {offenders}"
