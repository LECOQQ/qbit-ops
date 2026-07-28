"""Test `qbit-ops status --watch` at the CLI level.

Covers CLI-level validation, the table and JSONL watch renderers, and a
light regression check that one-shot `status` is unaffected. The pure
`watch_status()` loop itself (ordering, timing, drift, overlap,
recovery) is tested independently of Typer/Rich in `tests/test_status.py`.

Uses injected fakes throughout — a fake `time.sleep` that raises
`KeyboardInterrupt` after N calls to end a watch loop deterministically,
and a fake qBittorrent client — never a real wait or a real terminal.
"""

import json
import re

import pytest
from typer.testing import CliRunner

import qbit_ops.cli.commands.status as status_module
import qbit_ops.cli.error_boundary as error_boundary
import qbit_ops.cli.rendering as rendering
from qbit_ops.cli.app import app
from qbit_ops.cli.commands.status import DEFAULT_STATUS_WATCH_INTERVAL_SECONDS
from qbit_ops.cli.exit_codes import ExitCode, StatusExitCode
from tests.support import FakeQbitClient, make_config, make_torrent

ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _install_backend(
    monkeypatch: pytest.MonkeyPatch, client: FakeQbitClient
) -> None:
    monkeypatch.setattr(
        error_boundary, "load_qbit_config", lambda: make_config()
    )
    monkeypatch.setattr(error_boundary, "create_qbit_client", lambda: client)


def _stop_after_n_sleeps(
    monkeypatch: pytest.MonkeyPatch, n: int
) -> list[float]:
    """Make `time.sleep` raise KeyboardInterrupt after `n` calls.

    Returns the list of requested sleep durations for inspection.
    """
    delays: list[float] = []

    def _fake_sleep(seconds: float) -> None:
        delays.append(seconds)
        if len(delays) >= n:
            raise KeyboardInterrupt

    monkeypatch.setattr(status_module.time, "sleep", _fake_sleep)
    return delays


