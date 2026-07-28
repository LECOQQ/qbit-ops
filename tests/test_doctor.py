"""Test the `doctor` domain model: collection, checks, and serialization."""

from typing import Any

import pytest

import qbit_ops.doctor as doctor_module
from qbit_ops.doctor import (
    CheckStatus,
    ConnectionOutcome,
    collect_doctor_report,
    doctor_exit_code,
    doctor_report_to_csv_rows,
    doctor_report_to_json_dict,
)
from qbit_ops.qbit.compatibility import (
    CompatibilityManifestError,
    load_compatibility_evidence,
)
from tests.compatibility._captured_loader import load_captured_fixtures
from tests.support import FakeQbitClient, make_config, make_torrent

CLEAN_CONFIG = make_config(host="http://localhost:8080")
EMBEDDED_CREDENTIALS_CONFIG = make_config(
    host="http://admin:secret-password@localhost:8080"
)

EXPECTED_CHECK_ORDER = (
    "CFG001",
    "CFG002",
    "CFG003",
    "CONN001",
    "CONN002",
    "CONN003",
    "CONN004",
    "COMPAT001",
    "COMPAT002",
    "COMPAT003",
    "RUNTIME001",
    "RUNTIME002",
    "RUNTIME003",
)


def _healthy_client() -> FakeQbitClient:
    """Build a client that should produce an all-`pass` report."""
    return FakeQbitClient(
        torrents=[make_torrent(state="uploading")],
        qbittorrent_version="5.0.1",
        api_version="2.9.3",
    )


def _checks_by_code(report: Any) -> dict[str, Any]:
    return {check.code: check for check in report.checks}


def test_report_check_order_is_deterministic() -> None:
    """Ensure checks always appear in the same fixed, documented order."""
    report = collect_doctor_report(
        config=CLEAN_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.OK,
        connection_error=None,
        client=_healthy_client(),
    )

    assert tuple(check.code for check in report.checks) == EXPECTED_CHECK_ORDER


def test_all_pass_report_is_healthy() -> None:
    """Ensure a fully healthy instance produces an all-`pass` report."""
    report = collect_doctor_report(
        config=CLEAN_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.OK,
        connection_error=None,
        client=_healthy_client(),
    )

    assert report.overall_status == CheckStatus.PASS
    assert all(check.status == CheckStatus.PASS for check in report.checks)
    assert doctor_exit_code(report.overall_status) == 0


def test_config_load_failure_skips_everything_downstream() -> None:
    """Ensure a config error fails CFG001 and skips every other check."""
    report = collect_doctor_report(
        config=None,
        config_error=RuntimeError("Missing required environment variable(s)"),
        connection_outcome=None,
        connection_error=None,
        client=None,
    )

    checks = _checks_by_code(report)
    assert checks["CFG001"].status == CheckStatus.FAIL
    assert "Missing required" in (checks["CFG001"].detail or "")
    for code in EXPECTED_CHECK_ORDER[1:]:
        assert checks[code].status == CheckStatus.SKIPPED
    assert report.overall_status == CheckStatus.FAIL
    assert doctor_exit_code(report.overall_status) == 2


def test_malformed_host_fails_cfg002_but_still_checks_cfg003() -> None:
    """Ensure an independent local config check keeps running after a
    sibling check fails."""
    config = make_config(host="not-a-url")
    report = collect_doctor_report(
        config=config,
        config_error=None,
        connection_outcome=ConnectionOutcome.CONNECTION_FAILED,
        connection_error=RuntimeError("boom"),
        client=None,
    )

    checks = _checks_by_code(report)
    assert checks["CFG001"].status == CheckStatus.PASS
    assert checks["CFG002"].status == CheckStatus.FAIL
    assert checks["CFG003"].status == CheckStatus.PASS


def test_embedded_credentials_in_host_warn() -> None:
    """Ensure userinfo embedded in QBIT_HOST produces a warning, not a
    failure."""
    report = collect_doctor_report(
        config=EMBEDDED_CREDENTIALS_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.OK,
        connection_error=None,
        client=_healthy_client(),
    )

    checks = _checks_by_code(report)
    assert checks["CFG003"].status == CheckStatus.WARNING
    assert report.overall_status == CheckStatus.WARNING
    assert doctor_exit_code(report.overall_status) == 1


