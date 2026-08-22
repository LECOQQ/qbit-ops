"""Value-action modals -- one patron, four instantiations.

`category set`/`tag add`/`tag remove`/`throttle` all fill the same
four slots, in the same order (see `.agents/specs/tui-filters.md`,
"Les actions à valeur -- un seul patron, quatre instanciations"):
scope, input, consequence, context. `enter` goes to `PreviewScreen`
without mutating anything -- the CLI's own `--dry-run` default has the
same shape here.

See `qbit_ops.tui.modals`'s module docstring for the `self.app`
typing note.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Checkbox, Input, Static

from qbit_core.errors import InvalidInputError
from qbit_core.features.torrents import TorrentBulkAction
from qbit_core.shared.torrent_states import TorrentSnapshot
from qbit_ops.tui.modals.base import KeyHint, QbitModal
from qbit_ops.tui.value_form import (
    CategorySetDraft,
    PlanKwargs,
    TagsDraft,
    ThrottleDraft,
    _normalize_tags,
    format_scope,
    tag_add_verdict,
    tag_remove_verdict,
    throttle_current,
    throttle_verdict,
)

if TYPE_CHECKING:
    from qbit_ops.tui.app import QbitOpsTuiApp


class ValueActionScreen(QbitModal):
    """Shared frame: scope, one input, its live verdict, the
    instance's own context -- see the module docstring. Never mutates
    anything; `⏎` collects the argument and opens `PreviewScreen`."""

    MODAL_WIDTH = "medium"
    MODAL_TITLE = "Value"  # placeholder: never instantiated directly
    DIALOG_ID = "value-dialog"
    bulk_action: ClassVar[TorrentBulkAction]

    BINDINGS = [
        Binding("up", "app.focus_previous", "Up", show=False),
        Binding("down", "app.focus_next", "Down", show=False),
    ]

    def __init__(
        self,
        selected: tuple[TorrentSnapshot, ...],
        names: tuple[str, ...],
    ) -> None:
        super().__init__()
        self.selected = selected
        self._names = names

    def compose_dialog(self) -> ComposeResult:
        yield Static(format_scope(self._names), classes="v-scope")
        yield from self.compose_fields()
        yield Static("", classes="v-verdict")
        yield Static(id="v-source", classes="v-source")

    def compose_fields(self) -> ComposeResult:
        """The one field this modal collects -- implemented by each
        subclass."""
        raise NotImplementedError

    def on_mount(self) -> None:
        self._render_source()
        self._render_verdict()
        self.query(Input).first().focus()

    def _render_source(self) -> None:
        self.query_one("#v-source", Static).update(self.source_text())

    def source_text(self) -> str:
        raise NotImplementedError

    def _render_verdict(self) -> None:
        self.query_one(".v-verdict", Static).update(self.verdict_text())

    def verdict_text(self) -> str:
        raise NotImplementedError

    def plan_kwargs(self) -> PlanKwargs:
        raise NotImplementedError

    def on_input_changed(self, event: Input.Changed) -> None:
        self._render_verdict()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        self._render_verdict()

    def action_preview(self) -> None:
        """`⏎`: collect the argument and open `PreviewScreen` -- never
        mutates. An invalid draft re-renders the verdict as the error
        instead, and leaves the modal open (same disarm-not-crash shape
        as `FiltersScreen.apply_draft`)."""
        try:
            kwargs = self.plan_kwargs()
        except (InvalidInputError, ValueError) as error:
            self.query_one(".v-verdict", Static).update(f"✕ {error}")
            return
        app = cast("QbitOpsTuiApp", self.app)
        hashes = tuple(torrent.hash for torrent in self.selected)
        self.dismiss()
        app._open_preview_for_action(self.bulk_action, hashes, **kwargs)

    def action_dismiss(self, result: None = None) -> None:  # type: ignore[override]
        self.dismiss()


class CategorySetScreen(ValueActionScreen):
    MODAL_TITLE = "Set category"
    MODAL_KEYS = (
        KeyHint(("tab",), "Move"),
        KeyHint(("enter",), "Preview"),
        KeyHint(("ctrl+n",), "Create"),
        KeyHint(("escape",), "Cancel"),
    )
    bulk_action: ClassVar[TorrentBulkAction] = "category_set"

    BINDINGS = [
        *ValueActionScreen.BINDINGS,
        Binding("ctrl+n", "toggle_create", "Create"),
    ]

    def __init__(
        self,
        selected: tuple[TorrentSnapshot, ...],
        names: tuple[str, ...],
        known_categories: tuple[str, ...],
    ) -> None:
        super().__init__(selected, names)
        self.draft = CategorySetDraft()
        self._known = known_categories

    def compose_fields(self) -> ComposeResult:
        with Horizontal(classes="v-row"):
            yield Static("Category", classes="v-label")
            yield Input(id="v-category", classes="v-field")
        yield Checkbox("create it as well", id="v-create", classes="v-create")

    def action_toggle_create(self) -> None:
        checkbox = self.query_one("#v-create", Checkbox)
        checkbox.value = not checkbox.value
        self._render_verdict()

    def source_text(self) -> str:
        existing = " · ".join(self._known) if self._known else "(none)"
        return f"Existing       {existing}"

    def verdict_text(self) -> str:
        name = self.query_one("#v-category", Input).value.strip()
        if not name:
            return ""
        if name in self._known:
            return ""
        return f'✕ no category named "{name}" on this instance'

    def plan_kwargs(self) -> PlanKwargs:
        self.draft.category = self.query_one("#v-category", Input).value
        self.draft.create = self.query_one("#v-create", Checkbox).value
        return self.draft.to_plan_kwargs(known_categories=self._known)


class _TagsScreen(ValueActionScreen):
    """Shared by `tag add`/`tag remove`: same one field, different
    verdict/source/action."""

    MODAL_TITLE = "Tags"  # placeholder: never instantiated directly
    MODAL_KEYS = (
        KeyHint(("tab",), "Move"),
        KeyHint(("enter",), "Preview"),
        KeyHint(("escape",), "Cancel"),
    )

    def __init__(
        self, selected: tuple[TorrentSnapshot, ...], names: tuple[str, ...]
    ) -> None:
        super().__init__(selected, names)
        self.draft = TagsDraft()

    def compose_fields(self) -> ComposeResult:
        with Horizontal(classes="v-row"):
            yield Static("Tags", classes="v-label")
            yield Input(
                id="v-tags", classes="v-field", placeholder="comma-separated"
            )

    def _typed_tags(self) -> tuple[str, ...]:
        return _normalize_tags(self.query_one("#v-tags", Input).value)

    def plan_kwargs(self) -> PlanKwargs:
        self.draft.tags = self.query_one("#v-tags", Input).value
        return self.draft.to_plan_kwargs()


class TagAddScreen(_TagsScreen):
    MODAL_TITLE = "Add tags"
    bulk_action: ClassVar[TorrentBulkAction] = "tag_add"

    def __init__(
        self,
        selected: tuple[TorrentSnapshot, ...],
        names: tuple[str, ...],
        known_tags: tuple[str, ...],
    ) -> None:
        super().__init__(selected, names)
        self._known = known_tags

    def source_text(self) -> str:
        existing = " · ".join(self._known) if self._known else "(none)"
        return f"Existing       {existing}"

    def verdict_text(self) -> str:
        tags = self._typed_tags()
        if not tags:
            return ""
        return "\n".join(tag_add_verdict(tags, self.selected))


class TagRemoveScreen(_TagsScreen):
    MODAL_TITLE = "Remove tags"
    bulk_action: ClassVar[TorrentBulkAction] = "tag_remove"

    def source_text(self) -> str:
        counts = Counter(
            tag for torrent in self.selected for tag in torrent.tags
        )
        if not counts:
            return "On selection  (none)"
        parts = [
            f"{tag} ({count})"
            for tag, count in sorted(counts.items(), key=lambda i: -i[1])
        ]
        return "On selection  " + " · ".join(parts)

    def verdict_text(self) -> str:
        tags = self._typed_tags()
        if not tags:
            return ""
        return "\n".join(tag_remove_verdict(tags, self.selected))


class ThrottleScreen(ValueActionScreen):
    MODAL_TITLE = "Set transfer limits"
    MODAL_KEYS = (
        KeyHint(("tab",), "Move"),
        KeyHint(("enter",), "Preview"),
        KeyHint(("escape",), "Cancel"),
    )
    bulk_action: ClassVar[TorrentBulkAction] = "throttle"

    def __init__(
        self, selected: tuple[TorrentSnapshot, ...], names: tuple[str, ...]
    ) -> None:
        super().__init__(selected, names)
        self.draft = ThrottleDraft()

    def compose_fields(self) -> ComposeResult:
        with Horizontal(classes="v-row"):
            yield Static("↑ Upload", classes="v-label")
            yield Input(id="v-upload", classes="v-field")
        with Horizontal(classes="v-row"):
            yield Static("↓ Download", classes="v-label")
            yield Input(id="v-download", classes="v-field")
        yield Static(
            "number + unit · 0 unlimited · blank leaves it",
            classes="v-hint",
        )

    def source_text(self) -> str:
        return "Current        " + throttle_current(self.selected)

    def verdict_text(self) -> str:
        upload = self.query_one("#v-upload", Input).value
        download = self.query_one("#v-download", Input).value
        if not upload.strip() and not download.strip():
            return ""
        return throttle_verdict(upload, download)

    def plan_kwargs(self) -> PlanKwargs:
        self.draft.upload = self.query_one("#v-upload", Input).value
        self.draft.download = self.query_one("#v-download", Input).value
        return self.draft.to_plan_kwargs()
