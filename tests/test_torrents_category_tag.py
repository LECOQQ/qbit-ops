"""Test bulk `category`/`tag` assignment.

See `.agents/specs/bulk-category-tag.md`.

Domain-level (`plan_bulk_torrent_action`/`apply_bulk_torrent_action`/
`resolve_category_availability`) and CLI-level coverage for the four new
commands, all routed through `_run_bulk_torrent_action` exactly like
`pause`/`resume`/`start`/`reannounce`/`delete`.
"""

import json

import pytest
from typer.testing import CliRunner

from qbit_core.features.torrents import (
    apply_bulk_torrent_action,
    plan_bulk_torrent_action,
    resolve_category_availability,
)
from qbit_ops.cli.app import app
from qbit_ops.cli.exit_codes import ExitCode
from tests.support import FakeQbitClient, make_torrent

TORRENT_A = "abc123def456000000000000000000000000000a"
TORRENT_B = "abc987fed654000000000000000000000000000b"


# --- resolve_category_availability: the existence gate ----------------------


def test_resolve_category_availability_raises_without_create() -> None:
    client = FakeQbitClient(categories={"movies": {"name": "movies"}})

    with pytest.raises(ValueError, match="does not exist"):
        resolve_category_availability(client, "crosseed", create=False)


def test_resolve_category_availability_suggests_a_close_match() -> None:
    client = FakeQbitClient(categories={"cross-seed": {"name": "cross-seed"}})

    with pytest.raises(ValueError, match="Did you mean 'cross-seed'"):
        resolve_category_availability(client, "crosseed", create=False)


def test_resolve_category_availability_true_when_creation_needed() -> None:
    client = FakeQbitClient(categories={})

    assert resolve_category_availability(client, "movies", create=True) is True


def test_resolve_category_availability_false_when_already_registered() -> None:
    client = FakeQbitClient(categories={"movies": {"name": "movies"}})

    assert resolve_category_availability(client, "movies", create=True) is False
    assert (
        resolve_category_availability(client, "movies", create=False) is False
    )


# --- plan/apply: category_set -----------------------------------------------


def test_plan_category_set_flags_uncategorized_torrent_as_a_change() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, category="")],
    )

    plan = plan_bulk_torrent_action(
        client=client,
        action="category_set",
        select_all=True,
        category="movies",
    )

    assert plan.matched == 1
    assert [c.hash for c in plan.changes] == [TORRENT_A]
    assert plan.skipped == ()


def test_plan_category_set_skips_a_torrent_already_in_the_category() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, category="movies")],
    )

    plan = plan_bulk_torrent_action(
        client=client,
        action="category_set",
        select_all=True,
        category="movies",
    )

    assert plan.matched == 1
    assert plan.changes == ()
    assert [s.reason for s in plan.skipped] == ["already_set"]


def test_apply_category_set_creates_the_category_first_when_needed() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, category="")],
    )

    plan = plan_bulk_torrent_action(
        client=client,
        action="category_set",
        select_all=True,
        category="movies",
        category_needs_creation=True,
    )
    apply_bulk_torrent_action(client, plan)

    assert client.created_categories == ["movies"]
    assert client.set_categories == [([TORRENT_A], "movies")]


def test_plan_category_set_creation_needs_a_real_change() -> None:
    """Creating a category nothing ends up wearing is out of scope
    (see the spec's "Hors périmètre")."""
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, category="movies")],
    )

    plan = plan_bulk_torrent_action(
        client=client,
        action="category_set",
        select_all=True,
        category="movies",
        category_needs_creation=True,
    )

    assert plan.changes == ()
    assert plan.category_needs_creation is False


# --- plan/apply: category_clear ---------------------------------------------


def test_plan_category_clear_skips_an_already_uncategorized_torrent() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, category="")],
    )

    plan = plan_bulk_torrent_action(
        client=client, action="category_clear", select_all=True
    )

    assert plan.matched == 1
    assert plan.changes == ()
    assert [s.reason for s in plan.skipped] == ["already_cleared"]


def test_apply_category_clear_sets_a_blank_category() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, category="movies")],
    )

    plan = plan_bulk_torrent_action(
        client=client, action="category_clear", select_all=True
    )
    apply_bulk_torrent_action(client, plan)

    assert client.set_categories == [([TORRENT_A], "")]


# --- plan/apply: tag_add ----------------------------------------------------


