"""Collect and represent a qbit-ops diagnostic report.

This is the `doctor` "service": it must remain callable without Typer and
without Rich, mirroring `app.status`'s collection/render split, so it can
be reused by a future TUI without pulling in CLI or presentation
concerns.

Collection performs a bounded, documented number of remote calls: at most
one authenticated login (`auth_log_in()`, performed by the caller before
`collect_doctor_report()` is invoked) plus up to four read calls
(`app_version`, `app_web_api_version`, `transfer_info`, `torrents_info`)
made here — the same bounded budget `app.status.collect_status_snapshot`
uses, never a per-torrent call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from app.config import QbitConfig
from app.qbit_fields import get_field_as_string
from app.torrent_states import classify_torrent_state

SCHEMA_VERSION = "1"

_SUPPORTED_MAJOR_VERSIONS = {4, 5}
_VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")
_URL_USERINFO_PATTERN = re.compile(r"://[^/@\s]+:[^/@\s]+@")


class CheckStatus(StrEnum):
    """Represent the outcome of one diagnostic check."""

    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"
    SKIPPED = "skipped"


class ConnectionOutcome(StrEnum):
    """Classify the result of one client-creation/login attempt.

    Kept independent from `app.main`'s `QbitConnectionError`/
    `QbitAuthenticationError` so this module never imports `app.main`
    (which imports this module) — the caller classifies its own
    exceptions into this enum before calling `collect_doctor_report()`.
    """

    OK = "ok"
    CONNECTION_FAILED = "connection_failed"
    AUTHENTICATION_FAILED = "authentication_failed"


@dataclass(frozen=True)
class DoctorCheck:
    """Describe the outcome of one diagnostic check."""

    code: str
    section: str
    status: CheckStatus
    message: str
    detail: str | None = None
    remediation: str | None = None


@dataclass(frozen=True)
class DoctorReport:
    """Represent one complete diagnostic report."""

    schema_version: str
    generated_at: datetime
    checks: tuple[DoctorCheck, ...]
    overall_status: CheckStatus


_EXIT_CODE_BY_OVERALL_STATUS: dict[CheckStatus, int] = {
    CheckStatus.PASS: 0,
    CheckStatus.WARNING: 1,
    CheckStatus.FAIL: 2,
}


def doctor_exit_code(overall_status: CheckStatus) -> int:
    """Map a report's overall status to its documented CLI exit code."""
    return _EXIT_CODE_BY_OVERALL_STATUS[overall_status]


def collect_doctor_report(
    *,
    config: QbitConfig | None,
    config_error: Exception | None,
    connection_outcome: ConnectionOutcome | None,
    connection_error: Exception | None,
    client: Any | None,
) -> DoctorReport:
    """Build a full diagnostic report from already-attempted collection.

    The caller is responsible for loading configuration and creating/
    authenticating a qBittorrent client (both may fail); this function
    never performs those steps itself, only the bounded read calls that
    follow a successful login. Independent check failures never prevent
    unrelated checks from running: a failed prerequisite produces
    `SKIPPED` checks downstream instead of raising.
    """
    checks: list[DoctorCheck] = []

    checks.extend(_configuration_checks(config, config_error))
    config_ok = config is not None

    checks.extend(
        _connectivity_checks(
            config=config,
            config_ok=config_ok,
            connection_outcome=connection_outcome,
            connection_error=connection_error,
        )
    )
    client_ok = (
        client is not None and connection_outcome == ConnectionOutcome.OK
    )

    version_check, qbit_version = _version_check(
        client=client, client_ok=client_ok, config=config
    )
    checks.append(version_check)
    checks.append(
        _web_api_version_check(
            client=client, client_ok=client_ok, config=config
        )
    )

    parsed_version, compat_parsable_check = _version_parsable_check(
        qbit_version
    )
    checks.append(compat_parsable_check)
    checks.append(_version_supported_check(parsed_version))

    torrents, runtime_listing_check = _torrents_listing_check(
        client=client, client_ok=client_ok, config=config
    )
    checks.append(runtime_listing_check)
    checks.append(
        _transfer_info_check(client=client, client_ok=client_ok, config=config)
    )
    checks.append(_torrent_states_check(torrents))

    return DoctorReport(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(UTC),
        checks=tuple(checks),
        overall_status=_compute_overall_status(checks),
    )


def doctor_report_to_json_dict(report: DoctorReport) -> dict[str, Any]:
    """Convert a doctor report into a JSON-serializable structure."""
    return {
        "schema_version": report.schema_version,
        "generated_at": report.generated_at.isoformat(),
        "overall_status": report.overall_status.value,
        "checks": [_check_to_dict(check) for check in report.checks],
    }


