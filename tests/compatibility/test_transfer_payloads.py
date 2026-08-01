"""Compatibility contract tests: transfer_info() payload fixtures.

Exercises `qbit_ops.qbit.fields.get_transfer_rates`, the function
`qbit_ops.features.status` routes through.
"""

from __future__ import annotations

import pytest

from qbit_ops.qbit.fields import get_transfer_rates
from tests.compatibility._fixture_loader import load_fixture


def test_ordinary_mapping_reads_both_rates() -> None:
    fixture = load_fixture("transfer", "ordinary")

    assert get_transfer_rates(fixture.payload) == (1048576, 262144)


def test_qbittorrent_api_mapping_like_object_is_accepted() -> None:
    """The real `qbittorrentapi.transfer.TransferInfoDictionary` type is
    a `dict` subclass -- prove it works, not just a plain dict."""
    import qbittorrentapi.transfer as qbt_transfer

    fixture = load_fixture("transfer", "ordinary")
    real_object = qbt_transfer.TransferInfoDictionary(
        data=dict(fixture.payload)
    )

    assert get_transfer_rates(real_object) == (1048576, 262144)


def test_missing_optional_rate_field_defaults_to_zero() -> None:
    fixture = load_fixture("transfer", "missing_optional_rate")

    assert get_transfer_rates(fixture.payload) == (1048576, 0)


def test_explicit_none_rate_defaults_to_zero() -> None:
    fixture = load_fixture("transfer", "explicit_none_rate")

    assert get_transfer_rates(fixture.payload) == (0, 262144)


def test_malformed_non_mapping_transfer_info_raises_explicit_type_error() -> (
    None
):
    class _NotAMapping:
        pass

    with pytest.raises(TypeError, match="mapping"):
        get_transfer_rates(_NotAMapping())


def test_extra_future_field_is_silently_ignored() -> None:
    fixture = load_fixture("transfer", "extra_future_field")

    assert get_transfer_rates(fixture.payload) == (1048576, 262144)
