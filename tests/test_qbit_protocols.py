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
    QbitBulkTorrentMutator,
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
_typed_as_bulk_torrent_mutator: QbitBulkTorrentMutator = FakeQbitClient()
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
    assert isinstance(fake, QbitBulkTorrentMutator)
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

    # `QbitTorrentMutator`'s widened half (`apply_bulk_torrent_action`,
    # `qbit_core.features.torrents`) -- same rationale as above: a fake
    # whose keywords drifted from production's would still pass a bare
    # `isinstance` check.
    create_category_params = set(
        inspect.signature(fake.torrents_create_category).parameters
    )
    assert {"name"} <= create_category_params

    set_category_params = set(
        inspect.signature(fake.torrents_set_category).parameters
    )
    assert {"torrent_hashes", "category"} <= set_category_params

    add_tags_params = set(inspect.signature(fake.torrents_add_tags).parameters)
    assert {"torrent_hashes", "tags"} <= add_tags_params

    remove_tags_params = set(
        inspect.signature(fake.torrents_remove_tags).parameters
    )
    assert {"torrent_hashes", "tags"} <= remove_tags_params

    set_download_limit_params = set(
        inspect.signature(fake.torrents_set_download_limit).parameters
    )
    assert {"torrent_hashes", "limit"} <= set_download_limit_params

    set_upload_limit_params = set(
        inspect.signature(fake.torrents_set_upload_limit).parameters
    )
    assert {"torrent_hashes", "limit"} <= set_upload_limit_params


def test_protocols_contain_only_methods_actually_consumed() -> None:
    """Guard against protocol scope creep: no method exists "because the
    library has it" -- each name here must be one of the exact methods
    `grep -rn "client\\." src/` finds in production call sites (not
    only `src/qbit_ops/`: `qbit_core.features.torrents` calls several
    of these directly, e.g. `torrents_start`, `torrents_tags`).
    """
    expected_methods = {
        "torrents_info",
        "torrents_trackers",
        "torrents_categories",
        "torrents_tags",
        "transfer_info",
        "sync_maindata",
        "app_version",
        "app_web_api_version",
        "torrents_pause",
        "torrents_start",
        "torrents_reannounce",
        "torrents_create_category",
        "torrents_set_category",
        "torrents_add_tags",
        "torrents_remove_tags",
        "torrents_set_download_limit",
        "torrents_set_upload_limit",
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


def test_torrents_start_is_declared_not_the_unused_resume_alias() -> None:
    """`_call_bulk_torrent_action` calls `client.torrents_start(...)`
    directly, never `torrents_resume` (see `tests/test_torrents.py::
    test_bulk_resume_calls_torrents_start_without_torrents_resume`) --
    the protocol must type the method actually called, not its unused
    alias.

    `hasattr`, not `vars()`, against `QbitClient`: it declares no
    method of its own, only inherits every base protocol's, so
    `"x" not in vars(QbitClient)` would hold no matter what `x` is --
    exactly the kind of assertion `hasattr` catches and `vars()` can't.
    """
    assert "torrents_start" in vars(QbitTorrentMutator)
    assert hasattr(QbitClient, "torrents_start")
    assert "torrents_resume" not in vars(QbitTorrentMutator)
    assert not hasattr(QbitClient, "torrents_resume")


def test_torrents_delete_stays_out_of_the_tui_reachable_mutator() -> None:
    """`QbitTorrentMutator` types every LOW-risk bulk action
    `apply_bulk_torrent_action` can apply -- pause/resume/reannounce
    plus category/tags/throttle, widened from pause/resume/reannounce
    alone to close the gap those extra actions used to pass through
    untyped. What this test actually checks, and the one thing that
    does not move with that widening: deleting torrents is HIGH risk,
    so it lives in its own protocol, separate from every LOW-risk one
    -- moving it back would silently widen what a TUI-typed consumer
    can do.
    """
    assert "torrents_delete" not in vars(QbitTorrentMutator)
    assert "torrents_delete" in vars(QbitDestructiveMutator)


def test_bulk_torrent_mutator_covers_every_action_apply_bulk_can_reach() -> (
    None
):
    """`apply_bulk_torrent_action` is shared by the CLI (which can
    request `delete`) and the TUI (which cannot -- see
    `tests/test_tui_security.py`), so its own `client` parameter must
    structurally cover both: every LOW-risk action plus delete.
    `QbitTorrentMutator` alone is not enough -- that gap is exactly
    what used to leave `apply_bulk_torrent_action` typed `Any`.
    """
    fake = FakeQbitClient()

    assert isinstance(fake, QbitBulkTorrentMutator)
    assert not issubclass(QbitTorrentMutator, QbitBulkTorrentMutator)
    for name in vars(QbitTorrentMutator):
        if not name.startswith("_"):
            assert hasattr(QbitBulkTorrentMutator, name)
    assert hasattr(QbitBulkTorrentMutator, "torrents_delete")
