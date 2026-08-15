#!/usr/bin/env python3
"""Break a work-item's diff down by the kind of work it contains.

`+1825 / -194` says how much changed, not what changed. The same total
describes a large production feature, a documentation pass and a tooling
refactor, and those are not the same run.

Classification is **deterministic and path-based**. It is never asked of
a model: a metric a model produces cannot be compared across runs,
because the classifier itself drifts.

Every changed line belongs to exactly one category. The rules are
ordered and the first match wins, so a file cannot be counted twice.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import PurePosixPath

CATEGORIES = ("production", "tests", "docs", "tooling", "config", "other")

# Ordered: the first matching rule wins. `demo/` sits under tooling
# rather than docs -- it is a runnable disposable environment driven by
# Make targets, not prose a user reads.
RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tests", ("tests/",)),
    ("production", ("src/",)),
    (
        "docs",
        (
            "docs/",
            "README.md",
            "PHILOSOPHY.md",
            "CONTRIBUTING.md",
            "ROADMAP.md",
            "CHANGELOG.md",
        ),
    ),
    ("tooling", ("scripts/", "demo/", "Makefile")),
    (
        "config",
        (
            ".github/",
            "pyproject.toml",
            "poetry.lock",
            ".pre-commit-config.yaml",
            ".gitignore",
            "release-please-config.json",
            ".release-please-manifest.json",
            "Dockerfile",
        ),
    ),
)


def categorize(path: str) -> str:
    """Return the single category a changed path belongs to."""
    normalized = PurePosixPath(path).as_posix()
    for category, prefixes in RULES:
        if normalized.startswith(prefixes):
            return category
    return "other"


@dataclass
class Tally:
    files: int = 0
    added: int = 0
    removed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "files": self.files,
            "added": self.added,
            "removed": self.removed,
        }


@dataclass
class Breakdown:
    total: Tally = field(default_factory=Tally)
    by_category: dict[str, Tally] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "total": self.total.as_dict(),
            "by_category": {
                name: tally.as_dict()
                for name, tally in self.by_category.items()
                if tally.files
            },
        }


def breakdown_from_numstat(numstat: str) -> Breakdown:
    """Build the breakdown from `git diff --numstat` output.

    A binary file reports `-` instead of counts. It is tallied as a
    changed file with zero lines rather than dropped: a run that only
    replaced an image did change something.
    """
    result = Breakdown()
    for line in numstat.splitlines():
        if not line.strip():
            continue
        added_raw, removed_raw, path = line.split("\t", 2)
        added = 0 if added_raw == "-" else int(added_raw)
        removed = 0 if removed_raw == "-" else int(removed_raw)

        category = categorize(path)
        tally = result.by_category.setdefault(category, Tally())
        tally.files += 1
        tally.added += added
        tally.removed += removed

        result.total.files += 1
        result.total.added += added
        result.total.removed += removed
    return result


def collect(commit_range: str) -> Breakdown:
    completed = subprocess.run(
        ["git", "diff", "--numstat", commit_range],
        capture_output=True,
        text=True,
        check=True,
    )
    return breakdown_from_numstat(completed.stdout)


def render(breakdown: Breakdown) -> str:
    """Render the summary a REPORT.md carries."""
    total = breakdown.total
    lines = [
        f"{'Diff total':<12} +{total.added} / -{total.removed} "
        f"({total.files} files)"
    ]
    for name in CATEGORIES:
        tally = breakdown.by_category.get(name)
        if not tally or not tally.files:
            continue
        lines.append(
            f"{name.capitalize():<12} +{tally.added} / -{tally.removed} "
            f"({tally.files})"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "commit_range", help="Range to measure, e.g. main..feat/x."
    )
    parser.add_argument("--format", choices=("table", "json"), default="table")
    args = parser.parse_args(argv)

    breakdown = collect(args.commit_range)
    if args.format == "json":
        print(json.dumps({"diff": breakdown.as_dict()}, indent=2))
    else:
        print(render(breakdown))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