def doctor_report_to_csv_rows(
    report: DoctorReport,
) -> list[tuple[str, str, str, str, str, str]]:
    """Convert a doctor report into stable
    `section,code,status,message,detail,remediation` rows."""
    return [
        (
            check.section,
            check.code,
            check.status.value,
            check.message,
            check.detail or "",
            check.remediation or "",
        )
        for check in report.checks
    ]


def _check_to_dict(check: DoctorCheck) -> dict[str, Any]:
    """Convert one check into a JSON-serializable structure."""
    return {
        "code": check.code,
        "section": check.section,
        "status": check.status.value,
        "message": check.message,
        "detail": check.detail,
        "remediation": check.remediation,
    }


def _compute_overall_status(checks: list[DoctorCheck]) -> CheckStatus:
    """Compute overall status, keeping the most severe condition.

    Skipped checks never independently influence severity: a check is
    only skipped because an earlier prerequisite already failed, and
    that failure already accounts for the severity.
    """
    if any(check.status == CheckStatus.FAIL for check in checks):
        return CheckStatus.FAIL
    if any(check.status == CheckStatus.WARNING for check in checks):
        return CheckStatus.WARNING
    return CheckStatus.PASS


def _configuration_checks(
    config: QbitConfig | None,
    config_error: Exception | None,
) -> list[DoctorCheck]:
    """Build the local configuration checks (no remote calls)."""
    checks: list[DoctorCheck] = []

    if config is None:
        checks.append(
            DoctorCheck(
                code="CFG001",
                section="configuration",
                status=CheckStatus.FAIL,
                message="Configuration failed to load.",
                detail=str(config_error) if config_error else None,
                remediation=(
                    "Create a .env file from .env.example, or set "
                    "QBIT_HOST/QBIT_USER/QBIT_PASSWORD in the environment."
                ),
            )
        )
        checks.append(
            DoctorCheck(
                code="CFG002",
                section="configuration",
                status=CheckStatus.SKIPPED,
                message="Host URL validity not checked: configuration "
                "did not load.",
            )
        )
        checks.append(
            DoctorCheck(
                code="CFG003",
                section="configuration",
                status=CheckStatus.SKIPPED,
                message="Embedded-credential check skipped: configuration "
                "did not load.",
            )
        )
        return checks

    checks.append(
        DoctorCheck(
            code="CFG001",
            section="configuration",
            status=CheckStatus.PASS,
            message="Configuration loaded successfully.",
        )
    )

    parsed_host = urlsplit(config.host)
    if parsed_host.scheme in ("http", "https") and parsed_host.netloc:
        checks.append(
            DoctorCheck(
                code="CFG002",
                section="configuration",
                status=CheckStatus.PASS,
                message="QBIT_HOST is a well-formed http/https URL.",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                code="CFG002",
                section="configuration",
                status=CheckStatus.FAIL,
                message="QBIT_HOST is not a valid http/https URL.",
                remediation=(
                    "Set QBIT_HOST to a full URL, e.g. http://localhost:8080."
                ),
            )
        )

    if "@" in parsed_host.netloc:
        checks.append(
            DoctorCheck(
                code="CFG003",
                section="configuration",
                status=CheckStatus.WARNING,
                message=(
                    "QBIT_HOST embeds userinfo credentials, which qbit-ops "
                    "ignores in favor of QBIT_USER/QBIT_PASSWORD."
                ),
                remediation=(
                    "Remove the embedded user:pass@ segment from QBIT_HOST."
                ),
            )
        )
    else:
        checks.append(
            DoctorCheck(
                code="CFG003",
                section="configuration",
                status=CheckStatus.PASS,
                message="No embedded credentials in QBIT_HOST.",
            )
        )

    return checks


