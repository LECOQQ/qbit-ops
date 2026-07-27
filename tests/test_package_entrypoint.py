"""Characterize the package/entrypoint contract before any package move.

Section 4 of the migration-prerequisite brief: prove, before a future
`src/qbit_ops/` move, that:

- the console command remains `qbit-ops`;
- the configured entrypoint imports the intended callable;
- importing the core package does not import Textual;
- the TUI import stays deferred until the `tui` command is actually
  requested (not merely until its `--help`, which Click resolves
  without calling the command body at all);
- invoking a non-TUI command never pulls in Textual.

Module-import assertions run in a fresh subprocess (mirroring
`tests/test_errors.py`'s
`test_app_errors_importable_without_typer_or_rich_in_a_fresh_process`),
since other test modules already loaded into this pytest session's
`sys.modules` (e.g. `tests/test_tui_app.py`) would otherwise make an
in-process check meaningless.
"""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def test_console_script_entry_point_targets_app_main_app() -> None:
    """The installed `qbit-ops` script must resolve to `app.main:app`.

    Reads installed distribution metadata (`importlib.metadata`), not
    `pyproject.toml` -- this is the same mechanism `pipx`/an installed
    wheel uses to wire up the console command, independent of the
    source layout.
    """
    entry_points = importlib.metadata.entry_points(group="console_scripts")
    matches = [ep for ep in entry_points if ep.name == "qbit-ops"]

    assert len(matches) == 1, (
        "expected exactly one 'qbit-ops' console_scripts entry point, "
        f"found {[ep.value for ep in matches]}"
    )
    entry_point = matches[0]
    assert entry_point.value == "app.main:app"

    import app.main

    assert entry_point.load() is app.main.app


def test_importing_core_package_never_imports_textual() -> None:
    """`import app` alone must never pull in Textual."""
    script = (
        "import sys\n"
        "import app\n"
        "assert not any(\n"
        "    name == 'textual' or name.startswith('textual.')\n"
        "    for name in sys.modules\n"
        "), sorted(n for n in sys.modules if 'textual' in n)\n"
        "print('ok')\n"
    )
    result = _run(script)

    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_importing_cli_module_defers_tui_and_textual_until_requested() -> None:
    """`import app.main` must not import `app.tui` or Textual by itself.

    A second statement in the same process then imports `app.tui.app`
    directly, proving Textual *is* actually available (the `tui` extra
    is installed in this environment) -- the absence above is a
    deliberate deferral, not a missing dependency masking the real
    test.
    """
    script = (
        "import sys\n"
        "import app.main\n"
        "assert 'app.tui' not in sys.modules, sorted(sys.modules)\n"
        "assert not any(\n"
        "    name == 'textual' or name.startswith('textual.')\n"
        "    for name in sys.modules\n"
        "), sorted(n for n in sys.modules if 'textual' in n)\n"
        "import app.tui.app\n"
        "assert 'app.tui' in sys.modules\n"
        "assert any(\n"
        "    name == 'textual' or name.startswith('textual.')\n"
        "    for name in sys.modules\n"
        ")\n"
        "print('ok')\n"
    )
    result = _run(script)

    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_non_tui_command_invocation_never_imports_textual() -> None:
    """Running an ordinary command must never load Textual as a side effect."""
    script = (
        "import sys\n"
        "from typer.testing import CliRunner\n"
        "from app.main import app\n"
        "result = CliRunner().invoke(app, ['status', '--help'])\n"
        "assert result.exit_code == 0, result.output\n"
        "assert not any(\n"
        "    name == 'textual' or name.startswith('textual.')\n"
        "    for name in sys.modules\n"
        "), sorted(n for n in sys.modules if 'textual' in n)\n"
        "print('ok')\n"
    )
    result = _run(script)

    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_pyproject_console_script_matches_the_installed_entry_point() -> None:
    """Cross-check: `pyproject.toml`'s declared script still matches reality.

    This is the only test in the suite allowed to read `pyproject.toml`
    directly -- it characterizes the *packaging configuration itself*,
    not the runtime version-resolution path (constat A-2), which must
    never read this file (see `tests/test_version_resolution.py`).
    """
    import tomllib

    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    scripts = pyproject["tool"]["poetry"]["scripts"]

    assert scripts == {"qbit-ops": "app.main:app"}
