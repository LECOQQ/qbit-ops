"""Small, always-mounted status-line widgets.

Each is a marker `Static` subclass whose content is set from
`QbitOpsTuiApp` via `query_one(...).update(...)`, never mutating shared
state or dispatching anything itself.
"""

from __future__ import annotations

from textual.widgets import Static


class ConnectionBanner(Static):
    """A dismissible-looking, non-blocking banner shown while reconnecting.

    Never replaces the workspace content underneath it -- stale data
    stays visible.
    """


class LastActionBar(Static):
    """A compact, persistent record of the most recent bulk action.

    A five-second toast alone is easy to miss, so this stays until a
    later mutation replaces it -- exactly one result, never a history.
    Renders only safe structured data, never a tracker URL, raw remote
    message, credential, or passkey.
    """


class FilterSummary(Static):
    """A concise, always-visible line describing the active filter/search.

    e.g. "146 shown / 1,105 · stalled" or
    "24 shown / 1,105 · category: films · stalled · search: ubuntu".
    Purely presentational: derived from `TuiState`, never fetched. Only
    shown in the Torrents workspace.
    """
