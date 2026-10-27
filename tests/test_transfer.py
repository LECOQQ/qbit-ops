from __future__ import annotations

import pytest

from qbit_core.errors import InvalidInputError, QbitCoreError
from qbit_core.features.transfer import (
    SpeedLimits,
    TransferSettings,
    read_transfer_info,
    read_transfer_settings,
    restore_transfer_settings,
    set_alternative_limits,
    set_alternative_mode,
    set_global_limits,
)
from tests.support import FakeQbitClient


def _mutating_calls(client: FakeQbitClient) -> list[str]:
    return [
        name
        for name, _args, _kwargs in client.calls
        if name in ("transfer_set_speed_limits_mode", "app_set_preferences")
    ]


def test_snapshot_reads_mode_and_both_limit_pairs() -> None:
    client = FakeQbitClient(
        alt_speed_limits=True,
        global_limits=(2048, 4096),
        alt_limits=(1024, 0),
    )

    assert read_transfer_settings(client) == TransferSettings(
        alternative_mode=True,
        global_limits=SpeedLimits(download=2048, upload=4096),
        alternative_limits=SpeedLimits(download=1024, upload=0),
    )
    assert _mutating_calls(client) == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", True), ("0", False), (1, True), (0, False), (True, True)],
)
def test_mode_shapes_are_normalized(raw: object, expected: bool) -> None:
    client = FakeQbitClient()
    client.speed_limits_mode_raw = raw

    assert read_transfer_settings(client).alternative_mode is expected


def test_unexpected_mode_is_a_core_error() -> None:
    client = FakeQbitClient()
    client.speed_limits_mode_raw = "maybe"

    with pytest.raises(QbitCoreError):
        read_transfer_settings(client)


def test_negative_limit_means_unlimited() -> None:
    client = FakeQbitClient(global_limits=(-1, 5120), alt_limits=(-1, -1))

    settings = read_transfer_settings(client)

    assert settings.global_limits == SpeedLimits(0, 5120)
    assert settings.alternative_limits == SpeedLimits(0, 0)


def test_missing_preference_is_an_error_not_unlimited() -> None:
    client = FakeQbitClient()
    del client.preferences["alt_up_limit"]

    with pytest.raises(QbitCoreError, match="alt_up_limit"):
        read_transfer_settings(client)


def test_read_transfer_info_maps_the_payload() -> None:
    client = FakeQbitClient(
        download_speed=10,
        upload_speed=20,
        download_rate_limit=1024,
        upload_rate_limit=0,
        dht_nodes=7,
        session_downloaded=100,
        session_uploaded=200,
        connection_status="firewalled",
    )

    info = read_transfer_info(client)

    assert (info.download_speed, info.upload_speed) == (10, 20)
    assert (info.download_rate_limit, info.upload_rate_limit) == (1024, 0)
    assert info.connection_status == "firewalled"
    assert info.dht_nodes == 7
    assert (info.session_downloaded, info.session_uploaded) == (100, 200)


def test_set_alternative_mode_changes_then_is_idempotent() -> None:
    client = FakeQbitClient()

    assert set_alternative_mode(client, True) is True
    assert set_alternative_mode(client, True) is False
    assert client.speed_limits_mode_writes == [True]
    assert set_alternative_mode(client, False) is True
    assert client.speed_limits_mode_writes == [True, False]


def test_set_alternative_mode_writes_nothing_when_already_right() -> None:
    client = FakeQbitClient(alt_speed_limits=True)

    assert set_alternative_mode(client, True) is False
    assert _mutating_calls(client) == []


def test_limit_setters_write_only_their_own_pair() -> None:
    client = FakeQbitClient()

    set_alternative_limits(client, SpeedLimits(1024, 2048))
    set_global_limits(client, SpeedLimits(0, 4096))

    assert client.preference_writes == [
        {"alt_dl_limit": 1024, "alt_up_limit": 2048},
        {"dl_limit": 0, "up_limit": 4096},
    ]
    assert client.speed_limits_mode_writes == []


@pytest.mark.parametrize(
    "limits",
    [SpeedLimits(-1, 0), SpeedLimits(0, -5), SpeedLimits(1.5, 0)],  # type: ignore[arg-type]
)
def test_invalid_limits_fail_before_any_api_call(limits: SpeedLimits) -> None:
    client = FakeQbitClient()

    for setter in (set_alternative_limits, set_global_limits):
        with pytest.raises(InvalidInputError):
            setter(client, limits)
    assert client.calls == []


def test_bool_is_not_a_valid_limit() -> None:
    client = FakeQbitClient()

    with pytest.raises(InvalidInputError):
        set_global_limits(client, SpeedLimits(True, 0))  # type: ignore[arg-type]
    assert client.calls == []


def test_restore_writes_limits_before_mode() -> None:
    client = FakeQbitClient(
        alt_speed_limits=True,
        global_limits=(1024, 1024),
        alt_limits=(2048, 2048),
    )
    snapshot = read_transfer_settings(client)
    set_alternative_limits(client, SpeedLimits(4096, 4096))
    set_global_limits(client, SpeedLimits(0, 0))
    set_alternative_mode(client, False)
    client.calls.clear()

    restore_transfer_settings(client, snapshot)

    assert _mutating_calls(client) == [
        "app_set_preferences",
        "transfer_set_speed_limits_mode",
    ]
    assert read_transfer_settings(client) == snapshot


def test_restore_skips_the_mode_write_when_unchanged() -> None:
    client = FakeQbitClient()
    snapshot = read_transfer_settings(client)

    restore_transfer_settings(client, snapshot)

    assert client.speed_limits_mode_writes == []
    assert len(client.preference_writes) == 1


def test_restore_validates_the_whole_snapshot_first() -> None:
    client = FakeQbitClient()
    snapshot = TransferSettings(
        alternative_mode=True,
        global_limits=SpeedLimits(1024, 1024),
        alternative_limits=SpeedLimits(-1, 0),
    )

    with pytest.raises(InvalidInputError):
        restore_transfer_settings(client, snapshot)
    assert client.calls == []