def test_connection_failure_fails_conn001_and_skips_the_rest() -> None:
    """Ensure an unreachable instance fails CONN001 and skips every check
    that depends on a live client."""
    report = collect_doctor_report(
        config=CLEAN_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.CONNECTION_FAILED,
        connection_error=RuntimeError("Unable to connect to qBittorrent."),
        client=None,
    )

    checks = _checks_by_code(report)
    assert checks["CONN001"].status == CheckStatus.FAIL
    assert checks["CONN002"].status == CheckStatus.SKIPPED
    for code in ("CONN003", "CONN004", "RUNTIME001", "RUNTIME002"):
        assert checks[code].status == CheckStatus.SKIPPED
    assert checks["COMPAT001"].status == CheckStatus.SKIPPED
    assert checks["COMPAT002"].status == CheckStatus.SKIPPED
    assert checks["COMPAT003"].status == CheckStatus.SKIPPED
    assert checks["RUNTIME003"].status == CheckStatus.SKIPPED
    assert report.overall_status == CheckStatus.FAIL


def test_authentication_failure_passes_reachability_but_fails_auth() -> None:
    """Ensure a login-rejection still proves the host was reachable."""
    report = collect_doctor_report(
        config=CLEAN_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.AUTHENTICATION_FAILED,
        connection_error=RuntimeError("Authentication to qBittorrent failed."),
        client=None,
    )

    checks = _checks_by_code(report)
    assert checks["CONN001"].status == CheckStatus.PASS
    assert checks["CONN002"].status == CheckStatus.FAIL
    for code in ("CONN003", "CONN004", "RUNTIME001", "RUNTIME002"):
        assert checks[code].status == CheckStatus.SKIPPED


@pytest.mark.parametrize(
    "qbit_version",
    ["nightly", "unknown", ""],
)
def test_unparsable_version_warns_and_skips_evidence_check(
    qbit_version: str,
) -> None:
    """Ensure an unparsable version string warns instead of failing, and
    never invents a supported/unsupported verdict."""
    client = FakeQbitClient(
        torrents=[make_torrent(state="uploading")],
        qbittorrent_version=qbit_version,
    )
    report = collect_doctor_report(
        config=CLEAN_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.OK,
        connection_error=None,
        client=client,
    )

    checks = _checks_by_code(report)
    assert checks["COMPAT001"].status == CheckStatus.WARNING
    assert checks["COMPAT002"].status == CheckStatus.SKIPPED


def test_version_older_than_oldest_evidence_warns_not_fails() -> None:
    """Ensure a version older than the oldest tested entry warns rather
    than fails, and never claims incompatibility solely for age."""
    client = FakeQbitClient(
        torrents=[make_torrent(state="uploading")],
        qbittorrent_version="3.3.16",
    )
    report = collect_doctor_report(
        config=CLEAN_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.OK,
        connection_error=None,
        client=client,
    )

    checks = _checks_by_code(report)
    assert checks["COMPAT001"].status == CheckStatus.PASS
    assert checks["COMPAT002"].status == CheckStatus.WARNING
    assert "older than the oldest" in checks["COMPAT002"].message.lower()
    assert report.overall_status == CheckStatus.WARNING


_FORBIDDEN_SUPPORT_CLAIMS = (
    "is supported",
    "validated against qbittorrent 4.x and 5.x",
    "compatible with 4.6",
    "compatible with 4.6–5.2",
)


@pytest.mark.parametrize("qbit_version", ["4.6.7", "5.2.3", "5.9.0"])
def test_compat002_never_uses_forbidden_support_wording(
    qbit_version: str,
) -> None:
    """F-5: `doctor` must never tell a user their exact major version is
    `supported`, nor generalize the Docker matrix's exact-version
    evidence into a 4.x/5.x major-version claim -- only the matrix
    manifest (docs/COMPATIBILITY.md) may claim exact-version evidence."""
    client = FakeQbitClient(
        torrents=[make_torrent(state="uploading")],
        qbittorrent_version=qbit_version,
    )
    report = collect_doctor_report(
        config=CLEAN_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.OK,
        connection_error=None,
        client=client,
    )

    check = _checks_by_code(report)["COMPAT002"]
    rendered = " ".join(
        filter(None, [check.message, check.remediation])
    ).lower()
    for forbidden in _FORBIDDEN_SUPPORT_CLAIMS:
        assert forbidden not in rendered, (
            f"COMPAT002 rendered {forbidden!r} for version {qbit_version}: "
            f"{rendered!r}"
        )


