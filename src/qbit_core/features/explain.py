"""Build deterministic, evidence-based explanations for one torrent or tracker.

Every finding comes from a small, explicit rule catalogue evaluated
over already-classified data -- no generic rule engine, no confidence
scoring. When qbit-ops cannot classify something, it says so via
`ExplanationSeverity.UNKNOWN` or an explicit limitation rather than
guessing. Tracker-derived evidence is always the same secret-free
structural data `qbit_core.features.trackers` produces.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from qbit_core.features.torrents import (
    build_torrent_filter,
    get_safe_tracker_details,
)
from qbit_core.features.tracker_status import (
    TrackerAggregate,
    TrackerStatusReport,
    collect_tracker_status,
)
from qbit_core.features.trackers import (
    TrackerHealth,
    compute_tracker_aggregate_health,
)
from qbit_core.qbit.fields import (
    get_field_as_float,
    get_field_as_int,
    get_field_as_string,
)
from qbit_core.shared.selection import (
    ResolvedTorrent,
    TorrentNotFoundError,
    resolve_torrent_hash,
)
from qbit_core.shared.torrent_states import (
    TorrentStateGroup,
    classify_torrent_state,
    is_stopped_state,
)

SCHEMA_VERSION = "1"


class ExplanationSeverity(StrEnum):
    """Classify how urgently an explanation finding deserves attention."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


# Most severe first: `_overall_severity` picks the first value present
# among a report's findings, and `explanation_exit_code` relies on this
# same ordering being the one true precedence used everywhere.
_SEVERITY_PRECEDENCE = (
    ExplanationSeverity.CRITICAL,
    ExplanationSeverity.WARNING,
    ExplanationSeverity.UNKNOWN,
    ExplanationSeverity.INFO,
)


@dataclass(frozen=True)
class Evidence:
    """One concrete, machine-checkable fact backing a finding."""

    code: str
    label: str
    value: str | int | float | bool | None
    source: str


@dataclass(frozen=True)
class ExplanationFinding:
    """One deterministic conclusion, its evidence, and its known limits."""

    code: str
    severity: ExplanationSeverity
    title: str
    explanation: str
    evidence: tuple[Evidence, ...]
    limitations: tuple[str, ...] = ()
    next_commands: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExplanationReport:
    """The deterministic result of explaining one torrent or tracker."""

    schema_version: str
    generated_at: datetime
    target_type: str
    target_identity: str
    summary: str
    findings: tuple[ExplanationFinding, ...]
    overall_severity: ExplanationSeverity


_EXIT_CODE_BY_SEVERITY: dict[ExplanationSeverity, int] = {
    ExplanationSeverity.INFO: 0,
    ExplanationSeverity.WARNING: 1,
    ExplanationSeverity.UNKNOWN: 1,
    ExplanationSeverity.CRITICAL: 2,
}


def explanation_exit_code(severity: ExplanationSeverity) -> int:
    """Map a report's overall severity to its documented CLI exit code.

    `TARGET_UNAVAILABLE` (a torrent hash or tracker identity with no
    data at all) is deliberately not part of this mapping -- it is a
    distinct condition callers detect from a `None` report, not a
    severity value (see `explain_torrent`/`explain_tracker`).
    """
    return _EXIT_CODE_BY_SEVERITY[severity]


def _overall_severity(
    findings: tuple[ExplanationFinding, ...],
) -> ExplanationSeverity:
    """Compute a report's overall severity as the most severe finding's."""
    present = {finding.severity for finding in findings}
    for candidate in _SEVERITY_PRECEDENCE:
        if candidate in present:
            return candidate
    return ExplanationSeverity.INFO


# --- Torrent explanation -----------------------------------------------------

_MESSAGE_PRIORITY = (
    TrackerHealth.CRITICAL.value,
    TrackerHealth.WARNING.value,
    TrackerHealth.UNKNOWN.value,
    TrackerHealth.DISABLED.value,
    TrackerHealth.HEALTHY.value,
)


