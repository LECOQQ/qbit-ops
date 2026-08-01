"""Record the real HTTP endpoints `qbittorrentapi` sends to a real container.

Records the real request path while it is still forwarded to the
disposable container -- never faked, only observed.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import qbittorrentapi.request as qbit_request


@dataclass
class RecordedHttpRequests:
    """Every HTTP request qbittorrentapi issued during one recording window."""

    calls: list[tuple[str, str]] = field(default_factory=list)

    def endpoints(self) -> list[str]:
        """Return just the endpoint path (e.g. `torrents/stop`) per call."""
        return [
            urlsplit(url).path.rsplit("/api/v2/", 1)[-1]
            for _method, url in self.calls
        ]

    def count(self, endpoint_suffix: str) -> int:
        """Count calls whose endpoint path ends with `endpoint_suffix`."""
        return sum(
            1
            for endpoint in self.endpoints()
            if endpoint.endswith(endpoint_suffix)
        )


@contextmanager
def record_http_requests():
    """Record every real HTTP request the installed qbittorrentapi issues.

    Wraps (not replaces) `QbittorrentSession.request`: every request
    still reaches the real disposable container exactly as before --
    this only observes the method/URL qbittorrentapi chose.
    """
    recorded = RecordedHttpRequests()
    original_request = qbit_request.QbittorrentSession.request

    def _recording_request(self, method, url, **kwargs):  # noqa: ANN001, ANN003
        recorded.calls.append((method, url))
        return original_request(self, method, url, **kwargs)

    qbit_request.QbittorrentSession.request = _recording_request
    try:
        yield recorded
    finally:
        qbit_request.QbittorrentSession.request = original_request
