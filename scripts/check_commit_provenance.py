#!/usr/bin/env python3
"""Refuse commits carrying a forbidden provenance marker.

The repository's commit history is the record of who authored the work,
and agent-generated `Co-Authored-By` trailers pollute it with a
co-author that never existed as a person. Two more markers travel with
them and are refused for the same reason: a `Claude-Session:` trailer,
which points at a conversation nobody else can open, and the "Generated
with Claude Code" line, which is a footer for pull requests that reaches
commit messages by copy.

This is enforced mechanically rather than by convention: a rule that
lives only in a prompt is only as good as the prompt that happened to
be used, and an instruction that asks for the trailer defeats a
convention no gate checks.

Two entry points share this check:

- the `commit-msg` hook, which stops a single message at creation time;
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

# Matched case-insensitively at the start of a line, the shape Git uses
# for trailers. The agent name is searched anywhere in the value, not
# just at its start: "GitHub Copilot" and "Claude Opus 5" both name an
# agent without leading with the word that identifies it.
#
# Only names that identify an agent are refused -- a genuine human
# co-author stays legitimate, so the check narrows the history it
# protects rather than banning the trailer outright.
FORBIDDEN_TRAILER = re.compile(
    r"^\s*Co-Authored-By:.*\b(claude|anthropic|gpt|copilot|cursor)\b",
    re.IGNORECASE,
)

# A session trailer names a conversation, not an author -- so the
# narrowing that keeps a human co-author legitimate does not apply, and
# the whole trailer goes. `Claude-Session` is the shape seen in the
# wild; the others are matched by the same rule rather than waiting to
# meet each one.
FORBIDDEN_SESSION = re.compile(
    r"^\s*(claude|anthropic|codex|copilot|cursor)[- ]session\s*:",
    re.IGNORECASE,
)

# The "Generated with ..." footer belongs to a pull request description,
# where it addresses a reader who can act on it. It reaches a commit
# message by copy, and there it is only noise -- matched on the tool
# name so the emoji and the link format can change without a hole.
FORBIDDEN_GENERATED = re.compile(
    r"generated with.*\b(claude code|copilot|cursor|codex)\b",
    re.IGNORECASE,
)

FORBIDDEN = (FORBIDDEN_TRAILER, FORBIDDEN_SESSION, FORBIDDEN_GENERATED)


def offending_lines(message: str) -> list[str]:
    """Return the forbidden provenance lines found in one commit message.

    The trailer patterns anchor at the start of a line, as Git trailers
    do. The "Generated with" footer does not: it is prose, and it has
    been seen behind a bullet and behind an emoji, so it is searched
    anywhere in the line.
    """
    found: list[str] = []
    for line in message.splitlines():
        anchored = any(
            pattern.match(line)
            for pattern in (FORBIDDEN_TRAILER, FORBIDDEN_SESSION)
        )
        if anchored or FORBIDDEN_GENERATED.search(line):
            found.append(line.strip())
    return found


def _commits_in_range(commit_range: str) -> list[tuple[str, str, str]]:
    """Return `(sha, subject, message)` for every commit in the range."""
    listed = subprocess.run(
        ["git", "rev-list", commit_range],
        capture_output=True,
        text=True,
        check=True,
    )
    commits: list[tuple[str, str, str]] = []
    for sha in listed.stdout.split():
        shown = subprocess.run(
            ["git", "show", "-s", "--format=%s%n%n%B", sha],
            capture_output=True,
            text=True,
            check=True,
        )
        subject, _, message = shown.stdout.partition("\n\n")
        commits.append((sha[:9], subject.strip(), message))
    return commits


def _report(failures: list[tuple[str, str, list[str]]]) -> int:
    if not failures:
        return 0

    print(
        "Forbidden provenance marker found. The repository does not "
        "record an agent as a commit co-author, name a conversation "
        "nobody else can open, or carry a pull-request footer into a "
        "commit message.\n",
        file=sys.stderr,
    )
    for sha, subject, lines in failures:
        label = f"{sha} {subject}" if sha else subject
        print(f"  {label}", file=sys.stderr)
        for line in lines:
            print(f"      {line}", file=sys.stderr)
    print(
        "\nRemove the line. To rewrite commits already on a branch: "
        "`git rebase -i` and amend each message, or re-run the "
        "integration once the branch is clean.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--commit-msg-file",
        type=Path,
        help="Message file passed by the commit-msg hook.",
    )
    source.add_argument(
        "--range",
        dest="commit_range",
        help="Commit range to audit, e.g. main..feat/x.",
    )
    args = parser.parse_args(argv)

    if args.commit_msg_file is not None:
        message = args.commit_msg_file.read_text(encoding="utf-8")
        found = offending_lines(message)
        return _report([("", "(commit being created)", found)] if found else [])

    failures = [
        (sha, subject, found)
        for sha, subject, message in _commits_in_range(args.commit_range)
        if (found := offending_lines(message))
    ]
    if not failures:
        print(f"OK: no forbidden provenance trailer in {args.commit_range}.")
    return _report(failures)


if __name__ == "__main__":
    raise SystemExit(main())
