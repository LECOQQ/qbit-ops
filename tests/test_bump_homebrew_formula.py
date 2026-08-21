"""Test the Homebrew formula bumper.

The property that matters is negative: the script must rewrite the
formula's own `url`/`sha256` and **never** a `resource` block's. Those
pin the dependency closure, and silently repointing one at qbit-ops'
own tarball would produce a formula that still installs -- with a
dependency replaced by the wrong archive.
"""

from pathlib import Path

import pytest

from scripts.bump_homebrew_formula import BumpError, bump

NEW_URL = (
    "https://files.pythonhosted.org/packages/aa/bb/cc/qbit_ops-9.9.9.tar.gz"
)
NEW_SHA = "9" * 64

FORMULA = """class QbitOps < Formula
  include Language::Python::Virtualenv

  desc "Tiny qBittorrent CLI"
  url "https://files.pythonhosted.org/packages/old/qbit_ops-0.4.0.tar.gz"
  sha256 "4ffd3bd3a6e9c952c6a0a3ecc773e70368e9c1a88e94144b95b2e3d7fccc1472"
  license "MIT"

  resource "rich" do
    url "https://files.pythonhosted.org/packages/rich/rich-15.0.0.tar.gz"
    sha256 "aaaa000000000000000000000000000000000000000000000000000000000000"
  end

  resource "typer" do
    url "https://files.pythonhosted.org/packages/typer/typer-1.0.0.tar.gz"
    sha256 "bbbb000000000000000000000000000000000000000000000000000000000000"
  end
end
"""


def _bumped() -> str:
    updated, changed = bump(FORMULA, url=NEW_URL, sha256=NEW_SHA)
    assert changed is True
    return updated


# --- The formula's own coordinates move ------------------------------------


def test_the_formula_url_and_sha_are_rewritten() -> None:
    updated = _bumped()

    assert f'url "{NEW_URL}"' in updated
    assert f'sha256 "{NEW_SHA}"' in updated


def test_indentation_survives() -> None:
    """A formula Homebrew cannot parse is worse than one out of date."""
    updated = _bumped()

    assert f'  url "{NEW_URL}"' in updated
    assert f'  sha256 "{NEW_SHA}"' in updated


# --- The resource blocks do not ---------------------------------------------


def test_no_resource_url_is_touched() -> None:
    updated = _bumped()

    assert "rich/rich-15.0.0.tar.gz" in updated
    assert "typer/typer-1.0.0.tar.gz" in updated


def test_no_resource_sha_is_touched() -> None:
    updated = _bumped()

    assert "aaaa" + "0" * 60 in updated
    assert "bbbb" + "0" * 60 in updated


def test_exactly_two_lines_change() -> None:
    """Counted rather than asserted per-line: a rewrite that also touched
    something unnamed would pass every check above."""
    updated = _bumped()

    before = FORMULA.splitlines()
    after = updated.splitlines()
    assert len(before) == len(after)
    pairs = zip(before, after, strict=True)
    differing = [i for i, (a, b) in enumerate(pairs) if a != b]
    assert len(differing) == 2


# --- Idempotence and refusal ------------------------------------------------


def test_rerunning_reports_no_change() -> None:
    """Or the release workflow would push an empty commit each run."""
    once, _ = bump(FORMULA, url=NEW_URL, sha256=NEW_SHA)
    twice, changed = bump(once, url=NEW_URL, sha256=NEW_SHA)

    assert changed is False
    assert twice == once


def test_a_formula_without_top_level_coordinates_is_refused() -> None:
    """Guessing would edit the first resource instead."""
    resource_only = (
        'class X < Formula\n  resource "a" do\n    url "u"\n'
        '    sha256 "s"\n  end\nend\n'
    )

    with pytest.raises(BumpError, match="shape changed"):
        bump(resource_only, url=NEW_URL, sha256=NEW_SHA)


def test_the_script_is_executable() -> None:
    script = Path(__file__).resolve().parent.parent / "scripts"
    path = script / "bump_homebrew_formula.py"
    assert path.stat().st_mode & 0o111, f"{path} must be executable"