def explain_torrent(
    client: Any,
    torrent_hash: str,
) -> ExplanationReport | None:
    """Explain one torrent's current state using deterministic evidence.

    Bounded to `torrents_info()` once, then at most one
    `torrents_trackers()` call for the resolved torrent. Returns `None`
    when nothing matches; propagates `AmbiguousTorrentHashError`/
    `InvalidTorrentSelectorError` so the caller can present candidates
    instead of guessing. Resolves the hash, then delegates every
    rule-evaluation decision to `build_torrent_explanation` -- the pure
    builder is the single catalogue.
    """
    all_torrents = list(client.torrents_info())

    try:
        resolved = resolve_torrent_hash(all_torrents, torrent_hash)
    except TorrentNotFoundError:
        return None

    torrent = next(
        item
        for item in all_torrents
        if get_field_as_string(item, "hash").lower() == resolved.hash.lower()
    )

    try:
        raw_trackers = list(client.torrents_trackers(resolved.hash))
        safe_tracker_details: list[dict[str, Any]] | None = (
            get_safe_tracker_details(raw_trackers)
        )
    except Exception:
        safe_tracker_details = None

    return build_torrent_explanation(torrent, safe_tracker_details)


def build_torrent_explanation(
    torrent_item: Any,
    safe_tracker_details: list[dict[str, Any]] | None,
    *,
    generated_at: datetime | None = None,
) -> ExplanationReport:
    """Pure, deterministic torrent explanation builder -- zero API calls.

    The single rule catalogue behind `explain_torrent`: takes an
    already-fetched raw `torrents_info()` item and already-computed safe
    tracker details, evaluating the same findings for any caller
    (there is no second, TUI-only catalogue). `None` and `[]` are
    deliberately distinct: `None` means tracker data could not be
    obtained (an explicit limitation is attached); `[]` means it was
    obtained and the torrent has no tracker endpoints.
    """
    resolved = ResolvedTorrent(
        hash=get_field_as_string(torrent_item, "hash"),
        name=get_field_as_string(torrent_item, "name"),
    )
    state = get_field_as_string(torrent_item, "state")
    group = classify_torrent_state(state)
    progress = get_field_as_float(torrent_item, "progress")
    download_rate = get_field_as_int(torrent_item, "dlspeed")
    upload_rate = get_field_as_int(torrent_item, "upspeed")
    category = get_field_as_string(torrent_item, "category")

    finding = _build_torrent_finding(
        resolved=resolved,
        state=state,
        group=group,
        progress=progress,
        download_rate=download_rate,
        upload_rate=upload_rate,
        category=category,
        endpoints=(
            safe_tracker_details if safe_tracker_details is not None else []
        ),
        tracker_collection_failed=safe_tracker_details is None,
    )

    return ExplanationReport(
        schema_version=SCHEMA_VERSION,
        generated_at=generated_at or datetime.now(UTC),
        target_type="torrent",
        target_identity=resolved.hash,
        summary=finding.explanation,
        findings=(finding,),
        overall_severity=finding.severity,
    )


def _tally_endpoint_health(
    endpoints: list[dict[str, Any]],
) -> dict[TrackerHealth, int]:
    """Count a torrent's own endpoints by health, dispatching on `health`
    alone (never on `enabled`, which is `None` for unclassifiable
    statuses and would otherwise miscount an unknown endpoint as
    disabled)."""
    counts: dict[TrackerHealth, int] = {health: 0 for health in TrackerHealth}
    for endpoint in endpoints:
        counts[TrackerHealth(endpoint["health"])] += 1
    return counts


def _summarize_torrent_tracker_health(
    endpoints: list[dict[str, Any]],
    *,
    tracker_collection_failed: bool,
) -> tuple[TrackerHealth | None, str | None]:
    """Summarize a torrent's own tracker endpoints into one health value.

    Returns `(None, limitation)` when no usable observation exists at
    all (collection failed, or the torrent reported no trackers) --
    `explain torrent` must never guess a health value it cannot support.
    """
    if tracker_collection_failed:
        return None, "Tracker data could not be collected for this torrent."
    if not endpoints:
        return None, "No tracker endpoints were reported for this torrent."

    counts = _tally_endpoint_health(endpoints)
    health = compute_tracker_aggregate_health(
        healthy=counts[TrackerHealth.HEALTHY],
        warning=counts[TrackerHealth.WARNING],
        critical=counts[TrackerHealth.CRITICAL],
        disabled=counts[TrackerHealth.DISABLED],
        unknown=counts[TrackerHealth.UNKNOWN],
    )
    return health, None


