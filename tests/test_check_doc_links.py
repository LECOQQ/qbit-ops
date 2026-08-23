"""Tests for scripts/check_doc_links.py.

Every case builds a throwaway tree under `tmp_path`, never the
repository's own Markdown, so this suite cannot depend on -- or be
broken by -- real documentation content.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import check_doc_links as cdl
import pytest


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A minimal repository shaped like qbit-ops: docs/, src/, tests/."""
    for directory in ("docs", "src", "tests"):
        (tmp_path / directory).mkdir()
    (tmp_path / "docs" / "REAL.md").write_text("# real", encoding="utf-8")
    (tmp_path / "src" / "module.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "PHILOSOPHY.md").write_text("# root doc", encoding="utf-8")
    return tmp_path


def _write(repo: Path, name: str, body: str) -> None:
    (repo / name).write_text(body, encoding="utf-8")


def test_a_resolvable_tree_reports_nothing(repo: Path) -> None:
    _write(repo, "README.md", "See [real](docs/REAL.md) and `src/module.py`.")

    assert cdl.check(repo) == []


def test_a_broken_relative_link_is_reported(repo: Path) -> None:
    _write(repo, "README.md", "See [gone](docs/GONE.md).")

    findings = cdl.check(repo)

    assert [(f.kind, f.target) for f in findings] == [
        ("broken link", "docs/GONE.md")
    ]


def test_a_link_resolves_relative_to_its_own_file(repo: Path) -> None:
    (repo / "docs" / "sub").mkdir()
    _write(repo, "docs/sub/PAGE.md", "Back to [real](../REAL.md).")

    assert cdl.check(repo) == []


def test_a_dead_repo_anchored_reference_is_reported(repo: Path) -> None:
    _write(repo, "README.md", "The rule lives in `docs/TESTING.md`.")

    findings = cdl.check(repo)

    assert [(f.kind, f.target) for f in findings] == [
        ("dead reference", "docs/TESTING.md")
    ]


def test_a_section_citation_matching_a_real_section_passes(repo: Path) -> None:
    _write(repo, "docs/SECTIONED.md", "# Title\n\n## §5.2 Reserves\n")
    _write(repo, "README.md", "See `docs/SECTIONED.md` §5.2 for detail.")

    assert cdl.check(repo) == []


def test_a_dead_section_citation_is_reported(repo: Path) -> None:
    """The defect this extension exists for: a doc trimmed down to
    fewer sections leaves a citation pointing at a section that is no
    longer there, even though the file itself still resolves."""
    _write(repo, "docs/SECTIONED.md", "# Title\n\nNo numbered sections.\n")
    _write(repo, "README.md", "See `docs/SECTIONED.md` §5.2 for detail.")

    findings = cdl.check(repo)

    assert [(f.kind, f.target) for f in findings] == [
        ("dead section citation", "docs/SECTIONED.md §5.2")
    ]


def test_a_chained_section_citation_flags_only_the_missing_half(
    repo: Path,
) -> None:
    _write(repo, "docs/SECTIONED.md", "# Title\n\n## §1 Kept\n")
    _write(repo, "README.md", "See `docs/SECTIONED.md` §1/§9 for detail.")

    findings = cdl.check(repo)

    assert [(f.kind, f.target) for f in findings] == [
        ("dead section citation", "docs/SECTIONED.md §9")
    ]


def test_a_dead_section_citation_in_a_docstring_is_caught(
    repo: Path,
) -> None:
    _write(repo, "docs/SECTIONED.md", "# Title\n\nNo numbered sections.\n")
    _write(
        repo,
        "src/mod.py",
        '"""See `docs/SECTIONED.md` §9 for the rules."""\n',
    )

    findings = cdl.check(repo)

    assert [(f.kind, f.target) for f in findings] == [
        ("dead section citation", "docs/SECTIONED.md §9")
    ]


def test_contextual_shorthand_is_not_a_reference(repo: Path) -> None:
    """`shared/selection.py` names a file without naming a repo path."""
    _write(repo, "README.md", "See `shared/selection.py` and `conftest.py`.")

    assert cdl.check(repo) == []


def test_a_pytest_node_id_resolves_to_its_file(repo: Path) -> None:
    _write(repo, "README.md", "See `src/module.py::SomeClass`.")

    assert cdl.check(repo) == []


def test_a_line_locator_resolves_to_its_file(repo: Path) -> None:
    _write(repo, "README.md", "See `src/module.py:42` and `src/module.py:1-9`.")

    assert cdl.check(repo) == []


def test_a_dotted_attribute_resolves_to_its_module(repo: Path) -> None:
    _write(repo, "README.md", "See `src/module.SomeAttribute`.")

    assert cdl.check(repo) == []


def test_placeholders_and_globs_are_never_treated_as_paths(repo: Path) -> None:
    _write(repo, "README.md", "Use `docs/<feature>.md` and `docs/*.md`.")

    assert cdl.check(repo) == []


def test_an_ellipsis_stands_for_and_so_on_not_a_path(repo: Path) -> None:
    _write(repo, "README.md", "Anchored at `docs/...` or `src/...`.")

    assert cdl.check(repo) == []


def test_fenced_code_blocks_are_illustrative_not_referential(
    repo: Path,
) -> None:
    _write(
        repo,
        "README.md",
        "```\n`docs/EXAMPLE.md`\n```\n",
    )

    assert cdl.check(repo) == []


def test_an_explicit_ignore_directive_suppresses_the_next_line(
    repo: Path,
) -> None:
    _write(
        repo,
        "README.md",
        f"{cdl.IGNORE_DIRECTIVE}\nHistorically `docs/REMOVED.md`.\n",
    )

    assert cdl.check(repo) == []


def test_external_targets_are_never_checked(repo: Path) -> None:
    _write(
        repo,
        "README.md",
        "[web](https://example.test/x) [mail](mailto:a@b.test) [anchor](#top)",
    )

    assert cdl.check(repo) == []


def test_a_gitignored_target_is_a_build_artifact_not_a_dead_reference(
    repo: Path,
) -> None:
    """A recorded GIF only exists after `make demo-record`; naming it is
    correct even when the file is absent."""
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / ".gitignore").write_text("demo/output/\n", encoding="utf-8")
    (repo / "demo").mkdir()
    _write(repo, "README.md", "Recorded to `demo/output/demo.gif`.")

    assert cdl.check(repo) == []


