"""Shared test doubles and factories for CLI-level tests."""

from typing import Any

from qbit_ops.config import QbitConfig


class FakeQbitClient:
    """Provide the qBittorrent methods needed by CLI-level tests.

    `self.calls` is a generic, ordered log of every method call (name plus
    positional/keyword args) across the whole fake, used to assert that
    progress instrumentation never adds, removes, or reorders an API call.
    The narrower per-method counters/lists below serve call-site tests
    that only care about one method.
    """

    def __init__(
        self,
        *,
        torrents: list[dict[str, Any]] | None = None,
        trackers_by_hash: dict[str, list[dict[str, Any]]] | None = None,
        tracker_error_hashes: set[str] | None = None,
        qbittorrent_version: str = "5.0.1",
        api_version: str = "2.9.3",
        download_speed: int = 0,
        upload_speed: int = 0,
        all_time_downloaded: int = 0,
        all_time_uploaded: int = 0,
        global_ratio: str = "-1",
        connected_peers: int = 0,
        session_downloaded: int = 0,
        session_uploaded: int = 0,
        dht_nodes: int = 0,
        download_rate_limit: int = 0,
        upload_rate_limit: int = 0,
        alt_speed_limits: bool = False,
        free_space: int = -1,
        queueing: bool = False,
        connection_status: str = "connected",
        torrents_add_error: Exception | None = None,
        torrents_delete_error: Exception | None = None,
        categories: dict[str, dict[str, Any]] | None = None,
        create_category_error: Exception | None = None,
        set_category_error: Exception | None = None,
        add_tags_error: Exception | None = None,
        remove_tags_error: Exception | None = None,
        add_trackers_error: Exception | None = None,
        set_limit_error: Exception | None = None,
    ) -> None:
        """Store fake instance data returned to callers.

        `tracker_error_hashes` names torrents whose `torrents_trackers()`
        call raises instead of returning data. `global_ratio` defaults
        to `"-1"` and `free_space` to `-1`, matching what a freshly
        started instance actually reports on every captured version --
        both are markers, not measures, and defaulting them to a real
        number would make the sentinels invisible to every test that
        did not ask for them.
        """
        self.torrents = torrents or []
        self.trackers_by_hash = trackers_by_hash or {}
        self.tracker_error_hashes = tracker_error_hashes or set()
        self.qbittorrent_version = qbittorrent_version
        self.api_version = api_version
        self.download_speed = download_speed
        self.upload_speed = upload_speed
        self.all_time_downloaded = all_time_downloaded
        self.all_time_uploaded = all_time_uploaded
        self.global_ratio = global_ratio
        self.connected_peers = connected_peers
        self.session_downloaded = session_downloaded
        self.session_uploaded = session_uploaded
        self.dht_nodes = dht_nodes
        self.download_rate_limit = download_rate_limit
        self.upload_rate_limit = upload_rate_limit
        self.alt_speed_limits = alt_speed_limits
        self.free_space = free_space
        self.queueing = queueing
        self.connection_status = connection_status
        self.torrents_add_error = torrents_add_error
        self.torrents_delete_error = torrents_delete_error
        self.categories = dict(categories or {})
        self.create_category_error = create_category_error
        self.set_category_error = set_category_error
        self.add_tags_error = add_tags_error
        self.remove_tags_error = remove_tags_error
        self.add_trackers_error = add_trackers_error
        self.set_limit_error = set_limit_error
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.added_torrent_files: list[list[bytes]] = []
        self.created_categories: list[str] = []
        self.set_categories: list[tuple[str | list[str], str]] = []
        self.added_tags: list[tuple[str | list[str], list[str]]] = []
        self.removed_tags: list[tuple[str | list[str], list[str]]] = []
        self.download_limits: list[tuple[str | list[str], int]] = []
        self.upload_limits: list[tuple[str | list[str], int]] = []
        self.app_version_calls = 0
        self.app_web_api_version_calls = 0
        self.torrents_trackers_calls = 0
        self.torrents_info_calls = 0
        self.transfer_info_calls = 0
        self.sync_maindata_calls = 0
        self.paused_hashes: list[str | list[str]] = []
        self.resumed_hashes: list[str | list[str]] = []
        self.started_hashes: list[str | list[str]] = []
        self.reannounced_hashes: list[str | list[str]] = []
        self.deleted_hashes: list[tuple[str | list[str], bool]] = []
        self.added_trackers: list[tuple[str, str | list[str]]] = []
        self.removed_trackers: list[tuple[str, list[str]]] = []
        self.edited_trackers: list[tuple[str, str, str]] = []

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        """Append one call to the generic call log."""
        self.calls.append((name, args, kwargs))

    def app_version(self) -> str:
        """Return the fake qBittorrent version."""
        self.app_version_calls += 1
        self._record("app_version")
        return self.qbittorrent_version

    def app_web_api_version(self) -> str:
        """Return the fake Web API version."""
        self.app_web_api_version_calls += 1
        self._record("app_web_api_version")
        return self.api_version

    def transfer_info(self) -> dict[str, int]:
        """Return fake global transfer speeds."""
        self.transfer_info_calls += 1
        self._record("transfer_info")
        return {
            "dl_info_speed": self.download_speed,
            "up_info_speed": self.upload_speed,
        }

    def sync_maindata(self) -> dict[str, Any]:
        """Return a fake `sync_maindata()` payload, `server_state` only."""
        self.sync_maindata_calls += 1
        self._record("sync_maindata")
        return {
            "server_state": {
                "alltime_dl": self.all_time_downloaded,
                "alltime_ul": self.all_time_uploaded,
                "global_ratio": self.global_ratio,
                "total_peer_connections": self.connected_peers,
                "dl_info_data": self.session_downloaded,
                "up_info_data": self.session_uploaded,
                "dht_nodes": self.dht_nodes,
                "dl_rate_limit": self.download_rate_limit,
                "up_rate_limit": self.upload_rate_limit,
                "use_alt_speed_limits": self.alt_speed_limits,
                "free_space_on_disk": self.free_space,
                "queueing": self.queueing,
                "connection_status": self.connection_status,
            }
        }

    def torrents_info(
        self, torrent_hashes: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Return fake torrents, narrowed upstream when asked.

        Mirrors `qbittorrent-api`: passing hashes filters server-side, so
        a caller that fetches one torrent does not transfer the library.
        Modelled here too, or a test could not tell the two apart.
        """
        self.torrents_info_calls += 1
        self._record("torrents_info")
        if torrent_hashes is None:
            return self.torrents
        wanted = {value.lower() for value in torrent_hashes}
        return [
            torrent
            for torrent in self.torrents
            if str(torrent.get("hash", "")).lower() in wanted
        ]

    def torrents_trackers(self, torrent_hash: str) -> list[dict[str, Any]]:
        """Record a per-torrent tracker call that `status` must avoid."""
        self.torrents_trackers_calls += 1
        self._record("torrents_trackers", torrent_hash)
        if torrent_hash in self.tracker_error_hashes:
            raise RuntimeError(f"tracker lookup failed for {torrent_hash}")
        return self.trackers_by_hash.get(torrent_hash, [])

    def torrents_pause(self, torrent_hashes: str | list[str]) -> None:
        """Record fake torrent pauses."""
        self.paused_hashes.append(torrent_hashes)
        self._record("torrents_pause", torrent_hashes)

    def torrents_resume(self, torrent_hashes: str | list[str]) -> None:
        """Record fake torrent resumes."""
        self.resumed_hashes.append(torrent_hashes)
        self._record("torrents_resume", torrent_hashes)

    def torrents_start(self, torrent_hashes: str | list[str]) -> None:
        """Record fake torrent starts."""
        self.started_hashes.append(torrent_hashes)
        self._record("torrents_start", torrent_hashes)

    def torrents_reannounce(self, torrent_hashes: str | list[str]) -> None:
        """Record fake torrent reannouncements."""
        self.reannounced_hashes.append(torrent_hashes)
        self._record("torrents_reannounce", torrent_hashes)

    def torrents_delete(
        self, delete_files: bool, torrent_hashes: str | list[str]
    ) -> None:
        """Record a fake torrent deletion; raise `torrents_delete_error`."""
        self._record(
            "torrents_delete",
            delete_files=delete_files,
            torrent_hashes=torrent_hashes,
        )
        if self.torrents_delete_error is not None:
            raise self.torrents_delete_error
        self.deleted_hashes.append((torrent_hashes, delete_files))

    def torrents_add_trackers(
        self, torrent_hash: str, urls: str | list[str]
    ) -> None:
        """Record fake tracker additions; raise `add_trackers_error` if set."""
        self._record("torrents_add_trackers", torrent_hash, urls)
        if self.add_trackers_error is not None:
            raise self.add_trackers_error
        self.added_trackers.append((torrent_hash, urls))

    def torrents_remove_trackers(
        self,
        torrent_hash: str,
        urls: list[str],
    ) -> None:
        """Record fake tracker removals."""
        self.removed_trackers.append((torrent_hash, urls))
        self._record("torrents_remove_trackers", torrent_hash, tuple(urls))

    def torrents_edit_tracker(
        self,
        torrent_hash: str,
        original_url: str,
        new_url: str,
    ) -> None:
        """Record fake tracker replacements."""
        self.edited_trackers.append((torrent_hash, original_url, new_url))
        self._record(
            "torrents_edit_tracker", torrent_hash, original_url, new_url
        )

    def torrents_categories(self) -> dict[str, dict[str, Any]]:
        """Return fake registered categories."""
        self._record("torrents_categories")
        return dict(self.categories)

    def torrents_create_category(self, name: str, **kwargs: Any) -> None:
        """Record a fake category creation; raise `create_category_error`."""
        self._record("torrents_create_category", name, **kwargs)
        if self.create_category_error is not None:
            raise self.create_category_error
        self.created_categories.append(name)
        self.categories[name] = {"name": name}

    def torrents_set_category(
        self, torrent_hashes: str | list[str], category: str
    ) -> None:
        """Record a fake category assignment; raise `set_category_error`."""
        self._record(
            "torrents_set_category",
            torrent_hashes=torrent_hashes,
            category=category,
        )
        if self.set_category_error is not None:
            raise self.set_category_error
        self.set_categories.append((torrent_hashes, category))

    def torrents_add_tags(
        self, torrent_hashes: str | list[str], tags: str | list[str]
    ) -> None:
        """Record a fake tag addition; raise `add_tags_error` if set."""
        self._record(
            "torrents_add_tags", torrent_hashes=torrent_hashes, tags=tags
        )
        if self.add_tags_error is not None:
            raise self.add_tags_error
        tag_list = [tags] if isinstance(tags, str) else list(tags)
        self.added_tags.append((torrent_hashes, tag_list))

    def torrents_remove_tags(
        self, torrent_hashes: str | list[str], tags: str | list[str]
    ) -> None:
        """Record a fake tag removal; raise `remove_tags_error` if set."""
        self._record(
            "torrents_remove_tags", torrent_hashes=torrent_hashes, tags=tags
        )
        if self.remove_tags_error is not None:
            raise self.remove_tags_error
        tag_list = [tags] if isinstance(tags, str) else list(tags)
        self.removed_tags.append((torrent_hashes, tag_list))

    def torrents_set_download_limit(
        self, torrent_hashes: str | list[str], limit: int
    ) -> None:
        """Record a fake download rate limit; raise `set_limit_error`."""
        self._record(
            "torrents_set_download_limit",
            torrent_hashes=torrent_hashes,
            limit=limit,
        )
        if self.set_limit_error is not None:
            raise self.set_limit_error
        self.download_limits.append((torrent_hashes, limit))

    def torrents_set_upload_limit(
        self, torrent_hashes: str | list[str], limit: int
    ) -> None:
        """Record a fake upload rate limit; raise `set_limit_error`."""
        self._record(
            "torrents_set_upload_limit",
            torrent_hashes=torrent_hashes,
            limit=limit,
        )
        if self.set_limit_error is not None:
            raise self.set_limit_error
        self.upload_limits.append((torrent_hashes, limit))

    def torrents_add(self, **kwargs: Any) -> str:
        """Record a fake add call; raise `torrents_add_error` if set."""
        self._record("torrents_add", **kwargs)
        if self.torrents_add_error is not None:
            raise self.torrents_add_error
        self.added_torrent_files.append(list(kwargs.get("torrent_files") or []))
        return "Ok."


def make_config(**overrides: Any) -> QbitConfig:
    """Build a fake qBittorrent configuration for tests."""
    defaults: dict[str, Any] = {
        "host": "http://admin:secret-password@localhost:8080",
        "username": "admin",
        "password": "secret-password",
    }
    defaults.update(overrides)
    return QbitConfig(**defaults)


def make_torrent(**overrides: Any) -> dict[str, Any]:
    """Build a fake torrent record for tests."""
    defaults: dict[str, Any] = {
        "hash": "0123456789abcdef0123456789abcdef01234567",
        "name": "Fake Torrent",
        "state": "uploading",
        "progress": 1.0,
        "ratio": 1.0,
        "size": 1_000_000,
        "category": "",
        "dlspeed": 0,
        "upspeed": 0,
    }
    defaults.update(overrides)
    return defaults


def without_comments(text: str) -> str:
    """Drop whole-line `#` comments from a Makefile, YAML or Dockerfile.

    Tests must assert on what a file *does*, never on how it describes
    itself (AGENTS.md, "Asserter le comportement, jamais la prose").
    These files routinely document the very rule under test, so raw-text
    assertions can pass on a comment, or fail because one was reworded.

    Only fully-commented lines go: a trailing `## dev: ...` help tag in a
    Makefile is effective content and is preserved.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )
