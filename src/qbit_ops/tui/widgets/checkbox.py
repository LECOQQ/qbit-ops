"""`QbitCheckbox` -- the one `Checkbox` every qbit-ops modal composes.

Textual's default `ToggleButton._button` always renders `BUTTON_LEFT` +
`BUTTON_INNER` + `BUTTON_RIGHT` regardless of state -- `▐X▌`, on or off
alike, only the inner glyph's *colour* changes. Two solid half-blocks
around a bare `X` reads as decoration, not as a checkbox, and `▌`
(`BUTTON_RIGHT`) is East Asian Width *Ambiguous* -- `unicodedata.
east_asian_width("▌") == "A"` -- unlike `✓`/`✗`, both *Neutral* and
`rich.cells.cell_len` 1: this also removes the one place a checkbox
could render two cells wide under a CJK-leaning locale.

Reuses the product's positive/negative grammar -- `✓`/`✗` (U+2713/
U+2717, distinct from `✔`/`✕`, U+2714/U+2715, already spoken for by
selection and error text -- see `qbit_ops.tui.formatting`, so a
checkbox inside a value-action modal never repeats the glyph its own
`✕ ...` verdict line uses for something unrelated) and the brand
accent rather than green/red: `.qbit-dialog RadioSet`'s own on-mark
rule in `qbit_ops.tcss` already states the reason, "no modal here has
green/red semantics, only the brand accent" -- this keeps that true
for every checkbox too, not only every radio button.
"""

from __future__ import annotations

from textual.widgets import Checkbox


class QbitCheckbox(Checkbox):
    """A `Checkbox` with a one-glyph, state-coloured button, not `▐X▌`."""

    BUTTON_LEFT = ""
    BUTTON_RIGHT = ""

    @property
    def BUTTON_INNER(self) -> str:
        return "✓" if self.value else "✗"
