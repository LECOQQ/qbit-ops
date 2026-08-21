#!/usr/bin/env python3
"""Check that Markdown links and repo-anchored path references resolve.

Two independent checks, both hard failures:

1. Markdown links and reference definitions (`[text](target)`,
   `[id]: target`) whose target is local must resolve relative to the
   file that contains them.
2. Backtick-quoted paths *anchored at a repository root directory*
   (`docs/...`, `src/...`, `tests/...`) must exist.

The anchoring rule in (2) is what keeps this usable. Documentation is
full of contextual shorthand -- `shared/selection.py`, `conftest.py`,
`REPORT.md` -- which names a real file without naming a real path from
the repository root. Requiring the first segment to be an actual
top-level directory separates "this path is wrong" from "this is a
shorthand for the reader".
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SKIPPED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        ".worktrees",
        "__pycache__",
        "node_modules",
        ".ruff_cache",
        ".pytest_cache",
        ".mypy_cache",
        "dist",
        "build",
        "htmlcov",
        # Frozen records, not live documentation. A delivered feature's
        # SPEC and a workflow-history entry cite the world as it was;
        # this check enforces the world as it is. Holding an archive to
        # it means every deletion rewrites history to satisfy a linter,
        # which is the opposite of what an archive is for.
        "features",
        "workflow-history",
    }
)

# A backticked token is only treated as a path when it carries one of
# these suffixes, or ends in `/`. Without this, prose like
# `qbit_core.features.status` would be read as a filename.
PATH_SUFFIXES = frozenset(
    {
        ".md",
        ".py",
        ".toml",
        ".json",
        ".yml",
        ".yaml",
        ".cfg",
        ".txt",
        ".lock",
        ".sh",
        ".env",
        ".gif",
        ".png",
    }
)

# Placeholders (`<feature>`), globs and shell syntax mean the token is a
# template or a command, not a concrete path.
NON_LITERAL_CHARS = frozenset("<>*{}$()|\"'")

# An ellipsis stands in for "and so on" (`docs/...`), never a real path.
ELLIPSIS = "..."

MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
MARKDOWN_REF_DEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s+(\S+)", re.MULTILINE)
BACKTICK_TOKEN = re.compile(r"`([^`\n]+)`")

# An unquoted repository path in prose. The file extension is what keeps
# this from matching an ordinary phrase containing a slash.
BARE_PATH = re.compile(r"(?<![\w`/.-])([a-z_]+(?:/[\w.-]+)+\.[a-zA-Z]{2,4})")

IGNORE_DIRECTIVE = "<!-- doc-links: ignore-next-line -->"
# The same escape hatch, spelled for a language without HTML comments.
IGNORE_DIRECTIVE_PY = "# doc-links: ignore-next-line"


class DocLinkError(Exception):
    """Raised when the check cannot run at all (never for findings)."""


@dataclass(frozen=True)
class Finding:
    """One unresolved reference, located precisely enough to fix."""

    path: Path
    line: int
    kind: str
    target: str

    def render(self, root: Path) -> str:
        relative = self.path.relative_to(root)
        return f"{relative}:{self.line}: {self.kind} -> {self.target}"


def _is_git_ignored(path: Path, root: Path) -> bool:
    """Report whether Git ignores `path`.

    An ignored target is a generated artifact (a recorded demo GIF, a
    build output), not a broken reference: the documentation may
    legitimately name a file that only exists after a build step.
    Returns `False` when Git is unavailable rather than silently
    suppressing findings.
    """
    try:
        completed = subprocess.run(
            ["git", "check-ignore", "-q", "--", str(path)],
            cwd=root,
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    return completed.returncode == 0


def _markdown_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.md")
        if not SKIPPED_DIRS.intersection(path.relative_to(root).parts)
    )


def _python_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.py")
        if not SKIPPED_DIRS.intersection(path.relative_to(root).parts)
    )


def _python_prose(text: str) -> str:
    """Blank out everything in `text` that is not a comment or docstring.

    Line numbers are preserved so a finding still points at the right
    line. Code is blanked because a path inside a string literal is data
    the checker has no business judging -- only prose makes a claim.
    """
    kept: list[str] = [""] * (len(text.splitlines()) + 1)

    def keep(start_line: int, block: str) -> None:
        for offset, line in enumerate(block.splitlines()):
            index = start_line + offset
            if index < len(kept):
                kept[index] = line

    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for token in tokens:
            if token.type == tokenize.COMMENT:
                keep(token.start[0] - 1, token.string)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return "\n".join(kept)

    carriers = (
        ast.Module,
        ast.ClassDef,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
    )
    for node in ast.walk(tree):
        if not isinstance(node, carriers):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if not isinstance(first, ast.Expr):
            continue
        value = first.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            segment = ast.get_source_segment(text, first) or ""
            keep(first.lineno - 1, segment)

    return "\n".join(kept)


def _check_bare_references(
    text: str, source: Path, root: Path, root_dirs: frozenset[str]
) -> list[Finding]:
    """Flag an unquoted repository path that does not resolve.

    Markdown states its paths in backticks; code comments often do not
    (`see docs/COMPATIBILITY.md` reads naturally in prose). Requiring a
    file extension keeps this from firing on ordinary words.
    """
    findings: list[Finding] = []
    skipped = _ignored_lines(text)

    for match in BARE_PATH.finditer(text):
        token = match.group(1)
        if token.split("/")[0] not in root_dirs:
            continue
        line = _line_of(text, match.start())
        if line in skipped:
            continue
        candidate = root / _strip_locator(token)
        if _resolves(candidate) or _is_git_ignored(candidate, root):
            continue
        findings.append(Finding(source, line, "dead reference", token))
    return findings


def _root_directories(root: Path) -> frozenset[str]:
    return frozenset(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and entry.name not in SKIPPED_DIRS
    )


def _strip_locator(token: str) -> str:
    """Drop a pytest node id or a line-number locator from a reference.

    The two forms below are invented for the example, so this check
    would flag its own docstring without the directive:

    # doc-links: ignore-next-line
    `tests/support.py::FakeQbitClient` and `tests/test_x.py:109` both
    name a real file; only the part before the locator is a path.
    """
    token = token.split("::", 1)[0]
    head, separator, tail = token.partition(":")
    if separator and tail and not tail[0].isalpha():
        return head
    return token


def _resolves(candidate: Path) -> bool:
    if candidate.exists():
        return True
    # `pkg/_module.Attribute` names an attribute of `pkg/_module.py`.
    stem = candidate.with_suffix("")
    return bool(candidate.suffix) and (stem.with_suffix(".py")).exists()


def _ignored_lines(text: str) -> frozenset[int]:
    return frozenset(
        number + 2
        for number, line in enumerate(text.splitlines())
        if line.strip() in (IGNORE_DIRECTIVE, IGNORE_DIRECTIVE_PY)
    )


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _fenced_line_numbers(text: str) -> frozenset[int]:
    """Return the line numbers inside fenced code blocks.

    A fenced block is illustrative -- a command, a tree, a JSON schema --
    so a path inside it is an example, not a reference to resolve.
    """
    fenced: set[int] = set()
    inside = False
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            inside = not inside
            fenced.add(number)
            continue
        if inside:
            fenced.add(number)
    return frozenset(fenced)


def _check_links(text: str, source: Path, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    skipped = _ignored_lines(text)
    targets = [
        (match.group(1), match.start(1))
        for match in MARKDOWN_LINK.finditer(text)
    ]
    targets += [
        (match.group(1), match.start(1))
        for match in MARKDOWN_REF_DEF.finditer(text)
    ]

    for raw, offset in targets:
        target = raw.split()[0].strip().strip("<>")
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        line = _line_of(text, offset)
        if line in skipped:
            continue
        resolved = (source.parent / target.split("#")[0]).resolve()
        if resolved.exists() or _is_git_ignored(resolved, root):
            continue
        findings.append(Finding(source, line, "broken link", target))
    return findings


def _check_references(
    text: str, source: Path, root: Path, root_dirs: frozenset[str]
) -> list[Finding]:
    findings: list[Finding] = []
    skipped = _ignored_lines(text)
    fenced = _fenced_line_numbers(text)

    for match in BACKTICK_TOKEN.finditer(text):
        token = match.group(1).strip()
        if " " in token or NON_LITERAL_CHARS.intersection(token):
            continue
        if "/" not in token or ELLIPSIS in token:
            continue

        candidate_text = _strip_locator(token).rstrip("/")
        if not candidate_text or candidate_text.split("/")[0] not in root_dirs:
            continue
        line = _line_of(text, match.start())
        if line in skipped or line in fenced:
            continue

        candidate = root / candidate_text
        if _resolves(candidate) or _is_git_ignored(candidate, root):
            continue
        findings.append(Finding(source, line, "dead reference", token))
    return findings


def check(root: Path) -> list[Finding]:
    """Scan Markdown prose and Python prose under `root`.

    Python files are scanned because a reference inside a comment or a
    docstring rots exactly like one in Markdown, and was covered by
    nothing: a documentation pass that renames a section leaves the code
    citing a heading that no longer exists.
    """
    if not root.is_dir():
        raise DocLinkError(f"{root} is not a directory.")

    root_dirs = _root_directories(root)
    findings: list[Finding] = []
    for source in _markdown_files(root):
        text = source.read_text(encoding="utf-8", errors="replace")
        findings.extend(_check_links(text, source, root))
        findings.extend(_check_references(text, source, root, root_dirs))

    for source in _python_files(root):
        prose = _python_prose(
            source.read_text(encoding="utf-8", errors="replace")
        )
        findings.extend(_check_references(prose, source, root, root_dirs))
        findings.extend(_check_bare_references(prose, source, root, root_dirs))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root to scan (default: this repository).",
    )
    args = parser.parse_args(argv)

    try:
        findings = check(args.root.resolve())
    except DocLinkError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if findings:
        for finding in findings:
            print(finding.render(args.root.resolve()), file=sys.stderr)
        print(
            f"\nERROR: {len(findings)} unresolved documentation reference(s). "
            f"Fix the path, or mark a deliberate one with "
            f"'{IGNORE_DIRECTIVE}' on the preceding line.",
            file=sys.stderr,
        )
        return 1

    print("OK: every Markdown link and repo-anchored reference resolves.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
