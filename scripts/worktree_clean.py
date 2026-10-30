#!/usr/bin/env python3
"""Remove a feature worktree and its branch, refusing on any doubt.

Deliberately conservative: it would rather stop and explain than delete
something a human still needs. It refuses when the worktree holds any
change Git would not already have, and when the branch is not proven
integrated. It never runs `git worktree remove --force`.

Three integration proofs, because three integration strategies exist:

- **ancestry** -- after `git merge --no-ff`, the branch is an ancestor
  of the base. `git branch -d` enforces this itself.
- **content equivalence** -- after `git merge --squash`, the branch is
  deliberately *not* an ancestor, so ancestry proves nothing and
  `git branch -d` always refuses. What holds instead is that the
  delivered tree equals the approved tree. Comparing tree hashes proves
  in one step that nothing approved was lost and nothing foreign was
  taken in.
- **patch equivalence** -- after a rebase merge, the commits are
  rewritten, so the branch is not an ancestor, and the base has usually
  moved on, so the trees differ. What holds is that every commit of the
  branch has a commit with the same patch-id in the base
  (`git log --cherry-pick`). A single unmatched commit refuses.

`git branch -D` is reachable only through the last two proofs, never as
a way around the first.

The control-plane symlinks are unlinked explicitly before Git removes
the directory, so no directory walk can ever follow them back into the
main checkout.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
LINKED_ENTRIES = (
    "AGENTS.md",
    "CLAUDE.md",
    ".agents",
    ".harness",
    ".claude/rules/agent-harness.md",
)


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


def _unlink_control_plane(worktree: Path) -> dict[str, str]:
    """Remove only entries that really are symlinks; map each to its target.

    A regular file or a real directory under one of these names is not
    ours to delete: leave it, and let the dirty-worktree check refuse.
    """
    removed: dict[str, str] = {}
    for entry in LINKED_ENTRIES:
        target = worktree / entry
        if target.is_symlink():
            removed[entry] = os.readlink(target)
            target.unlink()
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


def unmatched_commits(root: Path, branch: str, base: str) -> list[str]:
    """Commits of `branch` with no patch-equivalent commit in `base`.

    An empty list proves a rebase integration: every commit was replayed
    under a new hash. Merge commits carry no patch and are skipped.
    """
    completed = _git(
        root,
        "log",
        "--right-only",
        "--cherry-pick",
        "--no-merges",
        "--format=%h %s",
        f"{base}...{branch}",
    )
    if completed.returncode != 0:
        raise WorktreeError(f"git log refused:\n{completed.stderr.strip()}")
    return completed.stdout.splitlines()


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

    proven_by_content = False
    if not keep_branch and not _is_merged(root, branch, base):
        missing = unmatched_commits(root, branch, base)
        equivalent, branch_tree, base_tree = squash_equivalence(
            root, branch, base
        )
        if not missing:
            print(
                f"Rebase integration proven: every commit of {branch} has "
                f"a patch-equivalent in {base}."
            )
        elif equivalent:
            print(
                f"Squash integration proven: {branch} and {base} share "
                f"tree {branch_tree}."
            )
        else:
            listing = "\n  ".join(missing)
            raise WorktreeError(
                f"Branch {branch!r} is neither an ancestor of {base!r} nor "
                "content-equivalent to it. Nothing was removed.\n"
                f"  {branch} tree {branch_tree}\n"
                f"  {base} tree {base_tree}\n"
                f"Commits with no patch-equivalent in {base!r}:\n"
                f"  {listing}\n"
                "Either the branch is not integrated, or the squash lost "
                "an approved change or took in a foreign one. Integrate "
                "it first, or re-run with --keep-branch to drop only the "
                "worktree."
            )
        proven_by_content = True

    removed_links = _unlink_control_plane(worktree)

    dirty = _dirty_entries(worktree)
    if dirty:
        # The recorded target, not a resolved one: `.harness` points at
        # a stable link that must keep following the installed version.
        for entry, link_target in removed_links.items():
            (worktree / entry).symlink_to(link_target)
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
    # `-D` is reachable only when tree or patch equivalence already proved
    # the integration -- the proofs `-d` structurally cannot make after a
    # squash or a rebase. It is never a fallback for a `-d` that refused.
    flag = "-D" if proven_by_content else "-d"
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