def test_findings_carry_a_usable_location(repo: Path) -> None:
    _write(repo, "README.md", "intro\n\nSee [gone](docs/GONE.md).\n")

    (finding,) = cdl.check(repo)

    assert finding.line == 3
    assert finding.render(repo) == "README.md:3: broken link -> docs/GONE.md"


def test_main_succeeds_on_a_clean_tree(repo: Path, capsys) -> None:
    _write(repo, "README.md", "See [real](docs/REAL.md).")

    assert cdl.main(["--root", str(repo)]) == 0
    assert "OK" in capsys.readouterr().out


def test_main_fails_and_lists_every_finding(repo: Path, capsys) -> None:
    _write(repo, "README.md", "[a](docs/A.md) [b](docs/B.md)")

    assert cdl.main(["--root", str(repo)]) == 1
    stderr = capsys.readouterr().err
    assert "docs/A.md" in stderr
    assert "docs/B.md" in stderr


def test_a_missing_root_is_a_setup_error_not_a_finding(
    tmp_path: Path, capsys
) -> None:
    assert cdl.main(["--root", str(tmp_path / "absent")]) == 2
    assert "ERROR" in capsys.readouterr().err


# --- Python prose ---------------------------------------------------------


def test_a_dead_reference_in_a_docstring_is_caught(repo: Path) -> None:
    """The defect this extension exists for: a documentation pass renames
    a file, and the code still cites the old name."""
    _write(repo, "src/mod.py", '"""See docs/GONE.md for the rules."""\n')

    findings = cdl.check(repo)

    assert [f.target for f in findings] == ["docs/GONE.md"]


def test_a_dead_reference_in_a_comment_is_caught(repo: Path) -> None:
    _write(repo, "src/mod.py", "# see `docs/GONE.md`\nx = 1\n")

    assert [f.target for f in cdl.check(repo)] == ["docs/GONE.md"]


def test_a_resolving_reference_in_prose_passes(repo: Path) -> None:
    _write(repo, "src/mod.py", '"""See docs/REAL.md and `src/module.py`."""\n')

    assert cdl.check(repo) == []


def test_code_is_not_prose(repo: Path) -> None:
    """A path inside a string literal is data, not a claim about the
    repository -- judging it would break any module that builds paths."""
    _write(repo, "src/mod.py", 'TARGET = "docs/GONE.md"\n')

    assert cdl.check(repo) == []


def test_the_python_directive_exempts_the_next_line(repo: Path) -> None:
    _write(
        repo,
        "src/mod.py",
        "# doc-links: ignore-next-line\n# see docs/GONE.md\nx = 1\n",
    )

    assert cdl.check(repo) == []


def test_a_file_that_does_not_parse_is_skipped_rather_than_crashing(
    repo: Path,
) -> None:
    _write(repo, "src/broken.py", "def (\n")

    cdl.check(repo)
