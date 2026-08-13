#!/usr/bin/env python3
"""Remove a feature worktree and its branch, refusing on any doubt.

Deliberately conservative: it would rather stop and explain than delete
something a human still needs. It refuses when the worktree holds any
change Git would not already have, and when the branch is not fully
merged. It never runs `git worktree remove --force`, and never
`git branch -D`.

The control-plane symlinks are unlinked explicitly before Git removes
the directory, so no directory walk can ever follow them back into the
main checkout.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
LINKED_ENTRIES = ("AGENTS.md", "CLAUDE.md", ".agents")


class WorktreeError(Exception):
    """Raised for any actionable teardown failure."""


def _git(
    root: Path, *args: str, check: bool = False
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=check,
    )


def _registered_worktrees(root: Path) -> set[Path]:
    completed = _git(root, "worktree", "list", "--porcelain")
    return {
        Path(line.removeprefix("worktree ").strip())
        for line in completed.stdout.splitlines()
        if line.startswith("worktree ")
    }


def _unlink_control_plane(worktree: Path) -> list[str]:
    """Remove only entries that really are symlinks.

    A regular file or a real directory under one of these names is not
    ours to delete: leave it, and let the dirty-worktree check refuse.
    """
    removed: list[str] = []
    for entry in LINKED_ENTRIES:
        target = worktree / entry
        if target.is_symlink():
            target.unlink()
            removed.append(entry)
    return removed


def _dirty_entries(worktree: Path) -> list[str]:
    completed = _git(worktree, "status", "--porcelain")
    return [line for line in completed.stdout.splitlines() if line.strip()]


def _is_merged(root: Path, branch: str, base: str) -> bool:
    return (
        _git(root, "merge-base", "--is-ancestor", branch, base).returncode == 0
    )


def clean(
    root: Path, slug: str, *, base: str, keep_branch: bool
) -> tuple[str, bool]:
    """Remove the worktree, and the branch unless `keep_branch`.

    Returns the branch name and whether it was deleted. Raises
    `WorktreeError` without deleting anything when a precondition fails.
    """
    if not SLUG_PATTERN.match(slug):
        raise WorktreeError(f"Invalid feature slug {slug!r}.")

    branch = f"feat/{slug}"
    worktree = (root / ".worktrees" / slug).resolve()

    if not worktree.is_dir():
        raise WorktreeError(f"{worktree} does not exist.")
    if worktree not in _registered_worktrees(root):
        raise WorktreeError(
            f"{worktree} is not a registered Git worktree. Refusing to "
            "delete a directory this script did not create."
        )

    if not keep_branch and not _is_merged(root, branch, base):
        raise WorktreeError(
            f"Branch {branch!r} is not fully merged into {base!r}. Nothing "
            "was removed. Integrate it first, or re-run with "
            "--keep-branch to drop only the worktree."
        )

    removed_links = _unlink_control_plane(worktree)

    dirty = _dirty_entries(worktree)
    if dirty:
        for entry in removed_links:
            (worktree / entry).symlink_to((root / entry).resolve())
        listing = "\n  ".join(dirty)
        raise WorktreeError(
            f"{worktree} has uncommitted or untracked changes:\n"
            f"  {listing}\n"
            "Nothing was removed. Commit, stash or discard them first."
        )

    completed = _git(root, "worktree", "remove", str(worktree))
    if completed.returncode != 0:
        raise WorktreeError(
            f"git worktree remove refused:\n{completed.stderr.strip()}"
        )

    worktrees_dir = root / ".worktrees"
    if worktrees_dir.is_dir() and not any(worktrees_dir.iterdir()):
        worktrees_dir.rmdir()

    if keep_branch:
        return branch, False

    # `-d` only: it refuses an unmerged branch, and that refusal is a
    # signal to surface, never something to force past with `-D`.
    deleted = _git(root, "branch", "-d", branch)
    if deleted.returncode != 0:
        raise WorktreeError(
            f"Worktree removed, but `git branch -d {branch}` refused:\n"
            f"{deleted.stderr.strip()}\n"
            "The branch was kept. Delete it yourself if you are sure."
        )
    return branch, True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("feature", help="Feature slug, e.g. 'search-regex'.")
    parser.add_argument(
        "--base",
        default="HEAD",
        help="Commit-ish the branch must be merged into (default: HEAD).",
    )
    parser.add_argument(
        "--keep-branch",
        action="store_true",
        help="Remove the worktree but keep the branch (skips the merge check).",
    )
    args = parser.parse_args(argv)

    try:
        branch, deleted = clean(
            REPO_ROOT,
            args.feature,
            base=args.base,
            keep_branch=args.keep_branch,
        )
    except WorktreeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"OK: worktree removed for {args.feature}.")
    print(f"    branch {branch}: {'deleted' if deleted else 'kept'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
