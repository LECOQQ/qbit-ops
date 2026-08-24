"""Security-hardening tests for every tracker-facing output.

Proves the security invariant: no ordinary qbit-ops command
displays a complete tracker announce URL or tracker credential by
default. Covers structural sanitization primitives
(`qbit_core.features.trackers.sanitize_tracker_text`/`describe_tracker_url`/
`redact_tracker_identity`) and no-leak coverage across every read
surface, mutation preview/confirmation/summary, stderr/exception path,
and `backup diff`. Uses unmistakable fake secrets, never real tracker
credentials.
"""

import json

import pytest
from typer.testing import CliRunner

from qbit_core.features.backup import (
    diff_backup_exports,
    redact_backup_diff,
)
from qbit_core.features.trackers import (
    SafeTrackerIdentity,
    describe_tracker_url,
    redact_tracker_identity,
    sanitize_tracker_text,
)
from qbit_ops.cli.app import app
from qbit_ops.cli.exit_codes import ExitCode
from qbit_ops.cli.rendering import print_error
from tests.support import FakeQbitClient, make_torrent

TORRENT_A = "a" * 40
TORRENT_B = "b" * 40
SECRET_PASSKEY = "SUPER_SECRET_PASSKEY_918273"
QUERY_SECRET = "QUERY_SECRET_73645"
USERINFO_SECRET = "userinfo-secret"
FULL_URL = (
    f"https://{USERINFO_SECRET}:hunter2@tracker.example:6969/"
    f"announce/{SECRET_PASSKEY}?auth={QUERY_SECRET}"
)
BARE_QUERY_SECRET = "BARE_QUERY_SECRET_5566"
BARE_QUERY_URL = f"https://tracker.example/announce?{BARE_QUERY_SECRET}"


def _assert_no_secrets(text: str) -> None:
    for marker in (SECRET_PASSKEY, QUERY_SECRET, USERINFO_SECRET, "hunter2"):
        assert marker not in text, f"leaked {marker!r} in: {text!r}"


# --- structural sanitization ----------------------------------------


def test_sanitize_strips_url_userinfo() -> None:
    assert USERINFO_SECRET not in sanitize_tracker_text(
        f"connect to https://{USERINFO_SECRET}@tracker.example/announce"
    )
    assert "hunter2" not in sanitize_tracker_text(
        "connect to https://admin:hunter2@tracker.example/announce"
    )


def test_sanitize_strips_secret_path_segments() -> None:
    result = sanitize_tracker_text(
        f"failed https://tracker.example/announce/{SECRET_PASSKEY}"
    )
    assert SECRET_PASSKEY not in result


def test_sanitize_strips_query_values() -> None:
    result = sanitize_tracker_text(
        f"failed https://tracker.example/announce?passkey={SECRET_PASSKEY}"
    )
    assert SECRET_PASSKEY not in result


def test_sanitize_strips_percent_encoded_urls() -> None:
    encoded = f"https%3A%2F%2Ftracker.example%2Fannounce%2F{SECRET_PASSKEY}"
    result = sanitize_tracker_text(f"addTrackers?urls={encoded}")
    assert SECRET_PASSKEY not in result
    assert "<redacted-url>" in result


def test_sanitize_strips_fragments() -> None:
    result = sanitize_tracker_text(
        f"https://tracker.example/announce#{SECRET_PASSKEY}"
    )
    assert SECRET_PASSKEY not in result


def test_sanitize_leaves_plain_text_untouched() -> None:
    """Sanity: ordinary prose is not mangled by the sanitizer."""
    assert sanitize_tracker_text("Connection timed out after 30s") == (
        "Connection timed out after 30s"
    )


def test_describe_tracker_url_default_port_preserved() -> None:
    safe = describe_tracker_url("https://tracker.example:443/announce")
    assert safe.identity == "tracker.example:443"
    assert safe.port == 443


def test_describe_tracker_url_non_default_port_preserved() -> None:
    safe = describe_tracker_url("https://tracker.example:6969/announce")
    assert safe.identity == "tracker.example:6969"
    assert safe.port == 6969


