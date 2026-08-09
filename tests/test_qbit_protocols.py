"""Characterize the qBittorrent client protocols (`qbit_core.qbit.protocols`).

Proves the minimal protocols actually match what production calls
(`Signature.parameters` covers keyword-argument names, not just method
presence: `isinstance(fake, Protocol)` runtime checks alone only verify
a method with that name *exists*, not that it accepts the keyword
arguments production actually calls it with) and that the project's
canonical fake (`tests.support.FakeQbitClient`) conforms structurally,
not only nominally.
"""

from __future__ import annotations

import inspect

from qbit_core.qbit.protocols import (
    QbitAppInfoReader,
    QbitClient,
    QbitDestructiveMutator,
    QbitTorrentMutator,
    QbitTorrentReader,
    QbitTrackerMutator,
    QbitTransferReader,
)
from tests.support import FakeQbitClient

# Static (pyright-checked) structural conformance: never executed, but
# `make check`'s `pyright` step fails to type-check this module if
# `FakeQbitClient` stops satisfying any of these protocols -- a
# complementary guarantee to the runtime `isinstance` checks below,
# which only run under pytest.
_typed_as_torrent_reader: QbitTorrentReader = FakeQbitClient()
_typed_as_transfer_reader: QbitTransferReader = FakeQbitClient()
_typed_as_app_info_reader: QbitAppInfoReader = FakeQbitClient()
_typed_as_torrent_mutator: QbitTorrentMutator = FakeQbitClient()
_typed_as_destructive_mutator: QbitDestructiveMutator = FakeQbitClient()
_typed_as_tracker_mutator: QbitTrackerMutator = FakeQbitClient()
_typed_as_full_client: QbitClient = FakeQbitClient()


def test_fake_qbit_client_conforms_to_every_focused_protocol() -> None:
    """`tests.support.FakeQbitClient` satisfies every protocol at runtime."""
    fake = FakeQbitClient()

    assert isinstance(fake, QbitTorrentReader)
    assert isinstance(fake, QbitTransferReader)
    assert isinstance(fake, QbitAppInfoReader)
    assert isinstance(fake, QbitTorrentMutator)
    assert isinstance(fake, QbitDestructiveMutator)
    assert isinstance(fake, QbitTrackerMutator)
    assert isinstance(fake, QbitClient)


def test_fake_qbit_client_methods_accept_the_exact_keyword_arguments_used() -> (
    None
):
    """A runtime `isinstance` check only proves method *names* match.

    This proves the *keyword argument names* production actually calls
    with are accepted too -- an `isinstance` pass alone would not catch
    a fake whose `torrents_add_trackers(self, hash, url)` used different
    parameter names than production's `torrent_hash=`/`urls=` keywords.
    """
    fake = FakeQbitClient()

    add_trackers_params = set(
        inspect.signature(fake.torrents_add_trackers).parameters
    )
    assert {"torrent_hash", "urls"} <= add_trackers_params

    remove_trackers_params = set(
        inspect.signature(fake.torrents_remove_trackers).parameters
    )
    assert {"torrent_hash", "urls"} <= remove_trackers_params

    edit_tracker_params = set(
        inspect.signature(fake.torrents_edit_tracker).parameters
    )
    assert {"torrent_hash", "original_url", "new_url"} <= edit_tracker_params


def test_protocols_contain_only_methods_actually_consumed() -> None:
    """Guard against protocol scope creep: no method exists "because the
    library has it" -- each name here must be one of the exact methods
    `grep -rn "client\\." src/qbit_ops/` finds in production call sites.
    """
    expected_methods = {
        "torrents_info",
        "torrents_trackers",
        "transfer_info",
        "sync_maindata",
        "app_version",
        "app_web_api_version",
        "torrents_pause",
        "torrents_resume",
        "torrents_reannounce",
        "torrents_delete",
        "torrents_add_trackers",
        "torrents_remove_trackers",
        "torrents_edit_tracker",
    }

    declared_methods: set[str] = set()
    for protocol in (
        QbitTorrentReader,
        QbitTransferReader,
        QbitAppInfoReader,
        QbitTorrentMutator,
        QbitDestructiveMutator,
        QbitTrackerMutator,
    ):
        declared_methods.update(
            name for name in vars(protocol) if not name.startswith("_")
        )

    assert declared_methods == expected_methods


def test_torrents_start_is_deliberately_absent_from_the_mutator_protocol() -> (
    None
):
    """`torrents_start` is probed via `getattr(client, ..., None)` in
    production, an optional-attribute pattern a `Protocol` cannot
    express -- it must not be declared here."""
    assert "torrents_start" not in vars(QbitTorrentMutator)
    assert "torrents_start" not in vars(QbitClient)


def test_torrents_delete_stays_out_of_the_tui_reachable_mutator() -> None:
    """`QbitTorrentMutator` documents itself as the only mutation surface
    the TUI may reach, and the TUI is restricted to LOW-risk actions.
    Deleting torrents is HIGH risk, so it lives in its own protocol --
    moving it back would silently widen what a TUI-typed consumer can do.
    """
    assert "torrents_delete" not in vars(QbitTorrentMutator)
    assert "torrents_delete" in vars(QbitDestructiveMutator)
