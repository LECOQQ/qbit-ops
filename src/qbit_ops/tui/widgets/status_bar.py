"""Small, always-mounted status-line widgets.

Each is a marker `Static` subclass whose content is set from
`QbitOpsTuiApp` via `query_one(...).update(...)`, never mutating shared
state or dispatching anything itself -- except `CommandBar`, which is
self-sufficient the same way Textual's own `Footer` is.
"""

from __future__ import annotations

from typing import Any

from textual.widgets import Input, Static

from qbit_core.features.status import StatusSnapshot
from qbit_ops.tui.formatting import (
    _format_command_bar,
    _format_command_entry,
    _format_command_value_entry,
    _format_global_rate,
)


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


class CommandBar(Static):
    """A compact, contextual replacement for Textual's default `Footer`.

    Renders `[key→Description]` tokens straight from
    `Screen.active_bindings` -- the same source `Footer` reads -- so the
    bar can never drift from real key availability, refreshing on
    `Screen.bindings_updated_signal` exactly like `Footer`.

    A live search replaces the `[/→Search]` token with a pipe-delimited
    `|search: xxx|` one. "Active" is real focus on `#search-input`,
    re-checked every render rather than a sticky flag, so it self-heals
    the instant focus leaves the input -- which also lets
    `Screen.active_bindings` drop every other single-key binding via
    Textual's `check_consume_key` while the input is focused, so
    `entries` naturally shrinks to just Search while typing.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._search_text: str | None = None

    def on_mount(self) -> None:
        self.screen.bindings_updated_signal.subscribe(
            self, self._bindings_changed
        )
        self._bindings_changed(self.screen)

    def set_search_state(self, text: str | None) -> None:
        """Push the live search text (or `None` once search closes) and
        re-render immediately -- called by `QbitOpsTuiApp` after every
        keystroke, on open, and on close."""
        self._search_text = text
        self._bindings_changed(self.screen)

    def _search_input_focused(self) -> bool:
        focused = self.app.focused
        return isinstance(focused, Input) and focused.id == "search-input"

    def _bindings_changed(self, screen: Any) -> None:
        entries = [
            (
                self.app.get_key_display(active.binding),
                active.binding.description,
            )
            for active in self.screen.active_bindings.values()
            if active.binding.show and active.enabled
        ]
        if self._search_text is None or not self._search_input_focused():
            self.update(_format_command_bar(entries))
            return

        tokens = [
            (
                _format_command_value_entry("search", self._search_text)
                if description == "Search"
                else _format_command_entry(key, description)
            )
            for key, description in entries
        ]
        self.update(" ".join(tokens))


class FooterTotal(Static):
    """The footer row's right-aligned `|Total: y|` token.

    A sibling of `CommandBar` in `#footer-row` (`width: auto`, pinned
    to the row's right edge by `CommandBar`'s own `width: 1fr`) rather
    than a token appended into `CommandBar`'s own string -- computing a
    right-aligned position inside a markup-bearing string is exactly
    the kind of raw-width arithmetic this codebase avoids. Empty (and
    so invisible) whenever search isn't active.
    """

    def set_total(self, total: int | None) -> None:
        if total is None:
            self.update("")
            return
        self.update(_format_command_value_entry("Total", f"{total:,}"))


class GlobalRateDisplay(Static):
    """The top-right global qBittorrent transfer-rate indicator.

    Reuses `TuiState.status.rates` -- the same data the Overview rail
    already renders -- never a second qBittorrent call. Blank before
    the first successful refresh, since there is no rate to show yet.
    """

    def render_state(self, status: StatusSnapshot | None) -> None:
        if status is None:
            self.update("")
            return
        rates = status.rates
        self.update(
            _format_global_rate(
                rates.download_bytes_per_second, rates.upload_bytes_per_second
            )
        )
