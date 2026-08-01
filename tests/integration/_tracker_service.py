"""Minimal, disposable, in-network-only BitTorrent tracker HTTP service.

Stdlib-only (`http.server`); never performs real peer bookkeeping.
Runs as its own container so its hostname resolves only inside the
disposable Docker network the harness creates -- never on the host,
never on a public network. Every `/announce` request is printed to
stdout as one JSON line so the harness can recover the request log via
`docker logs`.
"""

from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

try:
    from tests.integration._bencode import bencode
except ImportError:
    # Running standalone inside the tracker container (this file and
    # `_bencode.py` are bind-mounted flat, side by side, with no `tests`
    # package on that path -- see `_harness.py::start_tracker_container`).
    from _bencode import bencode  # type: ignore[import-not-found, no-redef]

FAKE_PLACEHOLDER_PASSKEY = "FAKE_PLACEHOLDER_PASSKEY_0000000000000000"


@dataclass
class RecordedAnnounce:
    """One recorded `/announce` request, for test assertions."""

    path: str
    query: dict[str, list[str]]


@dataclass
class TrackerLog:
    """Thread-safe log of every request the disposable tracker received."""

    _requests: list[RecordedAnnounce] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, path: str, query: dict[str, list[str]]) -> None:
        with self._lock:
            self._requests.append(RecordedAnnounce(path=path, query=query))

    def snapshot(self) -> list[RecordedAnnounce]:
        """Return a copy of every request recorded so far."""
        with self._lock:
            return list(self._requests)


def _make_handler(
    log: TrackerLog, *, emit_stdout: bool
) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            """Silence default stderr logging; requests are logged below."""

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            log.record(parsed.path, query)
            if emit_stdout:
                print(
                    json.dumps({"path": parsed.path, "query": query}),
                    file=sys.stdout,
                    flush=True,
                )

            if parsed.path == "/announce":
                body = bencode({"interval": 1800, "peers": b""})
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            self.send_response(404)
            self.end_headers()

    return _Handler


class DisposableTrackerService:
    """A minimal, deterministic tracker, bound to one host/port.

    Requires an explicit `bind_host`/`port` -- never guesses an
    interface, and never binds `0.0.0.0` from outside a container
    context, so it stays reachable only from inside the disposable
    Docker network it is deliberately published into.
    """

    def __init__(
        self, bind_host: str, port: int, *, emit_stdout: bool = False
    ) -> None:
        """Create the server socket; call `start()` to begin serving."""
        self.log = TrackerLog()
        self._server = ThreadingHTTPServer(
            (bind_host, port), _make_handler(self.log, emit_stdout=emit_stdout)
        )
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        """Return the bound port (useful when constructed with port 0)."""
        return self._server.server_address[1]

    def start(self) -> None:
        """Start serving in a background daemon thread."""
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Shut down the server and join its thread. Idempotent."""
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


def _run_foreground(bind_host: str, port: int) -> None:
    """Serve forever in the foreground, logging each request to stdout.

    Entry point used inside the disposable tracker container:
    `python3 _tracker_service.py <bind_host> <port>`.
    """
    service = DisposableTrackerService(bind_host, port, emit_stdout=True)
    print(
        f"qbit-ops disposable tracker listening on {bind_host}:{port}",
        flush=True,
    )
    service._server.serve_forever()  # noqa: SLF001 -- this *is* the foreground entry point


if __name__ == "__main__":
    _run_foreground(sys.argv[1], int(sys.argv[2]))
