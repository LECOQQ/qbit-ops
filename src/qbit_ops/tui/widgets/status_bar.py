"""Small, always-mounted status-line widgets.

Moved out of `qbit_ops.tui.app` (see docs/DECISIONS.md, TUI reorg
phase). No behavior change: each is a marker `Static` subclass whose
content is set from `QbitOpsTuiApp` via `query_one(...).update(...)`,
never mutating shared state or dispatching anything itself.
"""

from __future__ import annotations

from textual.widgets import Static


class ConnectionBanner(Static):
    """A dismissible-looking, non-blocking banner shown while reconnecting.

    Never replaces the workspace content underneath it -- stale data
    stays visible (see docs/TUI_ARCHITECTURE_REVIEW.md §5/§6).
    """


class LastActionBar(Static):
    """A compact, persistent record of the most recent bulk action.

    Closure review finding N-3: a five-second Textual toast was the only
    visible trace of a mutation whose Preview was no longer active, so
    an operator looking away could miss that a request had been sent --
    while the selection policy had already been applied on their behalf.
    This line is rendered from `QbitOpsTuiApp._last_mutation_result` and
    stays until a later mutation replaces it.

    Deliberately *not* a mutation history: exactly one result, the
    latest. It renders only safe structured data (action, counts, an
    already-sanitized error category, a local timestamp) -- never a
    tracker URL, raw remote message, credential, or passkey -- and never
    dispatches anything or touches the selection.
    """


class FilterSummary(Static):
    """A concise, always-visible line describing the active filter/search.

    e.g. "146 shown / 1,105 · stalled" or
    "24 shown / 1,105 · category: films · stalled · search: ubuntu" --
    see docs/COMMANDS.md ("TUI"). Purely presentational: derived from
    `TuiState`, never fetched. Only shown in the Torrents workspace.
    """
