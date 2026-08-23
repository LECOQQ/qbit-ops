"""`make lint` must report, never rewrite.

`ruff check` honours a `fix` default from `[tool.ruff]` in
`pyproject.toml`: when that default is `true`, plain `ruff check` --
exactly what `make lint` runs, with no `--fix` flag -- silently rewrites
the files it scans and still exits `0`. Since `make lint` feeds `make
check` and `make ci`, that default would mean no fixable Ruff violation
could ever fail CI: it gets corrected on the disposable checkout and the
correction is thrown away with it.

This test drives `ruff check` exactly as the `lint` Makefile target
does -- same config, no `--fix` -- against a throwaway file carrying a
fixable violation (an unused import), and asserts both that the file
survives untouched and that the run reports failure.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"

FIXABLE_VIOLATION = "import os\nimport sys\n\n\ndef f() -> int:\n    return 1\n"


def test_ruff_check_leaves_a_fixable_file_untouched(tmp_path: Path) -> None:
    target = tmp_path / "sample.py"
    target.write_text(FIXABLE_VIOLATION, encoding="utf-8")

    result = subprocess.run(
        [
            "poetry",
            "run",
            "ruff",
            "check",
            "--config",
            str(PYPROJECT),
            str(target),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert target.read_text(encoding="utf-8") == FIXABLE_VIOLATION, (
        "`ruff check` (what `make lint` runs) rewrote the file -- "
        f"stdout was:\n{result.stdout}"
    )
    assert result.returncode != 0, (
        "a fixable violation must fail `ruff check` when it is left "
        f"unfixed, not pass silently:\n{result.stdout}"
    )
