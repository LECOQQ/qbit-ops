"""Value-action drafts -- the four value modals' Textual-free models.

Mirrors `filter_form.py`: raw text held in a plain dataclass, parsed
and validated only once, at `Preview` (never live, same reasoning as
`FiltersDraft`). Each draft turns into exactly the keyword arguments
`TuiController.build_bulk_plan` already accepts for its action --
plumbing that predates this module (see its own docstring).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TypedDict

from qbit_core.errors import InvalidInputError, require_non_blank
from qbit_core.shared.parsers import parse_rate
from qbit_core.shared.torrent_states import TorrentSnapshot


def _normalize_tags(raw: str) -> tuple[str, ...]:
    """Strip, drop blanks and deduplicate a comma-separated tag list --
    the TUI-local counterpart of the CLI's `validate_tag_names`, which
    lives behind `qbit_ops.cli` and is therefore off-limits here (see
    `qbit_ops.tui.app`'s security-boundary docstring)."""
    return tuple(
        dict.fromkeys(part.strip() for part in raw.split(",") if part.strip())
    )


class PlanKwargs(TypedDict, total=False):
    """The keyword arguments `TuiController.build_bulk_plan` accepts
    for a value action -- each draft's `to_plan_kwargs()` returns the
    subset its own action uses; the modal passes the rest through
    unset."""

    category: str
    category_needs_creation: bool
    tags: tuple[str, ...]
    download_limit: int | None
    upload_limit: int | None


@dataclass
class CategorySetDraft:
    """`category set`'s one field plus its escape hatch."""

    category: str = ""
    create: bool = False

    def to_plan_kwargs(self, *, known_categories: Sequence[str]) -> PlanKwargs:
        """Raises `InvalidInputError` for a blank name, or an unknown
        one with `create` unchecked -- checked against `known_categories`
        (the cached instance-wide list), never a live call: opening or
        editing this modal costs zero API calls (criterion 9ter)."""
        name = require_non_blank(self.category, field_name="Category")
        needs_creation = name not in known_categories
        if needs_creation and not self.create:
            raise InvalidInputError(
                f'no category named "{name}" on this instance -- check '
                '"create it as well", or fix the name.'
            )
        return {"category": name, "category_needs_creation": needs_creation}


@dataclass
class TagsDraft:
    """`tag add`/`tag remove`'s one field -- same shape, different
    action string, so one draft class serves both."""

    tags: str = ""

    def to_plan_kwargs(self) -> PlanKwargs:
        """Raises `InvalidInputError` when nothing parses to a tag."""
        tags = _normalize_tags(self.tags)
        if not tags:
            raise InvalidInputError("Provide at least one non-blank tag name.")
        return {"tags": tags}


@dataclass
class ThrottleDraft:
    """`throttle`'s two independent fields -- never a selector (see
    `.agents/features/tui-filters/SPEC.md`, "throttle porte les deux
    directions"): the CLI already lets `--up`/`--down` differ, and a
    radio would say less than the command it represents."""

    upload: str = ""
    download: str = ""

    def to_plan_kwargs(self) -> PlanKwargs:
        """Raises `InvalidInputError` when neither direction is named
        (blank means "leave alone", not "unlimited") -- the TUI-local
        counterpart of the CLI's `validate_rate_limits`
        (`cli/validation.py:136`), reimplemented rather than imported
        for the same security-boundary reason as `_normalize_tags`."""
        if not self.upload.strip() and not self.download.strip():
            raise InvalidInputError(
                "Name at least one direction. Use 'unlimited' to remove "
                "a limit."
            )
        return {
            "upload_limit": (
                None if not self.upload.strip() else parse_rate(self.upload)
            ),
            "download_limit": (
                None if not self.download.strip() else parse_rate(self.download)
            ),
        }


def format_scope(names: Sequence[str]) -> str:
    """The "sélection" slot: "N selected · first name, +K more"."""
    if not names:
        return "0 selected"
    extra = len(names) - 1
    scope = f"{len(names)} selected · {names[0]}"
    if extra > 0:
        scope += f", +{extra} more"
    return scope


def tag_add_verdict(
    tags: tuple[str, ...], selected: Sequence[TorrentSnapshot]
) -> list[str]:
    """The "conséquence" slot for `tag add`: what each named tag will
    do, one line per tag."""
    total = len(selected)
    lines = []
    for tag in tags:
        already = sum(
            1 for torrent in selected if tag.casefold() in _casefolded(torrent)
        )
        if already >= total and total > 0:
            lines.append(f"* {tag}  already on all {total}")
        else:
            lines.append(f"* {tag}  adds to {total - already} of {total}")
    return lines


def tag_remove_verdict(
    tags: tuple[str, ...], selected: Sequence[TorrentSnapshot]
) -> list[str]:
    """The "conséquence" slot for `tag remove`: how many carry each
    named tag today, and how many would be left unaffected."""
    total = len(selected)
    lines = []
    for tag in tags:
        carrying = sum(
            1 for torrent in selected if tag.casefold() in _casefolded(torrent)
        )
        if carrying == 0:
            lines.append(f"* {tag}  not on any of {total}")
        else:
            unaffected = total - carrying
            suffix = f" · {unaffected} unaffected" if unaffected else ""
            lines.append(f"* {tag}  removes from {carrying} of {total}{suffix}")
    return lines


def _casefolded(torrent: TorrentSnapshot) -> set[str]:
    return {tag.casefold() for tag in torrent.tags}


def throttle_verdict(upload: str, download: str) -> str:
    """The "conséquence" slot for `throttle`: what each direction will
    do, blank meaning "left alone"."""
    up = upload.strip() or "left alone"
    down = download.strip() or "left alone"
    return f"* ↑ {up}      ↓ {down}"


def throttle_current(selected: Sequence[TorrentSnapshot]) -> str:
    """The "contexte" slot for `throttle`: current per-torrent limits,
    grouped by value so a shared limit reads as one entry, not one row
    per torrent."""
    return (
        f"\u2191 {_grouped_limits(t.upload_limit for t in selected)}   "
        f"\u2193 {_grouped_limits(t.download_limit for t in selected)}"
    )


def _grouped_limits(limits: Iterable[int]) -> str:
    from qbit_ops.tui.formatting import _format_byte_rate

    counts = Counter(limits)
    if not counts:
        return "unlimited"
    parts = [
        f"{count} {'unlimited' if limit == 0 else _format_byte_rate(limit)}"
        for limit, count in sorted(counts.items(), key=lambda item: -item[1])
    ]
    return " \u00b7 ".join(parts)
