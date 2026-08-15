#!/usr/bin/env python3
"""Refuse commits carrying a forbidden provenance trailer.

The repository's commit history is the record of who authored the work,
and agent-generated `Co-Authored-By` trailers pollute it with a
co-author that never existed as a person.

This is enforced mechanically rather than by convention because the
convention demonstrably failed: on `stats-tracker-breakdown`, every
commit carried the trailer -- not because an agent drifted, but because
the instruction it was given said to add it. A rule that lives only in a
prompt is only as good as the prompt that happened to be used.

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


def offending_lines(message: str) -> list[str]:
    """Return the forbidden trailer lines found in one commit message."""
    return [
        line.strip()
        for line in message.splitlines()
        if FORBIDDEN_TRAILER.match(line)
    ]


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
        "Forbidden provenance trailer found. The repository does not "
        "record an agent as a commit co-author.\n",
        file=sys.stderr,
    )
    for sha, subject, lines in failures:
        label = f"{sha} {subject}" if sha else subject
        print(f"  {label}", file=sys.stderr)
        for line in lines:
            print(f"      {line}", file=sys.stderr)
    print(
        "\nRemove the trailer. To rewrite commits already on a branch: "
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
