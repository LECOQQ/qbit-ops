"""Test the `doctor` domain model: collection, checks, and serialization."""

from typing import Any

import pytest

from app.doctor import (
    CheckStatus,
    ConnectionOutcome,
    collect_doctor_report,
    doctor_exit_code,
    doctor_report_to_csv_rows,
    doctor_report_to_json_dict,
)
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
def test_unparsable_version_warns_and_skips_supported_check(
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


def test_unsupported_major_version_warns_not_fails() -> None:
    """Ensure an out-of-range major version warns rather than fails."""
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
    assert report.overall_status == CheckStatus.WARNING


def test_unknown_torrent_states_warn() -> None:
    """Ensure an unrecognized torrent state produces a warning, reusing
    `app.status.classify_torrent_state`."""
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
