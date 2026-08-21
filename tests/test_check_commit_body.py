"""Test the commit-body checker's wrap rule.

Nothing reflows a commit body: `git log`, `git blame` and a forge show
it exactly as written. A line that stops early is therefore a ragged
block everywhere it is read, and the check is exact -- it asks whether
the continuation's first word would have fitted, never whether the
wording is good.

Length stays unjudged, and that is a decision rather than a gap: see
`AGENTS.md`, "un bullet dit ce qui change, pas pourquoi".
"""

import pytest

from scripts.check_commit_body import WRAP_LIMIT, early_wrap, violation


def _message(*body: str) -> str:
    return "feat(scope): a subject\n\n" + "\n".join(body) + "\n"


# --- The wrap fires only when the word actually fitted --------------------


def test_a_line_that_stopped_early_is_reported() -> None:
    filler = "- " + "x" * 30
    assert len(filler) + 1 + len("is") <= WRAP_LIMIT

    problem = early_wrap(_message(filler, "  is the rest"))

    assert problem is not None
    assert "wraps early" in problem
    assert "'is'" in problem


def test_a_line_that_could_not_take_the_next_word_is_accepted() -> None:
    """The exact boundary, not a margin: filling to the limit is the
    goal, so a line one character short of taking the next word is
    correctly wrapped."""
    word = "seventy"
    filler = "- " + "x" * (WRAP_LIMIT - len(word) - 2)
    assert len(filler) + 1 + len(word) == WRAP_LIMIT + 1

    assert early_wrap(_message(filler, f"  {word} more")) is None


def test_a_single_line_bullet_has_nothing_to_wrap() -> None:
    assert early_wrap(_message("- one line", "- another")) is None


def test_a_body_that_is_only_a_subject_is_accepted() -> None:
    assert early_wrap("feat(scope): a subject\n") is None


# --- What the rule must not reach ----------------------------------------


def test_a_trailer_is_never_measured() -> None:
    """`BREAKING CHANGE:` is exempted from the bullet rule already; the
    wrap rule must not re-impose a shape on it through the back door."""
    body = ("- a bullet", "", "BREAKING CHANGE: short", "  and its rest")

    assert early_wrap(_message(*body)) is None


def test_the_next_bullet_is_not_a_continuation() -> None:
    """Two bullets in a row are two lines by design, however short the
    first one is."""
    assert early_wrap(_message("- ab", "- cd")) is None


# --- The two rules stay independent ---------------------------------------


def test_prose_is_caught_by_the_other_rule_not_this_one() -> None:
    prose = "feat(scope): a subject\n\nA paragraph about the change.\n"

    assert violation(prose) is not None
    assert early_wrap(prose) is None


@pytest.mark.parametrize("limit", [WRAP_LIMIT])
def test_the_limit_is_the_documented_one(limit: int) -> None:
    assert limit == 72
