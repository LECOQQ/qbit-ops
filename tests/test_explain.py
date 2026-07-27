"""Test the `explain` domain model: torrent and tracker explanations."""

import pytest

from qbit_ops.explain import (
    ExplanationSeverity,
    build_torrent_explanation,
    evidence_to_dict,
    explain_torrent,
    explain_tracker,
    explanation_exit_code,
    explanation_report_to_dict,
    finding_to_dict,
)
from qbit_ops.selectors import AmbiguousTorrentHashError
from qbit_ops.torrents import get_safe_tracker_details
from tests.support import FakeQbitClient, make_torrent

HASH_A = "a" * 40
HASH_B = "b" * 40
SECRET_PASSKEY = "SUPER_SECRET_PASSKEY_918273"
PASSKEY_URL = f"https://tracker.example/announce/{SECRET_PASSKEY}"


# --- Torrent explanation: severity/exit code mapping -------------------------


def test_explanation_exit_code_mapping() -> None:
    """Ensure the documented severity -> exit code mapping holds."""
    assert explanation_exit_code(ExplanationSeverity.INFO) == 0
    assert explanation_exit_code(ExplanationSeverity.WARNING) == 1
    assert explanation_exit_code(ExplanationSeverity.UNKNOWN) == 1
    assert explanation_exit_code(ExplanationSeverity.CRITICAL) == 2


# --- Torrent explanation: state rules -----------------------------------------


def test_stalled_upload_explanation() -> None:
    """Ensure a complete, stalled-upload torrent gets a grounded warning."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(
                hash=HASH_A,
                name="T1",
                state="stalledUP",
                progress=1.0,
                upspeed=0,
            )
        ],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 2}]
        },
    )

    report = explain_torrent(client, HASH_A)

    assert report is not None
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.code == "TORRENT_STALLED_UP"
    assert finding.severity is ExplanationSeverity.WARNING
    assert report.overall_severity is ExplanationSeverity.WARNING
    assert explanation_exit_code(report.overall_severity) == 1
    assert "reannounce" in " ".join(finding.next_commands)
    assert not any(
        "network" in limitation.lower() or "failure" in limitation.lower()
        for limitation in finding.limitations
    )


def test_stalled_download_with_healthy_trackers() -> None:
    """Ensure a stalled download with healthy trackers is not blamed on them."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(
                hash=HASH_A,
                name="T1",
                state="stalledDL",
                progress=0.5,
                dlspeed=0,
            )
        ],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 2}]
        },
    )

    report = explain_torrent(client, HASH_A)

    assert report is not None
    finding = report.findings[0]
    assert finding.code == "TORRENT_STALLED_DL"
    assert "not attributable to tracker connectivity" in finding.explanation


def test_stalled_download_with_failing_trackers() -> None:
    """Ensure a stalled download with all-failing trackers surfaces that."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(
                hash=HASH_A, name="T1", state="stalledDL", progress=0.5
            )
        ],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 4}]
        },
    )

    report = explain_torrent(client, HASH_A)

    assert report is not None
    finding = report.findings[0]
    assert finding.code == "TORRENT_STALLED_DL"
    assert "currently failing" in finding.explanation
    assert any(
        "trackers status --tracker tracker.example" in command
        for command in finding.next_commands
    )


def test_stalled_download_with_failing_pseudo_tracker_suggests_nothing() -> (
    None
):
    """Ensure a DHT/PeX/LSD pseudo-tracker never appears in a suggested
    `trackers status --tracker` command, since that command cannot look
    it up (`trackers status` excludes pseudo-trackers entirely)."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1", state="stalledDL")],
        trackers_by_hash={HASH_A: [{"url": "** [DHT] **", "status": 4}]},
    )

    report = explain_torrent(client, HASH_A)

    assert report is not None
    finding = report.findings[0]
    for command in finding.next_commands:
        assert "trackers status --tracker" not in command


