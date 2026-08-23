"""`QbitRadioButton` -- the one `RadioButton` every qbit-ops modal composes.

The same fix as `QbitCheckbox` (`qbit_ops.tui.widgets.checkbox`), applied
to the other half of Textual's `ToggleButton` family: `RadioButton`
always renders `BUTTON_LEFT` + `BUTTON_INNER` + `BUTTON_RIGHT` --
`▐●▌`, selected or not, only the inner glyph's *colour* changing.
Two solid half-blocks around a dot was the second control grammar this
product carried -- `QbitCheckbox` already replaced its own `▐X▌` with
one glyph; this brings `RadioButton` to the same rule instead of
leaving the two to keep disagreeing.

`✓` marks the option currently selected in its `RadioSet`; every other
option renders a blank placeholder, never `✗` -- `✗` is this product's
mark for an actual negative (see `qbit_ops.tui.formatting`), and "one
of several alternatives, not this one" is not one.
"""

from __future__ import annotations

from textual.widgets import RadioButton


class QbitRadioButton(RadioButton):
    """A `RadioButton` with a one-glyph, state-coloured button, not `▐●▌`."""

    BUTTON_LEFT = ""
    BUTTON_RIGHT = ""

    @property
    def BUTTON_INNER(self) -> str:
        return "✓" if self.value else " "