def _pick_representative_message(
    endpoints: list[dict[str, Any]],
) -> str | None:
    """Pick one already-sanitized tracker message, most severe first.

    Mirrors `qbit_core.features.tracker_status`'s aggregate message-selection
    precedence (critical > warning > unknown > disabled > healthy) so an
    operator never sees a healthy endpoint's message while a failing one
    is available.
    """
    by_health: dict[str, str] = {}
    for endpoint in endpoints:
        message = endpoint.get("message")
        health = endpoint["health"]
        if message and health not in by_health:
            by_health[health] = message

    for health in _MESSAGE_PRIORITY:
        if health in by_health:
            return by_health[health]
    return None


def _tracker_status_command(identity: str) -> str:
    """Build a safe `trackers status --tracker <identity>` suggestion."""
    return f"qbit-ops trackers status --tracker {identity}"


def _build_torrent_finding(
    *,
    resolved: ResolvedTorrent,
    state: str,
    group: TorrentStateGroup,
    progress: float,
    download_rate: int,
    upload_rate: int,
    category: str,
    endpoints: list[dict[str, Any]],
    tracker_collection_failed: bool,
) -> ExplanationFinding:
    """Evaluate the small, explicit torrent-state rule catalogue.

    Exactly one finding is produced: `classify_torrent_state` groups are
    mutually exclusive by construction, so a torrent's state always
    triggers exactly one rule here.
    """
    common_evidence = (
        Evidence("name", "Name", resolved.name, "torrents_info"),
        Evidence("state", "State", state, "torrents_info"),
        Evidence("progress", "Progress", progress, "torrents_info"),
        Evidence(
            "download_rate", "Download rate", download_rate, "torrents_info"
        ),
        Evidence("upload_rate", "Upload rate", upload_rate, "torrents_info"),
        Evidence(
            "category",
            "Category",
            category or "(uncategorized)",
            "torrents_info",
        ),
    )
    inspect_command = f"qbit-ops torrents inspect --hash {resolved.hash}"
    reannounce_command = (
        f"qbit-ops torrents reannounce --hash {resolved.hash} --dry-run"
    )

    tracker_health, tracker_limitation = _summarize_torrent_tracker_health(
        endpoints, tracker_collection_failed=tracker_collection_failed
    )
    representative_message = _pick_representative_message(endpoints)
    problem_identities = sorted(
        {
            endpoint["tracker"]
            for endpoint in endpoints
            # `scheme is None` marks a pseudo-tracker or unparsable
            # identity, which `trackers status --tracker` can't look up.
            if endpoint["scheme"] is not None
            and endpoint["health"]
            in (TrackerHealth.CRITICAL.value, TrackerHealth.UNKNOWN.value)
        }
    )

    if group == "errored":
        evidence = common_evidence
        if representative_message:
            evidence += (
                Evidence(
                    "tracker_message",
                    "Tracker message",
                    representative_message,
                    "torrents_trackers",
                ),
            )
        limitations = [
            "qbit-ops does not determine the underlying cause beyond the "
            "reported state and any tracker message."
        ]
        if tracker_limitation:
            limitations.append(tracker_limitation)
        next_commands = [inspect_command, reannounce_command]
        next_commands.extend(
            _tracker_status_command(identity) for identity in problem_identities
        )
        return ExplanationFinding(
            code="TORRENT_ERROR_STATE",
            severity=ExplanationSeverity.CRITICAL,
            title="Torrent is in an error state",
            explanation=(
                f"qBittorrent reports this torrent in an error state "
                f"('{state}')."
            ),
            evidence=evidence,
            limitations=tuple(limitations),
            next_commands=tuple(next_commands),
        )

    if group == "checking":
        return ExplanationFinding(
            code="TORRENT_CHECKING",
            severity=ExplanationSeverity.INFO,
            title="Torrent data is being checked",
            explanation=(
                "qBittorrent is validating this torrent's data. Normal "
                "transfer activity is expected to be paused until "
                "checking completes."
            ),
            evidence=common_evidence,
            limitations=(
                "qbit-ops cannot estimate remaining checking time from "
                "current data.",
            ),
            next_commands=(inspect_command,),
        )

    if group == "stalled":
        evidence = common_evidence
        if tracker_health is not None:
            evidence += (
                Evidence(
                    "tracker_health",
                    "Trackers",
                    tracker_health.value,
                    "torrents_trackers",
                ),
            )
        limitations = []
        if tracker_limitation:
            limitations.append(tracker_limitation)
        next_commands = [inspect_command, reannounce_command]

        if state == "stalledUP":
            limitations.insert(
                0,
                "qBittorrent does not indicate whether a peer currently "
                "requests data from this torrent.",
            )
            return ExplanationFinding(
                code="TORRENT_STALLED_UP",
                severity=ExplanationSeverity.WARNING,
                title="Stalled upload",
                explanation=(
                    "The torrent is complete but currently has no active "
                    "upload transfer. This may simply mean no peer is "
                    "requesting data."
                ),
                evidence=evidence,
                limitations=tuple(limitations),
                next_commands=tuple(next_commands),
            )

        # stalledDL
        if tracker_health is TrackerHealth.CRITICAL:
            detail = (
                "All enabled tracker endpoints for this torrent are "
                "currently failing, which may explain why no peers are "
                "being discovered."
            )
        elif tracker_health is TrackerHealth.HEALTHY:
            detail = (
                "Tracker endpoints for this torrent are healthy; the "
                "stall is not attributable to tracker connectivity based "
                "on current evidence."
            )
        elif tracker_health in (TrackerHealth.WARNING, TrackerHealth.UNKNOWN):
            detail = (
                "Tracker endpoints for this torrent show mixed or "
                "unclassified health; a tracker connectivity issue "
                "cannot be ruled out."
            )
        else:
            detail = (
                "No usable tracker health information is available for "
                "this torrent."
            )
        limitations.insert(
            0,
            "qBittorrent does not directly report why no peers are "
            "currently exchanging data.",
        )
        next_commands.extend(
            _tracker_status_command(identity) for identity in problem_identities
        )
        return ExplanationFinding(
            code="TORRENT_STALLED_DL",
            severity=ExplanationSeverity.WARNING,
            title="Stalled download",
            explanation=(
                f"The torrent is incomplete and currently has no active "
                f"download transfer. {detail}"
            ),
            evidence=evidence,
            limitations=tuple(limitations),
            next_commands=tuple(next_commands),
        )

    if group in ("seeding", "downloading"):
        if is_stopped_state(state):
            return ExplanationFinding(
                code="TORRENT_STOPPED",
                severity=ExplanationSeverity.INFO,
                title="Torrent is stopped",
                explanation=(
                    "The torrent is stopped and not currently "
                    "transferring, consistent with its reported state."
                ),
                evidence=common_evidence,
                next_commands=(inspect_command,),
            )

        if group == "seeding":
            code, title, explanation = (
                "TORRENT_HEALTHY_SEEDING",
                "Healthy seeding",
                "The torrent is actively seeding with no observed issues.",
            )
        else:
            code, title, explanation = (
                "TORRENT_HEALTHY_DOWNLOADING",
                "Healthy downloading",
                (
                    "The torrent is actively downloading with no observed "
                    "issues."
                ),
            )
        return ExplanationFinding(
            code=code,
            severity=ExplanationSeverity.INFO,
            title=title,
            explanation=explanation,
            evidence=common_evidence,
            next_commands=(inspect_command,),
        )

    # group == "unknown"
    return ExplanationFinding(
        code="TORRENT_UNKNOWN_STATE",
        severity=ExplanationSeverity.UNKNOWN,
        title="Unrecognized torrent state",
        explanation=(
            f"qBittorrent reports state '{state}', which the current "
            "qbit-ops classifier does not recognize."
        ),
        evidence=common_evidence,
        limitations=(
            "qbit-ops cannot classify this state and makes no claim "
            "about the torrent's actual condition.",
        ),
        next_commands=(inspect_command, "qbit-ops doctor"),
    )


