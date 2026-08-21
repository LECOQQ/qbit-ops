#!/usr/bin/env python3
"""Point a Homebrew formula at a newly published PyPI sdist.

Run after the release is live on PyPI, never before: the formula pins a
SHA-256, and that hash does not exist until the artifact has been built
and uploaded. A release-please PR is written before the tag, so it
cannot carry this value -- the file it would hash has not been made yet.

    release PR -> merge -> tag -> publish.yml -> sdist on PyPI
                                                  ^ the hash exists here

Rewrites exactly two lines: the formula's own `url` and `sha256`. The
`resource` blocks are left alone -- they pin the dependency closure, and
regenerating those is `brew update-python-resources`, a different job
needing Homebrew itself.

Idempotent: a formula already pointing at the requested artifact is left
untouched and reported as such, so a re-run cannot produce an empty
commit.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

PYPI_JSON = "https://pypi.org/pypi/{package}/{version}/json"

# The formula's own url/sha are the ones before the first `resource`
# block. Anchored on that boundary rather than on "the first match":
# both read the same today, and only this one keeps reading correctly if
# a future formula grows a `head` or `bottle` stanza above them.
RESOURCE_START = re.compile(r"^\s*resource\s+\"", re.MULTILINE)
URL_LINE = re.compile(r'^(?P<indent>\s*)url\s+"(?P<value>[^"]*)"', re.MULTILINE)
SHA_LINE = re.compile(
    r'^(?P<indent>\s*)sha256\s+"(?P<value>[^"]*)"', re.MULTILINE
)


class BumpError(RuntimeError):
    """A refusal that names what to do about it."""


def resolve_sdist(package: str, version: str) -> tuple[str, str]:
    """The `(url, sha256)` of one release's source distribution.

    Reads PyPI rather than recomputing locally: the URL path is derived
    from a digest PyPI itself assigns, so it cannot be constructed from
    the file. Wheels are ignored -- Homebrew builds from source.
    """
    endpoint = PYPI_JSON.format(package=package, version=version)
    try:
        with urllib.request.urlopen(endpoint, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise BumpError(
                f"{package} {version} is not on PyPI. Publish the release "
                "before bumping the tap -- the hash does not exist yet."
            ) from error
        raise BumpError(f"PyPI returned {error.code} for {endpoint}") from error
    except OSError as error:
        raise BumpError(f"Could not reach PyPI: {error}") from error

    for entry in payload.get("urls", []):
        if entry.get("packagetype") == "sdist":
            return entry["url"], entry["digests"]["sha256"]

    raise BumpError(
        f"{package} {version} has no source distribution on PyPI. A "
        "Homebrew formula builds from source and cannot use a wheel."
    )


def _split_head(text: str) -> tuple[str, str]:
    """`(before the first resource block, the rest)`."""
    match = RESOURCE_START.search(text)
    if match is None:
        return text, ""
    return text[: match.start()], text[match.start() :]


def bump(text: str, *, url: str, sha256: str) -> tuple[str, bool]:
    """Rewrite the formula head. Returns the text and whether it changed."""
    head, tail = _split_head(text)

    url_match = URL_LINE.search(head)
    sha_match = SHA_LINE.search(head)
    if url_match is None or sha_match is None:
        raise BumpError(
            "No top-level `url`/`sha256` found before the first `resource` "
            "block. The formula's shape changed -- update this script "
            "rather than letting it edit the wrong line."
        )

    if url_match.group("value") == url and sha_match.group("value") == sha256:
        return text, False

    head = URL_LINE.sub(
        lambda m: f'{m.group("indent")}url "{url}"', head, count=1
    )
    head = SHA_LINE.sub(
        lambda m: f'{m.group("indent")}sha256 "{sha256}"', head, count=1
    )
    return head + tail, True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("formula", type=Path, help="path to the .rb formula")
    parser.add_argument("--version", required=True, help="release version")
    parser.add_argument("--package", default="qbit-ops")
    parser.add_argument("--url", help="skip the PyPI lookup (testing)")
    parser.add_argument("--sha256", help="skip the PyPI lookup (testing)")
    parser.add_argument(
        "--check",
        action="store_true",
        help="report whether a bump is needed without writing",
    )
    args = parser.parse_args(argv)

    version = args.version.removeprefix("v")

    try:
        if args.url and args.sha256:
            url, sha256 = args.url, args.sha256
        elif args.url or args.sha256:
            raise BumpError("--url and --sha256 go together, or neither.")
        else:
            url, sha256 = resolve_sdist(args.package, version)

        original = args.formula.read_text(encoding="utf-8")
        updated, changed = bump(original, url=url, sha256=sha256)
    except BumpError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if not changed:
        print(f"Already at {args.package} {version}; nothing to write.")
        return 0

    if args.check:
        print(f"Would bump to {args.package} {version}.")
        return 0

    args.formula.write_text(updated, encoding="utf-8")
    print(f"Bumped {args.formula} to {args.package} {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
