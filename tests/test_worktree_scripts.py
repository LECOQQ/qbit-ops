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

import check_commit_provenance as provenance
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

    (repo / ".gitignore").write_text(
        "AGENTS.md\n.agents\n.harness\n.claude/\n", encoding="utf-8"
    )
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    (repo / ".agents").mkdir()
    (repo / ".agents" / "STATE.md").write_text("idle\n", encoding="utf-8")

    _git(repo, "add", ".gitignore", "tracked.txt")
    _git(repo, "commit", "-qm", "init")
    return repo


@pytest.fixture(autouse=True)
def _no_real_harness(monkeypatch: pytest.MonkeyPatch) -> None:
    """The installed `agent-harness` writes outside `tmp_path`."""
    monkeypatch.setattr(wn, "HARNESS_COMMAND", "agent-harness-not-in-tests")


def _fake_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    script = tmp_path / "bin" / "agent-harness"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setattr(wn, "HARNESS_COMMAND", str(script))


@pytest.fixture
def fake_harness(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """A stand-in that places the three links `agent-harness link` places.

    Returns the file recording each call's arguments.
    """
    harness = tmp_path / "harness"
    harness.mkdir()
    (harness / "WORKFLOW.md").write_text("workflow\n", encoding="utf-8")
    calls = tmp_path / "harness-calls"
    _fake_command(
        tmp_path,
        monkeypatch,
        f'printf "%s\\n" "$*" >> "{calls}"\n'
        'mkdir -p "$2/.claude/rules"\n'
        f'ln -s "{repo / ".agents"}" "$2/.agents"\n'
        f'ln -s "{harness}" "$2/.harness"\n'
        f'ln -s "{repo / "AGENTS.md"}" "$2/.claude/rules/agent-harness.md"\n',
    )
    return calls


def _fake_venv(worktree: Path) -> None:
    """Create the interpreter `verify_isolation` refuses to provision."""
    interpreter = worktree / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()


# --- environment isolation ---------------------------------------------


def test_an_activated_environment_never_reaches_poetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIRTUAL_ENV", "/somewhere/else/.venv")
    monkeypatch.setenv("QBIT_OPS_UNRELATED", "kept")

    cleaned = wn._clean_env()

    assert "VIRTUAL_ENV" not in cleaned
    assert cleaned["QBIT_OPS_UNRELATED"] == "kept"


def test_the_venv_location_is_stated_never_inferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Poetry's own inference proved unstable across invocation contexts."""
    monkeypatch.delenv("POETRY_VIRTUALENVS_IN_PROJECT", raising=False)

    assert wn._clean_env()["POETRY_VIRTUALENVS_IN_PROJECT"] == "true"


def test_the_install_passes_the_cleaned_environment_to_poetry(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIRTUAL_ENV", "/main/checkout/.venv")
    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        seen["argv"] = argv
        seen["env"] = kwargs.get("env")
        # `rev-parse` probes whether the branch already exists and
        # `ls-files` whether an entry is tracked: both must answer "no"
        # for `create` to reach the install step at all.
        absent = argv[1] in {"rev-parse", "ls-files"}
        if argv[0] == "git" and argv[1] == "worktree":
            (repo / ".worktrees" / "probe").mkdir(parents=True)
        return subprocess.CompletedProcess(argv, 1 if absent else 0, "", "")

    monkeypatch.setattr(wn.subprocess, "run", fake_run)
    monkeypatch.setattr(wn, "verify_isolation", lambda _worktree: {})

    wn.create(repo, "probe", base="main", install=True)

    assert seen["argv"][:2] == ["poetry", "install"]
    assert seen["env"] is not None, (
        "`poetry install` ran with the inherited environment. It then "
        "installs the worktree's `src/` into the main checkout's venv and "
        "reports success."
    )
    assert "VIRTUAL_ENV" not in seen["env"]


def test_isolation_fails_loudly_when_a_package_resolves_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    _fake_venv(worktree)
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
    _fake_venv(worktree)
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
    _fake_venv(worktree)
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
    repo: Path, fake_harness: Path
) -> None:
    worktree, branch, linked = wn.create(
        repo, "version", base="HEAD", install=False
    )

    assert branch == "feat/version"
    assert worktree.is_dir()
    assert fake_harness.read_text() == f"link {worktree}\n"
    assert sorted(linked) == [
        ".agents",
        ".claude/rules/agent-harness.md",
        ".harness",
        "AGENTS.md",
    ]
    assert (worktree / "AGENTS.md").is_symlink()
    assert (worktree / ".agents" / "STATE.md").read_text() == "idle\n"
    assert _status(worktree) == []


def test_without_agent_harness_the_worktree_is_still_created(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    worktree, _, linked = wn.create(repo, "version", base="HEAD", install=False)

    assert linked == ["AGENTS.md"]
    assert not (worktree / ".agents").exists()
    err = capsys.readouterr().err
    assert "not installed" in err
    assert f"link {worktree}`" in err


def test_a_refused_harness_link_does_not_stop_the_worktree(
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _fake_command(tmp_path, monkeypatch, "exit 1\n")

    worktree, _, linked = wn.create(repo, "version", base="HEAD", install=False)

    assert worktree.is_dir()
    assert linked == ["AGENTS.md"]
    assert "[WARN]" in capsys.readouterr().err


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

    with pytest.raises(wc.WorktreeError, match="neither an ancestor"):
        wc.clean(repo, "version", base="main", keep_branch=False)

    assert worktree.is_dir()
    assert "feat/version" in _branches(repo)


def test_uncommitted_work_is_refused_and_the_symlinks_are_restored(
    repo: Path, fake_harness: Path
) -> None:
    worktree, _, linked = wn.create(repo, "version", base="HEAD", install=False)
    targets = {entry: os.readlink(worktree / entry) for entry in linked}
    (worktree / "scratch.txt").write_text("unsaved\n", encoding="utf-8")

    with pytest.raises(wc.WorktreeError, match="uncommitted or untracked"):
        wc.clean(repo, "version", base="HEAD", keep_branch=False)

    assert worktree.is_dir()
    assert len(targets) == 4
    for entry, target in targets.items():
        assert os.readlink(worktree / entry) == target


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
    repo: Path, tmp_path: Path, fake_harness: Path
) -> None:
    """The linked `.agents/` points back at the main checkout; a directory
    walk that followed it would destroy the real control-plane."""
    wn.create(repo, "version", base="HEAD", install=False)

    wc.clean(repo, "version", base="HEAD", keep_branch=False)

    assert (repo / ".agents" / "STATE.md").read_text() == "idle\n"
    assert (repo / "AGENTS.md").read_text() == "rules\n"
    assert (tmp_path / "harness" / "WORKFLOW.md").read_text() == "workflow\n"


def test_a_worktree_path_is_not_treated_as_part_of_the_main_checkout() -> None:
    """Worktrees nest under the main root, so `is_relative_to` is not enough."""
    root = Path("/repo")

    assert env_attest.belongs_to(Path("/repo/src/qbit_core/__init__.py"), root)
    assert not env_attest.belongs_to(
        Path("/repo/.worktrees/feat/src/qbit_core/__init__.py"), root
    )
    assert not env_attest.belongs_to(Path("/elsewhere/src"), root)


def test_a_main_environment_pointing_at_a_worktree_is_not_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    leaked = root / ".worktrees" / "feat" / "src" / "qbit_core" / "__init__.py"
    leaked.parent.mkdir(parents=True)
    leaked.touch()
    monkeypatch.setattr(env_attest.qbit_core, "__file__", str(leaked))

    attestation = env_attest.collect_attestation(root)

    assert attestation["isolated"] is False
    assert "qbit_core" in attestation["outside_expected_root"]


def test_verification_refuses_to_provision_a_missing_virtualenv(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    with pytest.raises(wn.WorktreeError, match="installed the project"):
        wn.verify_isolation(worktree)


# --- integration proofs -------------------------------------------------


def _commit(repo: Path, name: str, content: str, message: str) -> None:
    (repo / name).write_text(content, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", message)


def test_a_squashed_branch_is_proven_by_content_not_by_ancestry(
    repo: Path,
) -> None:
    """After a squash the branch is deliberately not an ancestor."""
    _git(repo, "checkout", "-qb", "feat/x")
    _commit(repo, "a.txt", "a\n", "feat: a")
    _commit(repo, "b.txt", "b\n", "feat: b")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--squash", "-q", "feat/x")
    _git(repo, "commit", "-qm", "feat: delivered")

    assert not wc._is_merged(repo, "feat/x", "main")

    equivalent, branch_tree, base_tree = wc.squash_equivalence(
        repo, "feat/x", "main"
    )
    assert equivalent
    assert branch_tree == base_tree


def test_a_foreign_change_breaks_the_equivalence_proof(repo: Path) -> None:
    _git(repo, "checkout", "-qb", "feat/x")
    _commit(repo, "a.txt", "a\n", "feat: a")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--squash", "-q", "feat/x")
    _git(repo, "commit", "-qm", "feat: delivered")
    _commit(repo, "stray.txt", "stray\n", "chore: stray")

    equivalent, branch_tree, base_tree = wc.squash_equivalence(
        repo, "feat/x", "main"
    )
    assert not equivalent
    assert branch_tree != base_tree


def test_a_lost_change_breaks_the_equivalence_proof(repo: Path) -> None:
    """A squash that drops an approved change must not pass."""
    _git(repo, "checkout", "-qb", "feat/x")
    _commit(repo, "a.txt", "a\n", "feat: a")
    _commit(repo, "b.txt", "b\n", "feat: b")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--squash", "-q", "feat/x")
    (repo / "b.txt").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "feat: delivered without b")

    equivalent, _, _ = wc.squash_equivalence(repo, "feat/x", "main")
    assert not equivalent


def test_an_unintegrated_branch_is_refused_by_both_proofs(repo: Path) -> None:
    _git(repo, "checkout", "-qb", "feat/x")
    _commit(repo, "a.txt", "a\n", "feat: a")
    _git(repo, "checkout", "-q", "main")

    assert not wc._is_merged(repo, "feat/x", "main")
    assert not wc.squash_equivalence(repo, "feat/x", "main")[0]


def _rebase_merge(
    repo: Path, branch: str, count: int, *, skip_last: bool = False
) -> None:
    """Replay the branch's last commits on main, rewriting their hashes."""
    _commit(repo, "other.txt", "other\n", "chore: other")
    commits = [f"{branch}~{offset}" for offset in range(count - 1, -1, -1)]
    if skip_last:
        commits = commits[:-1]
    _git(repo, "cherry-pick", *commits)
    _commit(repo, "later.txt", "later\n", "chore: later")


def test_a_rebase_merged_branch_is_removed_though_main_moved_on(
    repo: Path,
) -> None:
    worktree, _, _ = wn.create(repo, "x", base="HEAD", install=False)
    _commit(worktree, "a.txt", "a\n", "feat: a")
    _commit(worktree, "b.txt", "b\n", "feat: b")
    _rebase_merge(repo, "feat/x", 2)

    assert not wc._is_merged(repo, "feat/x", "main")
    assert not wc.squash_equivalence(repo, "feat/x", "main")[0]

    branch, deleted = wc.clean(repo, "x", base="main", keep_branch=False)

    assert (branch, deleted) == ("feat/x", True)
    assert not worktree.exists()
    assert "feat/x" not in _branches(repo)


def test_a_rebase_merge_missing_a_commit_is_refused(repo: Path) -> None:
    worktree, _, _ = wn.create(repo, "x", base="HEAD", install=False)
    _commit(worktree, "a.txt", "a\n", "feat: a")
    _commit(worktree, "b.txt", "b\n", "feat: b")
    _rebase_merge(repo, "feat/x", 2, skip_last=True)

    with pytest.raises(wc.WorktreeError, match="feat: b"):
        wc.clean(repo, "x", base="main", keep_branch=False)

    assert worktree.is_dir()
    assert "feat/x" in _branches(repo)


# --- commit provenance --------------------------------------------------


@pytest.mark.parametrize(
    "trailer",
    [
        "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>",
        "co-authored-by: claude <x@y.z>",
        "  Co-Authored-By: Anthropic <a@b.c>",
        "Co-Authored-By: GitHub Copilot <c@d.e>",
    ],
)
def test_an_agent_co_author_is_refused(trailer: str) -> None:
    message = f"feat(x): do something\n\nbody here\n\n{trailer}\n"

    assert provenance.offending_lines(message) == [trailer.strip()]


@pytest.mark.parametrize(
    "line",
    [
        "Claude-Session: https://claude.ai/code/session_01Y2gKBv",
        "claude-session: https://example.test/x",
        "  Codex-Session: https://example.test/y",
        "Copilot Session: https://example.test/z",
    ],
)
def test_a_session_trailer_is_refused(line: str) -> None:
    """A session names a conversation nobody else can open, so the
    narrowing that keeps a human co-author legitimate does not apply."""
    message = f"feat(x): do something\n\n{line}\n"
    assert provenance.offending_lines(message) == [line.strip()]


@pytest.mark.parametrize(
    "line",
    [
        # The fixture of one gate is the violation of another: these
        # lines are exactly what `generation-notice` forbids anywhere in
        # the repository, and they have to appear here to be tested.
        # ai-hygiene: allow-generation-notice
        "🤖 Generated with [Claude Code](https://claude.com/cc)",
        "Generated with Claude Code",  # ai-hygiene: allow-generation-notice
        "- generated with GitHub Copilot",
    ],
)
def test_a_generated_with_footer_is_refused(line: str) -> None:
    """It is a pull-request footer, and it reaches a commit message by
    copy. Matched anywhere in the line, because it has been seen behind
    an emoji and behind a bullet."""
    message = f"feat(x): do something\n\n{line}\n"
    assert provenance.offending_lines(message) == [line.strip()]


def test_prose_that_merely_mentions_a_session_is_left_alone() -> None:
    """The session pattern anchors like a Git trailer, so a body that
    talks about sessions is not a provenance marker."""
    message = (
        "feat(x): do something\n\n"
        "- a normal bullet about a session cache\n"
        "- and one naming Claude Code as a tool we use\n"
    )
    assert provenance.offending_lines(message) == []


def test_a_human_co_author_stays_legitimate() -> None:
    message = (
        "feat(x): do something\n\nCo-Authored-By: Quentin <q@example.test>\n"
    )

    assert provenance.offending_lines(message) == []


def test_a_clean_message_passes() -> None:
    assert provenance.offending_lines("fix(y): repair the thing\n") == []


def test_the_commit_msg_hook_refuses_and_names_the_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text(
        "feat(x): thing\n\nCo-Authored-By: Claude <n@a.c>\n", encoding="utf-8"
    )

    exit_code = provenance.main(["--commit-msg-file", str(message)])

    assert exit_code == 1
    assert "Co-Authored-By: Claude" in capsys.readouterr().err
