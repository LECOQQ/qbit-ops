"""Guard the tag/token normalization rule against re-duplication.

`qbit_core.shared.selection.normalize_tokens` is the one strip/drop-
blank/de-duplicate implementation. The CLI's `validate_tag_names` and
the TUI's `_normalize_tags` both delegate to it instead of
reimplementing the rule locally -- a third, private copy already lived
in `qbit_core.features.torrents` before this consolidation, and a
fourth (`qbit_ops.tui.filter_form._csv`) exists for the broader
CSV-field case; see `.agents/DECISIONS.md` for that follow-up.
"""

from __future__ import annotations

import ast
from pathlib import Path

import qbit_ops.cli.validation
import qbit_ops.tui.value_form


def _reimplements_strip_dedupe(path: Path) -> bool:
    """Whether `path` calls `dict.fromkeys` itself instead of delegating.

    `dict.fromkeys` is the distinctive call the normalization rule is
    built from (see `normalize_tokens`); a module that calls it directly
    has reimplemented the rule rather than reused it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "fromkeys"
        for node in ast.walk(tree)
    )


def test_cli_tag_validation_delegates_to_the_shared_normalizer() -> None:
    path = Path(qbit_ops.cli.validation.__file__)
    assert not _reimplements_strip_dedupe(path), (
        f"{path} reimplements strip/dedupe locally instead of calling "
        "qbit_core.shared.selection.normalize_tokens"
    )


def test_tui_tag_normalization_delegates_to_the_shared_normalizer() -> None:
    path = Path(qbit_ops.tui.value_form.__file__)
    assert not _reimplements_strip_dedupe(path), (
        f"{path} reimplements strip/dedupe locally instead of calling "
        "qbit_core.shared.selection.normalize_tokens"
    )


def test_cli_and_tui_normalize_the_same_raw_tag_list_identically() -> None:
    raw_values = [" foo ", "bar", "", "foo", "  ", "baz "]
    cli_result = qbit_ops.cli.validation.validate_tag_names(raw_values)
    tui_result = qbit_ops.tui.value_form._normalize_tags(",".join(raw_values))
    assert cli_result == tui_result == ("foo", "bar", "baz")
