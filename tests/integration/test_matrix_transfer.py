"""Speed-limit controls against real, disposable qBittorrent instances.

Proves the unit (bytes/s, whatever the Web API reference says), the
mode switch, the limit writes and the snapshot restore on every version.
"""

from __future__ import annotations

import pytest

from qbit_core import QbitConfig, create_qbit_client
from qbit_core.features.transfer import (
    SpeedLimits,
    read_transfer_info,
    read_transfer_settings,
    restore_transfer_settings,
    set_alternative_limits,
    set_alternative_mode,
    set_global_limits,
)
from tests.integration._harness import assert_target_is_disposable

_MIB = 1024 * 1024


@pytest.fixture()
def client(qbit_container):
    assert_target_is_disposable(qbit_container.host)
    return create_qbit_client(
        QbitConfig(
            host=qbit_container.host,
            username=qbit_container.username,
            password=qbit_container.password,
        )
    )


def test_limits_are_bytes_per_second_in_force_for_the_active_mode(client):
    set_global_limits(client, SpeedLimits(2 * _MIB, 0))
    set_alternative_limits(client, SpeedLimits(_MIB, 512 * 1024))

    set_alternative_mode(client, False)
    normal = read_transfer_info(client)
    assert (normal.download_rate_limit, normal.upload_rate_limit) == (
        2 * _MIB,
        0,
    )

    set_alternative_mode(client, True)
    alternative = read_transfer_info(client)
    assert (alternative.download_rate_limit, alternative.upload_rate_limit) == (
        _MIB,
        512 * 1024,
    )


def test_mode_switch_is_idempotent_and_read_back(client):
    set_alternative_mode(client, False)

    assert set_alternative_mode(client, True) is True
    assert read_transfer_settings(client).alternative_mode is True
    assert set_alternative_mode(client, True) is False
    assert set_alternative_mode(client, False) is True
    assert read_transfer_settings(client).alternative_mode is False


def test_limits_round_down_to_whole_kib(client):
    set_alternative_limits(client, SpeedLimits(5000, 1500))

    assert read_transfer_settings(client).alternative_limits == SpeedLimits(
        4096, 1024
    )


def test_limit_writes_leave_the_other_pair_and_the_mode_alone(client):
    set_alternative_mode(client, False)
    set_global_limits(client, SpeedLimits(3 * _MIB, _MIB))
    before = read_transfer_settings(client)

    set_alternative_limits(client, SpeedLimits(_MIB, _MIB))

    after = read_transfer_settings(client)
    assert after.global_limits == before.global_limits
    assert after.alternative_mode is before.alternative_mode


def test_restore_returns_the_snapshot_exactly(client):
    set_global_limits(client, SpeedLimits(2 * _MIB, 0))
    set_alternative_limits(client, SpeedLimits(_MIB, _MIB))
    set_alternative_mode(client, False)
    snapshot = read_transfer_settings(client)

    set_alternative_limits(client, SpeedLimits(64 * 1024, 64 * 1024))
    set_global_limits(client, SpeedLimits(0, 0))
    set_alternative_mode(client, True)
    assert read_transfer_settings(client) != snapshot

    restore_transfer_settings(client, snapshot)

    assert read_transfer_settings(client) == snapshot