def _connectivity_checks(
    *,
    config: QbitConfig | None,
    config_ok: bool,
    connection_outcome: ConnectionOutcome | None,
    connection_error: Exception | None,
) -> list[DoctorCheck]:
    """Build the connectivity/authentication checks."""
    if not config_ok:
        return [
            DoctorCheck(
                code="CONN001",
                section="connectivity",
                status=CheckStatus.SKIPPED,
                message="Reachability not checked: configuration invalid.",
            ),
            DoctorCheck(
                code="CONN002",
                section="connectivity",
                status=CheckStatus.SKIPPED,
                message="Authentication not checked: configuration invalid.",
            ),
        ]

    if connection_outcome == ConnectionOutcome.OK:
        return [
            DoctorCheck(
                code="CONN001",
                section="connectivity",
                status=CheckStatus.PASS,
                message="qBittorrent host is reachable.",
            ),
            DoctorCheck(
                code="CONN002",
                section="connectivity",
                status=CheckStatus.PASS,
                message="Authentication succeeded.",
            ),
        ]

    if connection_outcome == ConnectionOutcome.AUTHENTICATION_FAILED:
        return [
            DoctorCheck(
                code="CONN001",
                section="connectivity",
                status=CheckStatus.PASS,
                message=(
                    "qBittorrent host is reachable "
                    "(the login attempt received a response)."
                ),
            ),
            DoctorCheck(
                code="CONN002",
                section="connectivity",
                status=CheckStatus.FAIL,
                message="Authentication failed.",
                detail=_redact(connection_error, config),
                remediation="Verify QBIT_USER and QBIT_PASSWORD.",
            ),
        ]

    # ConnectionOutcome.CONNECTION_FAILED, or no outcome recorded at all.
    return [
        DoctorCheck(
            code="CONN001",
            section="connectivity",
            status=CheckStatus.FAIL,
            message=(
                "Could not establish a connection or complete a login "
                "attempt with qBittorrent."
            ),
            detail=_redact(connection_error, config),
            remediation=(
                "Verify QBIT_HOST is correct and qBittorrent's Web UI is "
                "reachable from this host."
            ),
        ),
        DoctorCheck(
            code="CONN002",
            section="connectivity",
            status=CheckStatus.SKIPPED,
            message="Authentication not checked: connection failed.",
        ),
    ]


def _version_check(
    *,
    client: Any | None,
    client_ok: bool,
    config: QbitConfig | None,
) -> tuple[DoctorCheck, str | None]:
    """Build the qBittorrent application-version check."""
    if not client_ok or client is None:
        return (
            DoctorCheck(
                code="CONN003",
                section="connectivity",
                status=CheckStatus.SKIPPED,
                message="Application version not checked: not connected.",
            ),
            None,
        )

    try:
        version = str(client.app_version())
    except Exception as error:
        return (
            DoctorCheck(
                code="CONN003",
                section="connectivity",
                status=CheckStatus.FAIL,
                message="Failed to read the qBittorrent application version.",
                detail=_redact(error, config),
            ),
            None,
        )

    return (
        DoctorCheck(
            code="CONN003",
            section="connectivity",
            status=CheckStatus.PASS,
            message=f"qBittorrent application version: {version}.",
        ),
        version,
    )


def _web_api_version_check(
    *,
    client: Any | None,
    client_ok: bool,
    config: QbitConfig | None,
) -> DoctorCheck:
    """Build the qBittorrent Web API-version check."""
    if not client_ok or client is None:
        return DoctorCheck(
            code="CONN004",
            section="connectivity",
            status=CheckStatus.SKIPPED,
            message="Web API version not checked: not connected.",
        )

    try:
        version = str(client.app_web_api_version())
    except Exception as error:
        return DoctorCheck(
            code="CONN004",
            section="connectivity",
            status=CheckStatus.FAIL,
            message="Failed to read the qBittorrent Web API version.",
            detail=_redact(error, config),
        )

    return DoctorCheck(
        code="CONN004",
        section="connectivity",
        status=CheckStatus.PASS,
        message=f"Web API version: {version}.",
    )


def _version_parsable_check(
    qbit_version: str | None,
) -> tuple[tuple[int, int, int] | None, DoctorCheck]:
    """Build the version-parsability compatibility check."""
    if qbit_version is None:
        return (
            None,
            DoctorCheck(
                code="COMPAT001",
                section="compatibility",
                status=CheckStatus.SKIPPED,
                message="Version format not checked: version unavailable.",
            ),
        )

    match = _VERSION_PATTERN.match(qbit_version)
    if match is None:
        return (
            None,
            DoctorCheck(
                code="COMPAT001",
                section="compatibility",
                status=CheckStatus.WARNING,
                message=(
                    f"qBittorrent version '{qbit_version}' has an "
                    "unrecognized format."
                ),
            ),
        )

    parsed = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return (
        parsed,
        DoctorCheck(
            code="COMPAT001",
            section="compatibility",
            status=CheckStatus.PASS,
            message="qBittorrent version string is parsable.",
        ),
    )


