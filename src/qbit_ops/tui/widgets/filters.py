"""`FiltersPanel` -- the shared filter-editing widget, one pane visible.

Every pane's rows are mounted once, as siblings, and switching tabs
only ever toggles `display` -- never remounts. Two things this buys:

- the depth budget (`FiltersScreen` -> `VerticalScroll` -> `FiltersPanel`
  -> row -> `Input`, five deep, `scripts/tui_wireframe.py --inventory`'s
  contract for `filters`) never grows with the pane switched to, since
  there is only ever one row container, not one per pane;
- each pane always contributes exactly `PANE_HEIGHT` rows (padded with
  blank spacer rows the same way
  `.agents/specs/tui-filters.wireframes/filters_modal.py`'s `build()`
  centres a short pane), so the dialog never resizes when `alt+left`/
  `alt+right` changes the active pane.

Building the whole `TorrentFilter` vocabulary in memory only, never
touching qBittorrent (see `FiltersDraft`).
"""

from __future__ import annotations

from collections.abc import Callable

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Checkbox, Input, RadioButton, RadioSet, Static

from qbit_core.shared.selection import TorrentFilter
from qbit_ops.tui.filter_form import FiltersDraft

# The zone every pane is centred into -- criterion 2's invariant.
PANE_HEIGHT = 10


def _pane_slug(pane: str) -> str:
    return pane.lower()


class _Row(Horizontal):
    """One line, scoped to exactly one pane. A row with no children is
    a blank spacer -- present so the pane it belongs to always totals
    `PANE_HEIGHT` rows, never removed, only ever hidden by `display`."""

    def __init__(self, pane: str, *children: Widget) -> None:
        super().__init__(*children, classes=f"f-row -{_pane_slug(pane)}")


def _label(text: str) -> Static:
    return Static(text, classes="f-label")


def _hint(text: str) -> Static:
    """A row's only content: no `.f-label` column width, so a longer
    explanatory line is never clipped to the label column."""
    return Static(text, classes="f-hint")


def _field(field: str, *, placeholder: str = "") -> Input:
    return Input(id=f"f-{field}", placeholder=placeholder, classes="f-field")


def _tri_state(field: str, options: tuple[str, str, str]) -> RadioSet:
    any_label, on_label, off_label = options
    return RadioSet(
        RadioButton(any_label),
        RadioButton(on_label),
        RadioButton(off_label),
        id=f"f-{field}",
        classes="f-tristate",
    )


# -- Organisation -------------------------------------------------------


def _organisation_rows() -> list[_Row]:
    pane = "Organisation"
    rows = [
        _Row(
            pane,
            _label("Category"),
            _field("categories", placeholder="films, tv"),
            _label("not"),
            _field("categories_excluded", placeholder="archive"),
        ),
        _Row(
            pane,
            _label("Tag any"),
            _field("tags_any", placeholder="stale, keep"),
        ),
        _Row(pane, _label("Tag all"), _field("tags_all")),
        _Row(
            pane,
            _label("Tag none"),
            _field("tags_none", placeholder="seed-forever"),
        ),
        _Row(
            pane,
            _label("Save path"),
            _field("save_paths", placeholder="/data/media"),
            _label("not"),
            _field("save_paths_excluded", placeholder="/tmp"),
        ),
        _Row(
            pane,
            _label("Name has"),
            _field("name_contains", placeholder="debian"),
            _label("not"),
            _field("name_excluded", placeholder="rc"),
        ),
        _Row(
            pane,
            _label("Name re"),
            _field("name_regex", placeholder="^ubuntu-2[24]\\."),
        ),
        _Row(pane),
        _Row(
            pane,
            _hint("not = exclude. Comma-separated; a value is matched whole."),
        ),
    ]
    return _padded(pane, rows)


# -- State ----------------------------------------------------------------


def _state_rows() -> list[_Row]:
    pane = "State"
    rows = [
        _Row(
            pane,
            _label("State"),
            _field("states", placeholder="seeding"),
            _label("not"),
            _field("states_excluded", placeholder="errored"),
        ),
        _Row(pane),
        _Row(
            pane,
            _label("Completion"),
            _tri_state("completed", ("Any", "Complete", "Incomplete")),
        ),
        _Row(
            pane,
            _label("Activity"),
            _tri_state("active", ("Any", "Active", "Inactive")),
        ),
        _Row(
            pane,
            _label("Private"),
            _tri_state("private", ("Any", "Private", "Public")),
        ),
        _Row(pane),
        _Row(
            pane,
            _label("Attention"),
            Checkbox("Stalled", id="f-stalled", classes="f-check"),
            Checkbox("Errored", id="f-errored", classes="f-check"),
        ),
        _Row(pane),
        _Row(
            pane,
            _hint(
                "downloading · seeding · checking · stalled "
                "· errored · unknown"
            ),
        ),
    ]
    return _padded(pane, rows)