def test_plan_tag_add_flags_a_change_when_a_target_tag_is_missing() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, tags="foo")],
    )

    plan = plan_bulk_torrent_action(
        client=client,
        action="tag_add",
        select_all=True,
        tags=["foo", "bar"],
    )

    assert plan.matched == 1
    assert [c.hash for c in plan.changes] == [TORRENT_A]


def test_plan_tag_add_skips_when_every_target_tag_is_already_present() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, tags="foo,bar")],
    )

    plan = plan_bulk_torrent_action(
        client=client,
        action="tag_add",
        select_all=True,
        tags=["foo", "bar"],
    )

    assert plan.changes == ()
    assert [s.reason for s in plan.skipped] == ["already_tagged"]


def test_apply_tag_add_calls_add_tags_with_only_the_matched_hashes() -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A, tags=""),
            make_torrent(hash=TORRENT_B, tags="urgent"),
        ],
    )

    plan = plan_bulk_torrent_action(
        client=client, action="tag_add", select_all=True, tags=["urgent"]
    )
    apply_bulk_torrent_action(client, plan)

    assert client.added_tags == [([TORRENT_A], ["urgent"])]


# --- plan/apply: tag_remove -------------------------------------------------


def test_plan_tag_remove_reports_no_changes_when_nobody_carries_the_tag() -> (
    None
):
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A, tags=""),
            make_torrent(hash=TORRENT_B, tags="other"),
        ],
    )

    plan = plan_bulk_torrent_action(
        client=client, action="tag_remove", select_all=True, tags=["ghost"]
    )

    assert plan.matched == 2
    assert plan.changes == ()
    assert {s.reason for s in plan.skipped} == {"not_tagged"}


def test_plan_tag_remove_is_a_change_when_at_least_one_target_tag_present() -> (
    None
):
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, tags="stale,keep")],
    )

    plan = plan_bulk_torrent_action(
        client=client,
        action="tag_remove",
        select_all=True,
        tags=["stale", "ghost"],
    )

    assert [c.hash for c in plan.changes] == [TORRENT_A]


def test_apply_tag_remove_calls_remove_tags() -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, tags="stale")],
    )

    plan = plan_bulk_torrent_action(
        client=client, action="tag_remove", select_all=True, tags=["stale"]
    )
    apply_bulk_torrent_action(client, plan)

    assert client.removed_tags == [([TORRENT_A], ["stale"])]


# ============================================================================
# CLI-level tests
# ============================================================================


# --- Acceptance 2: dry-run is the default, and the preview never mutates ---


@pytest.mark.parametrize(
    "argv",
    [
        ["torrents", "category", "set", "movies", "--all"],
        ["torrents", "category", "clear", "--all"],
        ["torrents", "tag", "add", "urgent", "--all"],
        ["torrents", "tag", "remove", "urgent", "--all"],
    ],
)
def test_dry_run_is_the_default_and_performs_no_mutation(
    runner: CliRunner,
    configure_qbit_backend,
    argv: list[str],
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, category="", tags="urgent")],
        categories={"movies": {"name": "movies"}},
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(app, argv)

    assert result.exit_code == ExitCode.SUCCESS
    assert "APPLIED" not in result.stdout
    assert client.set_categories == []
    assert client.added_tags == []
    assert client.removed_tags == []
    assert client.created_categories == []


# --- Acceptance 4: category set on an unknown name refuses before any ------
# --- mutation, in dry-run exactly as in real execution ----------------------


@pytest.mark.parametrize("dry_run_flag", ["--dry-run", "--no-dry-run"])
def test_category_set_on_unknown_name_refuses_before_any_mutation(
    runner: CliRunner,
    configure_qbit_backend,
    dry_run_flag: str,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, category="")],
        categories={"cross-seed": {"name": "cross-seed"}},
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        ["torrents", "category", "set", "crosseed", "--all", dry_run_flag],
    )

    assert result.exit_code == ExitCode.ERROR
    assert "does not exist" in result.stderr
    assert client.set_categories == []
    assert client.created_categories == []


# --- Acceptance 5: --create creates the category and the summary says so ---


def test_category_set_create_creates_the_category_and_reports_it(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, category="")],
        categories={},
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "category",
            "set",
            "crosseed",
            "--all",
            "--create",
            "--no-dry-run",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.created_categories == ["crosseed"]
    assert client.set_categories == [([TORRENT_A], "crosseed")]
    assert "category_created" in result.stdout
    assert "True" in result.stdout


# --- Acceptance 6: tag add creates an unknown tag with no flag -------------


