"""Build a pre-seeded `qBittorrent.conf` for the disposable demo container.

Writing this file before first boot avoids a per-boot random WebUI
password and disables DHT/PeX/LSD/UPnP/NAT-PMP from the very first
frame, instead of racing container startup with a post-boot API call.
"""

from __future__ import annotations

import base64
import hashlib

_PBKDF2_ITERATIONS = 100_000
_PBKDF2_DKLEN = 64
# Fixed salt: this demo's password is public ("demo-only") by design,
# so a fixed salt keeps `qBittorrent.conf` byte-identical across runs
# without weakening anything real.
_FIXED_SALT = b"qbit-ops-demo-fixed-salt"


def _webui_password_hash(password: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha512",
        password.encode("utf-8"),
        _FIXED_SALT,
        _PBKDF2_ITERATIONS,
        dklen=_PBKDF2_DKLEN,
    )
    salt_b64 = base64.b64encode(_FIXED_SALT).decode("ascii")
    hash_b64 = base64.b64encode(digest).decode("ascii")
    return f"@ByteArray({salt_b64}:{hash_b64})"


def build_conf(*, webui_port: int, username: str, password: str) -> str:
    """Return `qBittorrent.conf` text: peer discovery off, fixed WebUI auth.

    `Connection\\UPnP=false` covers both UPnP and NAT-PMP port mapping.
    This disables application-level outbound peer discovery only -- the
    Docker network itself still permits egress, it is not blocked.
    """
    password_hash = _webui_password_hash(password)
    return f"""[BitTorrent]
Session\\AnonymousModeEnabled=true
Session\\DHTEnabled=false
Session\\LSDEnabled=false
Session\\PeXEnabled=false
Session\\Port=6881
Session\\QueueingSystemEnabled=false
Session\\AddTorrentStopped=false
Session\\DefaultSavePath=/downloads

[LegalNotice]
Accepted=true

[Preferences]
Connection\\UPnP=false
Connection\\PortRangeMin=6881
Connection\\ResolvePeerCountries=false
Downloads\\SavePath=/downloads
WebUI\\Address=*
WebUI\\Port={webui_port}
WebUI\\Username={username}
WebUI\\Password_PBKDF2="{password_hash}"
WebUI\\ServerDomains=*
WebUI\\HostHeaderValidation=true
WebUI\\CSRFProtection=true
WebUI\\LocalHostAuth=false
"""