# -- Measures ---------------------------------------------------------------


def _range_row(
    pane: str, label: str, low: str, high: str, *, suffix: str = ""
) -> _Row:
    children = [_label(label), _field(low), _label("to"), _field(high)]
    if suffix:
        children.append(_label(suffix))
    return _Row(pane, *children)


def _measures_rows() -> list[_Row]:
    pane = "Measures"
    rows = [
        _range_row(pane, "Ratio", "ratio_min", "ratio_max"),
        _range_row(pane, "Size", "size_min", "size_max"),
        _range_row(pane, "Progress", "progress_min", "progress_max"),
        _range_row(pane, "Uploaded", "uploaded_min", "uploaded_max"),
        _Row(pane),
        _Row(pane, _label("Age"), _hint("time since, e.g. 90d · 6h · 2w")),
        _range_row(pane, "Added", "added_min", "added_max"),
        _range_row(pane, "Completed", "completed_at_min", "completed_at_max"),
        _range_row(pane, "Last act.", "last_activity_min", "last_activity_max"),
        _Row(
            pane,
            _label("Seeded"),
            _field("seeded_for", placeholder="30d"),
            _label("or longer"),
        ),
    ]
    return _padded(pane, rows)


# -- Trackers -----------------------------------------------------------


def _trackers_rows() -> list[_Row]:
    pane = "Trackers"
    rows = [
        _Row(
            pane,
            _label("Presence"),
            Checkbox(
                "only torrents with no tracker at all",
                id="f-no_trackers",
                classes="f-check",
            ),
        ),
        _Row(pane),
        _Row(
            pane,
            _hint("Host, exclusion and health need a per-torrent scan"),
        ),
        _Row(pane, _hint("(the INSPECT stage). They stay CLI-only:")),
        _Row(
            pane,
            _hint("--tracker   --exclude-tracker   --tracker-health"),
        ),
    ]
    return _padded(pane, rows)


def _padded(pane: str, rows: list[_Row]) -> list[_Row]:
    slack = PANE_HEIGHT - len(rows)
    top, bottom = slack // 2, slack - slack // 2
    return (
        [_Row(pane) for _ in range(top)]
        + rows
        + [_Row(pane) for _ in range(bottom)]
    )


PANE_ROW_BUILDERS: dict[str, Callable[[], list[_Row]]] = {
    "Organisation": _organisation_rows,
    "State": _state_rows,
    "Measures": _measures_rows,
    "Trackers": _trackers_rows,
}