def test_error_state_explanation() -> None:
    """Ensure an errored torrent produces a critical, evidence-backed
    finding."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1", state="error")],
        trackers_by_hash={
            HASH_A: [
                {
                    "url": "https://tracker.example/announce",
                    "status": 4,
                    "msg": "unregistered torrent",
                }
            ]
        },
    )

    report = explain_torrent(client, HASH_A)

    assert report is not None
    finding = report.findings[0]
    assert finding.code == "TORRENT_ERROR_STATE"
    assert finding.severity is ExplanationSeverity.CRITICAL
    assert report.overall_severity is ExplanationSeverity.CRITICAL
    assert explanation_exit_code(report.overall_severity) == 2
    messages = [item.value for item in finding.evidence]
    assert "unregistered torrent" in messages


def test_checking_state_explanation() -> None:
    """Ensure a checking torrent gets an informational, non-alarming finding."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1", state="checkingDL")],
        trackers_by_hash={HASH_A: []},
    )

    report = explain_torrent(client, HASH_A)

    assert report is not None
    finding = report.findings[0]
    assert finding.code == "TORRENT_CHECKING"
    assert finding.severity is ExplanationSeverity.INFO
    assert explanation_exit_code(report.overall_severity) == 0


def test_healthy_seeding_explanation() -> None:
    """Ensure an actively seeding torrent gets a plain informational finding."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1", state="uploading")],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 2}]
        },
    )

    report = explain_torrent(client, HASH_A)

    assert report is not None
    finding = report.findings[0]
    assert finding.code == "TORRENT_HEALTHY_SEEDING"
    assert finding.severity is ExplanationSeverity.INFO
    assert finding.limitations == ()


def test_healthy_downloading_explanation() -> None:
    """Ensure an actively downloading torrent gets a plain informational
    finding."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1", state="downloading")],
        trackers_by_hash={HASH_A: []},
    )

    report = explain_torrent(client, HASH_A)

    assert report is not None
    finding = report.findings[0]
    assert finding.code == "TORRENT_HEALTHY_DOWNLOADING"
    assert finding.severity is ExplanationSeverity.INFO


def test_stopped_torrent_is_not_reported_as_healthy_seeding() -> None:
    """Ensure a paused/stopped torrent is not mislabeled as actively seeding."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1", state="stoppedUP")],
        trackers_by_hash={HASH_A: []},
    )

    report = explain_torrent(client, HASH_A)

    assert report is not None
    finding = report.findings[0]
    assert finding.code == "TORRENT_STOPPED"
    assert finding.severity is ExplanationSeverity.INFO


def test_unknown_torrent_state_explanation() -> None:
    """Ensure an unrecognized state produces an honest UNKNOWN finding."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="T1", state="totallyNewState")
        ],
        trackers_by_hash={HASH_A: []},
    )

    report = explain_torrent(client, HASH_A)

    assert report is not None
    finding = report.findings[0]
    assert finding.code == "TORRENT_UNKNOWN_STATE"
    assert finding.severity is ExplanationSeverity.UNKNOWN
    assert explanation_exit_code(report.overall_severity) == 1
    assert "qbit-ops doctor" in finding.next_commands


def test_torrent_download_and_upload_rates_reach_evidence() -> None:
    """Ensure the actual dlspeed/upspeed fields are read, not a typo'd name."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(
                hash=HASH_A,
                name="T1",
                state="downloading",
                dlspeed=6789,
                upspeed=12345,
            )
        ],
        trackers_by_hash={HASH_A: []},
    )

    report = explain_torrent(client, HASH_A)

    assert report is not None
    evidence_by_code = {
        item.code: item.value for item in report.findings[0].evidence
    }
    assert evidence_by_code["download_rate"] == 6789
    assert evidence_by_code["upload_rate"] == 12345


def test_torrent_not_found_returns_none() -> None:
    """Ensure an unresolved hash returns None, mirroring `inspect_torrent`."""
    client = FakeQbitClient(torrents=[make_torrent(hash=HASH_A, name="T1")])

    assert explain_torrent(client, "deadbeef") is None


def test_ambiguous_hash_propagates() -> None:
    """Ensure an ambiguous prefix raises instead of guessing."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash="abc123def456", name="T1"),
            make_torrent(hash="abc987fed654", name="T2"),
        ]
    )

    with pytest.raises(AmbiguousTorrentHashError):
        explain_torrent(client, "abc")


