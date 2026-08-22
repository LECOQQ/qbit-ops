"""`FiltersDraft` -- the filters modal's model, entirely free of Textual.

The modal commits on `Apply` rather than filtering live (see
`.agents/features/tui-filters/SPEC.md`, "Le filtrage n'est plus en direct"), so
what a user has typed and what is actually applied are two different
values that must be able to disagree -- a plain dataclass of strings
compared against another is what makes that comparison possible without
touching a single widget.

Every one of `TorrentFilter`'s 27 fields is reachable here except the
three `INSPECTION_ONLY_FILTER_FIELDS`, which the `Trackers` pane
declares CLI-only instead of exposing (see `PANE_FIELDS` and
`CLI_ONLY_TORRENT_FILTER_FIELDS` below; `tests/test_tui_filter_form.py`
derives the 27 from `dataclasses.fields(TorrentFilter)`, so a field
added there cannot be silently left unclassified).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from qbit_core.features.torrents import build_torrent_filter
from qbit_core.shared.parsers import (
    parse_duration,
    parse_percentage,
    parse_ratio,
    parse_size,
)
from qbit_core.shared.selection import Range, TorrentFilter


def _csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _join(values: tuple[str, ...]) -> str:
    return ", ".join(values)


def _maybe[T](parser: Callable[[str], T], value: str) -> T | None:
    stripped = value.strip()
    return None if stripped == "" else parser(stripped)


def _duration_ago_range(
    since_at_least: str, since_at_most: str, *, now: datetime
) -> tuple[datetime | None, datetime | None]:
    """Two "N ago" durations into an absolute `(min, max)` window.

    `since_at_least` is the *older* edge -- "added at least this long
    ago" bounds how recent a match may be, so it becomes the window's
    `max`. `since_at_most` is the *newer* edge -- "no older than this"
    -- and becomes `min`. Kept local rather than imported from
    `qbit_ops.cli`: the TUI never imports the CLI package (see
    `qbit_ops.tui.app`'s security-boundary docstring).
    """
    return (
        (
            None
            if since_at_most.strip() == ""
            else now - timedelta(seconds=parse_duration(since_at_most))
        ),
        (
            None
            if since_at_least.strip() == ""
            else now - timedelta(seconds=parse_duration(since_at_least))
        ),
    )


def _format_ago(moment: datetime | None, *, now: datetime) -> str:
    """Best-effort reverse rendering: whole days since `now`.

    Lossy on purpose -- the original typed unit ("2w" vs "14d") is not
    recoverable from an absolute timestamp, and none of this feature's
    acceptance criteria depend on round-tripping it. Only used to
    pre-fill the fields when a `FiltersScreen` opens on an
    already-applied filter.
    """
    if moment is None:
        return ""
    days = round((now - moment).total_seconds() / 86400)
    return f"{days}d"


@dataclass
class FiltersDraft:
    """Every editable `TorrentFilter` control, as the raw text (or
    tri-state/bool) a widget holds -- never a `TorrentFilter` itself,
    so a half-typed value can be held and shown without parsing it."""

    # -- organisation
    categories: str = ""
    categories_excluded: str = ""
    tags_any: str = ""
    tags_all: str = ""
    tags_none: str = ""
    save_paths: str = ""
    save_paths_excluded: str = ""
    name_contains: str = ""
    name_excluded: str = ""
    name_regex: str = ""

    # -- state
    states: str = ""
    states_excluded: str = ""
    completed: int = 0  # 0 any, 1 completed, 2 incomplete
    active: int = 0  # 0 any, 1 active, 2 inactive
    private: int = 0  # 0 any, 1 private, 2 public
    stalled: bool = False
    errored: bool = False

    # -- measures
    ratio_min: str = ""
    ratio_max: str = ""
    size_min: str = ""
    size_max: str = ""
    progress_min: str = ""
    progress_max: str = ""
    uploaded_min: str = ""
    uploaded_max: str = ""
    added_min: str = ""
    added_max: str = ""
    completed_at_min: str = ""
    completed_at_max: str = ""
    last_activity_min: str = ""
    last_activity_max: str = ""
    seeded_for: str = ""

    # -- trackers (the one exposed field; the other three are CLI-only)
    no_trackers: bool = False

    def to_filter(self, *, now: datetime | None = None) -> TorrentFilter:
        """Parse every field and assemble one validated `TorrentFilter`.

        Raises `InvalidInputError`/`ValueError` on the first unparsable
        or contradictory value -- the same exceptions
        `build_torrent_filter` already raises, never reformulated here.
        """
        moment = now or datetime.now(tz=UTC)
        added_min, added_max = _duration_ago_range(
            self.added_min, self.added_max, now=moment
        )
        completed_at_min, completed_at_max = _duration_ago_range(
            self.completed_at_min, self.completed_at_max, now=moment
        )
        last_activity_min, last_activity_max = _duration_ago_range(
            self.last_activity_min, self.last_activity_max, now=moment
        )

        return build_torrent_filter(
            categories=_csv(self.categories),
            categories_excluded=_csv(self.categories_excluded),
            tags_any=_csv(self.tags_any),
            tags_all=_csv(self.tags_all),
            tags_excluded=_csv(self.tags_none),
            save_paths=_csv(self.save_paths),
            save_paths_excluded=_csv(self.save_paths_excluded),
            name_contains=_csv(self.name_contains),
            name_excluded=_csv(self.name_excluded),
            name_regex=self.name_regex.strip() or None,
            states=_csv(self.states),
            states_excluded=_csv(self.states_excluded),
            has_trackers=False if self.no_trackers else None,
            completed=self.completed == 1,
            incomplete=self.completed == 2,
            active=self.active == 1,
            inactive=self.active == 2,
            stalled=self.stalled,
            errored=self.errored,
            private=(
                True
                if self.private == 1
                else (False if self.private == 2 else None)
            ),
            ratio=Range(
                min=_maybe(parse_ratio, self.ratio_min),
                max=_maybe(parse_ratio, self.ratio_max),
            ),
            size=Range(
                min=_maybe(parse_size, self.size_min),
                max=_maybe(parse_size, self.size_max),
            ),
            progress=Range(
                min=_maybe(parse_percentage, self.progress_min),
                max=_maybe(parse_percentage, self.progress_max),
            ),
            uploaded=Range(
                min=_maybe(parse_size, self.uploaded_min),
                max=_maybe(parse_size, self.uploaded_max),
            ),
            seeding_time=Range(min=_maybe(parse_duration, self.seeded_for)),
            added=Range(min=added_min, max=added_max),
            completed_at=Range(min=completed_at_min, max=completed_at_max),
            last_activity=Range(min=last_activity_min, max=last_activity_max),
        )

    @classmethod
    def from_filter(
        cls, filters: TorrentFilter, *, now: datetime | None = None
    ) -> FiltersDraft:
        """The reverse rendering, used to pre-fill an already-applied
        filter when `FiltersScreen` opens. See `_format_ago` for the
        one lossy corner of this round-trip."""
        moment = now or datetime.now(tz=UTC)
        return cls(
            categories=_join(filters.categories),
            categories_excluded=_join(filters.categories_excluded),
            tags_any=_join(filters.tags.any_of),
            tags_all=_join(filters.tags.all_of),
            tags_none=_join(filters.tags.none_of),
            save_paths=_join(filters.save_path_prefixes),
            save_paths_excluded=_join(filters.save_paths_excluded),
            name_contains=_join(filters.name_contains),
            name_excluded=_join(filters.name_excluded),
            name_regex=filters.name_regex or "",
            states=_join(filters.states),
            states_excluded=_join(filters.states_excluded),
            completed=(
                1
                if filters.completed is True
                else (2 if filters.completed is False else 0)
            ),
            active=(
                1
                if filters.active is True
                else (2 if filters.active is False else 0)
            ),
            private=(
                1
                if filters.private is True
                else (2 if filters.private is False else 0)
            ),
            stalled=bool(filters.stalled),
            errored=bool(filters.errored),
            ratio_min=_str(filters.ratio.min),
            ratio_max=_str(filters.ratio.max),
            size_min=_str(filters.size.min),
            size_max=_str(filters.size.max),
            progress_min=_percent_str(filters.progress.min),
            progress_max=_percent_str(filters.progress.max),
            uploaded_min=_str(filters.uploaded.min),
            uploaded_max=_str(filters.uploaded.max),
            added_min=_format_ago(filters.added.max, now=moment),
            added_max=_format_ago(filters.added.min, now=moment),
            completed_at_min=_format_ago(filters.completed_at.max, now=moment),
            completed_at_max=_format_ago(filters.completed_at.min, now=moment),
            last_activity_min=_format_ago(
                filters.last_activity.max, now=moment
            ),
            last_activity_max=_format_ago(
                filters.last_activity.min, now=moment
            ),
            seeded_for=_str(filters.seeding_time.min),
            no_trackers=filters.has_trackers is False,
        )


def _str(value: object | None) -> str:
    return "" if value is None else str(value)


def _percent_str(value: float | None) -> str:
    return "" if value is None else f"{value * 100:g}%"


# Pane name, its border-strip abbreviation, and the `FiltersDraft`
# fields it owns -- the single map every pending/count computation and
# every widget layout walks, so a field can only ever live in one pane.
ORGANISATION_FIELDS: tuple[str, ...] = (
    "categories",
    "categories_excluded",
    "tags_any",
    "tags_all",
    "tags_none",
    "save_paths",
    "save_paths_excluded",
    "name_contains",
    "name_excluded",
    "name_regex",
)
STATE_FIELDS: tuple[str, ...] = (
    "states",
    "states_excluded",
    "completed",
    "active",
    "private",
    "stalled",
    "errored",
)
MEASURES_FIELDS: tuple[str, ...] = (
    "ratio_min",
    "ratio_max",
    "size_min",
    "size_max",
    "progress_min",
    "progress_max",
    "uploaded_min",
    "uploaded_max",
    "added_min",
    "added_max",
    "completed_at_min",
    "completed_at_max",
    "last_activity_min",
    "last_activity_max",
    "seeded_for",
)
TRACKERS_FIELDS: tuple[str, ...] = ("no_trackers",)

PANE_NAMES: tuple[str, ...] = ("Organisation", "State", "Measures", "Trackers")
PANE_ABBREVIATIONS: dict[str, str] = {
    "Organisation": "Org",
    "State": "Sta",
    "Measures": "Mea",
    "Trackers": "Trk",
}
PANE_FIELDS: dict[str, tuple[str, ...]] = {
    "Organisation": ORGANISATION_FIELDS,
    "State": STATE_FIELDS,
    "Measures": MEASURES_FIELDS,
    "Trackers": TRACKERS_FIELDS,
}

# `TorrentFilter` fields the Trackers pane declares CLI-only instead of
# exposing -- checked for exact equality against
# `INSPECTION_ONLY_FILTER_FIELDS` by `tests/test_tui_filter_form.py`,
# never assumed to still match it.
CLI_ONLY_TORRENT_FILTER_FIELDS: tuple[str, ...] = (
    "trackers",
    "trackers_excluded",
    "tracker_health",
)


def pane_has_pending_edits(
    pane: str, draft: FiltersDraft, applied: TorrentFilter
) -> bool:
    """Whether `pane`'s fields in `draft` differ from `applied`, once
    rendered back to the same string/tri-state shape -- the `*`
    marker. Compared as strings/ints, never by parsing `draft`: an
    invalid draft (mid-typo) must still be able to show as pending."""
    baseline = FiltersDraft.from_filter(applied)
    return any(
        getattr(draft, name) != getattr(baseline, name)
        for name in PANE_FIELDS[pane]
    )


def pane_applied_count(pane: str, applied: TorrentFilter) -> int:
    """How many of `applied`'s criteria belong to `pane` -- the tab's
    badge count. Reads `applied` alone, never `draft`: an edit that has
    not been committed does not count as "posed"."""
    if pane == "Organisation":
        return sum(
            (
                bool(applied.categories),
                bool(applied.categories_excluded),
                bool(applied.tags.any_of),
                bool(applied.tags.all_of),
                bool(applied.tags.none_of),
                bool(applied.save_path_prefixes),
                bool(applied.save_paths_excluded),
                bool(applied.name_contains),
                bool(applied.name_excluded),
                applied.name_regex is not None,
            )
        )
    if pane == "State":
        return sum(
            (
                bool(applied.states),
                bool(applied.states_excluded),
                applied.completed is not None,
                applied.active is not None,
                applied.private is not None,
                bool(applied.stalled),
                bool(applied.errored),
            )
        )
    if pane == "Measures":
        return sum(
            (
                not applied.ratio.is_unset,
                not applied.size.is_unset,
                not applied.progress.is_unset,
                not applied.uploaded.is_unset,
                not applied.seeding_time.is_unset,
                not applied.added.is_unset,
                not applied.completed_at.is_unset,
                not applied.last_activity.is_unset,
            )
        )
    if pane == "Trackers":
        return int(applied.has_trackers is not None)
    raise ValueError(f"Unknown pane {pane!r}")
