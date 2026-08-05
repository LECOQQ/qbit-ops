"""Check whether a newer stable qBittorrent release exists than the newest
entry in the packaged compatibility manifest
(`qbit_core.qbit.compatibility`, `qbit_core/data/qbittorrent-matrix.toml`).

This is a **detector, not an updater**: it never edits the matrix, never
marks a new release as supported, and never runs as part of ordinary
offline `make check` (no network access there). It is meant to be run
manually or as an extra step in the scheduled
`.github/workflows/qbittorrent-matrix.yml` workflow.

Exit codes:
    0  fresh      -- the matrix's newest entry is the latest known stable
                     release, or newer (should not happen, but is not an
                     error either).
    1  stale      -- a newer stable release was found. This means "matrix
                     review required", not "qbit-ops is incompatible" --
                     nothing about qbit-ops's behavior has been disproven.
    2  unknown     -- the authoritative source could not be reached or
                     parsed. Distinguished from `stale` on purpose: a
                     network failure must never be reported as staleness.

Uses the public, unauthenticated GitHub releases API for
`qbittorrent/qBittorrent` -- no token or repository secret required.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from packaging.version import InvalidVersion, Version

from qbit_core.qbit.compatibility import (
    CompatibilityManifestError,
    load_compatibility_evidence,
)

RELEASES_API_URL = (
    "https://api.github.com/repos/qbittorrent/qBittorrent/releases/latest"
)
REQUEST_TIMEOUT_SECONDS = 10


class FreshnessCheckError(RuntimeError):
    """Report that the authoritative source could not be reached or parsed."""


def fetch_latest_stable_version(url: str = RELEASES_API_URL) -> str:
    """Return the latest non-prerelease qBittorrent version, e.g. "5.2.3".

    Raises `FreshnessCheckError` on any network or parsing failure --
    callers must treat that as "unknown", never as "no newer release".
    """
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "qbit-ops-freshness-check",
        },
    )
    try:
        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise FreshnessCheckError(f"could not reach {url}: {error}") from error
    except json.JSONDecodeError as error:
        raise FreshnessCheckError(
            f"could not parse response from {url}: {error}"
        ) from error

    tag_name = payload.get("tag_name")
    if not tag_name:
        raise FreshnessCheckError(
            f"no 'tag_name' in response from {url}: {payload!r}"
        )

    # qbittorrent/qBittorrent tags releases as "release-X.Y.Z".
    version_text = tag_name.removeprefix("release-")
    try:
        Version(version_text)
    except InvalidVersion as error:
        raise FreshnessCheckError(
            f"tag_name {tag_name!r} does not parse as a version: {error}"
        ) from error

    return version_text


def newest_matrix_version() -> str:
    """Return the highest `expected_version` across every matrix entry."""
    try:
        evidence = load_compatibility_evidence()
    except CompatibilityManifestError as error:
        raise FreshnessCheckError(
            f"could not read the packaged compatibility manifest: {error}"
        ) from error

    return evidence.latest().expected_version.removeprefix("v")


def main() -> int:
    try:
        matrix_version = newest_matrix_version()
    except FreshnessCheckError as error:
        print(f"UNKNOWN: could not read the matrix manifest: {error}")
        return 2

    try:
        latest_stable = fetch_latest_stable_version()
    except FreshnessCheckError as error:
        print(
            f"UNKNOWN: could not determine the latest stable release: {error}"
        )
        print("This is a network/source failure, not evidence of staleness.")
        return 2

    if Version(latest_stable) > Version(matrix_version):
        print(
            f"STALE: qBittorrent {latest_stable} is available; the newest "
            f"matrix entry is {matrix_version}. MATRIX REVIEW REQUIRED -- "
            "this does not mean qbit-ops is incompatible with "
            f"{latest_stable}, only that it has not yet been "
            "container-integration tested against it. The packaged "
            "compatibility manifest (qbit_core/data/qbittorrent-matrix.toml) "
            "was not modified by this check."
        )
        return 1

    print(
        f"FRESH: matrix newest entry ({matrix_version}) is the latest "
        "stable release."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