def test_tracker_collection_failure_is_a_limitation_not_a_crash() -> None:
    """Ensure a torrents_trackers() failure degrades gracefully."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1", state="stalledDL")],
        tracker_error_hashes={HASH_A},
    )

    report = explain_torrent(client, HASH_A)

    assert report is not None
    finding = report.findings[0]
    assert any(
        "could not be collected" in limitation
        for limitation in finding.limitations
    )


# --- Torrent explanation: API-call budget -------------------------------------


def test_torrent_explanation_uses_at_most_one_tracker_lookup() -> None:
    """Ensure only the resolved torrent's trackers are ever scanned."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="T1"),
            make_torrent(hash=HASH_B, name="T2"),
        ],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 2}],
            HASH_B: [{"url": "https://other.example/announce", "status": 2}],
        },
    )

    explain_torrent(client, HASH_A)

    assert client.torrents_info_calls == 1
    assert client.torrents_trackers_calls == 1
    assert client.calls[-1] == ("torrents_trackers", (HASH_A,), {})


# --- Pure builder: build_torrent_explanation ----------------------------------
#
# `explain_torrent` (client-calling, ambiguity-aware) is now a thin wrapper
# around `build_torrent_explanation` (pure, zero-API) -- the TUI's `e`
# binding calls the builder directly with already-fetched data (see
# `qbit_ops.tui.state.TuiController.build_explanation`), so both interfaces must
# produce identical findings for identical evidence, with no second,
# TUI-only rule catalogue.


def test_pure_builder_matches_explain_torrent_for_the_same_torrent() -> None:
    torrent = make_torrent(hash=HASH_A, name="T1", state="stalledDL")
    raw_trackers = [{"url": "https://tracker.example/announce", "status": 2}]
    client = FakeQbitClient(
        torrents=[torrent], trackers_by_hash={HASH_A: raw_trackers}
    )

    via_client = explain_torrent(client, HASH_A)
    via_builder = build_torrent_explanation(
        torrent,
        get_safe_tracker_details(raw_trackers),
        generated_at=via_client.generated_at if via_client else None,
    )

    assert via_client is not None
    assert via_client.target_identity == via_builder.target_identity
    assert via_client.summary == via_builder.summary
    assert via_client.overall_severity == via_builder.overall_severity
    assert via_client.findings == via_builder.findings


def test_pure_builder_performs_zero_api_calls() -> None:
    """`build_torrent_explanation` never touches a client at all -- it
    does not even accept one."""
    import inspect

    signature = inspect.signature(build_torrent_explanation)
    assert "client" not in signature.parameters


def test_pure_builder_treats_none_tracker_details_as_collection_failure() -> (
    None
):
    torrent = make_torrent(hash=HASH_A, name="T1", state="stalledDL")

    report = build_torrent_explanation(torrent, None)

    finding = report.findings[0]
    assert any(
        "could not be collected" in limitation
        for limitation in finding.limitations
    )


def test_pure_builder_treats_empty_list_as_no_endpoints_reported() -> None:
    torrent = make_torrent(hash=HASH_A, name="T1", state="stalledDL")

    report = build_torrent_explanation(torrent, [])

    finding = report.findings[0]
    assert any(
        "No tracker endpoints were reported" in limitation
        for limitation in finding.limitations
    )


def test_pure_builder_uses_the_supplied_generated_at() -> None:
    from datetime import UTC, datetime

    torrent = make_torrent(hash=HASH_A, name="T1")
    fixed = datetime(2026, 1, 1, tzinfo=UTC)

    report = build_torrent_explanation(torrent, [], generated_at=fixed)

    assert report.generated_at == fixed