def _version_supported_check(
    parsed_version: tuple[int, int, int] | None,
) -> DoctorCheck:
    """Build the supported-version-range compatibility check.

    Unknown or unparsable versions produce a warning, never a failure:
    qbit-ops does not invent a compatibility guarantee it cannot verify.
    """
    if parsed_version is None:
        return DoctorCheck(
            code="COMPAT002",
            section="compatibility",
            status=CheckStatus.SKIPPED,
            message="Supported-version check skipped: version unparsable "
            "or unavailable.",
        )

    major = parsed_version[0]
    if major in _SUPPORTED_MAJOR_VERSIONS:
        return DoctorCheck(
            code="COMPAT002",
            section="compatibility",
            status=CheckStatus.PASS,
            message=f"qBittorrent major version {major} is supported.",
        )

    return DoctorCheck(
        code="COMPAT002",
        section="compatibility",
        status=CheckStatus.WARNING,
        message=(
            f"qBittorrent major version {major} has not been validated "
            "against qbit-ops."
        ),
        remediation=(
            "qbit-ops has been validated against qBittorrent 4.x and 5.x; "
            "some behaviors may differ on this version."
        ),
    )


def _torrents_listing_check(
    *,
    client: Any | None,
    client_ok: bool,
    config: QbitConfig | None,
) -> tuple[list[Any] | None, DoctorCheck]:
    """Build the torrent-listing runtime check."""
    if not client_ok or client is None:
        return (
            None,
            DoctorCheck(
                code="RUNTIME001",
                section="runtime",
                status=CheckStatus.SKIPPED,
                message="Torrent listing not checked: not connected.",
            ),
        )

    try:
        torrents = list(client.torrents_info())
    except Exception as error:
        return (
            None,
            DoctorCheck(
                code="RUNTIME001",
                section="runtime",
                status=CheckStatus.FAIL,
                message="Failed to list torrents.",
                detail=_redact(error, config),
            ),
        )

    return (
        torrents,
        DoctorCheck(
            code="RUNTIME001",
            section="runtime",
            status=CheckStatus.PASS,
            message=f"{len(torrents)} torrent(s) listed.",
        ),
    )


def _transfer_info_check(
    *,
    client: Any | None,
    client_ok: bool,
    config: QbitConfig | None,
) -> DoctorCheck:
    """Build the transfer-info runtime check."""
    if not client_ok or client is None:
        return DoctorCheck(
            code="RUNTIME002",
            section="runtime",
            status=CheckStatus.SKIPPED,
            message="Transfer info not checked: not connected.",
        )

    try:
        client.transfer_info()
    except Exception as error:
        return DoctorCheck(
            code="RUNTIME002",
            section="runtime",
            status=CheckStatus.FAIL,
            message="Failed to read global transfer information.",
            detail=_redact(error, config),
        )

    return DoctorCheck(
        code="RUNTIME002",
        section="runtime",
        status=CheckStatus.PASS,
        message="Global transfer information is available.",
    )


def _torrent_states_check(torrents: list[Any] | None) -> DoctorCheck:
    """Build the torrent-state-vocabulary runtime check.

    Reuses `app.torrent_states.classify_torrent_state` so the notion of
    an "unrecognized" torrent state can never diverge from `status`,
    which classifies its own torrents through the same function.
    """
    if torrents is None:
        return DoctorCheck(
            code="RUNTIME003",
            section="runtime",
            status=CheckStatus.SKIPPED,
            message="Torrent state vocabulary not checked: torrent "
            "listing unavailable.",
        )

    unknown_states: set[str] = set()
    for torrent in torrents:
        state = get_field_as_string(torrent, "state")
        if classify_torrent_state(state) == "unknown" and state != "":
            unknown_states.add(state)

    if unknown_states:
        state_list = ", ".join(sorted(unknown_states))
        return DoctorCheck(
            code="RUNTIME003",
            section="runtime",
            status=CheckStatus.WARNING,
            message=(
                f"Torrent state(s) not recognized by qbit-ops: {state_list}."
            ),
        )

    return DoctorCheck(
        code="RUNTIME003",
        section="runtime",
        status=CheckStatus.PASS,
        message="All torrent states are recognized.",
    )


def _redact(error: Exception | None, config: QbitConfig | None) -> str | None:
    """Strip known secrets and embedded URL credentials from error text.

    The single funnel every check must use before putting exception text
    into a `detail` field: `_create_qbit_client()`'s error messages may
    embed the configured host (which can itself carry userinfo
    credentials), and underlying `qbittorrentapi` exceptions can contain
    arbitrary text. Never expose passwords, cookies, or authorization
    headers.
    """
    if error is None:
        return None

    text = _URL_USERINFO_PATTERN.sub("://", str(error))
    if config is not None and config.password:
        text = text.replace(config.password, "***")
    return text
