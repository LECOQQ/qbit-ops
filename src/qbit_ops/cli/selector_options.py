"""Declare the torrent-selector CLI options exactly once.

`torrents list` and the five bulk mutation commands
(`pause`/`resume`/`start`/`reannounce`/`delete`) all expose the same
selector vocabulary. Declaring it per command let help text, flag
spelling and ordering drift independently; every option now has one
definition here, reused as an `Annotated` alias.

Only genuinely per-command wording stays per command: `--hash` and
`--all` name the action they perform, so they get one alias each, built
from a shared template where the wording allows it.

Consumed by `qbit_ops.cli.commands.torrents`; kept apart from it so the
command module reads as a list of commands rather than a wall of option
declarations.
"""

from typing import Annotated

import typer

from qbit_core.shared.selection import STATE_FILTER_VALUES

CATEGORY_FILTER_HELP = (
    "Restrict to a category (repeatable; combines with OR). Use "
    "'uncategorized' for torrents without a category."
)
STATE_FILTER_HELP = (
    "Restrict to a state group (repeatable; combines with OR). Supported: "
    f"{', '.join(sorted(STATE_FILTER_VALUES))}."
)
TRACKER_FILTER_HELP = (
    "Restrict to torrents using a tracker, matched by host[:port] (a full "
    "announce URL is also accepted; only its host and port are used)."
)


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
    str | None, typer.Option("--tracker", help=TRACKER_FILTER_HELP)
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
