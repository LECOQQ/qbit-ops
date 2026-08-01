"""Build a pre-seeded, hermetic `qBittorrent.conf` for a disposable container.

Writing this file into the container's `/config/qBittorrent/` *before*
first boot (via a bind mount) avoids two things a post-boot API call
cannot avoid:

1. qBittorrent's default-on DHT/PeX/LSD/GeoIP-update behavior would
   otherwise run, unsupervised, for the few seconds between container
   start and the harness's first authenticated API call (a fresh
   default container's log shows a real outbound geolocation-database
   update before any preference can be set over the API).
2. The per-boot random temporary WebUI password would require
   scraping container logs and racing qBittorrent's own startup
   timing, instead of a fixed, known credential the harness controls
   end to end.

The WebUI password format below
(`WebUI\\Password_PBKDF2=@ByteArray(<salt b64>:<hash b64>)`) is
PBKDF2-HMAC-SHA512, 100000 iterations, 64-byte digest -- reverse
engineered from a real qBittorrent container and verified against
4.6.7/5.0.0/5.1.4.
"""

from __future__ import annotations

import base64
import hashlib
import os

_PBKDF2_ITERATIONS = 100_000
_PBKDF2_SALT_BYTES = 16
_PBKDF2_DKLEN = 64


def build_webui_password_hash(password: str, salt: bytes | None = None) -> str:
    """Return the `WebUI\\Password_PBKDF2` value qBittorrent expects.

    `salt` is exposed only for deterministic unit testing; real callers
    must omit it so every harness run gets a fresh, unique salt.
    """
    resolved_salt = salt if salt is not None else os.urandom(_PBKDF2_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha512",
        password.encode("utf-8"),
        resolved_salt,
        _PBKDF2_ITERATIONS,
        dklen=_PBKDF2_DKLEN,
    )
    salt_b64 = base64.b64encode(resolved_salt).decode("ascii")
    hash_b64 = base64.b64encode(digest).decode("ascii")
    return f"@ByteArray({salt_b64}:{hash_b64})"


def build_hermetic_qbittorrent_conf(
    *,
    webui_port: int,
    webui_username: str,
    webui_password: str,
) -> str:
    """Return `qBittorrent.conf` text disabling every public-network
    *feature qBittorrent itself owns*.

    Disables, from the very first boot: DHT, PeX, LSD, UPnP, and the
    GeoIP-database auto-update. Pre-accepts the legal notice so no
    interactive prompt blocks the WebUI. Sets a fixed, known WebUI
    credential so the harness never needs to scrape a per-boot
    temporary password from container logs.

    This is application-level outbound isolation, not a network-level
    guarantee: the disposable Docker network the container runs on
    permits public egress, so a future qBittorrent release changing
    one of these defaults, or a malformed torrent, would not be caught
    by any test here. Do not describe the network itself as "hermetic"
    or "egress-blocked" -- only this configuration is.

    `WebUI\\LocalHostAuth=false` is set explicitly: without it, a
    future qBittorrent release could bypass authentication entirely
    for connections that appear to originate from localhost, and
    `auth_log_in()` would keep succeeding even if the sealed
    `Password_PBKDF2` above were silently ignored. Explicitly disabling
    the bypass makes `auth_log_in()` a real proof that the sealed
    password works.
    """
    password_hash = build_webui_password_hash(webui_password)
    return f"""[BitTorrent]
Session\\AnonymousModeEnabled=true
Session\\DHTEnabled=false
Session\\LSDEnabled=false
Session\\PeXEnabled=false
Session\\Port=6881
Session\\QueueingSystemEnabled=false
Session\\AddTorrentStopped=false

[LegalNotice]
Accepted=true

[Preferences]
Connection\\UPnP=false
Connection\\PortRangeMin=6881
Connection\\ResolvePeerCountries=false
WebUI\\Address=*
WebUI\\Port={webui_port}
WebUI\\Username={webui_username}
WebUI\\Password_PBKDF2="{password_hash}"
WebUI\\ServerDomains=*
WebUI\\HostHeaderValidation=true
WebUI\\CSRFProtection=true
WebUI\\LocalHostAuth=false
"""
