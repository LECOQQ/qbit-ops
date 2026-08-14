"""Tests for scripts/docker_tags.py.

Fully hermetic: the tag list is injected, so nothing here queries a Git
remote or a container registry.

The scenarios that matter are the ones where the naive implementation
was wrong -- republishing an older release, and backporting a patch to
an older line after a newer minor already shipped.
"""

from __future__ import annotations

from pathlib import Path

import docker_tags as dt
import pytest

# A repository that has shipped 0.3.x, 0.4.x and 0.5.0, plus a
# prerelease that must never rank.
RELEASED = [
    "v0.3.0",
    "v0.4.0",
    "v0.4.1",
    "v0.5.0",
    "v0.6.0-rc1",
]


def test_a_normal_release_moves_every_floating_tag() -> None:
    plan = dt.plan_docker_tags("v0.5.0", RELEASED)

    assert (plan.version, plan.minor) == ("0.5.0", "0.5")
    assert plan.is_newest is True
    assert plan.is_newest_in_minor is True


def test_an_older_manual_redispatch_moves_neither_floating_tag() -> None:
    """The regression this exists for: rerunning v0.4.0 by hand once
    v0.4.1 and v0.5.0 are out must not drag `latest` or `0.4` back."""
    plan = dt.plan_docker_tags("v0.4.0", RELEASED)

    assert plan.version == "0.4.0"
    assert plan.is_newest is False
    assert plan.is_newest_in_minor is False


def test_a_backport_moves_its_line_but_never_latest() -> None:
    """v0.4.1 is the newest of the 0.4 line, but 0.5.0 already shipped."""
    plan = dt.plan_docker_tags("v0.4.1", RELEASED)

    assert plan.minor == "0.4"
    assert plan.is_newest_in_minor is True
    assert plan.is_newest is False


def test_a_backport_published_after_a_newer_minor_still_moves_its_line() -> (
    None
):
    """The real backport shape: 0.4.2 is cut *after* 0.5.0 exists."""
    plan = dt.plan_docker_tags("v0.4.2", [*RELEASED, "v0.4.2"])

    assert plan.is_newest_in_minor is True
    assert plan.is_newest is False


def test_the_very_first_release_moves_everything() -> None:
    plan = dt.plan_docker_tags("v0.1.0", ["v0.1.0"])

    assert (plan.is_newest, plan.is_newest_in_minor) == (True, True)


def test_ranking_is_numeric_not_lexicographic() -> None:
    """`v1.10.0` outranks `v1.9.0`; string ordering says otherwise."""
    tags = ["v1.9.0", "v1.10.0"]

    assert dt.plan_docker_tags("v1.10.0", tags).is_newest is True
    assert dt.plan_docker_tags("v1.9.0", tags).is_newest is False


def test_patch_ranking_within_a_line_is_numeric_too() -> None:
    tags = ["v1.0.2", "v1.0.10"]

    assert dt.plan_docker_tags("v1.0.10", tags).is_newest_in_minor is True
    assert dt.plan_docker_tags("v1.0.2", tags).is_newest_in_minor is False


def test_a_prerelease_never_outranks_a_stable_release() -> None:
    """A pending v0.6.0-rc1 must not stop v0.5.0 from taking `latest`."""
    assert dt.plan_docker_tags("v0.5.0", RELEASED).is_newest is True


@pytest.mark.parametrize(
    "tag",
    ["v0.6.0-rc1", "v0.4", "0.4.0", "latest", "main", "v0.4.0+build1", ""],
)
def test_only_stable_release_tags_are_publishable(tag: str) -> None:
    with pytest.raises(dt.DockerTagError, match="only stable"):
        dt.plan_docker_tags(tag, [*RELEASED, tag])


def test_an_unknown_tag_fails_loudly_instead_of_silently_skipping() -> None:
    """Treating an unpushed tag as "not newest" would quietly withhold
    `latest` on a genuine release."""
    with pytest.raises(dt.DockerTagError, match="not among"):
        dt.plan_docker_tags("v9.9.9", RELEASED)


def test_the_exact_version_tag_is_always_published() -> None:
    """Whatever the ranking says, X.Y.Z is never suppressed."""
    for tag in ("v0.3.0", "v0.4.0", "v0.4.1", "v0.5.0"):
        plan = dt.plan_docker_tags(tag, RELEASED)
        assert plan.version == tag.removeprefix("v")


# --- CLI ---------------------------------------------------------------


def _tags_file(tmp_path: Path) -> Path:
    path = tmp_path / "tags.txt"
    path.write_text("\n".join(RELEASED), encoding="utf-8")
    return path


def test_the_cli_emits_github_output_pairs(tmp_path: Path, capsys) -> None:
    exit_code = dt.main(
        ["--tag", "v0.5.0", "--tags-file", str(_tags_file(tmp_path))]
    )

    assert exit_code == 0
    assert set(capsys.readouterr().out.split()) == {
        "version=0.5.0",
        "minor=0.5",
        "is_newest=true",
        "is_newest_in_minor=true",
    }


def test_the_cli_reports_false_for_an_older_redispatch(
    tmp_path: Path, capsys
) -> None:
    exit_code = dt.main(
        ["--tag", "v0.4.0", "--tags-file", str(_tags_file(tmp_path))]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "is_newest=false" in captured.out
    assert "is_newest_in_minor=false" in captured.out
    # The operator must be told why the floating tags stayed put.
    assert "leaving 'latest' untouched" in captured.err


def test_the_cli_fails_on_a_prerelease(tmp_path: Path, capsys) -> None:
    exit_code = dt.main(
        ["--tag", "v0.6.0-rc1", "--tags-file", str(_tags_file(tmp_path))]
    )

    assert exit_code == 1
    assert "only stable" in capsys.readouterr().err