def test_explain_torrent_output_unchanged_after_extraction() -> None:
    """Regression: extracting the pure builder must not change
    `explain_torrent`'s own output for an existing, already-covered
    scenario (healthy seeding, no trackers)."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1", state="uploading")],
        trackers_by_hash={HASH_A: []},
    )

    report = explain_torrent(client, HASH_A)

    assert report is not None
    assert report.findings[0].code == "TORRENT_HEALTHY_SEEDING"
    assert report.overall_severity == ExplanationSeverity.INFO


# --- Tracker explanation ------------------------------------------------------


def test_critical_tracker_explanation() -> None:
    """Ensure an all-failing tracker produces a critical finding."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="T1"),
            make_torrent(hash=HASH_B, name="T2"),
        ],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 4}],
            HASH_B: [{"url": "https://tracker.example/announce", "status": 4}],
        },
    )

    report = explain_tracker(client, "tracker.example")

    assert report is not None
    finding = report.findings[0]
    assert finding.code == "TRACKER_ALL_ENDPOINTS_FAILING"
    assert finding.severity is ExplanationSeverity.CRITICAL
    evidence_by_code = {item.code: item.value for item in finding.evidence}
    assert evidence_by_code["enabled_endpoints"] == 2
    assert evidence_by_code["critical_endpoints"] == 2
    assert evidence_by_code["healthy_endpoints"] == 0
    assert explanation_exit_code(report.overall_severity) == 2


def test_mixed_tracker_warning() -> None:
    """Ensure a mix of healthy/failing endpoints is a warning, not critical."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="T1"),
            make_torrent(hash=HASH_B, name="T2"),
        ],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 2}],
            HASH_B: [{"url": "https://tracker.example/announce", "status": 4}],
        },
    )

    report = explain_tracker(client, "tracker.example")

    assert report is not None
    finding = report.findings[0]
    assert finding.code == "TRACKER_MIXED_ENDPOINT_HEALTH"
    assert finding.severity is ExplanationSeverity.WARNING
    assert explanation_exit_code(report.overall_severity) == 1


def test_disabled_only_tracker_explanation() -> None:
    """Ensure a disabled-only tracker is informational, not a warning."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1")],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 0}]
        },
    )

    report = explain_tracker(client, "tracker.example")

    assert report is not None
    finding = report.findings[0]
    assert finding.code == "TRACKER_DISABLED_ONLY"
    assert finding.severity is ExplanationSeverity.INFO
    assert explanation_exit_code(report.overall_severity) == 0


def test_unknown_tracker_states_explanation() -> None:
    """Ensure unclassifiable states produce an honest UNKNOWN finding."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1")],
        trackers_by_hash={
            HASH_A: [
                {"url": "https://tracker.example/announce", "status": "weird"}
            ]
        },
    )

    report = explain_tracker(client, "tracker.example")

    assert report is not None
    finding = report.findings[0]
    assert finding.code == "TRACKER_UNKNOWN_STATES"
    assert finding.severity is ExplanationSeverity.UNKNOWN
    assert explanation_exit_code(report.overall_severity) == 1


def test_healthy_tracker_explanation() -> None:
    """Ensure a fully healthy tracker gets a concise informational finding."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1")],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 2}]
        },
    )

    report = explain_tracker(client, "tracker.example")

    assert report is not None
    finding = report.findings[0]
    assert finding.code == "TRACKER_HEALTHY"
    assert finding.severity is ExplanationSeverity.INFO


def test_partial_tracker_collection_failure_adds_a_secondary_finding() -> None:
    """Ensure a partial collection failure appends its own warning finding."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="T1"),
            make_torrent(hash=HASH_B, name="T2"),
        ],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 2}],
        },
        tracker_error_hashes={HASH_B},
    )

    report = explain_tracker(client, "tracker.example")

    assert report is not None
    codes = [finding.code for finding in report.findings]
    assert "TRACKER_HEALTHY" in codes
    assert "TRACKER_PARTIAL_COLLECTION" in codes
    partial = next(
        f for f in report.findings if f.code == "TRACKER_PARTIAL_COLLECTION"
    )
    assert partial.severity is ExplanationSeverity.WARNING
    assert report.overall_severity is ExplanationSeverity.WARNING
    assert any(
        "may be incomplete" in limitation for limitation in partial.limitations
    )


def test_tracker_with_no_observations_returns_none() -> None:
    """Ensure a tracker never observed returns None -- target unavailable."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1")],
        trackers_by_hash={
            HASH_A: [{"url": "https://other.example/announce", "status": 2}]
        },
    )

    assert explain_tracker(client, "tracker.example") is None