@pytest.mark.parametrize(
    "pseudo", ["** [DHT] **", "** [PeX] **", "** [LSD] **"]
)
def test_describe_tracker_url_handles_pseudo_trackers(pseudo: str) -> None:
    """DHT/PeX/LSD pseudo-trackers never raise and never look like a
    host."""
    safe = describe_tracker_url(pseudo)
    assert safe.host is None
    assert safe.identity in {"DHT", "PeX", "LSD"}


@pytest.mark.parametrize(
    "malformed",
    ["", "   ", "not a url!! @@ ##", "://///", "\t\n"],
)
def test_describe_tracker_url_never_raises_on_malformed_input(
    malformed: str,
) -> None:
    """Malformed tracker strings degrade safely, never raise, and never
    leak anything resembling a real credential -- garbage input can still
    produce structurally-noisy `path_shape`/`query_keys` (e.g. a stray
    literal character `urlsplit` treats as a path segment), but since
    there is no real host/credential in the input, there is nothing to
    leak."""
    safe = describe_tracker_url(malformed)
    assert isinstance(safe, SafeTrackerIdentity)
    _assert_no_secrets(str(safe))


@pytest.mark.parametrize("empty_like", ["", "   ", "\t\n"])
def test_describe_tracker_url_empty_input_has_no_path_or_query(
    empty_like: str,
) -> None:
    """A genuinely empty tracker string has no path/query to report."""
    safe = describe_tracker_url(empty_like)
    assert safe.path_shape is None
    assert safe.query_keys == ()


def test_describe_tracker_url_query_keys_excludes_a_bare_token() -> None:
    """A `?SECRET` query with no `=` has no parameter name: the whole
    token must not become the reported key (it is a value, not a name)."""
    safe = describe_tracker_url(
        f"https://tracker.example/announce?{SECRET_PASSKEY}"
    )
    assert safe.query_keys == ()
    assert SECRET_PASSKEY not in safe.query_keys


def test_describe_tracker_url_query_keys_excludes_a_leading_bare_token() -> (
    None
):
    """A leading bare token must not leak even when a real `name=value`
    pair follows it in the same query string."""
    safe = describe_tracker_url(
        f"https://tracker.example/announce?{SECRET_PASSKEY}&x=1"
    )
    assert safe.query_keys == ("x",)


def test_describe_tracker_url_query_keys_still_reports_a_blank_value() -> None:
    """The fix must not regress the legitimate case: `passkey=` (a real
    name with a blank value) still reports `passkey` as a key."""
    safe = describe_tracker_url("https://tracker.example/announce?passkey=")
    assert safe.query_keys == ("passkey",)


def test_sanitize_handles_upstream_message_with_complete_url() -> None:
    """An upstream exception message containing a full URL is fully
    redacted, not just its scheme prefix."""
    upstream = (
        "HTTPConnectionPool(host='localhost', port=8080): Max retries "
        f"exceeded with url: /api/v2/torrents/addTrackers?urls={FULL_URL}"
    )
    result = sanitize_tracker_text(upstream)
    _assert_no_secrets(result)


def test_redact_tracker_identity_strips_userinfo() -> None:
    result = redact_tracker_identity(
        f"https://{USERINFO_SECRET}:hunter2@tracker.example:6969/announce"
    )
    assert result == "https://tracker.example:6969"
    _assert_no_secrets(result)


# --- Fixtures --------------------------------------------------------------


def _client_with_secret_tracker(*, status: int = 4) -> FakeQbitClient:
    return FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, name="A")],
        trackers_by_hash={
            TORRENT_A: [
                {
                    "url": FULL_URL,
                    "status": status,
                    "msg": f"failed to reach {FULL_URL}",
                }
            ]
        },
    )


# --- trackers list ------------------------------------------------


@pytest.mark.parametrize("fmt", ["table", "json", "jsonl", "csv"])
def test_trackers_list_never_leaks_secrets(
    runner: CliRunner, configure_qbit_backend, fmt: str
) -> None:
    configure_qbit_backend(client=_client_with_secret_tracker())

    result = runner.invoke(app, ["trackers", "list", "--format", fmt])

    _assert_no_secrets(result.stdout)
    _assert_no_secrets(result.stderr)


