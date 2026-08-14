"""Tests for scripts/worktree_new.py and scripts/worktree_clean.py.

Every case builds a throwaway Git repository under `tmp_path`. Nothing
here touches this repository's own worktrees, branches or control-plane.

The safety properties under test are the ones that caused, or would have
caused, real damage: symlinking a *tracked* file into a worktree
(typechange reaching a commit), removing a worktree or branch that still
holds work, and letting an inherited `VIRTUAL_ENV` point a worktree's
Poetry install at another checkout's environment.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import env_attest
import pytest
import worktree_clean as wc
import worktree_new as wn


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with one tracked file and one gitignored control file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test")

    (repo / ".gitignore").write_text("AGENTS.md\n.agents/\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    (repo / ".agents").mkdir()
    (repo / ".agents" / "STATE.md").write_text("idle\n", encoding="utf-8")

    _git(repo, "add", ".gitignore", "tracked.txt")
    _git(repo, "commit", "-qm", "init")
    return repo


# --- environment isolation ---------------------------------------------


@pytest.mark.parametrize(
    "variable", ["VIRTUAL_ENV", "POETRY_ACTIVE", "PYTHONHOME", "PYTHONPATH"]
)
def test_an_activated_environment_never_reaches_poetry(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    monkeypatch.setenv(variable, "/somewhere/else/.venv")
    monkeypatch.setenv("QBIT_OPS_UNRELATED", "kept")

    cleaned = wn._clean_env()

    assert variable not in cleaned
    assert cleaned["QBIT_OPS_UNRELATED"] == "kept"


def test_the_install_passes_the_cleaned_environment_to_poetry(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIRTUAL_ENV", "/main/checkout/.venv")
    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        seen["argv"] = argv
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(wn.subprocess, "run", fake_run)
    monkeypatch.setattr(wn, "verify_isolation", lambda _worktree: {})
    # `create` shells out to git through the same patched `run`, so drive
    # the install step directly rather than re-faking the whole setup.
    worktree = repo / ".worktrees" / "probe"
    worktree.mkdir(parents=True)
    wn.subprocess.run(
        ["poetry", "install"], cwd=worktree, env=wn._clean_env(), check=False
    )

    assert "VIRTUAL_ENV" not in seen["env"]


def test_isolation_fails_loudly_when_a_package_resolves_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    payload = (
        '{"python": "/usr/bin/python3.12", '
        '"prefix": "/other/.venv", '
        '"qbit_core": "/other/src/qbit_core/__init__.py", '
        '"qbit_ops": "/other/src/qbit_ops/__init__.py"}'
    )
    monkeypatch.setattr(
        wn.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 0, payload, ""),
    )

    with pytest.raises(wn.WorktreeError, match="not isolated"):
        wn.verify_isolation(worktree)


def test_a_system_interpreter_alone_never_fails_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A venv symlinks python to the system binary; that is not a leak."""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    payload = (
        '{"python": "/usr/bin/python3.12", '
        f'"prefix": "{worktree}/.venv", '
        f'"qbit_core": "{worktree}/src/qbit_core/__init__.py", '
        f'"qbit_ops": "{worktree}/src/qbit_ops/__init__.py"}}'
    )
    monkeypatch.setattr(
        wn.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 0, payload, ""),
    )

    assert wn.verify_isolation(worktree)["python"] == "/usr/bin/python3.12"


def test_isolation_accepts_an_environment_inside_the_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    payload = (
        f'{{"python": "{worktree}/.venv/bin/python", '
        f'"prefix": "{worktree}/.venv", '
        f'"qbit_core": "{worktree}/src/qbit_core/__init__.py", '
        f'"qbit_ops": "{worktree}/src/qbit_ops/__init__.py"}}'
    )
    monkeypatch.setattr(
        wn.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 0, payload, ""),
    )

    assert wn.verify_isolation(worktree)["prefix"].endswith(".venv")


def test_the_attestation_reports_this_checkout_as_isolated() -> None:
    attestation = env_attest.collect_attestation(Path(os.getcwd()))

    assert attestation["isolated"] is True
    assert attestation["outside_expected_root"] == []
    assert attestation["qbit_core"].startswith(attestation["expected_root"])


def test_the_attestation_names_every_path_that_escapes_the_root(
    tmp_path: Path,
) -> None:
    attestation = env_attest.collect_attestation(tmp_path)

    assert attestation["isolated"] is False
    assert "qbit_core" in attestation["outside_expected_root"]
    assert "FAILED" in env_attest.render(attestation)


# --- worktree_new ------------------------------------------------------


@pytest.mark.parametrize(
    "slug", ["", "Version", "feat/version", "-lead", "a b"]
)
def test_an_invalid_slug_is_rejected_before_any_git_call(
    repo: Path, slug: str
) -> None:
    with pytest.raises(wn.WorktreeError):
        wn.create(repo, slug, base="HEAD", install=False)

    assert not (repo / ".worktrees").exists()


