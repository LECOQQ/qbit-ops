#!/usr/bin/env python3
"""Refuse traces of agentic authorship that the repository does not want.

Every rule here is **mechanical**: a deterministic pattern over a bounded
scope, with a stated reason and an explicit escape hatch. None of them
claims to detect that a text was written by a model -- that judgement is
statistical, and a CI gate must not make it.

The em-dash rule is the clearest example. An em dash proves nothing
about authorship; it is banned because this repository writes `--`, and
enforcing that mechanically removes one generation habit at the door
rather than in review. Read it as a house style rule that happens to
live here, not as a detector.

A line may opt out with the pragma `ai-hygiene: allow-<rule>`, which
keeps the reason next to the exception instead of in a distant
allowlist. That is how a genuine use survives -- the TUI renders an em
dash as the glyph for an inactive rate cell, and no style rule should
change user-visible output.

New rules arrive through a run's `REPORT.md` as an improvement
opportunity, then approval, then a control-plane run. Never
opportunistically, or this gate becomes the vibes detector it exists to
avoid.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Rule:
    """One mechanical check over a bounded set of files.

    `pattern` is searched line by line. `suffixes` and `names` bound the
    scope: a rule that applies everywhere is a rule nobody can reason
    about.
    """

    identifier: str
    category: str
    reason: str
    remedy: str
    pattern: re.Pattern[str]
    suffixes: tuple[str, ...] = ()
    names: tuple[str, ...] = ()

    def covers(self, path: Path) -> bool:
        return path.suffix in self.suffixes or path.name in self.names


TEXT_SUFFIXES = (".py", ".md", ".toml", ".yml", ".yaml", ".cfg", ".txt")
TEXT_NAMES = ("Makefile", "Dockerfile")

RULES: tuple[Rule, ...] = (
    Rule(
        identifier="agent-co-author",
        category="provenance",
        reason=(
            "The history records who authored the work. An agent is not a "
            "person and must not appear as a co-author."
        ),
        remedy="Remove the trailer from the commit message.",
        # Only agent names are refused: a human co-author stays legitimate.
        pattern=re.compile(
            r"^\s*Co-Authored-By:.*\b(claude|anthropic|gpt|copilot|cursor)\b",
            re.IGNORECASE,
        ),
        suffixes=TEXT_SUFFIXES,
        names=TEXT_NAMES,
    ),
    Rule(
        identifier="generation-notice",
        category="provenance",
        reason=(
            "A file does not announce how it was produced. The commit "
            "history carries that, and a notice inside the product ages "
            "into a false claim."
        ),
        remedy="Delete the notice.",
        pattern=re.compile(
            r"\b(generated (with|by) (claude|ai|an? llm|copilot)"
            r"|written by (claude|an? ai)"
            r"|🤖 generated)",
            re.IGNORECASE,
        ),
        suffixes=TEXT_SUFFIXES,
        names=TEXT_NAMES,
    ),
    Rule(
        identifier="control-plane-leak",
        category="generated-artifacts",
        reason=(
            "Published documentation must not point at the gitignored "
            "control-plane: a reader who clones this repository will never "
            "have those files, so the reference is dead on arrival."
        ),
        remedy=(
            "State the rule in the public document, or cite a tracked file."
        ),
        pattern=re.compile(r"`?\.agents/"),
        suffixes=(),
        names=(),
    ),
    Rule(
        identifier="em-dash",
        category="style",
        reason=(
            "This repository writes `--`, not an em dash. A house style "
            "rule, not evidence of authorship."
        ),
        remedy="Use `--`, or add the pragma when the character is data.",
        # The pragma is not optional here: without it the rule's own
        # definition is the first line it flags.
        pattern=re.compile("—"),  # ai-hygiene: allow-em-dash
        suffixes=TEXT_SUFFIXES,
        names=TEXT_NAMES,
    ),
)

# Documentation published to users. `control-plane-leak` applies here and
# nowhere else: source code may legitimately name the control-plane.
PUBLIC_DOC_PREFIXES = ("docs/", "README.md", "PHILOSOPHY.md", "CONTRIBUTING.md")

PRAGMA = "ai-hygiene: allow-"


def tracked_files(root: Path) -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [Path(line) for line in listed.stdout.splitlines() if line]


def _in_scope(rule: Rule, relative: Path) -> bool:
    if rule.identifier == "control-plane-leak":
        return str(relative).startswith(PUBLIC_DOC_PREFIXES)
    return rule.covers(relative)


def violations(root: Path) -> list[tuple[Rule, Path, int, str]]:
    """Return every rule breach as `(rule, path, line number, line)`."""
    found: list[tuple[Rule, Path, int, str]] = []
    for relative in tracked_files(root):
        applicable = [rule for rule in RULES if _in_scope(rule, relative)]
        if not applicable:
            continue
        try:
            text = (root / relative).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for rule in applicable:
                if f"{PRAGMA}{rule.identifier}" in line:
                    continue
                if rule.pattern.search(line):
                    found.append((rule, relative, number, line.strip()))
    return found


def report(found: list[tuple[Rule, Path, int, str]]) -> int:
    if not found:
        print(f"OK: {len(RULES)} AI hygiene rules, no violation.")
        return 0

    by_rule: dict[str, list[tuple[Path, int, str]]] = {}
    for rule, path, number, line in found:
        by_rule.setdefault(rule.identifier, []).append((path, number, line))

    for rule in RULES:
        breaches = by_rule.get(rule.identifier)
        if not breaches:
            continue
        print(
            f"\n[{rule.category}/{rule.identifier}] {len(breaches)} "
            f"violation(s)",
            file=sys.stderr,
        )
        print(f"  why: {rule.reason}", file=sys.stderr)
        print(f"  fix: {rule.remedy}", file=sys.stderr)
        for path, number, line in breaches[:20]:
            print(f"    {path}:{number}: {line[:100]}", file=sys.stderr)
        if len(breaches) > 20:
            print(f"    … and {len(breaches) - 20} more", file=sys.stderr)

    print(
        f"\nA line whose character is data, not prose, may opt out with "
        f"`{PRAGMA}<rule-id>`.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root to scan (default: this repository).",
    )
    args = parser.parse_args(argv)
    return report(violations(args.root))


if __name__ == "__main__":
    raise SystemExit(main())