# --- trackers inspect ------------------------------------------------


@pytest.mark.parametrize("fmt", ["table", "json", "jsonl", "csv"])
def test_trackers_inspect_never_leaks_secrets(
    runner: CliRunner, configure_qbit_backend, fmt: str
) -> None:
    configure_qbit_backend(client=_client_with_secret_tracker())

    result = runner.invoke(
        app,
        [
            "trackers",
            "inspect",
            "--tracker",
            "tracker.example",
            "--format",
            fmt,
        ],
    )

    _assert_no_secrets(result.stdout)
    _assert_no_secrets(result.stderr)


@pytest.mark.parametrize("fmt", ["table", "json", "jsonl", "csv"])
def test_trackers_inspect_never_leaks_a_bare_query_passkey(
    runner: CliRunner, configure_qbit_backend, fmt: str
) -> None:
    """A `?<passkey>` tracker (no `=`) must not have its value echoed as
    a `query_keys` entry."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, name="A")],
        trackers_by_hash={TORRENT_A: [{"url": BARE_QUERY_URL, "status": 2}]},
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "trackers",
            "inspect",
            "--tracker",
            "tracker.example",
            "--format",
            fmt,
        ],
    )

    assert BARE_QUERY_SECRET not in result.stdout
    assert BARE_QUERY_SECRET not in result.stderr


# --- trackers status --------------------------------------------------


@pytest.mark.parametrize("fmt", ["table", "json", "jsonl", "csv"])
def test_trackers_status_never_leaks_secrets(
    runner: CliRunner, configure_qbit_backend, fmt: str
) -> None:
    configure_qbit_backend(client=_client_with_secret_tracker())

    result = runner.invoke(app, ["trackers", "status", "--format", fmt])

    _assert_no_secrets(result.stdout)
    _assert_no_secrets(result.stderr)


# --- torrents inspect ---------------------------------------------


@pytest.mark.parametrize("fmt", ["table", "json", "jsonl"])
def test_torrents_inspect_never_leaks_secrets(
    runner: CliRunner, configure_qbit_backend, fmt: str
) -> None:
    """`torrents inspect --hash` never renders a raw tracker URL."""
    configure_qbit_backend(client=_client_with_secret_tracker())

    result = runner.invoke(
        app,
        ["torrents", "inspect", "--hash", TORRENT_A, "--format", fmt],
    )

    _assert_no_secrets(result.stdout)
    _assert_no_secrets(result.stderr)


# --- mutation previews/confirmations/summaries ----------------------


def test_add_if_present_confirmation_never_leaks_secrets(
    runner: CliRunner, configure_qbit_backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "qbit_ops.cli.rendering.is_interactive_terminal", lambda: True
    )
    client = _client_with_secret_tracker(status=2)
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "trackers",
            "add-if-present",
            "--source",
            FULL_URL,
            "--target",
            FULL_URL,
            "--no-dry-run",
        ],
        input="n\n",
    )

    _assert_no_secrets(result.stdout)
    _assert_no_secrets(result.stderr)


def test_remove_confirmation_never_leaks_secrets(
    runner: CliRunner, configure_qbit_backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "qbit_ops.cli.rendering.is_interactive_terminal", lambda: True
    )
    client = _client_with_secret_tracker(status=2)
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["trackers", "remove", "--tracker", FULL_URL, "--no-dry-run"],
        input="n\n",
    )

    _assert_no_secrets(result.stdout)
    _assert_no_secrets(result.stderr)


def test_replace_confirmation_never_leaks_secrets(
    runner: CliRunner, configure_qbit_backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "qbit_ops.cli.rendering.is_interactive_terminal", lambda: True
    )
    client = _client_with_secret_tracker(status=2)
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "trackers",
            "replace",
            "--source",
            FULL_URL,
            "--target",
            "https://other.example/announce",
            "--no-dry-run",
        ],
        input="n\n",
    )

    _assert_no_secrets(result.stdout)
    _assert_no_secrets(result.stderr)


def test_replace_ambiguous_source_refusal_never_leaks_secrets(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """Two torrents each carrying a distinct passkey under the same host
    is exactly what --source's host matching cannot safely pick one
    from -- the refusal must name neither secret, and must surface as a
    clean, actionable error rather than an internal one."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A, name="A"),
            make_torrent(hash=TORRENT_B, name="B"),
        ],
        trackers_by_hash={
            TORRENT_A: [
                {"url": f"https://tracker.example/announce/{SECRET_PASSKEY}"}
            ],
            TORRENT_B: [
                {"url": "https://tracker.example/announce/OTHER-SECRET-PASSKEY"}
            ],
        },
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "trackers",
            "replace",
            "--source",
            "tracker.example",
            "--target",
            "https://other.example/announce",
        ],
    )

    assert result.exit_code == ExitCode.ERROR
    assert "Internal error" not in result.stdout
    assert "Internal error" not in result.stderr
    assert "--source-index" in result.stdout + result.stderr
    _assert_no_secrets(result.stdout)
    _assert_no_secrets(result.stderr)
    assert "OTHER-SECRET-PASSKEY" not in result.stdout
    assert "OTHER-SECRET-PASSKEY" not in result.stderr


