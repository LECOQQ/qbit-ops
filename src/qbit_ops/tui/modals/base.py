"""`QbitModal` -- the frame every qbit-ops modal is built from.

A modal supplies its content, a title, a width name and the *keys* it
wants advertised. The frame, the scrolling, the title in the
interrupted border and the key hints in the border subtitle all come
from here, styled once in `qbit_ops/tui/qbit_ops.tcss`.

Border hints and the command bar share one grammar and one renderer
(`_format_command_entry`): `[key->Description]`, everywhere. A modal
names real Textual keys, never a sentence -- `tests/test_tui_app.py`
holds every announced key to being a binding that is actually active
on that screen.

The width is deliberately a *name*, not a number: a new modal picks
from a scale of four instead of re-deciding one, which is what stops
the widths from drifting apart again.
"""

from __future__ import annotations

from typing import Any, ClassVar, Final, NamedTuple

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen

from qbit_ops.tui.formatting import _format_command_bar

# The scale, and the only place it is written as numbers outside the
# stylesheet. `tests/test_tui_app.py` pins the two together, so a width
# changed in one and not the other fails rather than drifts.
#
# `wide` exists because `FiltersScreen` measured wrong on `large`: at
# 100 columns its own content -- fields and hint lines -- never exceeds
# ~59 cells, but its border *footer* does, once every announced key is
# rendered (68 cells on Linux, `Ctrl+R`; 64 on macOS, `^r`). Textual
# truncates a border label past `width - BORDER_LABEL_MARGIN`
# (`qbit_ops.tui.tab_bar`, measured against Textual's own
# `render_border_label`), so the real floor is the footer's own worst
# case plus that margin: 68 + 6 = 74. `large` was never the content's
# requirement, only headroom nothing used -- `wide` (76) clears that
# floor, and leaves `large` for a modal that genuinely needs it
# (`DetailsScreen`, `ExplainScreen`).
MODAL_WIDTHS: Final[dict[str, int]] = {
    "small": 48,
    "medium": 64,
    "wide": 76,
    "large": 100,
}


class KeyHint(NamedTuple):
    """One border-subtitle token: real keys, one curated label.

    `keys` are Textual key names, checked against the screen's live
    bindings. `label` is an editorial choice -- the real binding
    descriptions ("Previous option", "Delete all to the left") do not
    fit a 48-column border, which is the whole reason this list is
    curated rather than derived.
    """

    keys: tuple[str, ...]
    label: str


CLOSE_HINT: Final = KeyHint(("escape",), "Close")


class QbitModal(ModalScreen[None]):
    """One modal frame, three words of configuration."""

    MODAL_TITLE: ClassVar[str] = ""
    """Shown in the interrupted top border, coloured by the theme."""

    MODAL_WIDTH: ClassVar[str] = "medium"
    """A key of `MODAL_WIDTHS` -- never a column count."""

    MODAL_KEYS: ClassVar[tuple[KeyHint, ...]] = (CLOSE_HINT,)
    """The keys this modal advertises in its bottom border."""

    DIALOG_ID: ClassVar[str] = ""
    """The dialog container's id, so content rules can target it."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.MODAL_WIDTH not in MODAL_WIDTHS:
            raise ValueError(
                f"{cls.__name__}.MODAL_WIDTH is {cls.MODAL_WIDTH!r}; "
                f"expected one of {sorted(MODAL_WIDTHS)}"
            )
        for attribute in ("MODAL_TITLE", "DIALOG_ID"):
            if not getattr(cls, attribute):
                raise ValueError(f"{cls.__name__} declares no {attribute}")

    def compose(self) -> ComposeResult:
        with VerticalScroll(
            id=self.DIALOG_ID,
            classes=f"qbit-dialog -{self.MODAL_WIDTH}",
        ):
            yield from self.compose_dialog()

    def compose_dialog(self) -> ComposeResult:
        """The modal's own content, inside the shared frame."""
        return iter(())

    def key_hints(self) -> str:
        """This modal's hints, rendered from its live bindings.

        Key *displays* are never written down: they come from the
        binding Textual actually resolved, so `esc`, `space` and the
        arrow glyphs cannot drift from what the terminal answers. A key
        with no active binding renders nothing -- degrading a hint
        rather than crashing a running TUI; the test suite is what
        stops one from shipping.
        """
        entries: list[tuple[str, str]] = []
        for hint in self.MODAL_KEYS:
            bindings = [self._binding_for(key) for key in hint.keys]
            displays = [
                self.app.get_key_display(binding)
                for binding in bindings
                if binding is not None
            ]
            if displays:
                entries.append(("/".join(displays), hint.label))
        return _format_command_bar(entries)

    def _binding_for(self, key: str) -> Binding | None:
        """The binding Textual would actually run for `key` here.

        Two sources, because one is a wrong answer.
        `Screen.active_bindings` walks the *modal* chain, which stops
        at this screen -- so it never lists `escape` on a modal that
        does not declare it, even though the App's `priority=True`
        binding reaches it and closes it. Reading that source alone
        silently dropped the way out of three modals.
        """
        active = self.active_bindings.get(key)
        if active is not None:
            return active.binding
        return next(
            (
                binding
                for binding in self.app.BINDINGS
                if isinstance(binding, Binding)
                and binding.priority
                and binding.key == key
            ),
            None,
        )

    def on_mount(self) -> None:
        # Textual dispatches every `on_mount` in the MRO, subclass
        # first -- so a modal keeps its own `on_mount` (focus, initial
        # render) and still gets this frame, with no super() call.
        self.query_one(f"#{self.DIALOG_ID}").border_title = self.MODAL_TITLE
        # Subscribed, not read once: a modal focuses its first control
        # from its own `on_mount`, and that focus reaches
        # `active_bindings` a frame later. Reading here would measure
        # the screen before anything inside it was focused, and drop
        # every hint that focus brings. Same signal `CommandBar` uses.
        self.bindings_updated_signal.subscribe(self, self._render_hints)
        self._render_hints(self)

    def _render_hints(self, _screen: object) -> None:
        self.query_one(f"#{self.DIALOG_ID}").border_subtitle = self.key_hints()
