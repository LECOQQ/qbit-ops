#!/usr/bin/env python3
"""Decide which Docker tags a release tag is allowed to move.

Git tags are the source of truth: no registry is contacted. The exact
`X.Y.Z` tag is always published for a valid stable release; the two
floating tags only move when this release really is the newest in their
scope, so republishing an older tag can never drag them backwards.

    latest  moves only for the highest stable release overall
    X.Y     moves only for the highest stable patch of that X.Y line

Prereleases are refused outright, and never rank.

Stdlib only: this runs as bare `python3` in a workflow that does not
install the project.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Deliberately strict: `v1.2.3` and nothing else. A prerelease, a build
# metadata suffix or a two-component tag is not a stable release.
STABLE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


class DockerTagError(Exception):
    """Raised for any actionable tag-planning failure."""


@dataclass(frozen=True)
class DockerTagPlan:
    """Which tags this release may publish."""

    version: str
    minor: str
    is_newest: bool
    is_newest_in_minor: bool

    def as_github_output(self) -> str:
        return (
            f"version={self.version}\n"
            f"minor={self.minor}\n"
            f"is_newest={str(self.is_newest).lower()}\n"
            f"is_newest_in_minor={str(self.is_newest_in_minor).lower()}"
        )


def _parse_stable(tag: str) -> tuple[int, int, int] | None:
    match = STABLE_TAG.match(tag)
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def plan_docker_tags(tag: str, known_tags: list[str]) -> DockerTagPlan:
    """Rank `tag` among `known_tags` and decide which tags may move.

    Raises `DockerTagError` when `tag` is not a stable release tag, or
    when it is absent from `known_tags` -- an unknown tag cannot be
    ranked, and silently treating it as "not newest" would skip
    `latest` on a real release without saying why.
    """
    current = _parse_stable(tag)
    if current is None:
        raise DockerTagError(
            f"Refusing {tag!r}: only stable vX.Y.Z tags are published "
            "(no prereleases, no partial versions)."
        )

    stable = {
        candidate: parsed
        for candidate in known_tags
        if (parsed := _parse_stable(candidate)) is not None
    }
    if tag not in stable:
        raise DockerTagError(
            f"Refusing {tag!r}: it is not among the repository's stable "
            "tags. Push the release tag before publishing its image."
        )

    highest = max(stable.values())
    major, minor, _ = current
    highest_in_line = max(
        version for version in stable.values() if version[:2] == (major, minor)
    )

    return DockerTagPlan(
        version=tag.removeprefix("v"),
        minor=f"{major}.{minor}",
        is_newest=current == highest,
        is_newest_in_minor=current == highest_in_line,
    )


def discover_tags(remote: str) -> list[str]:
    """List the remote's tags without needing any local history.

    `git ls-remote` is used rather than `git tag` on purpose: a workflow
    checkout is shallow and carries no tags, and fetching them all would
    drag this repository's large history along for no reason.
    """
    try:
        completed = subprocess.run(
            ["git", "ls-remote", "--tags", "--refs", remote],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except OSError as error:
        raise DockerTagError(f"Could not run git: {error}") from error

    if completed.returncode != 0:
        raise DockerTagError(
            f"git ls-remote {remote} failed: {completed.stderr.strip()}"
        )

    return [
        line.split("refs/tags/", 1)[1]
        for line in completed.stdout.splitlines()
        if "refs/tags/" in line
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tag", required=True, help="Release tag to publish (vX.Y.Z)."
    )
    parser.add_argument(
        "--remote",
        default="origin",
        help="Git remote to read tags from (default: origin).",
    )
    parser.add_argument(
        "--tags-file",
        type=Path,
        default=None,
        help="Read candidate tags from this file, one per line, instead "
        "of querying the remote.",
    )
    args = parser.parse_args(argv)

    try:
        if args.tags_file is not None:
            known = args.tags_file.read_text(encoding="utf-8").split()
        else:
            known = discover_tags(args.remote)
        plan = plan_docker_tags(args.tag, known)
    except (DockerTagError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(plan.as_github_output())

    if not plan.is_newest:
        print(
            f"::notice::{args.tag} is not the newest stable release; "
            "leaving 'latest' untouched.",
            file=sys.stderr,
        )
    if not plan.is_newest_in_minor:
        print(
            f"::notice::{args.tag} is not the newest patch of "
            f"{plan.minor}; leaving '{plan.minor}' untouched.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
