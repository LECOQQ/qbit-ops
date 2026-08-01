"""Test the shared transient progress-feedback API in `qbit_ops.cli.rendering`.

Covers the presentation-only progress policy introduced alongside
`progress_enabled()`/`transient_spinner()`/`transient_progress()`: when
progress is allowed, that it never leaks into stdout, and that it cleans
up on normal completion, on an exception, and on `KeyboardInterrupt`.
Uses a Rich `Console` forced into terminal mode over an in-memory buffer
(`force_terminal=True`) rather than the developer's real terminal, per
the fake-TTY-stream requirement for this phase.
"""

import io

import pytest
from rich.console import Console

import qbit_ops.cli.rendering as ui
from qbit_ops.cli.rendering import (
    OutputFormat,
    progress_enabled,
    transient_progress,
    transient_spinner,
)

# --- Progress policy (pure, no console involved) -------------------------


def test_table_and_interactive_enables_progress() -> None:
    assert progress_enabled(
        output_format=OutputFormat.table, quiet=False, interactive=True
    )


def test_table_and_non_interactive_disables_progress() -> None:
    assert not progress_enabled(
        output_format=OutputFormat.table, quiet=False, interactive=False
    )


def test_json_disables_progress_even_when_interactive() -> None:
    assert not progress_enabled(
        output_format=OutputFormat.json, quiet=False, interactive=True
    )


def test_jsonl_disables_progress_even_when_interactive() -> None:
    assert not progress_enabled(
        output_format=OutputFormat.jsonl, quiet=False, interactive=True
    )


def test_csv_disables_progress_even_when_interactive() -> None:
    assert not progress_enabled(
        output_format=OutputFormat.csv, quiet=False, interactive=True
    )


def test_quiet_disables_progress_even_when_interactive_table() -> None:
    assert not progress_enabled(
        output_format=OutputFormat.table, quiet=True, interactive=True
    )


def test_output_format_none_is_treated_like_table() -> None:
    assert progress_enabled(output_format=None, quiet=False, interactive=True)
    assert not progress_enabled(
        output_format=None, quiet=False, interactive=False
    )


def test_progress_enabled_defaults_interactive_from_err_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ui, "err_console", Console(file=io.StringIO(), force_terminal=True)
    )
    assert progress_enabled(output_format=OutputFormat.table, quiet=False)

    monkeypatch.setattr(
        ui, "err_console", Console(file=io.StringIO(), force_terminal=False)
    )
    assert not progress_enabled(output_format=OutputFormat.table, quiet=False)


# --- Disabled helpers are safe no-ops -------------------------------------


def test_disabled_transient_spinner_is_a_safe_noop() -> None:
    with transient_spinner("Loading...", enabled=False):
        pass


def test_disabled_transient_progress_yields_a_working_noop_callback() -> None:
    with transient_progress("Scanning...", enabled=False) as advance:
        advance(1, 10)
        advance(10, 10)


def test_disabled_helpers_still_propagate_exceptions() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with transient_spinner("Loading...", enabled=False):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        with transient_progress("Scanning...", enabled=False) as advance:
            advance(0, 1)
            raise RuntimeError("boom")


# --- Cleanup with a forced-terminal console -------------------------------


def _forced_terminal_console() -> tuple[Console, io.StringIO]:
    """Build a Console that reports as an interactive terminal.

    Uses an in-memory buffer instead of a real terminal, so these tests
    do not depend on how the test suite itself is invoked. Returns the
    buffer alongside the console since `Console.file` is typed as a
    generic `IO[str]` and does not expose `.getvalue()`.
    """
    buffer = io.StringIO()
    return Console(file=buffer, force_terminal=True, width=80), buffer


def test_transient_spinner_never_writes_to_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_err_console, _ = _forced_terminal_console()
    fake_stdout_console, stdout_buffer = _forced_terminal_console()
    monkeypatch.setattr(ui, "err_console", fake_err_console)
    monkeypatch.setattr(ui, "console", fake_stdout_console)

    with transient_spinner("Loading torrents...", enabled=True):
        pass

    assert stdout_buffer.getvalue() == ""


def test_transient_progress_advances_and_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_err_console, _ = _forced_terminal_console()
    monkeypatch.setattr(ui, "err_console", fake_err_console)

    with transient_progress("Scanning...", enabled=True) as advance:
        for completed in range(1, 6):
            advance(completed, 5)


def test_transient_spinner_cleans_up_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rich's `Console.status()` always stops its live region in `__exit__`,
    regardless of how the block exits. Proven here by successfully using the
    same console again immediately afterward.
    """
    fake_err_console, buffer = _forced_terminal_console()
    monkeypatch.setattr(ui, "err_console", fake_err_console)

    with pytest.raises(RuntimeError, match="boom"):
        with transient_spinner("Loading...", enabled=True):
            raise RuntimeError("boom")

    # The console is still usable: no lingering Live display blocks it.
    fake_err_console.print("still usable")
    assert "still usable" in buffer.getvalue()


def test_transient_progress_cleans_up_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_err_console, buffer = _forced_terminal_console()
    monkeypatch.setattr(ui, "err_console", fake_err_console)

    with pytest.raises(RuntimeError, match="boom"):
        with transient_progress("Scanning...", enabled=True) as advance:
            advance(1, 10)
            raise RuntimeError("boom")

    fake_err_console.print("still usable")
    assert "still usable" in buffer.getvalue()


def test_transient_progress_cleans_up_on_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_err_console, buffer = _forced_terminal_console()
    monkeypatch.setattr(ui, "err_console", fake_err_console)

    with pytest.raises(KeyboardInterrupt):
        with transient_progress("Scanning...", enabled=True) as advance:
            advance(1, 10)
            raise KeyboardInterrupt

    fake_err_console.print("still usable")
    assert "still usable" in buffer.getvalue()


def test_two_sequential_progress_blocks_do_not_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rich raises `LiveError` if two `Live` displays (which both `Status` and
    `Progress` are built on) are active on the same console at once. This proves
    the first block is fully torn down before the second starts -- the same
    guarantee that lets a confirmation prompt safely follow a scan's progress
    bar.
    """
    fake_err_console, _ = _forced_terminal_console()
    monkeypatch.setattr(ui, "err_console", fake_err_console)

    with transient_progress("Scanning...", enabled=True) as advance:
        advance(1, 1)

    with transient_spinner("Loading...", enabled=True):
        pass