def test_compat002_exact_match_cites_container_integration_tested() -> None:
    """Matrix-aware wording: an exact application-version AND exact
    Web API match reports 'container integration tested', on the
    evidenced architecture, without claiming other architectures are
    incompatible."""
    client = FakeQbitClient(
        torrents=[make_torrent(state="uploading")],
        qbittorrent_version="5.2.3",
        api_version="2.15.1",
    )
    report = collect_doctor_report(
        config=CLEAN_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.OK,
        connection_error=None,
        client=client,
    )

    check = _checks_by_code(report)["COMPAT002"]
    assert check.status == CheckStatus.PASS
    message = check.message.lower()
    assert "container integration tested" in message
    assert "5.2.3" in message
    assert "2.15.1" in message
    assert "amd64" in message
    assert "docs/compatibility.md" in message


def test_unknown_torrent_states_warn() -> None:
    """Ensure an unrecognized torrent state produces a warning, reusing
    `qbit_ops.status.classify_torrent_state`."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(state="uploading"),
            make_torrent(hash="b" * 40, state="totallyNewState"),
        ]
    )
    report = collect_doctor_report(
        config=CLEAN_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.OK,
        connection_error=None,
        client=client,
    )

    checks = _checks_by_code(report)
    assert checks["RUNTIME003"].status == CheckStatus.WARNING
    assert "totallyNewState" in checks["RUNTIME003"].message


def test_api_call_failures_are_independent() -> None:
    """Ensure one failing bounded API call does not erase unrelated
    diagnostics: torrent listing failure must not prevent transfer info
    from succeeding, and vice versa."""

    class _PartiallyBrokenClient(FakeQbitClient):
        def torrents_info(self) -> list[dict[str, Any]]:
            raise RuntimeError("torrents_info exploded")

    client = _PartiallyBrokenClient(qbittorrent_version="5.0.1")
    report = collect_doctor_report(
        config=CLEAN_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.OK,
        connection_error=None,
        client=client,
    )

    checks = _checks_by_code(report)
    assert checks["RUNTIME001"].status == CheckStatus.FAIL
    assert checks["RUNTIME002"].status == CheckStatus.PASS
    assert checks["CONN003"].status == CheckStatus.PASS
    assert checks["CONN004"].status == CheckStatus.PASS
    assert checks["RUNTIME003"].status == CheckStatus.SKIPPED


def test_client_creation_and_bounded_calls() -> None:
    """Ensure collection never exceeds the documented bounded API budget:
    at most one call each to app_version, app_web_api_version,
    transfer_info, and torrents_info -- never a per-torrent call."""
    client = FakeQbitClient(
        torrents=[make_torrent(state="uploading") for _ in range(5)],
    )
    collect_doctor_report(
        config=CLEAN_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.OK,
        connection_error=None,
        client=client,
    )

    assert client.app_version_calls == 1
    assert client.app_web_api_version_calls == 1
    assert client.transfer_info_calls == 1
    assert client.torrents_info_calls == 1
    assert client.torrents_trackers_calls == 0


def test_connection_error_detail_redacts_embedded_password() -> None:
    """Ensure a password embedded in a connection error message is
    redacted before it reaches a check's `detail`."""
    report = collect_doctor_report(
        config=EMBEDDED_CREDENTIALS_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.CONNECTION_FAILED,
        connection_error=RuntimeError(
            "Unable to connect to qBittorrent at "
            "http://admin:secret-password@localhost:8080."
        ),
        client=None,
    )

    checks = _checks_by_code(report)
    detail = checks["CONN001"].detail or ""
    assert "secret-password" not in detail


def test_password_replacement_redacts_bare_password_too() -> None:
    """Ensure a bare (non-URL) password occurrence is also redacted."""
    report = collect_doctor_report(
        config=EMBEDDED_CREDENTIALS_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.AUTHENTICATION_FAILED,
        connection_error=RuntimeError(
            "Login rejected for password 'secret-password'."
        ),
        client=None,
    )

    checks = _checks_by_code(report)
    detail = checks["CONN002"].detail or ""
    assert "secret-password" not in detail
    assert "***" in detail


