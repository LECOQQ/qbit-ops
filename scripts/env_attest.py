#!/usr/bin/env python3
"""Attest which checkout, interpreter and virtualenv are actually in use.

An agent starting work in a worktree cannot tell from the prompt alone
whether its commands exercise that worktree's `src/` or another
checkout's. Poetry honours an inherited `VIRTUAL_ENV` ahead of
`virtualenvs.in-project`, so a mis-targeted `poetry install` leaves both
environments running the wrong code with no import error and no failing
test. This script makes that state visible in one command.

Exits `0` when the imported `qbit-ops` source lives under the expected
root, `1` otherwise.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import qbit_core
import qbit_ops


def _git(root: Path, *args: str) -> str | None:
    """Return trimmed git output, or None when git cannot answer."""
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def belongs_to(path: Path, root: Path) -> bool:
    """Report whether `path` belongs to `root` and not to a worktree of it.

    `Path.is_relative_to` alone is not enough: worktrees live at
    `<root>/.worktrees/<slug>`, so every worktree path is "relative to"
    the main checkout. A main environment repointed at a worktree's
    `src/` would pass that test while exercising the wrong code --
    observed, not hypothetical.
    """
    if not path.is_relative_to(root):
        return False
    return ".worktrees" not in path.relative_to(root).parts


def collect_attestation(expected_root: Path) -> dict[str, Any]:
    """Describe the running environment relative to `expected_root`.

    `isolated` is the single field a caller should branch on: it is true
    when the virtualenv and both installed packages live under the
    expected root.

    The interpreter path is reported but deliberately excluded from that
    verdict. A virtualenv symlinks its `python` to the system binary, so
    `sys.executable` resolves to `/usr/bin/python3.x` on a perfectly
    isolated environment -- judging on it would fail every correct
    setup.
    """
    root = expected_root.resolve()
    paths = {
        "prefix": Path(sys.prefix).resolve(),
        "qbit_core": Path(qbit_core.__file__).resolve(),
        "qbit_ops": Path(qbit_ops.__file__).resolve(),
    }
    outside = sorted(
        name for name, path in paths.items() if not belongs_to(path, root)
    )

    return {
        "expected_root": str(root),
        "python": str(Path(sys.executable).resolve()),
        "python_version": sys.version.split()[0],
        "virtualenv": str(paths["prefix"]),
        "qbit_core": str(paths["qbit_core"]),
        "qbit_ops": str(paths["qbit_ops"]),
        "git_branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD"),
        "git_sha": _git(root, "rev-parse", "--short", "HEAD"),
        "git_dirty": bool(_git(root, "status", "--porcelain")),
        "outside_expected_root": outside,
        "isolated": not outside,
    }


def render(attestation: dict[str, Any]) -> str:
    lines = [
        f"expected root  {attestation['expected_root']}",
        f"python         {attestation['python']} "
        f"({attestation['python_version']})",
        f"virtualenv     {attestation['virtualenv']}",
        f"qbit_core      {attestation['qbit_core']}",
        f"qbit_ops       {attestation['qbit_ops']}",
        f"git            {attestation['git_branch'] or '-'} "
        f"@ {attestation['git_sha'] or '-'}"
        f"{' (dirty)' if attestation['git_dirty'] else ''}",
    ]
    if attestation["isolated"]:
        lines.append("isolation      OK")
    else:
        outside = ", ".join(attestation["outside_expected_root"])
        lines.append(
            f"isolation      FAILED -- {outside} resolve outside the "
            "expected root. Commands here exercise another checkout's "
            "code. Re-run Poetry with no virtualenv activated."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Checkout the environment is expected to serve (default: cwd).",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Output format.",
    )
    args = parser.parse_args(argv)

    attestation = collect_attestation(args.root)
    if args.format == "json":
        print(json.dumps(attestation, indent=2, sort_keys=True))
    else:
        print(render(attestation))
    return 0 if attestation["isolated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