def test_tracker_explanation_reuses_tracker_status_collection() -> None:
    """Ensure `explain tracker` performs the same bounded scan as
    `trackers status`."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=HASH_A, name="T1"),
            make_torrent(hash=HASH_B, name="T2"),
        ],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 2}],
            HASH_B: [{"url": "https://other.example/announce", "status": 2}],
        },
    )

    explain_tracker(client, "tracker.example")

    assert client.torrents_info_calls == 1
    assert client.torrents_trackers_calls == 2


def test_tracker_explanation_accepts_a_full_url() -> None:
    """Ensure `--tracker` accepts a full URL, normalized before matching."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1")],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 2}]
        },
    )

    report = explain_tracker(client, "https://tracker.example/announce/PASSKEY")

    assert report is not None
    assert report.target_identity == "tracker.example"


# --- Safe next-command generation ---------------------------------------------


def test_next_commands_never_include_mutation_flags() -> None:
    """Ensure no suggested command appends --no-dry-run or --yes."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1", state="stalledDL")],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 4}]
        },
    )

    report = explain_torrent(client, HASH_A)

    assert report is not None
    for finding in report.findings:
        for command in finding.next_commands:
            assert "--no-dry-run" not in command
            assert "--yes" not in command


def test_next_commands_use_resolved_full_hash() -> None:
    """Ensure a resolved unique prefix expands to the full hash in
    suggestions."""
    client = FakeQbitClient(torrents=[make_torrent(hash=HASH_A, name="T1")])

    report = explain_torrent(client, HASH_A[:8])

    assert report is not None
    assert any(
        HASH_A in command for command in report.findings[0].next_commands
    )


# --- Secret sanitization ------------------------------------------------------


def test_torrent_explanation_never_leaks_a_secret() -> None:
    """Ensure no report field ever contains a raw tracker URL or passkey."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1", state="error")],
        trackers_by_hash={
            HASH_A: [
                {
                    "url": PASSKEY_URL,
                    "status": 4,
                    "msg": f"failed to reach {PASSKEY_URL}",
                }
            ]
        },
    )

    report = explain_torrent(client, HASH_A)
    assert report is not None
    payload = str(explanation_report_to_dict(report))
    assert SECRET_PASSKEY not in payload
    assert PASSKEY_URL not in payload


def test_tracker_explanation_never_leaks_a_secret() -> None:
    """Ensure no report field ever contains a raw tracker URL or passkey."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1")],
        trackers_by_hash={
            HASH_A: [
                {
                    "url": PASSKEY_URL,
                    "status": 4,
                    "msg": f"failed to reach {PASSKEY_URL}",
                }
            ]
        },
    )

    report = explain_tracker(client, "tracker.example")
    assert report is not None
    payload = str(explanation_report_to_dict(report))
    assert SECRET_PASSKEY not in payload
    assert PASSKEY_URL not in payload


# --- Serialization ------------------------------------------------------------


def test_evidence_and_finding_serialize_to_plain_dicts() -> None:
    """Ensure the dict converters round-trip through json.dumps cleanly."""
    import json

    client = FakeQbitClient(
        torrents=[make_torrent(hash=HASH_A, name="T1")],
        trackers_by_hash={
            HASH_A: [{"url": "https://tracker.example/announce", "status": 2}]
        },
    )
    report = explain_torrent(client, HASH_A)
    assert report is not None

    payload = explanation_report_to_dict(report)
    json.dumps(payload)  # must not raise
    assert payload["findings"][0] == finding_to_dict(report.findings[0])
    assert payload["findings"][0]["evidence"][0] == evidence_to_dict(
        report.findings[0].evidence[0]
    )
