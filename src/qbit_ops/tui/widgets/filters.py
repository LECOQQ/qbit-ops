"""`FiltersPanel` -- the shared filter-editing widget."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button,
    Checkbox,
    Input,
    RadioButton,
    RadioSet,
    Static,
)

from qbit_ops.features.torrents import TorrentFilter, build_torrent_filter


class FiltersPanel(Vertical):
    """The shared `TorrentFilter` vocabulary, applied entirely in memory.

    Only ever mounted inside `FiltersScreen`. No qBittorrent API call
    is ever triggered by a change here. Completion and Activity are
    each an exclusive `RadioSet`, so a contradictory pair (Completed
    *and* Incomplete) is structurally impossible through the UI --
    `build_torrent_filter`'s own rejection of that is defense in depth.
    """

    def compose(self) -> ComposeResult:
        with Horizontal(classes="f-columns"):
            with Vertical(classes="f-col"):
                yield Static("[bold]Category[/bold]")
                yield Input(placeholder="films, tv", classes="f-category")
                yield Static("[bold]State[/bold]")
                yield Input(placeholder="stalled, errored", classes="f-state")
                yield Static("[bold]Attention[/bold]")
                yield Checkbox("Stalled", classes="f-stalled")
                yield Checkbox("Errored", classes="f-errored")
            with Vertical(classes="f-col"):
                yield Static("[bold]Completion[/bold]")
                yield RadioSet(
                    RadioButton("Any"),
                    RadioButton("Completed"),
                    RadioButton("Incomplete"),
                    classes="f-completion",
                )
                yield Static("[bold]Activity[/bold]")
                yield RadioSet(
                    RadioButton("Any"),
                    RadioButton("Active"),
                    RadioButton("Inactive"),
                    classes="f-activity",
                )
        yield Static("", classes="f-error")
        with Horizontal(classes="f-actions"):
            yield Button("Apply", id="filters-apply", variant="primary")
            yield Button("Clear", id="filters-clear")
            yield Button("Cancel", id="filters-cancel")

    def build_filter(self) -> TorrentFilter:
        category_text = self.query_one(".f-category", Input).value
        state_text = self.query_one(".f-state", Input).value
        categories = [
            part.strip() for part in category_text.split(",") if part.strip()
        ]
        states = [
            part.strip() for part in state_text.split(",") if part.strip()
        ]

        completion_index = self.query_one(
            ".f-completion", RadioSet
        ).pressed_index
        activity_index = self.query_one(".f-activity", RadioSet).pressed_index

        return build_torrent_filter(
            categories=categories,
            states=states,
            completed=completion_index == 1,
            incomplete=completion_index == 2,
            active=activity_index == 1,
            inactive=activity_index == 2,
            stalled=self.query_one(".f-stalled", Checkbox).value,
            errored=self.query_one(".f-errored", Checkbox).value,
        )

    def sync_from(self, filters: TorrentFilter) -> None:
        self.query_one(".f-category", Input).value = ", ".join(
            filters.categories
        )
        self.query_one(".f-state", Input).value = ", ".join(filters.states)
        self._select_radio(
            ".f-completion",
            (
                1
                if filters.completed is True
                else (2 if filters.completed is False else 0)
            ),
        )
        self._select_radio(
            ".f-activity",
            (
                1
                if filters.active is True
                else (2 if filters.active is False else 0)
            ),
        )
        self.query_one(".f-stalled", Checkbox).value = bool(filters.stalled)
        self.query_one(".f-errored", Checkbox).value = bool(filters.errored)

    def _select_radio(self, selector: str, index: int) -> None:
        radio_set = self.query_one(selector, RadioSet)
        buttons = list(radio_set.query(RadioButton))
        buttons[index].value = True

    def show_error(self, message: str) -> None:
        self.query_one(".f-error", Static).update(message)
