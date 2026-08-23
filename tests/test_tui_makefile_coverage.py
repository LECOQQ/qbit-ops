"""`make test-tui` must actually run every `pytest.mark.tui` file.

The recipe lists its files by hand rather than by marker: switching it
to `pytest -m tui` was measured (2026-08-23) to change what runs --
+43% wall time (98s -> 147s under xdist) and it would silently drop
`test_tui_cli.py`, `test_tui_security.py` and `test_tui_state.py`,
which the recipe deliberately also runs even though they carry no
`tui` marker (they are fast, non-Pilot checks named in the target's own
help text -- "security" is `test_tui_security.py`). A behaviour change
of that size belongs to a decision, not a refactor.

So the list stays hand-written, and this gate keeps it honest instead:
every file carrying the module-level `pytestmark = pytest.mark.tui`
must appear in the `test-tui` recipe. It does not require the reverse
-- the recipe may run extra, unmarked files -- only that a marked file
can never again go silently missing the way five did.
"""

from __future__ import annotations

import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
MAKEFILE = TESTS_DIR.parent / "Makefile"

TUI_PYTESTMARK = re.compile(r"^pytestmark\s*=\s*pytest\.mark\.tui\s*$", re.M)

RECIPE_LINE = re.compile(r"^test-tui:.*\n(?:\t.*\n)*", re.M)
FILE_TOKEN = re.compile(r"tests/test_\w+\.py")


def _files_marked_tui() -> set[str]:
    marked = set()
    for path in TESTS_DIR.glob("test_*.py"):
        text = path.read_text(encoding="utf-8")
        if TUI_PYTESTMARK.search(text):
            marked.add(f"tests/{path.name}")
    return marked


def _files_in_test_tui_recipe() -> set[str]:
    text = MAKEFILE.read_text(encoding="utf-8")
    match = RECIPE_LINE.search(text)
    assert match, "no `test-tui:` recipe found in the Makefile"
    return set(FILE_TOKEN.findall(match.group(0)))


def test_every_tui_marked_file_is_covered_by_make_test_tui() -> None:
    marked = _files_marked_tui()
    recipe = _files_in_test_tui_recipe()

    assert marked, (
        "no file carries `pytestmark = pytest.mark.tui` -- "
        "the detector itself is broken"
    )
    missing = marked - recipe
    assert not missing, (
        f"`make test-tui` no longer runs: {sorted(missing)} -- "
        "add them to the `test-tui` recipe in the Makefile"
    )
