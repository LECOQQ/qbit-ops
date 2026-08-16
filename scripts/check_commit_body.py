#!/usr/bin/env python3
"""Refuse a commit body written as prose instead of bullets.

This repository's commit bodies are short bullets, one line per change.
The convention held for 91 commits against 12 exceptions -- and then
broke, on 2026-08-15, when eleven consecutive commits arrived as
paragraphs. Nobody noticed until a human read them.

It broke for the same reason the provenance trailer did: the rule lived
only in the history, never in `AGENTS.md` and never in a hook. An agent
that reads the last few commits to infer the house style now infers the
drift instead, and each prose commit makes the next one likelier.

What this checks is deliberately narrow: the body's first non-empty line
starts a bullet. It does not judge wording, length, or whether the
bullets are good ones. A subject-only commit is fine -- plenty of the
history's best commits have no body at all.

Two entry points, mirroring `check_commit_provenance.py`:

- the `commit-msg` hook, stopping a message at creation time;
- `--range`, which the orchestrator runs over a work-item before
  integrating, catching anything that reached the branch with hooks
  bypassed or uninstalled.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

BULLET = re.compile(r"^\s*[-*]\s+\S")

# A trailer is metadata, not body prose. `BREAKING CHANGE:` is part of
# the Conventional Commits spec and reads as a paragraph by design.
TRAILER = re.compile(
    r"^(BREAKING[ -]CHANGE:|[A-Z][A-Za-z-]+:\s)",
)

# Comment lines Git strips, plus the diff it appends under `--verbose`.
COMMENT = re.compile(r"^\s*#")
SCISSORS = "# ------------------------ >8 ------------------------"


def body_lines(message: str) -> list[str]:
    """The body, without the subject, comments, or a verbose diff."""
    text = message.split(SCISSORS)[0]
    lines = [line for line in text.splitlines() if not COMMENT.match(line)]
    return lines[1:] if lines else []


def violation(message: str) -> str | None:
    """Return the problem with this message's body, or `None`."""
    body = body_lines(message)
    meaningful = [line for line in body if line.strip()]
    if not meaningful:
        return None

    first = meaningful[0]
    if BULLET.match(first) or TRAILER.match(first):
        return None

    return (
        f"body starts with prose, not a bullet: {first.strip()[:60]!r}\n"
        "Commit bodies in this repository are short bullets, one line per\n"
        "change. A body of paragraphs reads as a design document, and the\n"
        "next agent copies whatever the recent history shows it."
    )


def _range_messages(commit_range: str) -> list[tuple[str, str]]:
    """`(sha, message)` for each commit in the range, oldest first."""
    shas = subprocess.run(
        ["git", "rev-list", "--reverse", commit_range],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [
        (
            sha,
            subprocess.run(
                ["git", "log", "--format=%B", "-1", sha],
                capture_output=True,
                text=True,
                check=True,
            ).stdout,
        )
        for sha in shas
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit-msg-file", type=Path)
    parser.add_argument("--range", dest="commit_range")
    args = parser.parse_args(argv)

    if args.commit_msg_file:
        problem = violation(args.commit_msg_file.read_text(encoding="utf-8"))
        if problem:
            print(f"ERROR: {problem}", file=sys.stderr)
            return 1
        return 0

    if not args.commit_range:
        parser.error("pass --commit-msg-file or --range")

    failures = 0
    for sha, message in _range_messages(args.commit_range):
        problem = violation(message)
        if problem:
            subject = message.splitlines()[0] if message.strip() else ""
            print(f"{sha[:8]} {subject}\n  {problem}\n", file=sys.stderr)
            failures += 1

    if failures:
        print(
            f"ERROR: {failures} commit body/bodies written as prose.",
            file=sys.stderr,
        )
        return 1

    print("OK: every commit body in range is bulleted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
