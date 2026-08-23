"""Tests for the AI hygiene gate.

Every case builds a throwaway repository under `tmp_path`: the gate
scans `git ls-files`, so it needs a real index rather than loose files.

The forbidden characters and phrases below carry a pragma on purpose.
A test suite that covers a rule has to contain what the rule refuses,
so it trips the gate unless it opts out line by line -- and a test that
cannot coexist with the rule it covers is not a test.

The pragma exempts the line it sits on, never the fixture content
written from it: the strings below are still scanned in full inside
each throwaway repository, which is what these tests measure.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import check_ai_hygiene as hygiene
import diff_categories
import pytest

EM_DASH = "—"  # ai-hygiene: allow-em-dash
GENERATION_NOTICE = (
    '"""Generated with Claude."""\n'  # ai-hygiene: allow-generation-notice
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.test")
    _git(repo, "config", "user.name", "T")
    return repo


def _track(repo: Path, name: str, content: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _git(repo, "add", name)


def _identifiers(repo: Path) -> list[str]:
    return [rule.identifier for rule, _, _, _ in hygiene.violations(repo)]


# --- red cases ----------------------------------------------------------


def test_an_agent_co_author_is_refused(repo: Path) -> None:
    _track(repo, "notes.md", "Co-Authored-By: Claude Opus 5 <n@a.c>\n")

    assert _identifiers(repo) == ["agent-co-author"]


def test_a_generation_notice_is_refused(repo: Path) -> None:
    _track(repo, "mod.py", GENERATION_NOTICE)

    assert "generation-notice" in _identifiers(repo)


def test_an_em_dash_is_refused_in_versioned_text(repo: Path) -> None:
    _track(repo, "doc.md", f"a sentence {EM_DASH} and its aside\n")

    assert _identifiers(repo) == ["em-dash"]


def test_public_documentation_may_not_cite_the_control_plane(
    repo: Path,
) -> None:
    _track(repo, "docs/GUIDE.md", "See `.agents/MEMORY.md` for details.\n")

    assert "control-plane-leak" in _identifiers(repo)


def test_an_untranslated_french_word_in_src_is_refused(repo: Path) -> None:
    _track(repo, "src/mod.py", '"""The "sélection" slot."""\n')

    assert "french-in-src" in _identifiers(repo)


# --- green cases --------------------------------------------------------


def test_a_human_co_author_stays_legitimate(repo: Path) -> None:
    _track(repo, "notes.md", "Co-Authored-By: Quentin <q@example.test>\n")

    assert _identifiers(repo) == []


def test_source_may_cite_the_control_plane(repo: Path) -> None:
    """The leak rule protects published docs, not the code itself."""
    _track(repo, "src/mod.py", '"""Documented in `.agents/DECISIONS.md`."""\n')

    assert _identifiers(repo) == []


def test_a_binary_file_is_skipped_rather_than_crashing(repo: Path) -> None:
    (repo / "blob.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
    _git(repo, "add", "blob.png")

    assert _identifiers(repo) == []


def test_an_untracked_file_is_out_of_scope(repo: Path) -> None:
    (repo / "loose.md").write_text(f"{EM_DASH}\n", encoding="utf-8")

    assert _identifiers(repo) == []


def test_the_facade_loanword_is_not_french_prose(repo: Path) -> None:
    _track(repo, "src/mod.py", '"""Two façades share one seam."""\n')

    assert _identifiers(repo) == []


def test_a_spec_heading_quoted_on_its_own_line_is_a_citation(
    repo: Path,
) -> None:
    _track(
        repo,
        "src/mod.py",
        '"""See `.agents/features/x/SPEC.md`, "un texte cité ici".\n"""\n',
    )

    assert _identifiers(repo) == []


def test_a_spec_citation_wrapped_across_two_lines_is_still_a_citation(
    repo: Path,
) -> None:
    """The file reference may end one line before the quoted heading
    starts -- torrents.py's real citation wraps exactly this way."""
    _track(
        repo,
        "src/mod.py",
        '"""out of scope (see `.agents/specs/\n'
        'x.md`, "Hors périmètre").\n"""\n',
    )

    assert _identifiers(repo) == []


def test_french_prose_outside_src_is_not_this_rules_concern(
    repo: Path,
) -> None:
    """AGENTS.md only requires English inside `src/`; this rule must
    not reach into `tests/` or `scripts/`."""
    _track(repo, "tests/test_mod.py", '"""The "sélection" slot."""\n')

    assert _identifiers(repo) == []


# --- allowed exceptions -------------------------------------------------


def test_a_pragma_exempts_the_line_it_sits_on(repo: Path) -> None:
    _track(
        repo,
        "glyph.py",
        f'CELL = "{EM_DASH}"  # ai-hygiene: allow-em-dash\n',
    )

    assert _identifiers(repo) == []


def test_a_pragma_exempts_only_its_own_rule(repo: Path) -> None:
    _track(
        repo,
        "notes.md",
        "Co-Authored-By: Claude <n@a.c>  ai-hygiene: allow-em-dash\n",
    )

    assert _identifiers(repo) == ["agent-co-author"]


def test_a_pragma_does_not_exempt_the_rest_of_the_file(repo: Path) -> None:
    _track(
        repo,
        "glyph.py",
        f'CELL = "{EM_DASH}"  # ai-hygiene: allow-em-dash\n'
        f'OTHER = "{EM_DASH}"\n',
    )

    assert _identifiers(repo) == ["em-dash"]


# --- the gate must not flag itself --------------------------------------


def test_this_repository_passes_its_own_gate() -> None:
    root = Path(__file__).resolve().parents[1]

    assert hygiene.violations(root) == []


def test_every_rule_states_a_reason_and_a_remedy() -> None:
    """A rule nobody can explain is a rule nobody can fix."""
    for rule in hygiene.RULES:
        assert rule.reason.strip()
        assert rule.remedy.strip()
        assert rule.category in {"provenance", "generated-artifacts", "style"}


# --- diff categorisation ------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/qbit_core/features/stats.py", "production"),
        ("tests/test_stats.py", "tests"),
        ("docs/COMMANDS.md", "docs"),
        ("README.md", "docs"),
        ("scripts/check_ai_hygiene.py", "tooling"),
        ("Makefile", "tooling"),
        ("demo/compose.yml", "tooling"),
        (".github/workflows/ci.yml", "config"),
        ("pyproject.toml", "config"),
        ("some/unmapped/file.txt", "other"),
    ],
)
def test_every_path_lands_in_exactly_one_category(
    path: str, expected: str
) -> None:
    assert diff_categories.categorize(path) == expected


def test_a_line_is_never_counted_twice() -> None:
    numstat = "10\t2\tsrc/a.py\n5\t1\ttests/test_a.py\n3\t0\tdocs/X.md\n"

    breakdown = diff_categories.breakdown_from_numstat(numstat)
    by_category = breakdown.by_category

    assert breakdown.total.added == 18
    assert sum(t.added for t in by_category.values()) == breakdown.total.added
    assert sum(t.files for t in by_category.values()) == breakdown.total.files


def test_a_binary_file_counts_as_changed_with_no_lines() -> None:
    breakdown = diff_categories.breakdown_from_numstat(
        "-\t-\tdocs/assets/demo.gif\n"
    )

    assert breakdown.total.files == 1
    assert breakdown.total.added == 0
    assert breakdown.by_category["docs"].files == 1
