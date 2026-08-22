"""`qbit_ops.tui.value_form` -- the value-action modals' Textual-free
models."""

from __future__ import annotations

import pytest

from qbit_core.errors import InvalidInputError
from qbit_core.shared.torrent_states import (
    TorrentSnapshot,
    build_torrent_snapshot,
)
from qbit_ops.tui.value_form import (
    CategorySetDraft,
    TagsDraft,
    ThrottleDraft,
    format_scope,
    tag_add_verdict,
    tag_remove_verdict,
    throttle_current,
    throttle_verdict,
)
from tests.support import make_torrent

pytestmark = pytest.mark.tui


def _snap(**overrides: object) -> TorrentSnapshot:
    return build_torrent_snapshot(make_torrent(**overrides))


# --- CategorySetDraft -------------------------------------------------


def test_category_set_accepts_a_known_category() -> None:
    draft = CategorySetDraft(category="films")
    assert draft.to_plan_kwargs(known_categories=("films", "tv")) == {
        "category": "films",
        "category_needs_creation": False,
    }


def test_category_set_refuses_an_unknown_category_without_create() -> None:
    draft = CategorySetDraft(category="arch-iso")
    with pytest.raises(InvalidInputError):
        draft.to_plan_kwargs(known_categories=("films",))


def test_category_set_allows_an_unknown_category_with_create() -> None:
    draft = CategorySetDraft(category="arch-iso", create=True)
    assert draft.to_plan_kwargs(known_categories=("films",)) == {
        "category": "arch-iso",
        "category_needs_creation": True,
    }


def test_category_set_refuses_a_blank_name() -> None:
    with pytest.raises(InvalidInputError):
        CategorySetDraft(category="   ").to_plan_kwargs(known_categories=())


# --- TagsDraft ----------------------------------------------------------


def test_tags_draft_parses_a_comma_separated_list() -> None:
    draft = TagsDraft(tags="stale, keep ,  stale")
    assert draft.to_plan_kwargs() == {"tags": ("stale", "keep")}


def test_tags_draft_refuses_an_empty_result() -> None:
    with pytest.raises(InvalidInputError):
        TagsDraft(tags=" , ,").to_plan_kwargs()


# --- ThrottleDraft --------------------------------------------------------


def test_throttle_draft_accepts_upload_only() -> None:
    assert ThrottleDraft(upload="2MiB/s").to_plan_kwargs() == {
        "upload_limit": 2 * 1024 * 1024,
        "download_limit": None,
    }


def test_throttle_draft_accepts_download_only() -> None:
    assert ThrottleDraft(download="512KiB/s").to_plan_kwargs() == {
        "upload_limit": None,
        "download_limit": 512 * 1024,
    }


def test_throttle_draft_accepts_both_directions_independently() -> None:
    kwargs = ThrottleDraft(
        upload="unlimited", download="1MiB/s"
    ).to_plan_kwargs()
    assert kwargs == {"upload_limit": 0, "download_limit": 1024 * 1024}


def test_throttle_draft_refuses_neither_direction_named() -> None:
    """The TUI-local counterpart of the CLI's `validate_rate_limits`
    guard (`cli/validation.py:136`, "Provide at least one of --down or
    --up")."""
    with pytest.raises(InvalidInputError):
        ThrottleDraft().to_plan_kwargs()


# --- format_scope ---------------------------------------------------------


def test_format_scope_names_the_first_and_counts_the_rest() -> None:
    assert (
        format_scope(("Alpha", "Beta", "Gamma"))
        == "3 selected · Alpha, +2 more"
    )


def test_format_scope_with_one_name_has_no_more_suffix() -> None:
    assert format_scope(("Alpha",)) == "1 selected · Alpha"


def test_format_scope_with_no_names() -> None:
    assert format_scope(()) == "0 selected"


# --- tag_add_verdict / tag_remove_verdict ---------------------------------


def test_tag_add_verdict_reports_partial_and_full_coverage() -> None:
    selected = (
        _snap(hash="a" * 40, tags="stale"),
        _snap(hash="b" * 40, tags=""),
        _snap(hash="c" * 40, tags=""),
    )
    lines = tag_add_verdict(("stale", "verified"), selected)
    assert lines == [
        "* stale  adds to 2 of 3",
        "* verified  adds to 3 of 3",
    ]


def test_tag_remove_verdict_reports_who_carries_it() -> None:
    selected = (
        _snap(hash="a" * 40, tags="stale"),
        _snap(hash="b" * 40, tags="stale"),
        _snap(hash="c" * 40, tags=""),
    )
    lines = tag_remove_verdict(("stale",), selected)
    assert lines == ["* stale  removes from 2 of 3 · 1 unaffected"]


def test_tag_remove_verdict_names_a_tag_nobody_carries() -> None:
    selected = (_snap(hash="a" * 40, tags=""),)
    lines = tag_remove_verdict(("ghost",), selected)
    assert lines == ["* ghost  not on any of 1"]


# --- throttle_verdict / throttle_current ----------------------------------


def test_throttle_verdict_shows_left_alone_for_a_blank_direction() -> None:
    assert throttle_verdict("2MiB/s", "") == "* ↑ 2MiB/s      ↓ left alone"


def test_throttle_current_groups_by_value() -> None:
    selected = (
        _snap(hash="a" * 40, up_limit=0, dl_limit=0),
        _snap(hash="b" * 40, up_limit=0, dl_limit=0),
        _snap(hash="c" * 40, up_limit=512_000, dl_limit=0),
    )
    current = throttle_current(selected)
    assert current.startswith("↑ 2 unlimited · 1 500.0 KiB/s")
    assert current.endswith("↓ 3 unlimited")