def test_no_secret_anywhere_in_json_or_csv_serialization() -> None:
    """Ensure serialized report forms never carry the configured
    password, matching the redaction proven at the check level."""
    report = collect_doctor_report(
        config=EMBEDDED_CREDENTIALS_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.CONNECTION_FAILED,
        connection_error=RuntimeError(
            "Unable to connect to qBittorrent at "
            "http://admin:secret-password@localhost:8080."
        ),
        client=None,
    )

    json_dict = doctor_report_to_json_dict(report)
    assert "secret-password" not in str(json_dict)

    csv_rows = doctor_report_to_csv_rows(report)
    assert "secret-password" not in str(csv_rows)


def test_overall_status_ignores_skipped_severity() -> None:
    """Ensure skipped checks never independently raise severity: with a
    genuinely healthy connection, extra skips must not appear and the
    report must stay `pass`."""
    report = collect_doctor_report(
        config=CLEAN_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.OK,
        connection_error=None,
        client=_healthy_client(),
    )

    assert CheckStatus.SKIPPED not in {c.status for c in report.checks}
    assert report.overall_status == CheckStatus.PASS


def test_json_dict_shape() -> None:
    """Ensure the JSON structure has the documented top-level shape."""
    report = collect_doctor_report(
        config=CLEAN_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.OK,
        connection_error=None,
        client=_healthy_client(),
    )

    payload = doctor_report_to_json_dict(report)
    assert payload["schema_version"] == "1"
    assert payload["overall_status"] == "pass"
    assert isinstance(payload["checks"], list)
    assert payload["checks"][0]["code"] == "CFG001"
    assert set(payload["checks"][0]) == {
        "code",
        "section",
        "status",
        "message",
        "detail",
        "remediation",
    }


def test_csv_rows_shape() -> None:
    """Ensure CSV rows follow the documented column order."""
    report = collect_doctor_report(
        config=CLEAN_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.OK,
        connection_error=None,
        client=_healthy_client(),
    )

    rows = doctor_report_to_csv_rows(report)
    assert rows[0][:3] == ("configuration", "CFG001", "pass")
    assert all(len(row) == 6 for row in rows)


# --- Matrix-aware compatibility evidence (COMPAT002/COMPAT003) -----------


def _report_for(qbit_version: str, api_version: str = "2.9.3") -> Any:
    client = FakeQbitClient(
        torrents=[make_torrent(state="uploading")],
        qbittorrent_version=qbit_version,
        api_version=api_version,
    )
    return collect_doctor_report(
        config=CLEAN_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.OK,
        connection_error=None,
        client=client,
    )


_ALL_EXACT_MATRIX_ENTRIES = tuple(load_compatibility_evidence().entries)


@pytest.mark.parametrize(
    "entry",
    _ALL_EXACT_MATRIX_ENTRIES,
    ids=[entry.id for entry in _ALL_EXACT_MATRIX_ENTRIES],
)
def test_every_exact_matrix_entry_matches_pass(entry: Any) -> None:
    """1/2: every current matrix entry, observed exactly (application and
    Web API version both matching), reports PASS with the
    container-integration-tested wording -- reads the manifest through
    the loader, never a duplicated inline list of versions."""
    version_string = entry.expected_version.removeprefix("v")
    report = _report_for(
        version_string, api_version=entry.expected_web_api_version
    )

    check = _checks_by_code(report)["COMPAT002"]
    assert check.status == CheckStatus.PASS
    message = check.message.lower()
    assert "container integration tested" in message
    assert version_string in message
    assert entry.expected_web_api_version in message
    assert entry.expected_architecture in message


@pytest.mark.parametrize(
    "entry",
    _ALL_EXACT_MATRIX_ENTRIES,
    ids=[entry.id for entry in _ALL_EXACT_MATRIX_ENTRIES],
)
def test_every_matrix_entry_matches_pass_using_captured_fixture_values(
    entry: Any,
) -> None:
    """Closes the loop the manifest-only parametrization above leaves
    open: drives `collect_doctor_report` with the *real* values a
    container returned (`tests/compatibility/fixtures/captured-container/`),
    not the manifest's own `expected_*` strings -- proving the exact-match
    comparison (string equality after `removeprefix("v")` on the
    application side; raw string equality on the Web API side) actually
    holds against production-shaped payloads, not just self-consistent
    fixtures."""
    fixtures_by_name = {
        fixture.name: fixture for fixture in load_captured_fixtures(entry.id)
    }
    observed_app_version = fixtures_by_name["app_version"].payload
    observed_web_api_version = fixtures_by_name["app_web_api_version"].payload

    report = _report_for(
        observed_app_version, api_version=observed_web_api_version
    )

    check = _checks_by_code(report)["COMPAT002"]
    assert check.status == CheckStatus.PASS, check
    assert "container integration tested" in check.message.lower()