class FiltersPanel(Vertical):
    """Every pane's rows, mounted once; only one pane's worth is shown.

    Holds a `FiltersDraft` -- never a `TorrentFilter` -- and never
    calls `set_filters`/reads/writes `TuiController` itself:
    `FiltersScreen` owns the commit points (Apply/Cancel/Clear).
    """

    def __init__(self) -> None:
        super().__init__()
        self.draft = FiltersDraft()

    def compose(self) -> ComposeResult:
        for builder in PANE_ROW_BUILDERS.values():
            yield from builder()

    def show_pane(self, pane: str) -> None:
        slug = _pane_slug(pane)
        for row in self.query(_Row):
            row.display = row.has_class(f"-{slug}")

    def sync_from(self, filters: TorrentFilter) -> None:
        self.draft = FiltersDraft.from_filter(filters)
        self._write_draft_to_widgets()

    def _write_draft_to_widgets(self) -> None:
        draft = self.draft
        self.query_one("#f-categories", Input).value = draft.categories
        self.query_one("#f-categories_excluded", Input).value = (
            draft.categories_excluded
        )
        self.query_one("#f-tags_any", Input).value = draft.tags_any
        self.query_one("#f-tags_all", Input).value = draft.tags_all
        self.query_one("#f-tags_none", Input).value = draft.tags_none
        self.query_one("#f-save_paths", Input).value = draft.save_paths
        self.query_one("#f-save_paths_excluded", Input).value = (
            draft.save_paths_excluded
        )
        self.query_one("#f-name_contains", Input).value = draft.name_contains
        self.query_one("#f-name_excluded", Input).value = draft.name_excluded
        self.query_one("#f-name_regex", Input).value = draft.name_regex
        self.query_one("#f-states", Input).value = draft.states
        self.query_one("#f-states_excluded", Input).value = (
            draft.states_excluded
        )
        self._select_radio("#f-completed", draft.completed)
        self._select_radio("#f-active", draft.active)
        self._select_radio("#f-private", draft.private)
        self.query_one("#f-stalled", Checkbox).value = draft.stalled
        self.query_one("#f-errored", Checkbox).value = draft.errored
        self.query_one("#f-ratio_min", Input).value = draft.ratio_min
        self.query_one("#f-ratio_max", Input).value = draft.ratio_max
        self.query_one("#f-size_min", Input).value = draft.size_min
        self.query_one("#f-size_max", Input).value = draft.size_max
        self.query_one("#f-progress_min", Input).value = draft.progress_min
        self.query_one("#f-progress_max", Input).value = draft.progress_max
        self.query_one("#f-uploaded_min", Input).value = draft.uploaded_min
        self.query_one("#f-uploaded_max", Input).value = draft.uploaded_max
        self.query_one("#f-added_min", Input).value = draft.added_min
        self.query_one("#f-added_max", Input).value = draft.added_max
        self.query_one("#f-completed_at_min", Input).value = (
            draft.completed_at_min
        )
        self.query_one("#f-completed_at_max", Input).value = (
            draft.completed_at_max
        )
        self.query_one("#f-last_activity_min", Input).value = (
            draft.last_activity_min
        )
        self.query_one("#f-last_activity_max", Input).value = (
            draft.last_activity_max
        )
        self.query_one("#f-seeded_for", Input).value = draft.seeded_for
        self.query_one("#f-no_trackers", Checkbox).value = draft.no_trackers

    def _select_radio(self, selector: str, index: int) -> None:
        radio_set = self.query_one(selector, RadioSet)
        buttons = list(radio_set.query(RadioButton))
        for i, button in enumerate(buttons):
            button.value = i == index

    def pull_draft_from_widgets(self) -> FiltersDraft:
        """Read every widget back into a fresh `FiltersDraft` -- the
        single point that turns "what is on screen" into the model
        `to_filter()`/`pane_has_pending_edits` reason about."""
        d = self.draft
        d.categories = self.query_one("#f-categories", Input).value
        d.categories_excluded = self.query_one(
            "#f-categories_excluded", Input
        ).value
        d.tags_any = self.query_one("#f-tags_any", Input).value
        d.tags_all = self.query_one("#f-tags_all", Input).value
        d.tags_none = self.query_one("#f-tags_none", Input).value
        d.save_paths = self.query_one("#f-save_paths", Input).value
        d.save_paths_excluded = self.query_one(
            "#f-save_paths_excluded", Input
        ).value
        d.name_contains = self.query_one("#f-name_contains", Input).value
        d.name_excluded = self.query_one("#f-name_excluded", Input).value
        d.name_regex = self.query_one("#f-name_regex", Input).value
        d.states = self.query_one("#f-states", Input).value
        d.states_excluded = self.query_one("#f-states_excluded", Input).value
        d.completed = self._radio_index("#f-completed")
        d.active = self._radio_index("#f-active")
        d.private = self._radio_index("#f-private")
        d.stalled = self.query_one("#f-stalled", Checkbox).value
        d.errored = self.query_one("#f-errored", Checkbox).value
        d.ratio_min = self.query_one("#f-ratio_min", Input).value
        d.ratio_max = self.query_one("#f-ratio_max", Input).value
        d.size_min = self.query_one("#f-size_min", Input).value
        d.size_max = self.query_one("#f-size_max", Input).value
        d.progress_min = self.query_one("#f-progress_min", Input).value
        d.progress_max = self.query_one("#f-progress_max", Input).value
        d.uploaded_min = self.query_one("#f-uploaded_min", Input).value
        d.uploaded_max = self.query_one("#f-uploaded_max", Input).value
        d.added_min = self.query_one("#f-added_min", Input).value
        d.added_max = self.query_one("#f-added_max", Input).value
        d.completed_at_min = self.query_one("#f-completed_at_min", Input).value
        d.completed_at_max = self.query_one("#f-completed_at_max", Input).value
        d.last_activity_min = self.query_one(
            "#f-last_activity_min", Input
        ).value
        d.last_activity_max = self.query_one(
            "#f-last_activity_max", Input
        ).value
        d.seeded_for = self.query_one("#f-seeded_for", Input).value
        d.no_trackers = self.query_one("#f-no_trackers", Checkbox).value
        self.draft = d
        return d

    def _radio_index(self, selector: str) -> int:
        radio_set = self.query_one(selector, RadioSet)
        pressed = radio_set.pressed_index
        return 0 if pressed is None or pressed < 0 else pressed
