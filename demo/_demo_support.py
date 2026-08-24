"""Small pieces shared by the `demo/` scripts that drive disposable
qBittorrent instances over their WebUI API.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request


class SeedError(RuntimeError):
    """Raised when a disposable demo qBittorrent instance misbehaves."""


def assert_demo_host(
    host: str, *, expected_port: int, expected_hostname: str = "127.0.0.1"
) -> None:
    """Refuse anything but the pinned disposable demo host."""
    parsed = urllib.parse.urlsplit(host)
    if parsed.hostname != expected_hostname or parsed.port != expected_port:
        raise SeedError(
            f"refusing to use {host!r}: expected the disposable demo host "
            f"{expected_hostname}:{expected_port}"
        )


def wait_for_webui(host: str, *, timeout_seconds: float) -> None:
    """Block until `host`'s WebUI answers, or raise `SeedError`."""
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"{host}/api/v2/app/webapiVersion", timeout=2
            ) as resp:
                if resp.status == 200:
                    return
        except urllib.error.HTTPError as error:
            if error.code == 403:
                return
            last_error = error
        except (urllib.error.URLError, OSError) as error:
            last_error = error
        time.sleep(1)
    raise SeedError(
        f"qBittorrent WebUI at {host} did not become ready: {last_error}"
    )
