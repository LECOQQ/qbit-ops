#!/usr/bin/env python3
"""Remove a feature worktree and its branch, refusing on any doubt.

Deliberately conservative: it would rather stop and explain than delete
something a human still needs. It refuses when the worktree holds any
change Git would not already have, and when the branch is not proven
integrated. It never runs `git worktree remove --force`.

Two integration proofs, because two integration strategies exist:

- **ancestry** -- after `git merge --no-ff`, the branch is an ancestor
  of the base. `git branch -d` enforces this itself.
- **content equivalence** -- after `git merge --squash`, the branch is
  deliberately *not* an ancestor, so ancestry proves nothing and
  `git branch -d` always refuses. What holds instead is that the
  delivered tree equals the approved tree. Comparing tree hashes proves
  in one step that nothing approved was lost and nothing foreign was
  taken in.

`git branch -D` is reachable only through the second proof, never as a
way around the first.

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
    """Ancestry proof: the branch is contained in `base`'s history."""
    return (
        _git(root, "merge-base", "--is-ancestor", branch, base).returncode == 0
    )


def _tree_of(root: Path, commitish: str) -> str:
    return _git(root, "rev-parse", f"{commitish}^{{tree}}").stdout.strip()


def squash_equivalence(
    root: Path, branch: str, base: str
) -> tuple[bool, str, str]:
    """Content-equivalence proof for a squashed integration.

    Returns whether the trees match plus both hashes, so a caller can
    record the evidence rather than merely assert it. Equal trees mean
    the delivered content *is* the approved content: no approved change
    was dropped by the squash, and no foreign change rode along.
    """
    branch_tree = _tree_of(root, branch)
    base_tree = _tree_of(root, base)
    return branch_tree == base_tree, branch_tree, base_tree


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

    squashed = False
    if not keep_branch and not _is_merged(root, branch, base):
        equivalent, branch_tree, base_tree = squash_equivalence(
            root, branch, base
        )
        if not equivalent:
            raise WorktreeError(
                f"Branch {branch!r} is neither an ancestor of {base!r} nor "
                "content-equivalent to it. Nothing was removed.\n"
                f"  {branch} tree {branch_tree}\n"
                f"  {base} tree {base_tree}\n"
                "Either the branch is not integrated, or the squash lost "
                "an approved change or took in a foreign one. Integrate "
                "it first, or re-run with --keep-branch to drop only the "
                "worktree."
            )
        squashed = True
        print(
            f"Squash integration proven: {branch} and {base} share tree "
            f"{branch_tree}."
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

    # `-d` is the default: it re-checks ancestry itself, so its refusal
    # is a signal to surface rather than something to force past.
    #
    # `-D` is reachable only when `squash_equivalence` already proved the
    # delivered tree identical to the approved one -- the proof `-d`
    # structurally cannot make after a squash. It is never a fallback
    # for a `-d` that refused.
    flag = "-D" if squashed else "-d"
    deleted = _git(root, "branch", flag, branch)
    if deleted.returncode != 0:
        raise WorktreeError(
            f"Worktree removed, but `git branch {flag} {branch}` refused:\n"
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
