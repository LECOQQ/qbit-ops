"""Test the `doctor` domain model: collection, checks, and serialization."""

from pathlib import Path
from typing import Any

import pytest

import qbit_core.features.doctor as doctor_module
import qbit_core.qbit.compatibility as compat_module
from qbit_core.features.doctor import (
    CheckStatus,
    ConnectionOutcome,
    collect_doctor_report,
    doctor_exit_code,
    doctor_report_to_csv_rows,
    doctor_report_to_json_dict,
)
from qbit_core.qbit.compatibility import (
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
    "TUI001",
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
    report = collect_doctor_report(
        config=CLEAN_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.OK,
        connection_error=None,
        client=_healthy_client(),
    )

    assert tuple(check.code for check in report.checks) == EXPECTED_CHECK_ORDER


def test_all_pass_report_is_healthy() -> None:
    report = collect_doctor_report(
        config=CLEAN_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.OK,
        connection_error=None,
        client=_healthy_client(),
    )

    assert report.overall_status == CheckStatus.PASS
    # TUI001 is `SKIPPED` on every run: no process can read the
    # terminal's font, so it reports rather than judges -- and a check
    # that could never pass must not hold the exit code hostage.
    assert all(
        check.status in (CheckStatus.PASS, CheckStatus.SKIPPED)
        for check in report.checks
    )
    assert doctor_exit_code(report.overall_status) == 0


def test_cfg001_names_the_configuration_source_when_known() -> None:
    report = collect_doctor_report(
        config=CLEAN_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.OK,
        connection_error=None,
        client=_healthy_client(),
        config_source=Path("/home/user/.config/qbit-ops/.env"),
    )

    cfg001 = _checks_by_code(report)["CFG001"]
    assert cfg001.status == CheckStatus.PASS
    assert "/home/user/.config/qbit-ops/.env" in cfg001.message


def test_cfg001_stays_generic_without_a_known_source() -> None:
    report = collect_doctor_report(
        config=CLEAN_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.OK,
        connection_error=None,
        client=_healthy_client(),
    )

    cfg001 = _checks_by_code(report)["CFG001"]
    assert cfg001.message == "Configuration loaded successfully."


def test_config_load_failure_skips_everything_downstream() -> None:
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


def test_missing_configuration_names_a_command_that_actually_exists() -> None:
    """`doctor` diagnoses, it never executes -- so naming the human
    gesture is its whole job. Asserted against the real command tree so
    a renamed or dropped `init` cannot leave the remediation pointing
    at nothing.
    """
    report = collect_doctor_report(
        config=None,
        config_error=RuntimeError("Missing required environment variable(s)"),
        connection_outcome=None,
        connection_error=None,
        client=None,
    )

    remediation = _checks_by_code(report)["CFG001"].remediation or ""
    named = [
        word.strip("'\"`.,")
        for word in remediation.split()
        if word.strip("'\"`.,") in _registered_root_command_names()
    ]
    assert "init" in named, remediation


def _registered_root_command_names() -> set[str]:
    from qbit_ops.cli.app import app

    names = set()
    for command in app.registered_commands:
        assert command.callback is not None
        names.add(command.name or command.callback.__name__.replace("_", "-"))
    return names


def test_malformed_host_fails_cfg002_but_still_checks_cfg003() -> None:
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
    """`doctor` must never tell a user their exact major version is
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
    """A failing torrent-listing call must not prevent transfer info from
    succeeding, and vice versa."""

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
    """At most one call each to app_version, app_web_api_version,
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

    report = collect_doctor_report(
        config=CLEAN_CONFIG,
        config_error=None,
        connection_outcome=ConnectionOutcome.OK,
        connection_error=None,
        client=_healthy_client(),
    )

    skipped = {c.code for c in report.checks if c.status is CheckStatus.SKIPPED}
    # TUI001 is the one check that is always skipped -- it reports what a
    # terminal font would need and cannot verify it. A second one here
    # would mean a real check stopped running.
    assert skipped == {"TUI001"}
    assert report.overall_status == CheckStatus.PASS


def test_json_dict_shape() -> None:
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
    """Reads the manifest through the loader, never a duplicated inline
    list of versions."""
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
    """Drives `collect_doctor_report` with the *real* values a container
    returned (`tests/compatibility/fixtures/captured-container/`), not
    the manifest's own `expected_*` strings, proving the exact-match
    comparison holds against production-shaped payloads."""
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

    report = _report_for("5.2.3", api_version="2.11.4")  # expects 2.15.1

    check = _checks_by_code(report)["COMPAT002"]
    assert check.status == CheckStatus.WARNING
    message = check.message.lower()
    assert "differs from" in message
    assert "unsupported" not in message
    assert "incompatible" not in message


def test_untested_patch_version_between_entries_is_neutral_pass() -> None:
    """A version strictly between two exact tested entries, but itself
    untested, is a neutral PASS -- never inferred support for the
    intervening range."""
    report = _report_for("5.0.5")  # between qbit-5.0.0 and qbit-5.1.4

    check = _checks_by_code(report)["COMPAT002"]
    assert check.status == CheckStatus.PASS
    message = check.message.lower()
    assert "not container-integration tested" in message
    assert "no incompatibility" in message
    assert "4.6.7" in message
    assert "5.2.3" in message


def test_version_newer_than_5_2_3_warns() -> None:

    report = _report_for("6.0.0")

    check = _checks_by_code(report)["COMPAT002"]
    assert check.status == CheckStatus.WARNING
    message = check.message.lower()
    assert "newer than the latest" in message
    assert "5.2.3" in message
    assert "incompatible" not in message
    assert "unsupported" not in message


def test_version_older_than_4_6_7_warns() -> None:

    report = _report_for("4.0.0")

    check = _checks_by_code(report)["COMPAT002"]
    assert check.status == CheckStatus.WARNING
    message = check.message.lower()
    assert "older than the oldest" in message
    assert "4.6.7" in message


def test_malformed_application_version_skips_evidence_check() -> None:

    report = _report_for("not-a-real-version")

    checks = _checks_by_code(report)
    assert checks["COMPAT001"].status == CheckStatus.WARNING
    assert checks["COMPAT002"].status == CheckStatus.SKIPPED


def test_malformed_web_api_version_warns_capability_without_crashing() -> None:

    report = _report_for("5.2.3", api_version="not-a-real-version")

    check = _checks_by_code(report)["COMPAT003"]
    assert check.status == CheckStatus.WARNING
    assert "unrecognized format" in check.message.lower()


def test_missing_packaged_evidence_warns_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

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
    """An empty packaged manifest (the loader's own fail-closed error)
    gets the same WARNING contract as any other manifest error."""

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


def test_unparseable_packaged_manifest_warns_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end through the real loader (unlike the two tests above,
    which inject an already-built `CompatibilityManifestError`): a
    manifest entry that isn't a table raises `TypeError`, not
    `KeyError`, deep inside `parse_manifest_text()`. `doctor` must still
    render `WARNING`, not crash on an exception type its `except
    CompatibilityManifestError` doesn't name."""
    monkeypatch.setattr(
        compat_module,
        "_read_packaged_manifest_text",
        lambda: 'entry = ["oops"]',
    )
    report = _report_for("5.2.3")

    check = _checks_by_code(report)["COMPAT002"]
    assert check.status == CheckStatus.WARNING
    assert "manifest unavailable" in check.message.lower()


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
    """Checked for both COMPAT002 and COMPAT003, since either could
    reintroduce blanket "supported" wording."""
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

    report = _report_for("5.0.5")
    rows = doctor_report_to_csv_rows(report)

    assert all(len(row) == 6 for row in rows)
    codes = [row[1] for row in rows]
    assert "COMPAT002" in codes
    assert "COMPAT003" in codes


def test_compatibility_checks_perform_no_network_or_docker_access() -> None:
    """`doctor`'s compatibility checks never import networking or Docker
    machinery -- only the packaged evidence loader and
    `packaging.version`. `urllib.parse` is allowed (pure string parsing
    for CFG002's URL-shape check, no I/O); only the network-performing
    `urllib.request` submodule is forbidden. `doctor` must also never
    import from `tests/`."""
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
    """The evidence classification must be computed from
    `load_compatibility_evidence()` at runtime, never a second embedded
    list qbit-ops's own tests can drift from without noticing."""
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
    """Evidence absence (COMPAT002, neutral) and real capability absence
    (COMPAT003, a warning) are genuinely separate concerns."""
    report = _report_for("5.0.5", api_version="1.9.0")

    checks = _checks_by_code(report)
    assert checks["COMPAT002"].status == CheckStatus.PASS
    assert checks["COMPAT003"].status == CheckStatus.WARNING
    assert "required capability missing" in checks["COMPAT003"].message.lower()
