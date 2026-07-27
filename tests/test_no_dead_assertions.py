"""Guard against reintroducing a dead `assert ... or True` (F-10).

The independent review found three such assertions (in
`test_captured_container_payloads.py` and twice in `test_tui_app.py`);
all three were removed and replaced with meaningful checks. This is a
permanent regression guard, not a one-off fix -- item 17 of the phase
spec explicitly requires proving reintroduction is caught.
"""

from __future__ import annotations

import re
from pathlib import Path

_DEAD_ASSERT_PATTERN = re.compile(r"\bor True\b")
_THIS_FILE = Path(__file__).resolve()
_SEARCH_ROOTS = (Path(__file__).parent.parent / "src", Path(__file__).parent)


def test_no_dead_or_true_assertions_anywhere_in_src_or_tests() -> None:
    offenders = []
    for root in _SEARCH_ROOTS:
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts or path.resolve() == _THIS_FILE:
                continue
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                stripped = line.strip()
                if stripped.startswith(
                    "assert "
                ) and _DEAD_ASSERT_PATTERN.search(stripped):
                    offenders.append(f"{path}:{line_number}: {stripped}")

    assert not offenders, f"dead 'assert ... or True' found: {offenders}"
