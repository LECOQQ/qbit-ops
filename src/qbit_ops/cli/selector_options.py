"""Declare the torrent-selector CLI options exactly once.

`torrents list` and the five bulk mutation commands
(`pause`/`resume`/`start`/`reannounce`/`delete`) all expose the same
selector vocabulary. Declaring it per command let help text, flag
spelling and ordering drift independently; every option now has one
definition here, reused as an `Annotated` alias.

Only genuinely per-command wording stays per command: `--hash` and
`--all` name the action they perform, so they get one alias each, built
from a shared template where the wording allows it.

Also assembles those raw option values into one `TorrentFilter`
(`build_filter_from_options`): turning `10GiB` or `90d` into a number is
a presentation concern, so it happens here rather than in `qbit_core`,
which only ever sees resolved values.

Consumed by every command group exposing a selector; kept apart from
them so a command module reads as a list of commands rather than a wall
of option declarations.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

import typer

from qbit_core.features.torrents import (
    TRACKER_HEALTH_FILTER_VALUES,
    build_torrent_filter,
)
from qbit_core.shared.parsers import (
    parse_duration,
    parse_percentage,
    parse_ratio,
    parse_size,
)
from qbit_core.shared.selection import STATE_FILTER_VALUES, Range, TorrentFilter

CATEGORY_FILTER_HELP = (
    "Restrict to a category (repeatable; combines with OR). Use "
    "'uncategorized' for torrents without a category."
)
STATE_FILTER_HELP = (
    "Restrict to a state group (repeatable; combines with OR). Supported: "
    f"{', '.join(sorted(STATE_FILTER_VALUES))}."
)
TRACKER_FILTER_HELP = (
    "Restrict to torrents using a tracker, matched by host[:port] (repeatable; "
    "combines with OR). A full announce URL is also accepted; only its host "
    "and port are used."
)
_TRACKER_HEALTH_VALUES = ", ".join(
    sorted(health.value for health in TRACKER_HEALTH_FILTER_VALUES)
)
TRACKER_HEALTH_FILTER_HELP = (
    "Restrict to torrents whose own tracker endpoints aggregate to a health "
    f"verdict (repeatable; combines with OR). Supported: "
    f"{_TRACKER_HEALTH_VALUES}. A torrent with no usable observation matches "
    "none of them. Requires a per-torrent tracker lookup."
)


SIZE_VALUE_HELP = (
    "Accepts bytes, or a binary (KiB/MiB/GiB/TiB) or decimal "
    "(KB/MB/GB/TB) unit; the suffix decides the multiplier."
)
DURATION_VALUE_HELP = "Accepts a number followed by s, m, h, d or w."


def _hash_help(verb: str) -> str:
    """Build the `--hash` help text for one action."""
    return (
        f"{verb} the torrent matching a full infohash or unique "
        "leading prefix (case-insensitive)."
    )


# --- shared filter options --------------------------------------------------

CategoryOption = Annotated[
    list[str], typer.Option("--category", help=CATEGORY_FILTER_HELP)
]
StateOption = Annotated[
    list[str], typer.Option("--state", help=STATE_FILTER_HELP)
]
TrackerOption = Annotated[
    list[str], typer.Option("--tracker", help=TRACKER_FILTER_HELP)
]
# The `trackers` report commands take a single `--tracker` and apply it
# *after* aggregation, restricting the report rather than the selection.
# One declaration, so `trackers list` inherits `trackers status`'s
# arity and wording instead of imitating them.
REPORT_TRACKER_FILTER_HELP = (
    "Restrict the report to one tracker, matched by host[:port] (a full "
    "announce URL is also accepted; only its host and port are used)."
)
ReportTrackerOption = Annotated[
    str | None, typer.Option("--tracker", help=REPORT_TRACKER_FILTER_HELP)
]
CompletedOption = Annotated[
    bool, typer.Option("--completed", help="Restrict to completed torrents.")
]
IncompleteOption = Annotated[
    bool, typer.Option("--incomplete", help="Restrict to incomplete torrents.")
]
ActiveOption = Annotated[
    bool,
    typer.Option("--active", help="Restrict to torrents that are not stopped."),
]
InactiveOption = Annotated[
    bool, typer.Option("--inactive", help="Restrict to stopped torrents.")
]
StalledOption = Annotated[
    bool, typer.Option("--stalled", help="Restrict to stalled torrents.")
]
ErroredOption = Annotated[
    bool,
    typer.Option("--errored", help="Restrict to torrents reporting an error."),
]

# `start` documents `--completed` in terms of the Web UI action it
# reproduces; every other command uses the plain wording above.
StartCompletedOption = Annotated[
    bool,
    typer.Option(
        "--completed",
        help=(
            "Restrict to completed torrents (Web UI 'Start All' is "
            "--completed --all)."
        ),
    ),
]

# --- shared mutation options ------------------------------------------------

DryRunOption = Annotated[
    bool,
    typer.Option(
        "--dry-run/--no-dry-run",
        help="Apply changes instead of previewing them.",
    ),
]
VerboseOption = Annotated[
    bool, typer.Option("--verbose", help="Print impacted torrent details.")
]

# --- per-action options -----------------------------------------------------

PauseHashOption = Annotated[
    str | None, typer.Option("--hash", help=_hash_help("Pause"))
]
ResumeHashOption = Annotated[
    str | None, typer.Option("--hash", help=_hash_help("Resume"))
]
StartHashOption = Annotated[
    str | None, typer.Option("--hash", help=_hash_help("Start"))
]
ReannounceHashOption = Annotated[
    str | None, typer.Option("--hash", help=_hash_help("Reannounce"))
]
DeleteHashOption = Annotated[
    str | None, typer.Option("--hash", help=_hash_help("Delete"))
]
StatsHashOption = Annotated[
    str | None, typer.Option("--hash", help=_hash_help("Report on"))
]

PauseAllOption = Annotated[
    bool, typer.Option("--all", help="Pause all torrents.")
]
ResumeAllOption = Annotated[
    bool, typer.Option("--all", help="Resume all stopped torrents.")
]
StartAllOption = Annotated[
    bool, typer.Option("--all", help="Start all stopped torrents.")
]
ReannounceAllOption = Annotated[
    bool, typer.Option("--all", help="Reannounce all torrents.")
]
DeleteAllOption = Annotated[
    bool, typer.Option("--all", help="Delete all torrents.")
]
StatsAllOption = Annotated[
    bool,
    typer.Option(
        "--all",
        help=(
            "Report on every torrent present. An explicit selection, so "
            "the instance-wide all-time block is omitted."
        ),
    ),
]
CategorySetHashOption = Annotated[
    str | None, typer.Option("--hash", help=_hash_help("Set the category for"))
]
CategoryClearHashOption = Annotated[
    str | None,
    typer.Option("--hash", help=_hash_help("Clear the category for")),
]
TagAddHashOption = Annotated[
    str | None, typer.Option("--hash", help=_hash_help("Add the tag(s) to"))
]
TagRemoveHashOption = Annotated[
    str | None,
    typer.Option("--hash", help=_hash_help("Remove the tag(s) from")),
]

CategorySetAllOption = Annotated[
    bool, typer.Option("--all", help="Set the category for all torrents.")
]
CategoryClearAllOption = Annotated[
    bool, typer.Option("--all", help="Clear the category for all torrents.")
]
TagAddAllOption = Annotated[
    bool, typer.Option("--all", help="Add the tag(s) to all torrents.")
]
TagRemoveAllOption = Annotated[
    bool, typer.Option("--all", help="Remove the tag(s) from all torrents.")
]


# --- exclusions -------------------------------------------------------------

ExcludeCategoryOption = Annotated[
    list[str],
    typer.Option(
        "--exclude-category",
        help="Skip torrents in a category (repeatable).",
    ),
]
ExcludeStateOption = Annotated[
    list[str],
    typer.Option(
        "--exclude-state", help="Skip torrents in a state group (repeatable)."
    ),
]

# --- tags -------------------------------------------------------------------

TagOption = Annotated[
    list[str],
    typer.Option(
        "--tag",
        help=(
            "Restrict to torrents carrying a tag (repeatable; combines with "
            "OR). Matching ignores case."
        ),
    ),
]
TagAllOption = Annotated[
    list[str],
    typer.Option(
        "--tag-all",
        help=(
            "Restrict to torrents carrying every listed tag (repeatable; "
            "combines with AND)."
        ),
    ),
]
ExcludeTagOption = Annotated[
    list[str],
    typer.Option("--exclude-tag", help="Skip torrents carrying a tag."),
]

# --- storage ----------------------------------------------------------------

SavePathOption = Annotated[
    list[str],
    typer.Option(
        "--save-path",
        help=(
            "Restrict to torrents saved under a path (repeatable). Matches "
            "the directory and anything beneath it; case-sensitive."
        ),
    ),
]
ExcludeSavePathOption = Annotated[
    list[str],
    typer.Option(
        "--exclude-save-path", help="Skip torrents saved under a path."
    ),
]

# --- name -------------------------------------------------------------------

NameContainsOption = Annotated[
    list[str],
    typer.Option(
        "--name-contains",
        help=(
            "Restrict to names containing a substring (repeatable; combines "
            "with OR). Ignores case. Deterministic -- unlike the read-only "
            "fuzzy search, it never widens a selection."
        ),
    ),
]
ExcludeNameOption = Annotated[
    list[str],
    typer.Option("--exclude-name", help="Skip names containing a substring."),
]
NameRegexOption = Annotated[
    str | None,
    typer.Option(
        "--name-regex",
        help=(
            "Restrict to names matching a regular expression (searched, not "
            "anchored; case-sensitive -- use (?i) to fold case)."
        ),
    ),
]

# --- trackers ---------------------------------------------------------------

ExcludeTrackerOption = Annotated[
    list[str],
    typer.Option(
        "--exclude-tracker",
        help=(
            "Skip torrents announcing to a tracker, matched by host[:port] "
            "(repeatable). Requires a per-torrent tracker lookup."
        ),
    ),
]
NoTrackerOption = Annotated[
    bool,
    typer.Option(
        "--no-tracker",
        help=(
            "Restrict to torrents with no active tracker. Costs no extra "
            "API call."
        ),
    ),
]
TrackerHealthOption = Annotated[
    list[str],
    typer.Option("--tracker-health", help=TRACKER_HEALTH_FILTER_HELP),
]

# --- privacy ----------------------------------------------------------------

PrivateOption = Annotated[
    bool | None,
    typer.Option(
        "--private/--public",
        help=(
            "Restrict to private or public torrents. Requires qBittorrent "
            "5.0+; on older versions nothing matches."
        ),
    ),
]

# --- bounded measures -------------------------------------------------------

RatioMinOption = Annotated[
    str | None,
    typer.Option("--ratio-min", help="Restrict to a share ratio at least N."),
]
RatioMaxOption = Annotated[
    str | None,
    typer.Option("--ratio-max", help="Restrict to a share ratio at most N."),
]
SizeMinOption = Annotated[
    str | None,
    typer.Option("--size-min", help=f"Minimum torrent size. {SIZE_VALUE_HELP}"),
]
SizeMaxOption = Annotated[
    str | None,
    typer.Option("--size-max", help=f"Maximum torrent size. {SIZE_VALUE_HELP}"),
]
ProgressMinOption = Annotated[
    str | None,
    typer.Option(
        "--progress-min",
        help="Minimum progress, as '95%' or a 0-1 fraction like '0.95'.",
    ),
]
ProgressMaxOption = Annotated[
    str | None,
    typer.Option(
        "--progress-max",
        help="Maximum progress, as '95%' or a 0-1 fraction like '0.95'.",
    ),
]
UploadedMinOption = Annotated[
    str | None,
    typer.Option(
        "--uploaded-min", help=f"Minimum bytes uploaded. {SIZE_VALUE_HELP}"
    ),
]
UploadedMaxOption = Annotated[
    str | None,
    typer.Option(
        "--uploaded-max", help=f"Maximum bytes uploaded. {SIZE_VALUE_HELP}"
    ),
]
SeededForOption = Annotated[
    str | None,
    typer.Option(
        "--seeded-for",
        help=(
            "Restrict to torrents seeded for at least this long. "
            f"{DURATION_VALUE_HELP}"
        ),
    ),
]
OlderThanOption = Annotated[
    str | None,
    typer.Option(
        "--older-than",
        help=(
            f"Restrict to torrents added more than this long ago. "
            f"{DURATION_VALUE_HELP}"
        ),
    ),
]
CompletedBeforeOption = Annotated[
    str | None,
    typer.Option(
        "--completed-before",
        help=(
            "Restrict to torrents that finished downloading more than this "
            f"long ago. Never matches an unfinished torrent. "
            f"{DURATION_VALUE_HELP}"
        ),
    ),
]
CompletedWithinOption = Annotated[
    str | None,
    typer.Option(
        "--completed-within",
        help=(
            "Restrict to torrents that finished downloading within this "
            f"long. {DURATION_VALUE_HELP}"
        ),
    ),
]
InactiveForOption = Annotated[
    str | None,
    typer.Option(
        "--inactive-for",
        help=(
            "Restrict to torrents with no upload or download for at least "
            f"this long. {DURATION_VALUE_HELP}"
        ),
    ),
]
ActiveWithinOption = Annotated[
    str | None,
    typer.Option(
        "--active-within",
        help=(
            "Restrict to torrents that transferred data within this long. "
            f"{DURATION_VALUE_HELP}"
        ),
    ),
]
NewerThanOption = Annotated[
    str | None,
    typer.Option(
        "--newer-than",
        help=(
            f"Restrict to torrents added within this long. "
            f"{DURATION_VALUE_HELP}"
        ),
    ),
]


def build_filter_from_options(
    *,
    category: list[str] = [],  # noqa: B006 - mirrors the Typer option shape
    exclude_category: list[str] = [],  # noqa: B006
    tag: list[str] = [],  # noqa: B006
    tag_all: list[str] = [],  # noqa: B006
    exclude_tag: list[str] = [],  # noqa: B006
    save_path: list[str] = [],  # noqa: B006
    exclude_save_path: list[str] = [],  # noqa: B006
    name_contains: list[str] = [],  # noqa: B006
    exclude_name: list[str] = [],  # noqa: B006
    name_regex: str | None = None,
    state: list[str] = [],  # noqa: B006
    exclude_state: list[str] = [],  # noqa: B006
    tracker: list[str] = [],  # noqa: B006
    exclude_tracker: list[str] = [],  # noqa: B006
    no_tracker: bool = False,
    tracker_health: list[str] = [],  # noqa: B006
    completed: bool = False,
    incomplete: bool = False,
    active: bool = False,
    inactive: bool = False,
    stalled: bool = False,
    errored: bool = False,
    private: bool | None = None,
    ratio_min: str | None = None,
    ratio_max: str | None = None,
    size_min: str | None = None,
    size_max: str | None = None,
    progress_min: str | None = None,
    progress_max: str | None = None,
    uploaded_min: str | None = None,
    uploaded_max: str | None = None,
    seeded_for: str | None = None,
    older_than: str | None = None,
    newer_than: str | None = None,
    completed_before: str | None = None,
    completed_within: str | None = None,
    inactive_for: str | None = None,
    active_within: str | None = None,
    now: datetime | None = None,
) -> TorrentFilter:
    """Parse raw option values and assemble one validated `TorrentFilter`.

    Raises `InvalidInputError` (a `ValueError`) on any unparsable value
    or contradictory combination, always before a qBittorrent call.

    `now` is injectable so a test can pin the moment `--older-than`
    resolves against; production always passes the real clock.
    """
    moment = now or datetime.now(tz=UTC)

    return build_torrent_filter(
        categories=category,
        categories_excluded=exclude_category,
        tags_any=tag,
        tags_all=tag_all,
        tags_excluded=exclude_tag,
        save_paths=save_path,
        save_paths_excluded=exclude_save_path,
        name_contains=name_contains,
        name_excluded=exclude_name,
        name_regex=name_regex,
        states=state,
        states_excluded=exclude_state,
        trackers=tracker,
        trackers_excluded=exclude_tracker,
        has_trackers=False if no_tracker else None,
        tracker_health=tracker_health,
        completed=completed,
        incomplete=incomplete,
        active=active,
        inactive=inactive,
        stalled=stalled,
        errored=errored,
        private=private,
        ratio=Range(
            min=_maybe(parse_ratio, ratio_min),
            max=_maybe(parse_ratio, ratio_max),
        ),
        size=Range(
            min=_maybe(parse_size, size_min), max=_maybe(parse_size, size_max)
        ),
        progress=Range(
            min=_maybe(parse_percentage, progress_min),
            max=_maybe(parse_percentage, progress_max),
        ),
        uploaded=Range(
            min=_maybe(parse_size, uploaded_min),
            max=_maybe(parse_size, uploaded_max),
        ),
        seeding_time=Range(min=_maybe(parse_duration, seeded_for)),
        added=_resolve_added_window(older_than, newer_than, moment),
        completed_at=_resolve_relative_window(
            completed_before, completed_within, moment
        ),
        last_activity=_resolve_relative_window(
            inactive_for, active_within, moment
        ),
    )


def _resolve_added_window(
    older_than: str | None, newer_than: str | None, moment: datetime
) -> Range[datetime]:
    """Resolve `--older-than` / `--newer-than` against `added_on`."""
    return _resolve_relative_window(older_than, newer_than, moment)


def _resolve_relative_window(
    at_least_ago: str | None, within: str | None, moment: datetime
) -> Range[datetime]:
    """Turn a pair of relative durations into an absolute window.

    "at least N ago" bounds the window's *upper* end (must be older
    than that instant); "within N" bounds its lower end.
    """
    return Range(
        min=(
            None
            if within is None
            else moment - timedelta(seconds=parse_duration(within))
        ),
        max=(
            None
            if at_least_ago is None
            else moment - timedelta(seconds=parse_duration(at_least_ago))
        ),
    )


def _maybe[T](parser: "Callable[[str], T]", value: str | None) -> T | None:
    """Apply a parser only when the option was actually given."""
    return None if value is None else parser(value)
