"""Guard against reintroducing a dead `assert ... or True` anywhere in the
tracked repository.

A line-based regex (requiring `assert ... or True` on the *same
physical line*) does not detect a multiline form:

    assert (
        expression
        or True
    )

This module uses an AST-based non-vacuity check instead: it walks the
real parsed `ast.Assert` nodes, so it is immune to formatting
(single-line/multiline), cannot match a comment, a string, or a
docstring example (those never become `ast.Assert` nodes at all), and
only fires when Python's own AST makes the vacuity unambiguous -- a
boolean-`or` expression, at any nesting/line-wrapping, where at least
one operand is a literal constant whose truthiness is statically
`True` (or the whole test is itself such a constant).
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _tracked_python_files() -> list[Path]:
    """Return every `*.py` file tracked by git, repo-relative paths resolved.

    Uses `git ls-files` (not `rglob`) so the guard inspects exactly what
    is committed -- an untracked scratch file can never make this test
    fail or silently skip a real offender, and `__pycache__` is never
    tracked so it needs no separate exclusion.
    """
    result = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [_REPO_ROOT / line for line in result.stdout.splitlines() if line]


def _is_truthy_constant(node: ast.expr) -> bool:
    """Return whether `node` is a literal constant with unambiguous truthy
    value.

    Only `ast.Constant` qualifies -- a name, call, comparison, or any
    other expression is never treated as "constant" here, however
    predictable it might look; that would risk rejecting a valid
    assertion that merely contains `or`.
    """
    if not isinstance(node, ast.Constant):
        return False
    try:
        return bool(node.value)
    except TypeError:
        return False


def _assert_is_vacuous(node: ast.Assert) -> bool:
    test = node.test
    if _is_truthy_constant(test):
        return True
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        # Any operand that is an unambiguous truthy constant makes the
        # whole `or` chain evaluate truthy regardless of the other
        # operands' values (short-circuit reaches it and it can never
        # be falsy) -- this is what makes the assertion vacuous, not
        # merely "contains or".
        return any(_is_truthy_constant(value) for value in test.values)
    return False


def find_dead_assertions(source: str, filename: str = "<string>") -> list[int]:
    """Return the line number of every vacuous `assert` in `source`.

    Operates on the parsed AST, never on raw text -- a comment, a
    string literal, or a docstring code example containing the text
    `or True` is never seen as an `ast.Assert` node and therefore
    never flagged.
    """
    tree = ast.parse(source, filename=filename)
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Assert) and _assert_is_vacuous(node)
    )


def test_dead_assertion_discovery_covers_tracked_files() -> None:
    """Guard the discovery mechanism itself: if it ever found zero files,
    the whole-repository check below would vacuously pass."""
    files = _tracked_python_files()
    assert files
    assert any(path.name == "doctor.py" for path in files)


def test_no_dead_assertions_anywhere_in_the_tracked_repository() -> None:
    offenders = []
    for path in _tracked_python_files():
        source = path.read_text(encoding="utf-8")
        for line_number in find_dead_assertions(source, filename=str(path)):
            offenders.append(f"{path.relative_to(_REPO_ROOT)}:{line_number}")

    assert not offenders, f"vacuous 'assert' found: {offenders}"


# --- Focused unit tests for accepted and rejected expressions ------------

_VACUOUS_SAMPLES = {
    "single_line_or_true": "assert expression or True\n",
    "multiline_parenthesized_or_true": (
        "assert (\n" "    expression\n" "    or True\n" ")\n"
    ),
    "true_or_expression": "assert True or expression\n",
    "bare_true": "assert True\n",
    "truthy_string_constant": 'assert "always truthy" or expression\n',
    "truthy_number_constant": "assert 1 or expression\n",
    "or_chain_with_extra_operand": "assert b or True or c\n",
}

_VALID_SAMPLES = {
    "plain_assertion": "assert expression\n",
    "comparison_with_or": "assert x == 1 or x == 2\n",
    "or_between_two_names": "assert left or right\n",
    "falsy_constant_operand": "assert 0 or expression\n",
    "empty_string_operand": 'assert "" or expression\n',
    "membership_check_with_or": "assert status in (1, 2) or status is None\n",
    "comment_mentioning_or_true": "# assert x or True\nassert expression\n",
    "docstring_mentioning_or_true": (
        '"""Example: `assert x or True` is forbidden."""\n'
        "assert expression\n"
    ),
    "string_literal_mentioning_or_true": (
        'text = "assert x or True"\nassert expression\n'
    ),
}


@pytest.mark.parametrize("name", sorted(_VACUOUS_SAMPLES))
def test_vacuous_samples_are_detected(name: str) -> None:
    source = _VACUOUS_SAMPLES[name]
    assert find_dead_assertions(source) == [1], (name, source)


@pytest.mark.parametrize("name", sorted(_VALID_SAMPLES))
def test_valid_samples_are_not_flagged(name: str) -> None:
    source = _VALID_SAMPLES[name]
    assert find_dead_assertions(source) == [], (name, source)


def test_multiline_or_true_form_is_detected(
    tmp_path,
) -> None:
    """A multiline `assert (\\n ... \\n or True\\n)` -- the form a
    line-based regex guard would miss -- must be caught by the
    AST-based guard."""
    sabotage_file = tmp_path / "sabotage_dead_assertion.py"
    sabotage_file.write_text(
        "def test_example() -> None:\n"
        "    observed = 999\n"
        "    assert (\n"
        "        observed == 1\n"
        "        or True\n"
        "    )\n",
        encoding="utf-8",
    )

    offenders = find_dead_assertions(
        sabotage_file.read_text(encoding="utf-8"), filename=str(sabotage_file)
    )

    assert offenders == [3]