# --- Tracker explanation ------------------------------------------------------

_TRACKER_HEALTH_FINDING: dict[
    TrackerHealth, tuple[str, ExplanationSeverity, str, str]
] = {
    TrackerHealth.CRITICAL: (
        "TRACKER_ALL_ENDPOINTS_FAILING",
        ExplanationSeverity.CRITICAL,
        "All observed endpoints are failing",
        "All enabled observations for this tracker are failing.",
    ),
    TrackerHealth.WARNING: (
        "TRACKER_MIXED_ENDPOINT_HEALTH",
        ExplanationSeverity.WARNING,
        "Mixed endpoint health",
        "Observations are mixed: some endpoints work while others fail "
        "or remain unknown.",
    ),
    TrackerHealth.DISABLED: (
        "TRACKER_DISABLED_ONLY",
        ExplanationSeverity.INFO,
        "Only disabled endpoints observed",
        "No enabled endpoint was observed. Disabled endpoints do not "
        "independently indicate a connectivity failure.",
    ),
    TrackerHealth.UNKNOWN: (
        "TRACKER_UNKNOWN_STATES",
        ExplanationSeverity.UNKNOWN,
        "Unrecognized tracker states",
        "qBittorrent returned states that qbit-ops does not currently "
        "classify.",
    ),
    TrackerHealth.HEALTHY: (
        "TRACKER_HEALTHY",
        ExplanationSeverity.INFO,
        "All observed endpoints are healthy",
        "All enabled observations for this tracker are healthy.",
    ),
}