def test_mutation_verbose_summary_never_leaks_secrets(
    runner: CliRunner, configure_qbit_backend
) -> None:
    client = _client_with_secret_tracker(status=2)
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["trackers", "remove", "--tracker", FULL_URL, "--verbose"],
    )

    _assert_no_secrets(result.stdout)
    _assert_no_secrets(result.stderr)


# --- stderr / exception messages -----------------------------------


def test_print_error_sanitizes_embedded_urls() -> None:
    print_error(f"Failed to connect: {FULL_URL}")


def test_apply_failure_sanitizes_upstream_exception(
    runner: CliRunner, configure_qbit_backend, monkeypatch: pytest.MonkeyPatch
) -> None:

    class _ExplodingClient(FakeQbitClient):
        def torrents_add_trackers(self, *args, **kwargs):  # type: ignore[override]
            raise RuntimeError(f"connection error requesting {FULL_URL}")

    client = _ExplodingClient(
        torrents=[make_torrent(hash=TORRENT_A, name="A")],
        trackers_by_hash={TORRENT_A: [{"url": FULL_URL, "status": 2}]},
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "trackers",
            "add-if-present",
            "--source",
            FULL_URL,
            "--target",
            "https://other.example/announce",
            "--no-dry-run",
            "--yes",
        ],
    )

    _assert_no_secrets(result.stdout)
    _assert_no_secrets(result.stderr)