def test_exact_application_version_with_mismatched_web_api_warns() -> None:
    """3: an exact application-version match whose observed Web API
    differs from the recorded evidence is a WARNING, never
    'unsupported'."""
    report = _report_for("5.2.3", api_version="2.11.4")  # expects 2.15.1

    check = _checks_by_code(report)["COMPAT002"]
    assert check.status == CheckStatus.WARNING
    message = check.message.lower()
    assert "differs from" in message
    assert "unsupported" not in message
    assert "incompatible" not in message


def test_untested_patch_version_between_entries_is_neutral_pass() -> None:
    """4/16: a version strictly between two exact tested entries, but
    itself untested, is a neutral PASS -- never inferred support for
    the intervening range."""
    report = _report_for("5.0.5")  # between qbit-5.0.0 and qbit-5.1.4

    check = _checks_by_code(report)["COMPAT002"]
    assert check.status == CheckStatus.PASS
    message = check.message.lower()
    assert "not container-integration tested" in message
    assert "no incompatibility" in message
    assert "4.6.7" in message
    assert "5.2.3" in message


def test_version_newer_than_5_2_3_warns() -> None:
    """5: a version newer than the newest exact evidence warns, never
    'incompatible'/'unsupported'."""
    report = _report_for("6.0.0")

    check = _checks_by_code(report)["COMPAT002"]
    assert check.status == CheckStatus.WARNING
    message = check.message.lower()
    assert "newer than the latest" in message
    assert "5.2.3" in message
    assert "incompatible" not in message
    assert "unsupported" not in message


def test_version_older_than_4_6_7_warns() -> None:
    """6: a version older than the oldest exact evidence warns, never
    'incompatible' solely for age."""
    report = _report_for("4.0.0")

    check = _checks_by_code(report)["COMPAT002"]
    assert check.status == CheckStatus.WARNING
    message = check.message.lower()
    assert "older than the oldest" in message
    assert "4.6.7" in message


def test_malformed_application_version_skips_evidence_check() -> None:
    """7: a malformed application version never crashes doctor; COMPAT001
    warns and COMPAT002 is skipped, exactly the existing unparsable-
    version contract."""
    report = _report_for("not-a-real-version")

    checks = _checks_by_code(report)
    assert checks["COMPAT001"].status == CheckStatus.WARNING
    assert checks["COMPAT002"].status == CheckStatus.SKIPPED


def test_malformed_web_api_version_warns_capability_without_crashing() -> None:
    """8: a malformed Web API version never crashes doctor -- COMPAT003
    warns that the format is unrecognized rather than raising."""
    report = _report_for("5.2.3", api_version="not-a-real-version")

    check = _checks_by_code(report)["COMPAT003"]
    assert check.status == CheckStatus.WARNING
    assert "unrecognized format" in check.message.lower()


def test_missing_packaged_evidence_warns_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """9: if the packaged compatibility manifest cannot be read at all,
    doctor must not crash -- COMPAT002 warns and says nothing about
    incompatibility."""

    def _raise_missing() -> Any:
        raise CompatibilityManifestError("simulated missing package data")

    monkeypatch.setattr(
        doctor_module, "load_compatibility_evidence", _raise_missing
    )
    report = _report_for("5.2.3")

    check = _checks_by_code(report)["COMPAT002"]
    assert check.status == CheckStatus.WARNING
    assert "manifest unavailable" in check.message.lower()
    assert "incompatible" not in check.message.lower()


def test_empty_packaged_evidence_warns_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """10: an empty packaged manifest (the loader's own fail-closed
    error) must not crash doctor either -- same WARNING contract as any
    other manifest error."""

    def _raise_empty() -> Any:
        raise CompatibilityManifestError(
            "compatibility manifest declares zero matrix entries"
        )

    monkeypatch.setattr(
        doctor_module, "load_compatibility_evidence", _raise_empty
    )
    report = _report_for("5.2.3")

    check = _checks_by_code(report)["COMPAT002"]
    assert check.status == CheckStatus.WARNING
    assert "zero matrix entries" in (check.detail or "")