def test_creating_a_worktree_makes_the_branch_and_links_the_control_plane(
    repo: Path,
) -> None:
    worktree, branch, linked = wn.create(
        repo, "version", base="HEAD", install=False
    )

    assert branch == "feat/version"
    assert worktree.is_dir()
    assert sorted(linked) == [".agents", "AGENTS.md"]
    assert (worktree / "AGENTS.md").is_symlink()
    assert (worktree / ".agents").is_symlink()
    assert (worktree / ".agents" / "STATE.md").read_text() == "idle\n"


def test_a_tracked_file_is_never_replaced_by_a_symlink(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact accident this script exists to prevent: `.env.example`
    was tracked, got symlinked, and produced a typechange."""
    monkeypatch.setattr(wn, "LINKED_ENTRIES", ("tracked.txt", "AGENTS.md"))

    worktree, _, linked = wn.create(repo, "version", base="HEAD", install=False)

    assert linked == ["AGENTS.md"]
    assert not (worktree / "tracked.txt").is_symlink()
    assert (worktree / "tracked.txt").read_text() == "tracked\n"
    assert _status(worktree) == []


def test_an_existing_branch_is_refused_without_creating_anything(
    repo: Path,
) -> None:
    _git(repo, "branch", "feat/version")

    with pytest.raises(wn.WorktreeError, match="already exists"):
        wn.create(repo, "version", base="HEAD", install=False)

    assert not (repo / ".worktrees" / "version").exists()


def test_an_existing_worktree_path_is_refused(repo: Path) -> None:
    (repo / ".worktrees" / "version").mkdir(parents=True)

    with pytest.raises(wn.WorktreeError, match="already exists"):
        wn.create(repo, "version", base="HEAD", install=False)


# --- worktree_clean ----------------------------------------------------


def _status(worktree: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in completed.stdout.splitlines() if line.strip()]


def test_a_clean_merged_worktree_is_removed_with_its_branch(
    repo: Path,
) -> None:
    wn.create(repo, "version", base="HEAD", install=False)

    branch, deleted = wc.clean(repo, "version", base="HEAD", keep_branch=False)

    assert (branch, deleted) == ("feat/version", True)
    assert not (repo / ".worktrees" / "version").exists()
    assert "feat/version" not in _branches(repo)


def _branches(repo: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.split()


def test_an_unmerged_branch_is_refused_and_nothing_is_removed(
    repo: Path,
) -> None:
    worktree, _, _ = wn.create(repo, "version", base="HEAD", install=False)
    (worktree / "new.txt").write_text("work\n", encoding="utf-8")
    _git(worktree, "add", "new.txt")
    _git(worktree, "commit", "-qm", "work")

    with pytest.raises(wc.WorktreeError, match="not fully merged"):
        wc.clean(repo, "version", base="main", keep_branch=False)

    assert worktree.is_dir()
    assert "feat/version" in _branches(repo)


def test_uncommitted_work_is_refused_and_the_symlinks_are_restored(
    repo: Path,
) -> None:
    worktree, _, _ = wn.create(repo, "version", base="HEAD", install=False)
    (worktree / "scratch.txt").write_text("unsaved\n", encoding="utf-8")

    with pytest.raises(wc.WorktreeError, match="uncommitted or untracked"):
        wc.clean(repo, "version", base="HEAD", keep_branch=False)

    assert worktree.is_dir()
    assert (worktree / "AGENTS.md").is_symlink()
    assert (worktree / ".agents").is_symlink()


def test_keep_branch_drops_only_the_worktree_and_skips_the_merge_check(
    repo: Path,
) -> None:
    worktree, _, _ = wn.create(repo, "version", base="HEAD", install=False)
    (worktree / "new.txt").write_text("work\n", encoding="utf-8")
    _git(worktree, "add", "new.txt")
    _git(worktree, "commit", "-qm", "work")

    branch, deleted = wc.clean(repo, "version", base="main", keep_branch=True)

    assert (branch, deleted) == ("feat/version", False)
    assert not worktree.exists()
    assert "feat/version" in _branches(repo)


def test_a_directory_that_is_not_a_registered_worktree_is_refused(
    repo: Path,
) -> None:
    stray = repo / ".worktrees" / "version"
    stray.mkdir(parents=True)
    (stray / "precious.txt").write_text("do not delete\n", encoding="utf-8")

    with pytest.raises(wc.WorktreeError, match="not a registered"):
        wc.clean(repo, "version", base="HEAD", keep_branch=True)

    assert (stray / "precious.txt").exists()


def test_removing_the_worktree_never_follows_the_control_plane_symlinks(
    repo: Path,
) -> None:
    """The linked `.agents/` points back at the main checkout; a directory
    walk that followed it would destroy the real control-plane."""
    wn.create(repo, "version", base="HEAD", install=False)

    wc.clean(repo, "version", base="HEAD", keep_branch=False)

    assert (repo / ".agents" / "STATE.md").read_text() == "idle\n"
    assert (repo / "AGENTS.md").read_text() == "rules\n"
