"""The qbit-ops palette, expressed as the Textual theme the TUI runs on.

The direction matters: the theme is built *from* the brand, so
`$primary` resolves to qbit-ops' warm orange rather than Textual's own
blue. Everything downstream -- the stylesheet, `formatting.py`, the
widgets -- reads a value from here; nothing else in the TUI names a
brand colour.

Semantic colours (`$warning`, `$error`, `$success`) and every neutral
keep the values the app is designed against: they are the health/state
vocabulary and the dark ground, not the brand.
"""

from __future__ import annotations

from typing import Final

from textual.theme import Theme

THEME_NAME: Final = "qbit-ops"

# The brand gradient's two ends, warm orange to coral.
_BRAND_ORANGE: Final = "#ff9933"
_BRAND_CORAL: Final = "#d62839"

# The restrained blue that marks the *inactive* workspace tab and an
# idle transfer direction. Real information, not decoration, so it
# needs its own legible hue rather than the brand orange or a dim grey
# -- it is this theme's `$secondary`.
_BRAND_BLUE: Final = "#5fa8d3"

# Textual derives `$panel` from `$primary` when it is left unset, so a
# brand-orange primary would turn every separator and inactive border
# warm brown -- and an inactive border in the same family as the focus
# accent is *less* readable, not more. Pinned to the cool grey the app
# is designed against: this change unifies the brand, not the ground.
_PANEL: Final = "#242f38"

QBIT_OPS_THEME: Final = Theme(
    name=THEME_NAME,
    primary=_BRAND_ORANGE,
    secondary=_BRAND_BLUE,
    accent=_BRAND_CORAL,
    warning="#ffa62b",
    error="#ba3c5b",
    success="#4EBF71",
    foreground="#e0e0e0",
    panel=_PANEL,
    dark=True,
)

# Textual round-trips every theme colour through HSL, which can move a
# channel by one. Rich has no access to CSS variables and needs a
# literal hex, so it is given the *resolved* value rather than the
# declared one -- otherwise a `Text` style and the CSS rule beside it
# would differ by that one channel forever.
# Lower-cased: the repo writes hex in lower case, and a Rich style
# compared against a formatted `#{:02x}` triple must match exactly.
_RESOLVED: Final[dict[str, str]] = {
    name: value.lower()
    for name, value in QBIT_OPS_THEME.to_color_system().generate().items()
}


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


BRAND_ACCENT: Final = _RESOLVED["primary"]
"""`$primary`, resolved: the single accent for titles, focus and marks."""

BRAND_SECONDARY: Final = _RESOLVED["secondary"]
"""`$secondary`, resolved: inactive tab, idle transfer direction."""

# `BrandHeader` interpolates per column between these two, which is why
# they are RGB and why a single variable cannot carry them.
BRAND_GRADIENT_START: Final = _rgb(BRAND_ACCENT)
BRAND_GRADIENT_END: Final = _rgb(_RESOLVED["accent"])

# The gradient ends reach the stylesheet through
# `QbitOpsTuiApp.get_css_variables`; a `Theme` has one `primary` slot
# and cannot express the second end on its own.
BRAND_CSS_VARIABLES: Final[dict[str, str]] = {
    "brand-gradient-start": BRAND_ACCENT,
    "brand-gradient-end": _RESOLVED["accent"],
}