def _capture_watch_kwargs(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replace `watch_status` with a spy that records kwargs and stops.

    Lets validation/interval-resolution tests assert on exactly what was
    passed to the loop without needing to simulate any iterations.
    """
    captured: dict = {}

    def _fake_watch_status(**kwargs: object) -> None:
        captured.update(kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(status_module, "watch_status", _fake_watch_status)
    return captured


def _make_client() -> FakeQbitClient:
    return FakeQbitClient(
        torrents=[make_torrent(hash="a" * 40, state="uploading")],
    )


# --- Validation --------------------------------------------------------


def test_watch_defaults_to_the_documented_positive_interval(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure omitting --interval with --watch uses the documented default."""
    _install_backend(monkeypatch, _make_client())
    captured = _capture_watch_kwargs(monkeypatch)

    result = runner.invoke(app, ["status", "--watch"])

    assert result.exit_code == ExitCode.SUCCESS
    assert captured["interval"] == DEFAULT_STATUS_WATCH_INTERVAL_SECONDS
    assert DEFAULT_STATUS_WATCH_INTERVAL_SECONDS > 0


@pytest.mark.parametrize("bad_interval", ["0", "-1", "nan", "inf", "-inf"])
def test_invalid_interval_values_are_rejected_before_any_api_call(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    bad_interval: str,
) -> None:
    """Ensure zero, negative, NaN, and infinite intervals are all rejected."""
    client = _make_client()
    _install_backend(monkeypatch, client)

    result = runner.invoke(
        app, ["status", "--watch", "--interval", bad_interval]
    )

    assert result.exit_code == StatusExitCode.INVALID_USAGE
    assert client.calls == []


@pytest.mark.parametrize("output_format", ["json", "csv"])
def test_watch_rejects_unsupported_formats_before_any_api_call(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    output_format: str,
) -> None:
    """Ensure --watch only ever accepts table or jsonl."""
    client = _make_client()
    _install_backend(monkeypatch, client)

    result = runner.invoke(
        app, ["status", "--watch", "--format", output_format]
    )

    assert result.exit_code == StatusExitCode.INVALID_USAGE
    assert client.calls == []


def test_watch_rejects_quiet_before_any_api_call(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure --watch --quiet fails fast instead of running silently forever."""
    client = _make_client()
    _install_backend(monkeypatch, client)

    result = runner.invoke(app, ["status", "--watch", "--quiet"])

    assert result.exit_code == StatusExitCode.INVALID_USAGE
    assert client.calls == []


def test_interval_without_watch_is_rejected(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure --interval alone (no --watch) is treated as invalid usage."""
    client = _make_client()
    _install_backend(monkeypatch, client)

    result = runner.invoke(app, ["status", "--interval", "3"])

    assert result.exit_code == StatusExitCode.INVALID_USAGE
    assert client.calls == []


# --- Table watch ---------------------------------------------------------


def test_table_watch_creates_exactly_one_live_display(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure one Rich Live display is created, not one per iteration."""
    _install_backend(monkeypatch, _make_client())
    _stop_after_n_sleeps(monkeypatch, 3)

    enter_count = 0
    real_live_status_display = rendering.live_status_display

    def _spying_live_status_display():
        nonlocal enter_count
        cm = real_live_status_display()

        class _Wrapper:
            def __enter__(self):
                nonlocal enter_count
                enter_count += 1
                return cm.__enter__()

            def __exit__(self, *exc_info):
                return cm.__exit__(*exc_info)

        return _Wrapper()

    monkeypatch.setattr(
        rendering, "live_status_display", _spying_live_status_display
    )

    result = runner.invoke(app, ["status", "--watch"])

    assert result.exit_code == ExitCode.SUCCESS
    assert enter_count == 1


def test_table_watch_updates_the_display_instead_of_appending(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure multiple iterations reuse the same display via update calls."""
    _install_backend(monkeypatch, _make_client())
    _stop_after_n_sleeps(monkeypatch, 3)

    update_calls = []
    real_live_status_display = rendering.live_status_display

    def _spying_live_status_display():
        with real_live_status_display() as update:

            def _spying_update(snapshot, watch):
                update_calls.append(watch.iteration)
                update(snapshot, watch)

            yield _spying_update

    from contextlib import contextmanager

    monkeypatch.setattr(
        rendering,
        "live_status_display",
        contextmanager(_spying_live_status_display),
    )

    result = runner.invoke(app, ["status", "--watch"])

    assert result.exit_code == ExitCode.SUCCESS
    assert update_calls == sorted(update_calls)
    assert len(update_calls) >= 3
    assert update_calls == list(range(1, len(update_calls) + 1))


def test_table_watch_never_uses_transient_spinner_or_progress(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure the watch loop never reaches for the transient progress API."""
    _install_backend(monkeypatch, _make_client())
    _stop_after_n_sleeps(monkeypatch, 2)

    def _fail_if_called(*_args: object, **_kwargs: object):
        raise AssertionError("transient progress helper must not be used")

    monkeypatch.setattr(rendering, "transient_spinner", _fail_if_called)
    monkeypatch.setattr(rendering, "transient_progress", _fail_if_called)

    result = runner.invoke(app, ["status", "--watch"])

    assert result.exit_code == ExitCode.SUCCESS


def test_table_watch_interruption_emits_no_traceback_and_exits_zero(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure Ctrl+C during table watch is a clean, documented exit."""
    _install_backend(monkeypatch, _make_client())
    _stop_after_n_sleeps(monkeypatch, 2)

    result = runner.invoke(app, ["status", "--watch"])

    assert result.exit_code == 0
    assert result.exception is None
    assert "Traceback" not in result.output


def test_table_watch_interruption_during_collection_closes_cleanly(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure Ctrl+C mid-collection (not just mid-sleep) exits cleanly."""

    class _InterruptingClient(FakeQbitClient):
        def torrents_info(self):
            if self.torrents_info_calls >= 1:
                raise KeyboardInterrupt
            return super().torrents_info()

    client = _InterruptingClient(torrents=[make_torrent(hash="a" * 40)])
    _install_backend(monkeypatch, client)
    # The interrupt happens during collect(), not sleep() — but the first
    # iteration still completes and sleeps once before the second collect
    # raises, so sleep must be faked too, or this test would really wait
    # out the default 5s interval.
    monkeypatch.setattr(status_module.time, "sleep", lambda seconds: None)

    result = runner.invoke(app, ["status", "--watch"])

    assert result.exit_code == 0
    assert result.exception is None
    assert "Traceback" not in result.output


# --- JSONL watch -----------------------------------------------------------


def test_jsonl_watch_emits_one_valid_object_per_line(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure each JSONL watch line independently parses as JSON."""
    _install_backend(monkeypatch, _make_client())
    _stop_after_n_sleeps(monkeypatch, 3)

    result = runner.invoke(app, ["status", "--watch", "--format", "jsonl"])

    assert result.exit_code == 0
    lines = [line for line in result.stdout.splitlines() if line]
    assert len(lines) == 3
    for line in lines:
        payload = json.loads(line)
        assert payload["schema_version"] == "1"


def test_emit_status_jsonl_flushes_stdout_after_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure `emit_status_jsonl` explicitly flushes after every line.

    Exercised directly (not through `CliRunner`, which swaps `sys.stdout`
    for its own capture buffer during `invoke()` — a patch applied
    before that swap would silently target the wrong object).
    """
    from qbit_ops.features.status import build_unavailable_snapshot

    events: list[str] = []

    class _FakeStdout:
        def write(self, text: str) -> int:
            events.append(f"write:{text!r}")
            return len(text)

        def flush(self) -> None:
            events.append("flush")

    monkeypatch.setattr(rendering.sys, "stdout", _FakeStdout())

    snapshot = build_unavailable_snapshot(code="x", message="y")
    rendering.emit_status_jsonl(snapshot)

    assert "flush" in events
    assert events.index("flush") > events.index(
        next(event for event in events if event.startswith("write"))
    )


def test_jsonl_watch_keeps_stderr_empty_on_successful_iterations(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure successful JSONL watch iterations never write to stderr."""
    _install_backend(monkeypatch, _make_client())
    _stop_after_n_sleeps(monkeypatch, 3)

    result = runner.invoke(app, ["status", "--watch", "--format", "jsonl"])

    assert result.exit_code == 0
    assert result.stderr == ""


def test_jsonl_watch_output_contains_no_ansi_escape_sequences(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure JSONL watch output is plain text, no Rich/ANSI decoration."""
    _install_backend(monkeypatch, _make_client())
    _stop_after_n_sleeps(monkeypatch, 3)

    result = runner.invoke(app, ["status", "--watch", "--format", "jsonl"])

    assert ANSI_ESCAPE_PATTERN.search(result.stdout) is None


def test_jsonl_watch_interrupted_output_ends_after_a_complete_line(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure interruption never leaves a partial JSON line in stdout."""
    _install_backend(monkeypatch, _make_client())
    _stop_after_n_sleeps(monkeypatch, 4)

    result = runner.invoke(app, ["status", "--watch", "--format", "jsonl"])

    assert result.exit_code == 0
    raw = result.stdout
    assert raw.endswith("\n")
    for line in raw.splitlines():
        json.loads(line)  # raises if any line is incomplete/invalid


# --- Regression: one-shot status is unaffected ------------------------


def test_one_shot_status_table_is_unaffected_by_watch_support(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure plain `status` (no --watch) still returns a single snapshot."""
    _install_backend(monkeypatch, _make_client())

    result = runner.invoke(app, ["status"])

    assert result.exit_code == StatusExitCode.HEALTHY
    assert "qbit-ops" in result.stdout
    assert "watching" not in result.stdout


def test_one_shot_status_json_is_unaffected_by_watch_support(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure plain `status --format json` still emits exactly one document."""
    _install_backend(monkeypatch, _make_client())

    result = runner.invoke(app, ["status", "--format", "json"])

    assert result.exit_code == StatusExitCode.HEALTHY
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "1"


def test_one_shot_status_quiet_is_unaffected_by_watch_support(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure plain `status --quiet` still suppresses all normal output."""
    _install_backend(monkeypatch, FakeQbitClient(torrents=[]))

    result = runner.invoke(app, ["status", "--quiet"])

    assert result.exit_code == StatusExitCode.HEALTHY
    assert result.stdout == ""
