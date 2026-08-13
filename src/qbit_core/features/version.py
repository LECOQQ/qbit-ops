"""Collect the qbit-ops, Python, and qBittorrent versions.

Kept free of Typer and Rich so the collection stays reusable and
testable without the CLI layer. `qbit_core` never imports `qbit_ops`,
so the qbit-ops version is injected by its caller.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from typing import Any

from qbit_core.qbit.protocols import QbitAppInfoReader


@dataclass(frozen=True)
class VersionReport:
    """Report the four versions, remote ones being `None` when unavailable.

    `qbit_ops` and `python` are local facts and are always present;
    `qbittorrent` and `web_api` are `None` when the instance could not
    be reached.
    """

    qbit_ops: str
    python: str
    qbittorrent: str | None
    web_api: str | None


def collect_version_report(
    *,
    qbit_ops_version: str,
    client: QbitAppInfoReader | None = None,
) -> VersionReport:
    """Collect the version report, at most one call per remote version.

    `client` is `None` when the instance is known to be unreachable, and
    the remote versions stay `None`. Remote versions are reported as the
    instance returns them, without normalization. Exceptions raised by
    the client are left to propagate: only the caller can tell an
    expected unavailability from an unexpected failure.
    """
    qbittorrent: str | None = None
    web_api: str | None = None
    if client is not None:
        qbittorrent = str(client.app_version())
        web_api = str(client.app_web_api_version())

    return VersionReport(
        qbit_ops=qbit_ops_version,
        python=platform.python_version(),
        qbittorrent=qbittorrent,
        web_api=web_api,
    )


def version_report_to_json_dict(report: VersionReport) -> dict[str, Any]:
    """Convert a version report into a JSON-serializable structure."""
    return {
        "qbit_ops": report.qbit_ops,
        "python": report.python,
        "qbittorrent": report.qbittorrent,
        "web_api": report.web_api,
    }
