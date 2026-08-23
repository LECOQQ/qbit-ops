#!/usr/bin/env python3
"""Refuse a commit body written as prose instead of bullets.

This repository's commit bodies are short bullets, one line per change.
Enforced mechanically, for the same reason the provenance trailer is:
a rule that lives only in the history, never in `AGENTS.md` and never
in a hook, is a rule an agent reading the last few commits can infer
as drift instead of convention -- and each prose commit that slips
through makes the next one likelier.

What this checks is deliberately narrow: the body's first non-empty line
starts a bullet, and no bullet wraps earlier than it had to. It does not
judge wording, length, or whether the bullets are good ones. A
subject-only commit is fine -- plenty of the history's best commits have
no body at all.

Length stays unjudged on purpose. Capping a bullet's lines would push a
writer to compress meaning rather than drop it, which optimises the
wrong thing. What the wrap rule does reach is the padding: a bullet that
broke a line early looks like it needed two, and often does not.

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
# A wrapped bullet's continuation: indented, and not itself a bullet.
CONTINUATION = re.compile(r"^\s+\S")

# 72, the width `git log` indents a body into on an 80-column terminal.
# Measured against this repository's own history before being enforced:
# 1287 of 1317 body lines already sit within it.
WRAP_LIMIT = 72

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


def early_wrap(message: str) -> str | None:
    """Return the first bullet line that broke sooner than it had to.

    Git wraps nothing: a body is displayed exactly as written, in `git
    log`, in `git blame` and on a forge. So a line that stops at 61
    characters when the next word would have fitted in 72 is not a
    style preference -- it is a ragged block everywhere it is read.

    The test is exact, never a guess: take the first word of the
    continuation line and ask whether it fitted. No judgement about
    wording enters here.
    """
    body = body_lines(message)
    lines = [line for line in body if line.strip()]
    for index, line in enumerate(lines[:-1]):
        following = lines[index + 1]
        if not CONTINUATION.match(following):
            continue
        if TRAILER.match(line) or TRAILER.match(following):
            continue
        first_word = following.strip().split(" ")[0]
        if len(line) + 1 + len(first_word) <= WRAP_LIMIT:
            return (
                f"line wraps early at {len(line)} characters: "
                f"{first_word!r} would have fitted within {WRAP_LIMIT}.\n"
                f"  {line}\n"
                "Nothing reflows a commit body -- it is read exactly as\n"
                "written. Fill the line, or make the bullet shorter."
            )
    return None


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
        message = args.commit_msg_file.read_text(encoding="utf-8")
        problem = violation(message) or early_wrap(message)
        if problem:
            print(f"ERROR: {problem}", file=sys.stderr)
            return 1
        return 0

    if not args.commit_range:
        parser.error("pass --commit-msg-file or --range")

    failures = 0
    for sha, message in _range_messages(args.commit_range):
        problem = violation(message) or early_wrap(message)
        if problem:
            subject = message.splitlines()[0] if message.strip() else ""
            print(f"{sha[:8]} {subject}\n  {problem}\n", file=sys.stderr)
            failures += 1

    if failures:
        print(
            f"ERROR: {failures} commit body/bodies rejected.",
            file=sys.stderr,
        )
        return 1

    print("OK: every commit body in range is bulleted and fills its lines.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