def explain_tracker(
    client: Any,
    tracker: str,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> ExplanationReport | None:
    """Explain one tracker's aggregate health using deterministic evidence.

    Reuses `qbit_core.features.tracker_status.collect_tracker_status`,
    restricted afterward to the requested identity -- never a second
    collection pass. Returns `None` when qBittorrent reported no
    observation for the requested identity, deliberately distinct from
    `trackers status --tracker`'s empty-selection-is-healthy convention.
    """
    filters = build_torrent_filter(trackers=(tracker,))
    report = collect_tracker_status(client, filters, on_progress=on_progress)

    if not report.trackers:
        return None

    aggregate = report.trackers[0]
    findings = [_build_tracker_finding(aggregate)]

    if report.collection_errors > 0:
        findings.append(_build_partial_collection_finding(report, aggregate))

    findings_tuple = tuple(findings)

    return ExplanationReport(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(UTC),
        target_type="tracker",
        target_identity=aggregate.identity,
        summary=findings_tuple[0].explanation,
        findings=findings_tuple,
        overall_severity=_overall_severity(findings_tuple),
    )


def _build_tracker_finding(aggregate: TrackerAggregate) -> ExplanationFinding:
    """Evaluate the small, explicit tracker-health rule catalogue."""
    code, severity, title, explanation = _TRACKER_HEALTH_FINDING[
        aggregate.health
    ]
    enabled_endpoints = aggregate.endpoint_count - aggregate.disabled_count

    evidence = [
        Evidence(
            "torrent_count",
            "Torrents",
            aggregate.torrent_count,
            "trackers_status",
        ),
        Evidence(
            "endpoint_count",
            "Endpoints",
            aggregate.endpoint_count,
            "trackers_status",
        ),
        Evidence(
            "enabled_endpoints",
            "Enabled endpoints",
            enabled_endpoints,
            "trackers_status",
        ),
        Evidence(
            "healthy_endpoints",
            "Healthy endpoints",
            aggregate.healthy_count,
            "trackers_status",
        ),
        Evidence(
            "warning_endpoints",
            "Warning endpoints",
            aggregate.warning_count,
            "trackers_status",
        ),
        Evidence(
            "critical_endpoints",
            "Critical endpoints",
            aggregate.critical_count,
            "trackers_status",
        ),
        Evidence(
            "disabled_endpoints",
            "Disabled endpoints",
            aggregate.disabled_count,
            "trackers_status",
        ),
        Evidence(
            "unknown_endpoints",
            "Unknown endpoints",
            aggregate.unknown_count,
            "trackers_status",
        ),
    ]
    if aggregate.representative_message:
        evidence.append(
            Evidence(
                "representative_message",
                "Representative message",
                aggregate.representative_message,
                "trackers_status",
            )
        )

    limitations: list[str] = []
    next_commands = [
        f"qbit-ops trackers inspect --tracker {aggregate.identity}"
    ]

    if aggregate.health in (TrackerHealth.CRITICAL, TrackerHealth.WARNING):
        limitations.append(
            "qbit-ops cannot determine whether a failing endpoint "
            "reflects a tracker-side outage, a network issue, or a "
            "torrent-specific rejection."
        )
        next_commands.append("qbit-ops doctor")
    elif aggregate.health is TrackerHealth.UNKNOWN:
        limitations.append(
            "qbit-ops does not recognize the raw status code(s) "
            "reported for this tracker and makes no health claim "
            "beyond 'unknown'."
        )

    return ExplanationFinding(
        code=code,
        severity=severity,
        title=title,
        explanation=explanation,
        evidence=tuple(evidence),
        limitations=tuple(limitations),
        next_commands=tuple(next_commands),
    )


def _build_partial_collection_finding(
    report: TrackerStatusReport,
    aggregate: TrackerAggregate,
) -> ExplanationFinding:
    """Build the secondary finding surfacing a partial collection failure."""
    return ExplanationFinding(
        code="TRACKER_PARTIAL_COLLECTION",
        severity=ExplanationSeverity.WARNING,
        title="Partial tracker collection failure",
        explanation=(
            f"Tracker data collection failed for {report.collection_errors} "
            "of the torrents scanned."
        ),
        evidence=(
            Evidence(
                "collection_errors",
                "Collection errors",
                report.collection_errors,
                "trackers_status",
            ),
            Evidence(
                "matched_torrents",
                "Matched torrents",
                report.matched_torrents,
                "trackers_status",
            ),
        ),
        limitations=(
            "Tracker collection failed for some torrents scanned in this "
            "report; those torrents may or may not use this tracker, so "
            "the endpoint counts above may be incomplete.",
        ),
        next_commands=(
            f"qbit-ops trackers inspect --tracker {aggregate.identity}",
        ),
    )


# --- Serialization ------------------------------------------------------------


def evidence_to_dict(evidence: Evidence) -> dict[str, Any]:
    """Convert one evidence item into a JSON-serializable structure."""
    return {
        "code": evidence.code,
        "label": evidence.label,
        "value": evidence.value,
        "source": evidence.source,
    }


def finding_to_dict(finding: ExplanationFinding) -> dict[str, Any]:
    """Convert one finding into a JSON-serializable structure."""
    return {
        "code": finding.code,
        "severity": finding.severity.value,
        "title": finding.title,
        "explanation": finding.explanation,
        "evidence": [evidence_to_dict(item) for item in finding.evidence],
        "limitations": list(finding.limitations),
        "next_commands": list(finding.next_commands),
    }


def explanation_report_to_dict(report: ExplanationReport) -> dict[str, Any]:
    """Convert an explanation report into a JSON-serializable structure."""
    return {
        "schema_version": report.schema_version,
        "generated_at": report.generated_at.isoformat(),
        "target_type": report.target_type,
        "target_identity": report.target_identity,
        "summary": report.summary,
        "overall_severity": report.overall_severity.value,
        "findings": [finding_to_dict(finding) for finding in report.findings],
    }