@pytest.mark.parametrize(
    "qbit_version,api_version",
    [
        ("4.6.7", "2.9.3"),
        ("5.0.0", "2.11.2"),
        ("5.1.4", "2.11.4"),
        ("5.2.3", "2.15.1"),
        ("5.9.0", "2.9.3"),
        ("4.0.0", "2.9.3"),
        ("5.0.5", "2.9.3"),
    ],
)
def test_compat002_never_uses_broad_or_absent_support_wording(
    qbit_version: str, api_version: str
) -> None:
    """11: no classification, across every case, ever says an absent
    exact version is 'unsupported', nor claims a broad supported range
    -- checked for both compatibility checks (COMPAT002's evidence
    classification and COMPAT003's capability check), since either
    could reintroduce blanket "supported" wording."""
    report = _report_for(qbit_version, api_version=api_version)
    checks = _checks_by_code(report)

    for code in ("COMPAT002", "COMPAT003"):
        check = checks[code]
        rendered = " ".join(
            filter(None, [check.message, check.remediation])
        ).lower()
        for forbidden in _FORBIDDEN_SUPPORT_CLAIMS:
            assert forbidden not in rendered, (
                code,
                qbit_version,
                forbidden,
                rendered,
            )
        assert "unsupported" not in rendered, (code, qbit_version, rendered)


def test_json_schema_is_stable_with_new_checks() -> None:
    """12: the JSON structure keeps its documented top-level/per-check
    shape even though the checks it carries changed."""
    report = _report_for("5.0.5")
    payload = doctor_report_to_json_dict(report)

    assert payload["schema_version"] == "1"
    assert isinstance(payload["checks"], list)
    codes = [check["code"] for check in payload["checks"]]
    assert "COMPAT002" in codes
    assert "COMPAT003" in codes
    for check in payload["checks"]:
        assert set(check) == {
            "code",
            "section",
            "status",
            "message",
            "detail",
            "remediation",
        }


def test_csv_rows_are_stable_with_new_checks() -> None:
    """13: CSV rows keep their documented column order and width."""
    report = _report_for("5.0.5")
    rows = doctor_report_to_csv_rows(report)

    assert all(len(row) == 6 for row in rows)
    codes = [row[1] for row in rows]
    assert "COMPAT002" in codes
    assert "COMPAT003" in codes


def test_compatibility_checks_perform_no_network_or_docker_access() -> None:
    """14: `doctor`'s compatibility checks never import networking or
    Docker machinery -- only the packaged evidence loader and
    `packaging.version`. `urllib.parse` is allowed (pure string
    parsing for CFG002's URL-shape check, no I/O); only the
    network-performing `urllib.request` submodule is forbidden. Also
    guards item 9 of the previous phase's sabotage list applied here:
    `doctor` must never import from `tests/`."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(doctor_module))
    imported_full_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_full_names.update(n.name for n in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_full_names.add(node.module)

    forbidden_prefixes = (
        "socket",
        "urllib.request",
        "requests",
        "docker",
        "http.client",
        "tests",
    )
    offenders = {
        name
        for name in imported_full_names
        if any(
            name == prefix or name.startswith(prefix + ".")
            for prefix in forbidden_prefixes
        )
    }
    assert not offenders, imported_full_names


def test_doctor_never_hardcodes_a_second_copy_of_the_matrix_versions() -> None:
    """1: `doctor.py`'s source must not contain a duplicated, hardcoded
    list of the exact matrix versions -- the evidence classification
    must be computed from `load_compatibility_evidence()` at runtime,
    never a second embedded list qbit-ops's own tests can drift from
    without noticing."""
    import inspect

    source = inspect.getsource(doctor_module)
    evidence = load_compatibility_evidence()
    hardcoded_version_literals = [
        f'"{entry.expected_version.removeprefix("v")}"'
        for entry in evidence.entries
    ]
    offenders = [
        literal for literal in hardcoded_version_literals if literal in source
    ]
    assert not offenders, offenders


def test_untested_version_pass_is_distinct_from_capability_absence() -> None:
    """17: evidence absence (COMPAT002, neutral) and real capability
    absence (COMPAT003, a warning) are genuinely separate concerns --
    an untested-but-plausible version can still be missing a real,
    version-gated capability, and the two checks must disagree."""
    report = _report_for("5.0.5", api_version="1.9.0")

    checks = _checks_by_code(report)
    assert checks["COMPAT002"].status == CheckStatus.PASS
    assert checks["COMPAT003"].status == CheckStatus.WARNING
    assert "required capability missing" in checks["COMPAT003"].message.lower()
