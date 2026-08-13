"""Lock the Makefile's categorised help contract.

`make help` only lists targets carrying a `## <category>: ` tag, so an
untagged target is invisible rather than broken -- a failure mode no
other check would catch. These tests make the tag mandatory.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MAKEFILE = Path(__file__).resolve().parent.parent / "Makefile"

CATEGORIES = ("dev", "qa", "use", "diag")

DOCUMENTED_TARGET = re.compile(
    r"^(?P<name>[a-zA-Z0-9_-]+):[^\n#]*## (?P<help>.+)$"
)
HELP_SECTION_CALL = re.compile(
    r"\$\(call help_section,[^,]+,(?P<category>\w+)\)"
)


@pytest.fixture(scope="module")
def makefile_text() -> str:
    return MAKEFILE.read_text(encoding="utf-8")


def _documented_targets(text: str) -> dict[str, str]:
    targets: dict[str, str] = {}
    for line in text.splitlines():
        match = DOCUMENTED_TARGET.match(line)
        if match:
            targets[match.group("name")] = match.group("help")
    return targets


def test_every_documented_target_declares_a_known_category(
    makefile_text: str,
) -> None:
    untagged = {
        name: help_text
        for name, help_text in _documented_targets(makefile_text).items()
        if not help_text.startswith(tuple(f"{tag}: " for tag in CATEGORIES))
    }
    assert not untagged, (
        f"These targets would not appear in `make help`: {sorted(untagged)}. "
        f"Prefix the help text with one of {CATEGORIES}, e.g. '## qa: ...'."
    )


def test_help_renders_every_category_used_by_a_target(
    makefile_text: str,
) -> None:
    rendered = set(HELP_SECTION_CALL.findall(makefile_text))
    used = {
        help_text.split(":", 1)[0]
        for help_text in _documented_targets(makefile_text).values()
        if help_text.split(":", 1)[0] in CATEGORIES
    }
    assert used <= rendered, (
        "Categories used by targets but never rendered: "
        f"{sorted(used - rendered)}"
    )


@pytest.mark.parametrize(
    "target",
    ["check", "check-fast", "check-docs", "worktree-new", "worktree-clean"],
)
def test_key_targets_stay_documented(makefile_text: str, target: str) -> None:
    assert target in _documented_targets(makefile_text)


def test_phony_covers_every_documented_target(makefile_text: str) -> None:
    phony_line = next(
        line
        for line in makefile_text.splitlines()
        if line.startswith(".PHONY:")
    )
    declared = set(phony_line.removeprefix(".PHONY:").split())
    # `.sync-stamp` is a real file target, never phony.
    documented = set(_documented_targets(makefile_text)) - {".sync-stamp"}
    assert (
        documented <= declared
    ), f"Documented but not .PHONY: {sorted(documented - declared)}"