def test_tag_add_creates_an_unknown_tag_without_any_flag(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, tags="")],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "tag", "add", "brand-new", "--all", "--no-dry-run"]
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.added_tags == [([TORRENT_A], ["brand-new"])]


# --- Acceptance 7: tag remove of a tag nobody carries is NO_CHANGES, -------
# --- never NO_MATCH ----------------------------------------------------------


def test_tag_remove_of_an_absent_tag_reports_no_changes_not_no_match(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, tags="")],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "tag", "remove", "ghost", "--all", "--no-dry-run"]
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "NO_CHANGES" in result.stdout
    assert "NO_MATCH" not in result.stdout
    assert client.removed_tags == []


# --- Acceptance 8: category clear on already-uncategorized torrents is -----
# --- NO_CHANGES --------------------------------------------------------------


def test_category_clear_on_uncategorized_torrents_reports_no_changes(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, category="")],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "category", "clear", "--all", "--no-dry-run"]
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert "NO_CHANGES" in result.stdout
    assert client.set_categories == []


# --- Acceptance 3: --format json carries the shared bulk-action shape ------


@pytest.mark.parametrize(
    "argv,extra_keys",
    [
        (
            ["torrents", "category", "set", "movies", "--all"],
            {"category", "category_created"},
        ),
        (["torrents", "category", "clear", "--all"], set()),
        (["torrents", "tag", "add", "urgent", "--all"], {"tags"}),
        (["torrents", "tag", "remove", "urgent", "--all"], {"tags"}),
    ],
)
def test_format_json_carries_the_shared_bulk_action_shape(
    runner: CliRunner,
    configure_qbit_backend,
    argv: list[str],
    extra_keys: set[str],
) -> None:
    client = FakeQbitClient(
        torrents=[make_torrent(hash=TORRENT_A, category="", tags="urgent")],
        categories={"movies": {"name": "movies"}},
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(app, [*argv, "--format", "json"])

    assert result.exit_code == ExitCode.SUCCESS
    payload = json.loads(result.stdout)
    for key in (
        "action",
        "matched",
        "hashes",
        "status",
        "dry_run",
        "applied",
    ):
        assert key in payload
    assert extra_keys <= set(payload)


# --- Acceptance 1: same filters as `torrents list`, plus --hash/--all ------


def test_category_set_accepts_a_selection_filter(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A, category="", name="A", tags=""),
            make_torrent(hash=TORRENT_B, category="", name="B", tags="skip-me"),
        ],
        categories={"movies": {"name": "movies"}},
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "category",
            "set",
            "movies",
            "--exclude-tag",
            "skip-me",
            "--no-dry-run",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.set_categories == [([TORRENT_A], "movies")]


def test_tag_remove_selection_filter_is_independent_of_the_positional_tags(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    """`--tag` selects torrents to act on; the positional argument names
    what gets removed -- the two must never be confused."""
    client = FakeQbitClient(
        torrents=[
            make_torrent(hash=TORRENT_A, tags="old-tag,stale"),
            make_torrent(hash=TORRENT_B, tags="stale"),
        ],
    )
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app,
        [
            "torrents",
            "tag",
            "remove",
            "stale",
            "--tag",
            "old-tag",
            "--no-dry-run",
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert client.removed_tags == [([TORRENT_A], ["stale"])]


# --- CLI-boundary validation --------------------------------------------


def test_tag_add_rejects_all_blank_tag_names(
    runner: CliRunner,
    configure_qbit_backend,
) -> None:
    client = FakeQbitClient(torrents=[make_torrent(hash=TORRENT_A)])
    configure_qbit_backend(client=client)

    result = runner.invoke(
        app, ["torrents", "tag", "add", "  ", "--all", "--no-dry-run"]
    )

    assert result.exit_code == ExitCode.ERROR
    assert "at least one" in result.stderr
    assert client.added_tags == []


# --- Acceptance 9: every new operation is registered as low risk ----------


def test_all_four_new_operations_are_low_risk() -> None:
    from qbit_core.shared.execution import (
        MUTATION_RISK,
        MutationOperation,
        MutationRisk,
    )

    for operation in (
        MutationOperation.TORRENTS_CATEGORY_SET,
        MutationOperation.TORRENTS_CATEGORY_CLEAR,
        MutationOperation.TORRENTS_TAG_ADD,
        MutationOperation.TORRENTS_TAG_REMOVE,
    ):
        assert MUTATION_RISK[operation] is MutationRisk.LOW
