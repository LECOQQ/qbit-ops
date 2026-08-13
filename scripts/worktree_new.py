#!/usr/bin/env python3
"""Create a dedicated branch, worktree and virtualenv for a feature.

The agent control-plane (`AGENTS.md`, `.agents/`) is gitignored, so a
fresh worktree does not contain it. This script links it in -- but only
after Git confirms each target is ignored. Replacing a *tracked* file
with a symlink produces a typechange that can reach a commit, which is
exactly the accident this script exists to prevent.

The worktree never shares the main `.venv`: the project is installed
there in editable mode pointing at the main checkout's `src/`, so the
tests would silently exercise the wrong code.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# Control-plane entries linked into a new worktree. Each one is still
# verified ignored at run time; this list is a convenience, never the
# safety mechanism.
LINKED_ENTRIES = ("AGENTS.md", "CLAUDE.md", ".agents")


class WorktreeError(Exception):
    """Raised for any actionable setup failure."""


def _git(
    root: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=check,
    )


def _validate_slug(slug: str) -> str:
    if not SLUG_PATTERN.match(slug):
        raise WorktreeError(
            f"Invalid feature slug {slug!r}. Use lowercase letters, digits "
            "and hyphens, starting with a letter or digit (e.g. "
            "'version', 'search-regex')."
        )
    return slug


def _is_ignored(root: Path, entry: str) -> bool:
    completed = _git(root, "check-ignore", "-q", "--", entry, check=False)
    return completed.returncode == 0


def _is_tracked(root: Path, entry: str) -> bool:
    completed = _git(
        root, "ls-files", "--error-unmatch", "--", entry, check=False
    )
    return completed.returncode == 0


def _link_control_plane(root: Path, worktree: Path) -> list[str]:
    """Symlink the gitignored control-plane into `worktree`.

    Refuses any entry Git tracks, and any entry Git does not confirm as
    ignored. Returns the entries actually linked.
    """
    linked: list[str] = []
    for entry in LINKED_ENTRIES:
        source = root / entry
        if not source.exists():
            continue
        if _is_tracked(root, entry):
            print(
                f"[SKIP] {entry} is tracked by Git -- refusing to symlink.",
                file=sys.stderr,
            )
            continue
        if not _is_ignored(root, entry):
            print(
                f"[SKIP] {entry} is not confirmed gitignored -- refusing to "
                "symlink.",
                file=sys.stderr,
            )
            continue
        (worktree / entry).symlink_to(source.resolve())
        linked.append(entry)
    return linked


def create(
    root: Path, slug: str, *, base: str, install: bool
) -> tuple[Path, str, list[str]]:
    """Create the branch, worktree and (optionally) its virtualenv.

    Returns the worktree path, the branch name and the linked entries.
    Raises `WorktreeError` before touching anything if the branch or the
    worktree path already exists.
    """
    _validate_slug(slug)

    branch = f"feat/{slug}"
    worktree = root / ".worktrees" / slug

    if worktree.exists():
        raise WorktreeError(f"{worktree} already exists.")
    existing = _git(
        root, "rev-parse", "--verify", "--quiet", branch, check=False
    )
    if existing.returncode == 0:
        raise WorktreeError(
            f"Branch {branch!r} already exists. Finish or remove it first "
            f"(see `make worktree-clean FEATURE={slug}`)."
        )

    completed = _git(
        root, "worktree", "add", "-b", branch, str(worktree), base, check=False
    )
    if completed.returncode != 0:
        raise WorktreeError(
            f"git worktree add failed:\n{completed.stderr.strip()}"
        )

    linked = _link_control_plane(root, worktree)

    if install:
        print(f"Installing the worktree virtualenv in {worktree} ...")
        installed = subprocess.run(
            ["poetry", "install", "--extras", "tui", "--no-interaction"],
            cwd=worktree,
            check=False,
        )
        if installed.returncode != 0:
            raise WorktreeError(
                f"poetry install failed in {worktree}. The worktree and "
                "branch were created; fix the environment there, or remove "
                f"them with `make worktree-clean FEATURE={slug}`."
            )

    return worktree, branch, linked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("feature", help="Feature slug, e.g. 'search-regex'.")
    parser.add_argument(
        "--base",
        default="HEAD",
        help="Commit-ish the new branch starts from (default: HEAD).",
    )
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="Skip `poetry install` in the new worktree.",
    )
    args = parser.parse_args(argv)

    try:
        worktree, branch, linked = create(
            REPO_ROOT,
            args.feature,
            base=args.base,
            install=not args.no_install,
        )
    except WorktreeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    relative = worktree.relative_to(REPO_ROOT)
    print(f"\nOK: branch {branch} ready in {relative}")
    print(f"    linked: {', '.join(linked) if linked else '(nothing)'}")
    if args.no_install:
        print("    no virtualenv installed (--no-install)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
