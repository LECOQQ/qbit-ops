#!/usr/bin/env python3
"""Create a dedicated branch, worktree and virtualenv for a feature.

The agent control-plane (`AGENTS.md`, `.agents/`) is gitignored, so a
fresh worktree does not contain it. This script links it in -- but only
after Git confirms each target is ignored. Replacing a *tracked* file
with a symlink produces a typechange that can reach a commit, which is
exactly the accident this script exists to prevent.

The worktree never shares the main `.venv`. If it did, the project
there is installed in editable mode pointing at the main checkout's
`src/`, so both environments would silently exercise the wrong code --
no import error, no failing test, nothing to notice.

Poetry honours an inherited `VIRTUAL_ENV` ahead of its own
`virtualenvs.in-project` setting, so that failure is one `poetry
install` away whenever the caller's shell has a virtualenv active.
`_clean_env()` removes it, and `verify_isolation()` proves afterwards
that the worktree really resolves to its own tree.
"""

from __future__ import annotations

import argparse
import json
import os
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


# Poetry decides where a project's virtualenv lives by inference, and
# that inference is not stable across invocation contexts: the same call
# provisioned the worktree correctly from a shell and silently reused an
# inherited environment when run under `make`. Two settings remove the
# guesswork.
#
# `VIRTUAL_ENV` is honoured ahead of `virtualenvs.in-project`, so an
# activated shell makes `poetry install` target *that* environment no
# matter which directory it runs in -- it has to go. Nothing else is
# removed: also stripping `PATH`, `PYTHONPATH`, `PYTHONHOME` and
# `POETRY_ACTIVE` was tried as a precaution and made things worse.
#
# `POETRY_VIRTUALENVS_IN_PROJECT` then states the requirement instead of
# relying on the machine's global Poetry config, which a contributor may
# not share.
_ENV_VAR_THAT_STEERS_POETRY = "VIRTUAL_ENV"


def _clean_env() -> dict[str, str]:
    """Return an environment that pins Poetry to the worktree's own venv."""
    cleaned = {
        key: value
        for key, value in os.environ.items()
        if key != _ENV_VAR_THAT_STEERS_POETRY
    }
    cleaned["POETRY_VIRTUALENVS_IN_PROJECT"] = "true"
    return cleaned


def verify_isolation(worktree: Path) -> dict[str, str]:
    """Prove the worktree resolves `qbit_core` inside its own tree.

    Raises `WorktreeError` when the interpreter, the virtualenv, or the
    imported package belongs to another checkout -- the silent failure
    this whole module exists to prevent. Returns the observed facts so a
    caller can report them.
    """
    probe = (
        "import json, sys, qbit_core, qbit_ops; "
        "print(json.dumps({"
        "'python': sys.executable, "
        "'prefix': sys.prefix, "
        "'qbit_core': qbit_core.__file__, "
        "'qbit_ops': qbit_ops.__file__}))"
    )
    # Run the worktree's own interpreter rather than `poetry run`: the
    # check must observe the environment, never create one. `poetry run`
    # provisions a virtualenv when it finds none, which turns a failed
    # verification into a second, empty environment and hides what
    # actually went wrong.
    interpreter = worktree / ".venv" / "bin" / "python"
    if not interpreter.exists():
        raise WorktreeError(
            f"{worktree} has no virtualenv at {interpreter}. `poetry "
            "install` reported success, so it installed the project "
            "somewhere else -- most likely an environment inherited from "
            "the calling shell. Remove the worktree and retry with no "
            "virtualenv activated."
        )

    completed = subprocess.run(
        [str(interpreter), "-c", probe],
        cwd=worktree,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise WorktreeError(
            "Could not verify the worktree environment:\n"
            f"{completed.stderr.strip()}"
        )

    try:
        observed: dict[str, str] = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise WorktreeError(
            f"Unreadable environment probe output: {completed.stdout!r}"
        ) from exc

    # `python` is reported by the probe but never judged: a virtualenv
    # symlinks its interpreter to the system binary, so `sys.executable`
    # resolves outside the worktree even when the environment is
    # perfectly isolated.
    root = worktree.resolve()
    for key in ("prefix", "qbit_core", "qbit_ops"):
        observed_path = Path(observed[key]).resolve()
        if not observed_path.is_relative_to(root):
            raise WorktreeError(
                f"{worktree} is not isolated: {key} resolves to "
                f"{observed_path}, outside the worktree. The environment "
                "would exercise another checkout's code without failing "
                "a single test. Remove the worktree and retry with no "
                "virtualenv activated."
            )
    return observed


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
            # Without `_clean_env()` this call inherits `VIRTUAL_ENV` and
            # installs the worktree's `src/` into the *main* checkout's
            # venv, reporting success. This is the one site where the
            # cleaned environment is load-bearing.
            env=_clean_env(),
            check=False,
        )
        if installed.returncode != 0:
            raise WorktreeError(
                f"poetry install failed in {worktree}. The worktree and "
                "branch were created; fix the environment there, or remove "
                f"them with `make worktree-clean FEATURE={slug}`."
            )
        verify_isolation(worktree)

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
    else:
        print("    isolation verified: qbit_core resolves inside the worktree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