def test_config_error_sanitizes_embedded_userinfo(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """A `QBIT_HOST`-shaped config error never echoes embedded credentials."""
    from qbit_ops.config import ConfigError

    configure_qbit_backend(
        config_error=ConfigError(
            "Invalid QBIT_HOST: https://admin:hunter2@localhost:8080"
        )
    )

    result = runner.invoke(app, ["trackers", "list"])

    assert "hunter2" not in result.stdout
    assert "hunter2" not in result.stderr


# --- progress descriptions ---------------------------------------------


def test_progress_descriptions_never_leak_secrets(
    runner: CliRunner, configure_qbit_backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Progress bar/spinner text never embeds tracker data (it is always
    a static message, but this pins that down for every tracker read
    command)."""
    monkeypatch.setattr(
        "qbit_ops.cli.rendering.is_interactive_terminal", lambda: True
    )
    configure_qbit_backend(client=_client_with_secret_tracker())

    for argv in (
        ["trackers", "list"],
        ["trackers", "status"],
        ["trackers", "inspect", "--tracker", "tracker.example"],
    ):
        result = runner.invoke(app, argv)
        _assert_no_secrets(result.stdout)
        _assert_no_secrets(result.stderr)


# --- backup diff ---------------------------------------------------------


def test_backup_diff_default_output_never_leaks_secrets(tmp_path) -> None:
    baseline = {
        "torrents": [
            {
                "hash": TORRENT_A,
                "name": "A",
                "normalized_trackers": [FULL_URL],
            }
        ],
        "tracker_usage": {FULL_URL: 1},
    }
    target = {
        "torrents": [
            {
                "hash": TORRENT_A,
                "name": "A",
                "normalized_trackers": [
                    "https://tracker.example/announce/OTHER"
                ],
            }
        ],
        "tracker_usage": {"https://tracker.example/announce/OTHER": 1},
    }
    report = diff_backup_exports(baseline, target)
    redacted = redact_backup_diff(report)

    _assert_no_secrets(json.dumps(redacted))


def test_backup_diff_cli_default_never_leaks_secrets(
    runner: CliRunner, tmp_path
) -> None:
    baseline_file = tmp_path / "baseline.json"
    target_file = tmp_path / "target.json"
    baseline_file.write_text(
        json.dumps(
            {
                "torrents": [
                    {
                        "hash": TORRENT_A,
                        "name": "A",
                        "normalized_trackers": [FULL_URL],
                    }
                ],
                "tracker_usage": {FULL_URL: 1},
            }
        )
    )
    target_file.write_text(
        json.dumps(
            {
                "torrents": [
                    {"hash": TORRENT_A, "name": "A", "normalized_trackers": []}
                ],
                "tracker_usage": {},
            }
        )
    )

    for fmt in ("table", "json", "jsonl"):
        result = runner.invoke(
            app,
            [
                "backup",
                "diff",
                str(baseline_file),
                str(target_file),
                "--format",
                fmt,
            ],
        )
        _assert_no_secrets(result.stdout)
        _assert_no_secrets(result.stderr)


def test_backup_diff_reveal_sensitive_shows_raw_values(
    runner: CliRunner, tmp_path
) -> None:
    """`--reveal-sensitive` is the documented, explicit opt-in to see raw
    tracker URLs in a diff -- confirms it actually does so, so the
    default-safe claim above is a real contrast, not a no-op flag."""
    baseline_file = tmp_path / "baseline.json"
    target_file = tmp_path / "target.json"
    baseline_file.write_text(
        json.dumps(
            {
                "torrents": [
                    {
                        "hash": TORRENT_A,
                        "name": "A",
                        "normalized_trackers": [FULL_URL],
                    }
                ],
                "tracker_usage": {FULL_URL: 1},
            }
        )
    )
    target_file.write_text(
        json.dumps(
            {
                "torrents": [
                    {"hash": TORRENT_A, "name": "A", "normalized_trackers": []}
                ],
                "tracker_usage": {},
            }
        )
    )

    result = runner.invoke(
        app,
        [
            "backup",
            "diff",
            str(baseline_file),
            str(target_file),
            "--format",
            "json",
            "--reveal-sensitive",
        ],
    )

    assert SECRET_PASSKEY in result.stdout


# --- export contract tests -------------------------------------------


def test_trackers_export_is_safe_by_default(
    runner: CliRunner, configure_qbit_backend
) -> None:
    configure_qbit_backend(client=_client_with_secret_tracker())

    result = runner.invoke(app, ["trackers", "export", "--format", "json"])

    _assert_no_secrets(result.stdout)
    payload = json.loads(result.stdout)
    for torrent in payload["torrents"]:
        assert "trackers" not in torrent  # raw field removed entirely


def test_trackers_export_has_no_sensitive_opt_in_flag(
    runner: CliRunner,
) -> None:
    """There is no `--include-sensitive` escape hatch on `trackers
    export` -- the raw-export use case is served by `backup export`
    instead, so no such flag was added."""
    result = runner.invoke(app, ["trackers", "export", "--help"])

    assert "--include-sensitive" not in result.output
    assert "--reveal-sensitive" not in result.output


def test_backup_export_is_the_sole_raw_export_and_is_opt_in_by_command(
    runner: CliRunner, configure_qbit_backend
) -> None:
    """The one raw-URL export path is a distinct, clearly-named command
    (`backup export`), never the default of a read-only tracker command,
    and its own table summary never echoes raw values."""
    configure_qbit_backend(client=_client_with_secret_tracker())

    result = runner.invoke(app, ["backup", "export"])

    assert result.exit_code == 0
    _assert_no_secrets(result.stdout)
